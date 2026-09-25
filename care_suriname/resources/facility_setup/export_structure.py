"""Export the facility itself, its organisations, roles and places."""

from care.emr.models.healthcare_service import HealthcareService
from care.emr.models.location import FacilityLocation, FacilityLocationOrganization
from care.emr.models.organization import FacilityOrganization
from care.emr.models.patient import PatientIdentifierConfig
from care.emr.models.tag_config import TagConfig
from care.security.models.role import RoleModel, RolePermission
from care_suriname.resources.facility_setup.format import (
    FACILITY_ROOT,
    FacilitySetupError,
    tree_order,
)


def _name(instance):
    return instance.name if instance else None


def _org_ref(organization):
    """Departments by name; CARE's own facility root by a fixed marker."""
    if organization is None:
        return None
    return FACILITY_ROOT if organization.system_generated else organization.name


def export_facility(facility):
    geo_path, geo = [], facility.geo_organization
    while geo is not None:
        geo_path.insert(0, {"org_type": geo.org_type, "name": geo.name})
        geo = geo.parent
    return {
        "name": facility.name,
        "description": facility.description,
        "facility_type": facility.facility_type,
        "features": list(facility.features or []),
        "latitude": str(facility.latitude) if facility.latitude else None,
        "longitude": str(facility.longitude) if facility.longitude else None,
        "pincode": facility.pincode,
        "address": facility.address,
        "phone_number": facility.phone_number,
        "is_public": facility.is_public,
        "print_templates": facility.print_templates,
        "geo_organization_path": geo_path,
    }


def export_facility_organizations(facility):
    rows = [
        {
            "name": org.name,
            "org_type": org.org_type,
            "description": org.description,
            "metadata": org.metadata,
            # The system root ("Administration") is recreated by CARE itself.
            "parent": org.parent.name
            if org.parent and not org.parent.system_generated
            else None,
        }
        for org in FacilityOrganization.objects.filter(
            facility=facility, system_generated=False
        ).select_related("parent")
    ]
    names = [row["name"] for row in rows]
    if len(names) != len(set(names)):
        msg = "Two departments share a name; the import matches them by name."
        raise FacilitySetupError(msg)
    return tree_order(rows, lambda row: row["parent"])


def export_custom_roles():
    roles = []
    for role in RoleModel.objects.filter(is_system=False, is_archived=False):
        slugs = sorted(
            RolePermission.objects.filter(role=role).values_list(
                "permission__slug", flat=True
            )
        )
        roles.append(
            {
                "name": role.name,
                "description": role.description,
                "contexts": list(role.contexts or []),
                "permissions": slugs,
            }
        )
    return sorted(roles, key=lambda role: role["name"])


def export_identifier_configs(facility):
    configs = PatientIdentifierConfig.objects.filter(facility=facility)
    instance = PatientIdentifierConfig.objects.filter(facility__isnull=True)
    return [
        {"scope": "facility", "status": c.status, "config": c.config} for c in configs
    ] + [
        {"scope": "instance", "status": c.status, "config": c.config} for c in instance
    ]


def export_tag_configs(facility):
    """Active tags only: archived ones (for example old demo reasons) stay."""
    rows = []
    for tag in TagConfig.objects.filter(
        facility=facility, status="active"
    ).select_related("parent", "facility_organization", "organization"):
        if tag.organization_id:
            msg = f"Tag {tag.display!r} is tied to an instance organisation."
            raise FacilitySetupError(msg)
        rows.append(
            {
                "name": f"{tag.category}/{tag.display}",
                "display": tag.display,
                "description": tag.description,
                "category": tag.category,
                "priority": tag.priority,
                "resource": tag.resource,
                "status": tag.status,
                "metadata": tag.metadata,
                "facility_organization": _org_ref(tag.facility_organization),
                "parent": f"{tag.parent.category}/{tag.parent.display}"
                if tag.parent
                else None,
            }
        )
    return tree_order(rows, lambda row: row["parent"])


def export_locations(facility):
    rows = []
    for location in FacilityLocation.objects.filter(facility=facility).select_related(
        "parent"
    ):
        rows.append(
            {
                "name": location.name,
                "description": location.description,
                "status": location.status,
                "operational_status": location.operational_status,
                "mode": location.mode,
                "form": location.form,
                "location_type": location.location_type,
                "sort_index": location.sort_index,
                "metadata": location.metadata,
                "parent": _name(location.parent),
                "organizations": sorted(
                    _org_ref(link.organization)
                    for link in FacilityLocationOrganization.objects.filter(
                        location=location
                    ).select_related("organization")
                ),
            }
        )
    names = [row["name"] for row in rows]
    if len(names) != len(set(names)):
        msg = "Two locations share a name; the import matches them by name."
        raise FacilitySetupError(msg)
    return tree_order(rows, lambda row: row["parent"])


def export_healthcare_services(facility):
    location_names = dict(
        FacilityLocation.objects.filter(facility=facility).values_list("id", "name")
    )
    services = []
    for service in HealthcareService.objects.filter(facility=facility).select_related(
        "managing_organization"
    ):
        missing = [i for i in service.locations if i not in location_names]
        if missing:
            msg = f"Service {service.name!r} points at unknown locations."
            raise FacilitySetupError(msg)
        services.append(
            {
                "name": service.name,
                "service_type": service.service_type,
                "internal_type": service.internal_type,
                "extra_details": service.extra_details,
                "styling_metadata": service.styling_metadata,
                "locations": [location_names[i] for i in service.locations],
                "managing_organization": _org_ref(service.managing_organization),
            }
        )
    return services
