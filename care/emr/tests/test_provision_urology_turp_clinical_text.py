from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from model_bakery import baker

from care.facility.models import Facility
from care_suriname.clinical_text_catalogs.urology_turp import (
    TURP_CLINICAL_TEXT_CATALOG,
    TURP_TEMPLATE_BODY,
)
from care_suriname.models.clinical_text import ClinicalTextResource


class ProvisionUrologyTurpClinicalTextTest(TestCase):
    def setUp(self):
        self.actor = baker.make(
            get_user_model(),
            is_superuser=True,
            username="catalog-provisioner",
        )
        self.facility = baker.make(Facility, created_by=self.actor)
        self.options = {
            "facilities": [str(self.facility.external_id)],
            "user": self.actor.username,
        }

    def test_command_creates_complete_idempotent_audited_catalog(self):
        call_command("provision_urology_turp_clinical_text", **self.options)
        call_command("provision_urology_turp_clinical_text", **self.options)

        resources = ClinicalTextResource.objects.filter(facility=self.facility)
        self.assertEqual(resources.count(), len(TURP_CLINICAL_TEXT_CATALOG))
        self.assertFalse(resources.exclude(version=1).exists())
        self.assertFalse(resources.exclude(created_by=self.actor).exists())
        self.assertFalse(resources.exclude(updated_by=self.actor).exists())

        template = resources.get(kind="template", key=".turp")
        self.assertEqual(template.payload["body"], TURP_TEMPLATE_BODY)
        self.assertIn("SPOELKATHETER", template.payload["body"])
        self.assertNotIn("HEMATURIEKATHETER", template.payload["body"])
        self.assertEqual(
            template.payload["scopes"],
            [
                "urology",
                "medisch-dossier",
                "operations",
            ],
        )

    def test_command_reconciles_drift_and_increments_only_changed_version(self):
        call_command("provision_urology_turp_clinical_text", **self.options)
        template = ClinicalTextResource.objects.get(
            facility=self.facility,
            kind="template",
            key=".turp",
        )
        template.label = "Verouderde TURP"
        template.status = "inactive"
        template.version = 7
        template.save(update_fields=["label", "status", "version", "modified_date"])

        call_command("provision_urology_turp_clinical_text", **self.options)

        template.refresh_from_db()
        self.assertEqual(template.label, "TURP operatieverloop")
        self.assertEqual(template.status, "active")
        self.assertEqual(template.version, 8)
        self.assertEqual(template.updated_by, self.actor)
        unchanged = ClinicalTextResource.objects.filter(
            facility=self.facility,
        ).exclude(id=template.id)
        self.assertFalse(unchanged.exclude(version=1).exists())

    def test_command_supports_unique_fixture_facility_name(self):
        call_command(
            "provision_urology_turp_clinical_text",
            facility_names=[self.facility.name],
            user=self.actor.username,
        )

        self.assertEqual(
            ClinicalTextResource.objects.filter(facility=self.facility).count(),
            len(TURP_CLINICAL_TEXT_CATALOG),
        )

    def test_command_rejects_invalid_or_missing_targets_before_writing(self):
        with self.assertRaisesRegex(CommandError, "Invalid facility UUID"):
            call_command(
                "provision_urology_turp_clinical_text",
                facilities=["not-a-uuid"],
                user=self.actor.username,
            )
        with self.assertRaisesRegex(CommandError, "Provisioning user not found"):
            call_command(
                "provision_urology_turp_clinical_text",
                facilities=[str(self.facility.external_id)],
                user="missing-user",
            )
        ordinary_user = baker.make(get_user_model(), username="ordinary-user")
        with self.assertRaisesRegex(CommandError, "must be a CARE superuser"):
            call_command(
                "provision_urology_turp_clinical_text",
                facilities=[str(self.facility.external_id)],
                user=ordinary_user.username,
            )

        self.assertFalse(ClinicalTextResource.objects.exists())
