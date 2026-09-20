from datetime import date
from uuid import UUID

from django.db import connection, transaction

from care.emr.models.activity_definition import ActivityDefinition
from care.emr.models.encounter import Encounter, EncounterOrganization
from care.emr.models.observation_definition import ObservationDefinition
from care.emr.models.organization import (
    FacilityOrganization,
    FacilityOrganizationUser,
    Organization,
    OrganizationUser,
)
from care.emr.models.patient import Patient
from care.facility.models import Facility
from care.security.models import PermissionModel, RoleModel, RolePermission
from care.users.models import User

PREFIX = "LAB-BROWSER-MATRIX-20260920"
ID_NAMES = """geo facility root_org clinical_org doctor_role_org denied_role_org
read_role doctor denied patient active_encounter closed_encounter hb_gdl hb_mmol group"""
IDS = {
    name: UUID(f"51000000-0000-4000-8000-{index:012d}")
    for index, name in enumerate(ID_NAMES.split(), start=1)
}
READ_PERMISSION_NAMES = """can_list_patients can_view_clinical_data
can_list_encounter can_read_encounter can_read_encounter_clinical_data
can_read_service_request can_read_diagnostic_report can_read_facility
can_view_facility_organization can_read_activity_definition
can_read_observation_definition"""
READ_PERMISSION_SLUGS = set(READ_PERMISSION_NAMES.split())


def _ensure_isolated_empty_or_owned():
    if connection.settings_dict["NAME"] != "care_test":
        raise RuntimeError("Refusing to provision outside care_test.")
    ownership = (
        (User.objects.get_entire_queryset(), {IDS["doctor"], IDS["denied"]}),
        (Facility._base_manager.all(), {IDS["facility"]}),  # noqa: SLF001
        (Patient._base_manager.all(), {IDS["patient"]}),  # noqa: SLF001
        (
            Encounter._base_manager.all(),  # noqa: SLF001
            {IDS["active_encounter"], IDS["closed_encounter"]},
        ),
    )
    for queryset, allowed in ownership:
        found = set(queryset.values_list("external_id", flat=True))
        if found - allowed:
            raise RuntimeError("care_test contains records outside this fixture.")


def _create_user(*, external_id, username, password, user_type):
    user = User.objects.filter(external_id=external_id).first()
    if user:
        if user.username != username or not user.check_password(password):
            message = f"Existing fixture user mismatch: {username}"
            raise RuntimeError(message)
        return user
    return User.objects.create_user(
        external_id=external_id,
        username=username,
        password=password,
        email=f"{username}@isolated.test",
        first_name="Laboratory",
        last_name=user_type.title(),
        phone_number="+5970000001" if user_type == "doctor" else "+5970000002",
        gender="male",
        user_type=user_type,
        is_staff=False,
        is_superuser=False,
    )


def _create_definition(facility, *, key, slug, title, loinc, unit):
    definition, created = ObservationDefinition.objects.get_or_create(
        external_id=IDS[key],
        defaults={
            "facility": facility,
            "slug": f"f-{facility.external_id}-{slug}",
            "version": 1,
            "title": title,
            "status": "active",
            "description": f"{PREFIX} synthetic definition",
            "category": "laboratory",
            "code": {
                "system": "http://loinc.org",
                "code": loinc,
                "display": title,
            },
            "permitted_data_type": "quantity",
            "permitted_unit": {
                "system": "http://unitsofmeasure.org",
                "code": unit,
                "display": unit,
            },
            "derived_from_uri": "",
            "qualified_ranges": [],
        },
    )
    if not created and (
        definition.facility_id != facility.id
        or definition.status != "active"
        or definition.version != 1
    ):
        message = f"Existing fixture definition mismatch: {key}"
        raise RuntimeError(message)
    return definition


