import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from care.facility.models import Facility
from care_suriname.clinical_text_catalogs.urology_turp import (
    TURP_CATALOG_VERSION,
    TURP_CLINICAL_TEXT_CATALOG,
)
from care_suriname.models.clinical_text import ClinicalTextResource
from care_suriname.resources.clinical_text import ClinicalTextResourceWriteSpec

# Databases this catalog may be written into. The declared catalog is a
# deterministic fixture for automated tests, not a mirror of production.
#
# Clinicians author the real Smart Text catalog through the UI, and that live
# content is the source of truth. The two are expected to drift: TURP's live
# template already uses a shared anesthesia list this fixture does not. Seeding
# a non-test database would overwrite clinician-authored content with an older
# declaration and record it as a routine version bump.
SEEDABLE_DATABASE_NAMES = frozenset({"care_test"})


class Command(BaseCommand):
    help = (
        "Seed the versioned TURP Smart Text fixture into a test database. "
        "Refuses non-test databases: the live catalog is authored through the "
        "Smart Text UI and is the source of truth."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-non-test-database",
            action="store_true",
            help=(
                "Seed a database that is not a known test database. This "
                "overwrites clinician-authored Smart Text content."
            ),
        )
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument(
            "--facility",
            action="append",
            dest="facilities",
            help="External facility UUID. Repeat for multiple facilities.",
        )
        target.add_argument(
            "--facility-name",
            action="append",
            dest="facility_names",
            help=(
                "Exact unique facility name. Repeat for multiple facilities; "
                "intended for fixture-driven environments with generated UUIDs."
            ),
        )
        parser.add_argument(
            "--user",
            required=True,
            help="Existing CARE username recorded as the provisioning actor.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self._require_seedable_database(
            allow_override=options["allow_non_test_database"]
        )
        facilities = self._resolve_facilities(options)
        actor = self._load_actor(options["user"])
        counts = {"created": 0, "updated": 0, "unchanged": 0}

        for facility in facilities:
            for definition in TURP_CLINICAL_TEXT_CATALOG:
                action = self._provision_resource(facility, actor, definition)
                counts[action] += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"TURP clinical-text catalog v{TURP_CATALOG_VERSION} provisioned "
                f"for {len(facilities)} facility/facilities: "
                f"{counts['created']} created, {counts['updated']} updated, "
                f"{counts['unchanged']} unchanged."
            )
        )

    def _require_seedable_database(self, *, allow_override):
        db_name = connections["default"].settings_dict["NAME"]
        # Django prefixes its throwaway test databases with "test_", so the
        # command's own test suite runs against e.g. "test_care_test".
        if db_name in SEEDABLE_DATABASE_NAMES or db_name.startswith("test_"):
            return
        if allow_override:
            self.stderr.write(
                self.style.WARNING(
                    f"Seeding '{db_name}', which is not a known test database. "
                    "Clinician-authored Smart Text content may be overwritten."
                )
            )
            return
        message = (
            f"Refusing to seed database '{db_name}'.\n\n"
            "This command writes a test fixture. The live Smart Text catalog is "
            "authored through the UI and is the source of truth, so seeding here "
            "would overwrite clinician-authored content with an older "
            "declaration and log it as a routine update.\n\n"
            f"Permitted databases: {', '.join(sorted(SEEDABLE_DATABASE_NAMES))}.\n"
            "Override deliberately with --allow-non-test-database."
        )
        raise CommandError(message)

    @staticmethod
    def _resolve_facilities(options):
        if options.get("facility_names"):
            return Command._load_facilities_by_name(options["facility_names"])
        facility_ids = Command._parse_facility_ids(options["facilities"])
        return Command._load_facilities(facility_ids)

    @staticmethod
    def _parse_facility_ids(raw_ids):
        facility_ids = []
        for raw_id in raw_ids:
            try:
                facility_id = uuid.UUID(raw_id)
            except (TypeError, ValueError) as error:
                message = f"Invalid facility UUID: {raw_id}"
                raise CommandError(message) from error
            if facility_id not in facility_ids:
                facility_ids.append(facility_id)
        return facility_ids

    @staticmethod
    def _load_facilities(facility_ids):
        facilities_by_id = {
            facility.external_id: facility
            for facility in Facility.objects.filter(external_id__in=facility_ids)
        }
        missing = [
            str(facility_id)
            for facility_id in facility_ids
            if facility_id not in facilities_by_id
        ]
        if missing:
            message = f"Facility/facilities not found: {', '.join(missing)}"
            raise CommandError(message)
        return [facilities_by_id[facility_id] for facility_id in facility_ids]

    @staticmethod
    def _load_facilities_by_name(raw_names):
        names = []
        for raw_name in raw_names:
            name = raw_name.strip()
            if not name:
                raise CommandError("Facility name cannot be empty")
            if name not in names:
                names.append(name)

        facilities = []
        for name in names:
            matches = list(Facility.objects.filter(name=name))
            if len(matches) != 1:
                message = (
                    f"Facility name must match exactly one facility: {name} "
                    f"({len(matches)} found)"
                )
                raise CommandError(message)
            facilities.append(matches[0])
        return facilities

    @staticmethod
    def _load_actor(username):
        actor = get_user_model().objects.filter(username=username).first()
        if actor is None:
            message = f"Provisioning user not found: {username}"
            raise CommandError(message)
        if not actor.is_superuser:
            raise CommandError("Provisioning user must be a CARE superuser")
        return actor

    @staticmethod
    def _provision_resource(facility, actor, definition):
        spec = ClinicalTextResourceWriteSpec.model_validate(
            {**definition, "facility": facility.external_id}
        )
        resource = (
            ClinicalTextResource.objects.select_for_update()
            .filter(facility=facility, kind=spec.kind.value, key=spec.key)
            .first()
        )
        desired = {
            "label": spec.label.strip(),
            "description": spec.description.strip(),
            "status": spec.status.value,
            "payload": spec.payload,
        }
        if resource is None:
            ClinicalTextResource.objects.create(
                facility=facility,
                kind=spec.kind.value,
                key=spec.key,
                version=1,
                created_by=actor,
                updated_by=actor,
                **desired,
            )
            return "created"
        if all(getattr(resource, field) == value for field, value in desired.items()):
            return "unchanged"

        for field, value in desired.items():
            setattr(resource, field, value)
        resource.version += 1
        resource.updated_by = actor
        resource.save(
            update_fields=[
                *desired,
                "version",
                "updated_by",
                "modified_date",
            ]
        )
        return "updated"
