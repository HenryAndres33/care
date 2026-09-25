from io import StringIO
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from model_bakery import baker

from care.emr.models.patient import (
    PatientIdentifier,
    PatientIdentifierConfig,
    PatientIdentifierConfigCache,
)
from care.emr.models.questionnaire import FormSubmission, Questionnaire
from care.emr.resources.form_submission.spec import FormSubmissionStatusChoices
from care.utils.tests.base import CareAPITestBase
from care_suriname.reports.form_submission_artifact import (
    build_form_submission_artifact_html,
)
from care_suriname.resources.dossier_number import DOSSIER_NUMBER_SYSTEM


def provision(*args):
    output = StringIO()
    call_command("provision_dossier_number_identifier", *args, stdout=output)
    return output.getvalue()


def dossier_configs():
    return PatientIdentifierConfig.objects.filter(config__system=DOSSIER_NUMBER_SYSTEM)


class DossierNumberTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        PatientIdentifierConfigCache.clear_cache()
        self.user = self.create_user(username="dossier-admin", is_superuser=True)

    def test_dry_run_changes_nothing(self):
        self.assertIn("dry-run", provision())
        self.assertFalse(dossier_configs().exists())

    def test_apply_creates_one_instance_identifier_and_is_idempotent(self):
        self.assertIn("created", provision("--apply", "--user", "dossier-admin"))
        self.assertIn("present", provision("--apply", "--user", "dossier-admin"))
        config = dossier_configs().get()
        self.assertIsNone(config.facility)
        self.assertEqual(config.status, "active")
        self.assertEqual(config.config["display"], "Dossiernummer")
        self.assertEqual(config.config["use"], "official")
        self.assertTrue(config.config["unique"])
        self.assertFalse(config.config.get("required", False))

    def test_apply_requires_a_superuser(self):
        with self.assertRaises(CommandError):
            provision("--apply", "--user", "nobody")
        self.assertFalse(dossier_configs().exists())

    def test_note_pdf_header_carries_the_dossier_number(self):
        provision("--apply", "--user", "dossier-admin")
        facility = self.create_facility(user=self.user)
        organization = self.create_facility_organization(facility=facility)
        patient = self.create_patient(name="Synthetische Patiënt")
        baker.make(
            PatientIdentifier,
            patient=patient,
            config=dossier_configs().get(),
            value="AZP-000123",
        )
        patient.build_instance_identifiers()
        patient.save()
        encounter = self.create_encounter(
            patient=patient, facility=facility, organization=organization
        )
        encounter.period = {"start": "2026-09-25T09:30:00Z"}
        encounter.save(update_fields=["period"])
        submission = FormSubmission(
            questionnaire=baker.make(Questionnaire, slug="dossier-note"),
            patient=patient,
            encounter=encounter,
            status=FormSubmissionStatusChoices.submitted.value,
            response_dump={"anamnese": "Synthetisch"},
            workflow_finalized_at=timezone.now(),
            workflow_finalized_by=self.user,
            created_by=self.user,
        )

        html = build_form_submission_artifact_html(
            artifact_id=uuid4(), submission=submission, generated_at=timezone.now()
        )

        self.assertIn("Patiëntnr. AZP-000123", html)