@transaction.atomic
def provision(*, doctor_password, denied_password):
    if not doctor_password or not denied_password:
        raise RuntimeError("Both fixture passwords are required.")
    _ensure_isolated_empty_or_owned()
    doctor_role = RoleModel.objects.get(name="Doctor")
    staff_role = RoleModel.objects.get(name="Staff")

    read_role, role_created = RoleModel.objects.get_or_create(
        external_id=IDS["read_role"],
        defaults={
            "name": f"{PREFIX} Read Only",
            "description": "Isolated laboratory browser negative-control role",
            "contexts": ["FACILITY"],
            "is_system": False,
        },
    )
    permissions = PermissionModel.objects.filter(slug__in=READ_PERMISSION_SLUGS)
    if set(permissions.values_list("slug", flat=True)) != READ_PERMISSION_SLUGS:
        raise RuntimeError("Required read-only permissions are unavailable.")
    if role_created:
        RolePermission.objects.bulk_create(
            [RolePermission(role=read_role, permission=item) for item in permissions]
        )
    elif (
        set(
            RolePermission.objects.filter(
                role=read_role, temp_deleted=False
            ).values_list("permission__slug", flat=True)
        )
        != READ_PERMISSION_SLUGS
    ):
        raise RuntimeError("Existing fixture read-only role permissions differ.")

    geo, _ = Organization.objects.get_or_create(
        external_id=IDS["geo"],
        defaults={"name": PREFIX, "org_type": "govt"},
    )
    doctor = _create_user(
        external_id=IDS["doctor"],
        username="lab-browser-doctor",
        password=doctor_password,
        user_type="doctor",
    )
    denied = _create_user(
        external_id=IDS["denied"],
        username="lab-browser-readonly",
        password=denied_password,
        user_type="staff",
    )
    doctor.geo_organization = geo
    denied.geo_organization = geo
    doctor.save(update_fields=["geo_organization"])
    denied.save(update_fields=["geo_organization"])

    facility, facility_created = Facility.objects.get_or_create(
        external_id=IDS["facility"],
        defaults={
            "name": PREFIX,
            "description": "Isolated laboratory browser fixture",
            "facility_type": 3,
            "features": [],
            "address": "Synthetic isolated test address",
            "phone_number": "+5970000000",
            "geo_organization": geo,
            "created_by": doctor,
        },
    )
    if not facility_created and facility.name != PREFIX:
        raise RuntimeError("Existing fixture facility mismatch.")
    root = facility.default_internal_organization
    if facility_created:
        root.external_id = IDS["root_org"]
        root.save(update_fields=["external_id"])
        root_membership = FacilityOrganizationUser.objects.get(
            organization=root, user=doctor
        )
        root_membership.role = doctor_role
        root_membership.save(update_fields=["role"])
    elif root.external_id != IDS["root_org"]:
        raise RuntimeError("Existing fixture root organization mismatch.")

    clinical, _ = FacilityOrganization.objects.get_or_create(
        external_id=IDS["clinical_org"],
        defaults={
            "facility": facility,
            "parent": root,
            "name": f"{PREFIX} Urology",
            "org_type": "dept",
        },
    )
    FacilityOrganizationUser.objects.get_or_create(
        organization=clinical, user=doctor, defaults={"role": doctor_role}
    )
    FacilityOrganizationUser.objects.get_or_create(
        organization=clinical, user=denied, defaults={"role": read_role}
    )
    for key, name, user, role in (
        ("doctor_role_org", "Doctor", doctor, doctor_role),
        ("denied_role_org", "Staff", denied, staff_role),
    ):
        role_org, _ = Organization.objects.get_or_create(
            external_id=IDS[key], defaults={"name": name, "org_type": "role"}
        )
        OrganizationUser.objects.get_or_create(
            organization=role_org, user=user, defaults={"role": role}
        )

    patient, _ = Patient.objects.get_or_create(
        external_id=IDS["patient"],
        defaults={
            "name": f"{PREFIX} Patient",
            "gender": "male",
            "date_of_birth": date(1980, 5, 4),
            "phone_number": "+5970000010",
            "address": "Synthetic isolated test address",
            "blood_group": "O_POS",
            "geo_organization": geo,
        },
    )
    encounters = {}
    for key, status in (
        ("active_encounter", "in_progress"),
        ("closed_encounter", "completed"),
    ):
        encounter, _ = Encounter.objects.get_or_create(
            external_id=IDS[key],
            defaults={
                "patient": patient,
                "facility": facility,
                "status": status,
                "encounter_class": "imp",
                "priority": "routine",
                "external_identifier": f"{PREFIX}-{status}",
                "created_by": doctor,
            },
        )
        EncounterOrganization.objects.get_or_create(
            encounter=encounter, organization=clinical
        )
        encounters[key] = encounter

    definitions = [
        _create_definition(
            facility,
            key="hb_gdl",
            slug="hemoglobin-gdl",
            title="Hemoglobine g/dL",
            loinc="718-7",
            unit="g/dL",
        ),
        _create_definition(
            facility,
            key="hb_mmol",
            slug="hemoglobin-mmol",
            title="Hemoglobine mmol/L",
            loinc="59260-0",
            unit="mmol/L",
        ),
    ]
    ActivityDefinition.objects.get_or_create(
        external_id=IDS["group"],
        defaults={
            "facility": facility,
            "slug": f"f-{facility.external_id}-blood-count",
            "version": 1,
            "title": "Bloedbeeld",
            "classification": "laboratory",
            "status": "active",
            "description": f"{PREFIX} synthetic group",
            "usage": "Isolated browser test only",
            "kind": "service_request",
            "observation_result_requirements": [item.id for item in definitions],
        },
    )
    facility.sync_cache()
    return {
        "facility": str(facility.external_id),
        "organization": str(clinical.external_id),
        "patient": str(patient.external_id),
        "active_encounter": str(encounters["active_encounter"].external_id),
        "closed_encounter": str(encounters["closed_encounter"].external_id),
        "doctor": str(doctor.external_id),
        "denied": str(denied.external_id),
        "definitions": [str(item.external_id) for item in definitions],
        "group": str(IDS["group"]),
    }
