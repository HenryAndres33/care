from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from model_bakery import baker

from care.emr.models.activity_definition import ActivityDefinition
from care.emr.models.observation_definition import ObservationDefinition
from care.facility.models import Facility
from care_suriname.management.commands.configure_laboratory_reference_catalogue import (
    HB_SLUG_VALUE,
)


class ConfigureLaboratoryReferenceCatalogueTests(TestCase):
    def setUp(self):
        self.actor = baker.make(
            get_user_model(), username="reference-admin", is_superuser=True
        )
        self.facility = baker.make(Facility, created_by=self.actor)
        self.group = baker.make(
            ActivityDefinition,
            facility=self.facility,
            slug=ActivityDefinition.calculate_slug_from_facility(
                self.facility.external_id, "bloedbeeld"
            ),
            classification="laboratory",
            status="active",
            latest=True,
            observation_result_requirements=[],
        )

    def run_command(self, *extra):
        stdout = StringIO()
        call_command(
            "configure_laboratory_reference_catalogue",
            "--facility",
            str(self.facility.external_id),
            "--activity-definition",
            self.group.slug,
            "--user",
            self.actor.username,
            *extra,
            stdout=stdout,
        )
        return stdout.getvalue()

    def definition(self):
        slug = ObservationDefinition.calculate_slug_from_facility(
            self.facility.external_id, HB_SLUG_VALUE
        )
        return ObservationDefinition.objects.filter(slug=slug).first()

    def test_default_dry_run_is_read_only_and_reports_exact_plan(self):
        output = self.run_command()
        self.assertIn("mode=dry-run", output)
        self.assertIn("catalogue=general-academic-adult-v2", output)
        self.assertIn("target_reference=general-academic-adult-v1", output)
        self.assertIn("loinc=59260-0", output)
        self.assertIn("unit=http://unitsofmeasure.org|mmol/L", output)
        self.assertIsNone(self.definition())

    def test_apply_is_idempotent_and_rollback_unlinks_then_retires(self):
        self.run_command("--apply")
        definition = self.definition()
        self.assertEqual(definition.status, "active")
        self.assertEqual(definition.code["code"], "59260-0")
        self.assertEqual(definition.permitted_unit["code"], "mmol/L")
        self.assertEqual(definition.qualified_ranges, [])
        self.assertIn("laboratory_reference", definition.meta["care_suriname"])
        self.assertEqual(
            definition.meta["care_suriname"]["laboratory_reference"][
                "catalogue_version"
            ],
            "general-academic-adult-v1",
        )
        self.group.refresh_from_db()
        self.assertEqual(self.group.observation_result_requirements, [definition.pk])
        original_external_id = definition.external_id
        definition.history = {"native_history_marker": "preserve"}
        definition.save(update_fields=["history", "modified_date"])

        second = self.run_command("--apply")
        self.assertIn("reused", second)
        self.group.refresh_from_db()
        self.assertEqual(self.group.observation_result_requirements, [definition.pk])

        self.run_command("--rollback")
        definition.refresh_from_db()
        self.group.refresh_from_db()
        self.assertEqual(definition.status, "retired")
        self.assertEqual(definition.external_id, original_external_id)
        self.assertFalse(definition.deleted)
        self.assertEqual(definition.history, {"native_history_marker": "preserve"})
        self.assertEqual(self.group.observation_result_requirements, [])

        self.run_command("--apply")
        definition.refresh_from_db()
        self.assertEqual(definition.status, "active")
        self.assertEqual(
            ObservationDefinition.objects.filter(slug=definition.slug).count(), 1
        )

    def test_rollback_refuses_a_mismatched_reserved_definition(self):
        self.run_command("--apply")
        definition = self.definition()
        definition.permitted_unit = {
            "system": "http://unitsofmeasure.org",
            "code": "g/dL",
            "display": "g/dL",
        }
        definition.save(update_fields=["permitted_unit", "modified_date"])

        with self.assertRaisesMessage(
            CommandError, "refusing overwrite: permitted_unit"
        ):
            self.run_command("--rollback")
        self.group.refresh_from_db()
        definition.refresh_from_db()
        self.assertEqual(definition.status, "active")
        self.assertIn(definition.pk, self.group.observation_result_requirements)


if __name__ == "__main__":
    import unittest

    unittest.main()
