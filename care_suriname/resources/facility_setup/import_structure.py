"""Create the facility, its organisations, roles and places."""

from care.emr.models.healthcare_service import HealthcareService
from care.emr.models.location import FacilityLocation, FacilityLocationOrganization
from care.emr.models.organization import FacilityOrganization, Organization
from care.emr.models.patient import PatientIdentifierConfig
from care.emr.models.tag_config import TagConfig
from care.facility.models import Facility
from care.security.models.permission import PermissionModel
from care.security.models.role import RoleModel, RolePermission
from care_suriname.resources.facility_setup.format import FacilitySetupError

FACILITY_FIELDS = (
    "description",
    "facility_type",
    "features",
    "latitude",
    "longitude",
    "pincode",
    "address",
    "phone_number",
    "is_public",
    "print_templates",
)


def find_or_create_organization(org_type, name, parent, admin):
    existing = (
        Organization.objects.filter(org_type=org_type, name=name, parent=parent)
        .order_by("id")
        .first()
    )
    if existing:
        return existing
    return Organization.objects.create(
        org_type=org_type, name=name, parent=parent, created_by=admin
    )


def create_facility(data, name, admin):
    if Facility.objects.filter(name=name).exists():
        msg = f"A facility named {name!r} already exists."
        raise FacilitySetupError(msg)
    geo = None
    for level in data["geo_organization_path"]:
        geo = find_or_create_organization(level["org_type"], level["name"], geo, admin)
    facility = Facility(
        name=name,
        geo_organization=geo,
        created_by=admin,
        **{field: data[field] for field in FACILITY_FIELDS},
    )
    facility.save()
    return facility


def create_facility_organizations(rows, facility, admin):
    created = {}
    for row in rows:
        created[row["name"]] = FacilityOrganization.objects.create(
            facility=facility,
            name=row["name"],
            org_type=row["org_type"],
            description=row["description"],
            metadata=row["metadata"],
            parent=created.get(row["parent"]),
            created_by=admin,
        )
    return created


def create_roles(rows):
    """Reuse an identical role; refuse one that differs rather than merge."""
    for row in rows:
        permissions = list(PermissionModel.objects.filter(slug__in=row["permissions"]))
        if len(permissions) != len(row["permissions"]):
            msg = f"Role {row['name']!r} needs permissions this server lacks."
            raise FacilitySetupError(msg)
        existing = RoleModel.objects.filter(name=row["name"]).first()
        if existing:
            current = set(
                RolePermission.objects.filter(role=existing).values_list(
                    "permission__slug", flat=True
                )
            )
            if current != set(row["permissions"]):
                msg = f"Role {row['name']!r} exists with other permissions."
                raise FacilitySetupError(msg)
            continue
        role = RoleModel.objects.create(
            name=row["name"],
            description=row["description"],
            contexts=row["contexts"],
            is_system=False,
        )
        RolePermission.objects.bulk_create(
            RolePermission(role=role, permission=permission)
            for permission in permissions
        )


def create_identifier_configs(rows, facility, admin):
    for row in rows:
        facility_scope = row["scope"] == "facility"
        if not facility_scope:
            system = (row["config"] or {}).get("system")
            if PatientIdentifierConfig.objects.filter(
                facility__isnull=True, config__system=system
            ).exists():
                continue
        PatientIdentifierConfig.objects.create(
            facility=facility if facility_scope else None,
            status=row["status"],
            config=row["config"],
            created_by=admin,
        )


def create_tag_configs(rows, facility, organizations, admin):
    created = {}
    for row in rows:
        created[row["name"]] = TagConfig.objects.create(
            facility=facility,
            facility_organization=organizations.get(row["facility_organization"]),
            parent=created.get(row["parent"]),
            display=row["display"],
            description=row["description"],
            category=row["category"],
            priority=row["priority"],
            resource=row["resource"],
            status=row["status"],
            metadata=row["metadata"],
            created_by=admin,
        )


LOCATION_FIELDS = (
    "name",
    "description",
    "status",
    "operational_status",
    "mode",
    "form",
    "location_type",
    "sort_index",
    "metadata",
)


def create_locations(rows, facility, organizations, admin):
    created = {}
    for row in rows:
        location = FacilityLocation.objects.create(
            facility=facility,
            parent=created.get(row["parent"]),
            created_by=admin,
            **{field: row[field] for field in LOCATION_FIELDS},
        )
        for name in row["organizations"]:
            FacilityLocationOrganization.objects.create(
                location=location, organization=organizations[name]
            )
        created[row["name"]] = location
    return created


def create_healthcare_services(rows, facility, organizations, locations, admin):
    created = {}
    for row in rows:
        created[row["name"]] = HealthcareService.objects.create(
            facility=facility,
            name=row["name"],
            service_type=row["service_type"],
            internal_type=row["internal_type"],
            extra_details=row["extra_details"],
            styling_metadata=row["styling_metadata"],
            locations=[locations[name].id for name in row["locations"]],
            managing_organization=organizations.get(row["managing_organization"]),
            created_by=admin,
        )
    return created
