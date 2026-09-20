import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from care.emr.models.activity_definition import ActivityDefinition
from care.emr.models.observation_definition import ObservationDefinition
from care.facility.models import Facility
from care_suriname.resources.laboratory_reference import get_reference_metadata
from care_suriname.resources.laboratory_reference.configuration import (
    CATALOGUE_VERSION,
    METADATA_KEY,
    METADATA_NAMESPACE,
)

HB_LOINC = "59260-0"
HB_UNIT_SYSTEM = "http://unitsofmeasure.org"
HB_UNIT_CODE = "mmol/L"
HB_SLUG_VALUE = "hemoglobine-mmol-l-general-academic-adult-v1"


class Command(BaseCommand):
    help = (
        "Plan, apply, or roll back the separate governed Hb mmol/L definition. "
        "Dry-run is the default; apply/rollback require explicit flags."
    )

    def add_arguments(self, parser):
        parser.add_argument("--facility", required=True)
        parser.add_argument(
            "--activity-definition",
            action="append",
            required=True,
            dest="activity_definitions",
            help="Exact full slug for a facility laboratory ActivityDefinition; repeatable.",
        )
        parser.add_argument(
            "--user",
            required=True,
            help="Existing CARE superuser recorded as configuration actor.",
        )
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true")
        mode.add_argument("--rollback", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        facility = self._facility(options["facility"])
        groups = self._groups(facility, options["activity_definitions"])
        actor = self._actor(options["user"])
        definition = self._definition(facility)
        metadata = get_reference_metadata(HB_LOINC, HB_UNIT_SYSTEM, HB_UNIT_CODE)
        mode = (
            "rollback"
            if options["rollback"]
            else "apply"
            if options["apply"]
            else "dry-run"
        )
        self.stdout.write(self._plan(mode, facility, groups, definition, metadata))
        if mode == "dry-run":
            transaction.set_rollback(True)
            return
        if mode == "apply":
            definition, created = self._apply(
                facility, groups, actor, definition, metadata
            )
            action = "created" if created else "reused"
            self.stdout.write(
                self.style.SUCCESS(f"Hb mmol/L definition {action}: {definition.slug}")
            )
            return
        self._rollback(groups, actor, definition)
        self.stdout.write(
            self.style.SUCCESS("Hb mmol/L definition unlinked and retired.")
        )

    @staticmethod
    def _facility(raw_id):
        try:
            external_id = uuid.UUID(raw_id)
        except (TypeError, ValueError) as error:
            message = f"Invalid facility UUID: {raw_id}"
            raise CommandError(message) from error
        facility = Facility.objects.filter(external_id=external_id).first()
        if facility is None:
            message = f"Facility not found: {external_id}"
            raise CommandError(message)
        return facility

    @staticmethod
    def _groups(facility, slugs):
        unique = tuple(dict.fromkeys(slugs))
        groups = list(
            ActivityDefinition.objects.select_for_update().filter(
                facility=facility,
                slug__in=unique,
                classification="laboratory",
                status="active",
                latest=True,
            )
        )
        by_slug = {group.slug: group for group in groups}
        missing = [slug for slug in unique if slug not in by_slug]
        if missing:
            raise CommandError(
                "Active latest facility laboratory ActivityDefinition not found: "
                + ", ".join(missing)
            )
        return tuple(by_slug[slug] for slug in unique)

    @staticmethod
    def _actor(username):
        actor = get_user_model().objects.filter(username=username).first()
        if actor is None or not actor.is_superuser:
            raise CommandError("Configuration actor must be an existing CARE superuser")
        return actor

    @staticmethod
    def _definition(facility):
        slug = ObservationDefinition.calculate_slug_from_facility(
            facility.external_id, HB_SLUG_VALUE
        )
        return (
            ObservationDefinition.objects.select_for_update()
            .filter(facility=facility, slug=slug)
            .first()
        )

    @staticmethod
    def _plan(mode, facility, groups, definition, metadata):
        state = "absent" if definition is None else f"present:{definition.status}"
        return (
            f"mode={mode} catalogue={CATALOGUE_VERSION} "
            f"target_reference={metadata['catalogue_version']} "
            f"facility={facility.external_id} "
            f"definition={state} loinc={HB_LOINC} unit={HB_UNIT_SYSTEM}|{HB_UNIT_CODE} "
            f"groups={','.join(group.slug for group in groups)}"
        )

    @classmethod
    def _apply(cls, facility, groups, actor, definition, metadata):
        desired = cls._desired(facility, actor, metadata)
        created = definition is None
        if created:
            definition = ObservationDefinition.objects.create(**desired)
        else:
            if definition.status not in ("active", "retired"):
                message = (
                    "Existing Hb mmol/L definition has unsupported status: "
                    f"{definition.status}"
                )
                raise CommandError(message)
            cls._require_exact(definition, desired)
            if definition.status == "retired":
                definition.status = "active"
                definition.updated_by = actor
                definition.save(update_fields=["status", "updated_by", "modified_date"])
        for group in groups:
            if definition.pk not in group.observation_result_requirements:
                group.observation_result_requirements = [
                    *group.observation_result_requirements,
                    definition.pk,
                ]
                group.updated_by = actor
                group.save(
                    update_fields=[
                        "observation_result_requirements",
                        "updated_by",
                        "modified_date",
                    ]
                )
        return definition, created

    @staticmethod
    def _desired(facility, actor, metadata):
        return {
            "facility": facility,
            "version": 1,
            "slug": ObservationDefinition.calculate_slug_from_facility(
                facility.external_id, HB_SLUG_VALUE
            ),
            "title": "Hemoglobine (mmol/L)",
            "status": "active",
            "description": "Separate Hb mmol/L definition; patient results are never converted.",
            "derived_from_uri": metadata["sources"][0]["url"],
            "category": "laboratory",
            "code": {
                "system": "http://loinc.org",
                "code": HB_LOINC,
                "display": "Hemoglobine [Moles/volume] in Blood",
            },
            "permitted_data_type": "quantity",
            "permitted_unit": {
                "system": HB_UNIT_SYSTEM,
                "code": HB_UNIT_CODE,
                "display": HB_UNIT_CODE,
            },
            "qualified_ranges": [],
            "meta": {METADATA_NAMESPACE: {METADATA_KEY: metadata}},
            "created_by": actor,
            "updated_by": actor,
        }

    @staticmethod
    def _require_exact(definition, desired):
        fields = (
            "title",
            "description",
            "derived_from_uri",
            "category",
            "code",
            "permitted_data_type",
            "permitted_unit",
            "qualified_ranges",
        )
        changed = [
            field for field in fields if getattr(definition, field) != desired[field]
        ]
        actual_reference = definition.meta.get(METADATA_NAMESPACE, {}).get(METADATA_KEY)
        desired_reference = desired["meta"][METADATA_NAMESPACE][METADATA_KEY]
        if actual_reference != desired_reference:
            changed.append("governed_reference_metadata")
        if changed:
            raise CommandError(
                "Existing Hb mmol/L definition differs; refusing overwrite: "
                + ", ".join(changed)
            )

    @staticmethod
    def _rollback(groups, actor, definition):
        if definition is None:
            raise CommandError("Governed Hb mmol/L definition is absent")
        metadata = get_reference_metadata(HB_LOINC, HB_UNIT_SYSTEM, HB_UNIT_CODE)
        desired = Command._desired(definition.facility, actor, metadata)
        Command._require_exact(definition, desired)
        for group in groups:
            if definition.pk in group.observation_result_requirements:
                group.observation_result_requirements = [
                    pk
                    for pk in group.observation_result_requirements
                    if pk != definition.pk
                ]
                group.updated_by = actor
                group.save(
                    update_fields=[
                        "observation_result_requirements",
                        "updated_by",
                        "modified_date",
                    ]
                )
        definition.status = "retired"
        definition.updated_by = actor
        definition.save(update_fields=["status", "updated_by", "modified_date"])
