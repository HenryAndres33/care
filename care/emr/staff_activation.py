"""Administrator-approved activation using native CARE identity and memberships."""

import json

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from care.emr.models.organization import (
    FacilityOrganizationUser,
    Organization,
    OrganizationUser,
)
from care.security.models import RoleModel
from care.users.models import User
from care.utils.shortcuts import get_object_or_404


def require_activation_admin(actor):
    if (
        not actor.is_superuser
        or not actor.is_active
        or actor.deleted
        or actor.is_service_account
    ):
        raise PermissionDenied("Only a system administrator may activate doctors")


def _configuration(user, facility_id):
    if (
        user.deleted
        or not user.is_active
        or user.is_service_account
        or user.is_superuser
        or not user.first_name.strip()
        or not user.last_name.strip()
    ):
        return None
    appointments = list(
        FacilityOrganizationUser.objects.select_for_update()
        .select_related("organization__facility", "role")
        .filter(
            user=user,
            organization__facility__external_id=facility_id,
            organization__facility__deleted=False,
            organization__facility__is_active=True,
            organization__deleted=False,
            organization__active=True,
            role__name="Doctor",
            role__is_system=True,
            role__deleted=False,
            role__is_archived=False,
            role__temp_deleted=False,
            role__contexts__contains=["FACILITY"],
        )
        .exclude(organization__org_type="root")
    )
    organizations = list(
        Organization.objects.select_for_update().filter(
            name="Doctor", org_type="role", active=True
        )
    )
    roles = list(
        RoleModel.objects.select_for_update().filter(
            name="Member",
            is_system=True,
            is_archived=False,
            temp_deleted=False,
            contexts__contains=["ROLE_ORG"],
        )
    )
    if not appointments or len(organizations) != 1 or len(roles) != 1:
        return None
    organization, role = organizations[0], roles[0]
    if not {"can_read_questionnaire", "can_submit_questionnaire"}.issubset(
        role.get_permission_sk_for_role()
    ):
        return None
    links = list(
        OrganizationUser.objects.select_for_update().filter(
            user=user, organization=organization
        )
    )
    if len(links) > 1 or (links and links[0].role_id != role.id):
        return None
    return organization, role, links


@transaction.atomic
def doctor_activation(*, actor, username, facility_id, activate=False):
    require_activation_admin(actor)
    user = get_object_or_404(
        User.objects.select_for_update(), username=username, deleted=False
    )
    configuration = _configuration(user, facility_id)
    status = "review"
    if configuration:
        organization, role, links = configuration
        status = "configured" if links and user.verified else "missing"
    if activate and status == "review":
        raise ValidationError(
            "Doctor identity or membership requires administrator review"
        )
    if activate and status == "missing":
        was_verified = user.verified
        if not links:
            OrganizationUser.objects.create(
                user=user, organization=organization, role=role
            )
        if not user.verified:
            user.verified = True
            user.save(update_fields=["verified"])
        # Audit and changes commit together. Never include credentials or patient data.
        LogEntry.objects.create(
            user_id=actor.pk,
            content_type=ContentType.objects.get_for_model(User),
            object_id=str(user.pk),
            object_repr=user.username,
            action_flag=CHANGE,
            change_message=json.dumps(
                {
                    "action": "doctor_clinical_activation",
                    "facility": str(facility_id),
                    "previously_verified": was_verified,
                    "member_added": not bool(links),
                }
            ),
        )
        status = "configured"
    return {"username": user.username, "status": status, "verified": user.verified}
