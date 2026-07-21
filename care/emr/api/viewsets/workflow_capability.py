import uuid

from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from care.emr.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care.emr.models.organization import FacilityOrganizationUser
from care.emr.workflow_capabilities import (
    correspondence_delivery_enabled,
    workflow_mutations_enabled,
)
from care.facility.models import Facility
from care.utils.shortcuts import get_object_or_404

UUID_V4 = 4


class WorkflowCapabilityViewSet(ClinicalNoStoreResponseMixin, viewsets.ViewSet):
    """Expose PHI-free facility rollout state to authenticated clients."""

    def list(self, request, *args, **kwargs):
        if set(request.query_params) != {"facility"}:
            raise ValidationError("Exactly one facility query parameter is required")
        try:
            facility_id = uuid.UUID(request.query_params["facility"])
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError("facility must be a UUIDv4") from exc
        if facility_id.version != UUID_V4:
            raise ValidationError("facility must be a UUIDv4")
        facility = get_object_or_404(
            Facility,
            external_id=facility_id,
            deleted=False,
            is_active=True,
        )
        if not FacilityOrganizationUser.objects.filter(
            user=request.user,
            organization__facility=facility,
            organization__active=True,
            role__deleted=False,
            role__is_archived=False,
        ).exists():
            raise PermissionDenied("Verified facility membership is required")

        workflow_enabled = workflow_mutations_enabled(facility.external_id)
        delivery_enabled = correspondence_delivery_enabled(facility.external_id)
        if delivery_enabled and not workflow_enabled:
            # Invalid deployment configuration must never advertise a usable send path.
            delivery_enabled = False
        return Response(
            {
                "facility": str(facility.external_id),
                "workflow_mutations": {
                    "enabled": workflow_enabled,
                    "blocker_code": (
                        None if workflow_enabled else "workflow_mutations_disabled"
                    ),
                },
                "correspondence_delivery": {
                    "enabled": delivery_enabled,
                    "blocker_code": (
                        None if delivery_enabled else "correspondence_delivery_disabled"
                    ),
                },
            }
        )
