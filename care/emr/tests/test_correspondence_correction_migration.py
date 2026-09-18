from http import HTTPStatus
from uuid import uuid4

from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.urls import reverse
from model_bakery import baker
from rest_framework.test import APIClient

from care.emr.models.questionnaire import (
    FormSubmission,
    FormSubmissionCommand,
    Questionnaire,
)
from care.emr.resources.form_submission.commands import (
    finalized_form_submission_snapshot_hash,
)
from care.emr.resources.form_submission.spec import FormSubmissionStatusChoices
from care.emr.signals.patient.facility_name_identifier import (
    FacilityPatientNameIdentifierConfig,
)
from care.emr.signals.patient.name_identifier import NameIdentifierConfig
from care.emr.signals.patient.phone_number_identifier import (
    PhoneNumberIdentifierConfig,
)
from care.security.permissions.encounter import EncounterPermissions
from care.security.permissions.patient import PatientPermissions
from care.security.permissions.questionnaire import QuestionnairePermissions
from care.utils.tests.base import CareAPITestBase


class TestCorrespondenceCorrectionMigration(TransactionTestCase):
    fake = CareAPITestBase.fake
    migrate_from = ("emr", "0085_correspondence_delivery_ledger")
    migrate_to = ("emr", "0086_correspondence_source_correction")
    reset_sequences = True

    @staticmethod
    def _migrate_to_latest():
        # The real leaf of the graph, not a pinned name: pinning 0090 here left
        # the shared test database at 0090 for every test that ran afterwards
        # (found 18 September 2026 while adding emr 0107).
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def setUp(self):
        cache.clear()
        FacilityPatientNameIdentifierConfig.CACHED_CONFIG.clear()
        NameIdentifierConfig.CACHED_CONFIG.clear()
        PhoneNumberIdentifierConfig.CACHED_CONFIG.clear()
        self._migrate_to_latest()
        self.user = CareAPITestBase.create_user(self)
        self.facility = CareAPITestBase.create_facility(self, user=self.user)
        self.organization = CareAPITestBase.create_facility_organization(
            self, facility=self.facility
        )
        self.patient = CareAPITestBase.create_patient(self)
        self.encounter = CareAPITestBase.create_encounter(
            self,
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
        )
        # Since the form-submission authorization tightening, submitting also
        # needs `can_submit_questionnaire` in one of the questionnaire's
        # organizations (same fixture shape as test_form_submission_api).
        self.questionnaire_organization = CareAPITestBase.create_organization(self)
        self.questionnaire = baker.make(
            Questionnaire,
            organization_cache=[self.questionnaire_organization.id],
            slug=f"migration-correction-{uuid4()}",
            title="Migration Correction Fixture",
        )
        role = CareAPITestBase.create_role_with_permissions(
            self,
            [
                PatientPermissions.can_view_clinical_data.name,
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_submit_encounter_questionnaire.name,
                QuestionnairePermissions.can_submit_questionnaire.name,
            ],
        )
        CareAPITestBase.attach_role_facility_organization_user(
            self, self.organization, self.user, role
        )
        CareAPITestBase.attach_role_organization_user(
            self, self.questionnaire_organization, self.user, role
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.submission = baker.make(
            FormSubmission,
            questionnaire=self.questionnaire,
            patient=self.patient,
            encounter=self.encounter,
            status=FormSubmissionStatusChoices.draft.value,
            response_dump={"measurement": 80},
            created_by=self.user,
            updated_by=self.user,
        )
        finalized = self.client.post(
            reverse(
                "form_submission-idempotent-finalize",
                kwargs={"external_id": self.submission.external_id},
            ),
            {
                "client_request_id": str(uuid4()),
                "expected_version": 1,
                "patient": str(self.patient.external_id),
                "encounter": str(self.encounter.external_id),
                "questionnaire": self.questionnaire.slug,
            },
            format="json",
        )
        if finalized.status_code != HTTPStatus.OK:
            raise AssertionError(finalized.json())
        self.submission.refresh_from_db()
        amended = self.client.post(
            reverse(
                "form_submission-idempotent-amend",
                kwargs={"external_id": self.submission.external_id},
            ),
            {
                "client_request_id": str(uuid4()),
                "expected_version": self.submission.resource_version,
                "patient": str(self.patient.external_id),
                "encounter": str(self.encounter.external_id),
                "questionnaire": self.questionnaire.slug,
                "amendment_type": "amendment",
                "reason": "Correct migration fixture",
                "response_dump": {"measurement": 45},
            },
            format="json",
        )
        if amended.status_code != HTTPStatus.CREATED:
            raise AssertionError(amended.json())
        self.result = FormSubmission.objects.get(
            external_id=amended.json()["form_submission"]["id"]
        )
        self.command = FormSubmissionCommand.objects.get(command_type="amend")

    def tearDown(self):
        self._migrate_to_latest()
        super().tearDown()

    def _reverse(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        return executor.loader.project_state([self.migrate_from]).apps

    def _apply(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        return executor.loader.project_state([self.migrate_to]).apps

    def _assert_correction_tables_rolled_back(self):
        tables = set(connection.introspection.table_names())
        self.assertNotIn("emr_formsubmissionserieshead", tables)
        self.assertNotIn("emr_correspondencesourcecorrection", tables)
        self.assertNotIn("emr_correspondencecorrectionoutbox", tables)

    def test_valid_command_backfills_exact_head_correction_and_outbox(self):
        self._reverse()

        apps = self._apply()

        head_model = apps.get_model("emr", "FormSubmissionSeriesHead")
        correction_model = apps.get_model("emr", "CorrespondenceSourceCorrection")
        outbox_model = apps.get_model("emr", "CorrespondenceCorrectionOutbox")
        self.assertEqual(head_model.objects.count(), 1)
        correction = correction_model.objects.get()
        self.assertEqual(correction.previous_submission_id, self.submission.id)
        self.assertEqual(correction.new_submission_id, self.result.id)
        self.assertEqual(correction.corrected_by_id, self.command.actor_id)
        self.assertEqual(correction.sequence, 1)
        self.assertEqual(outbox_model.objects.filter(status="pending").count(), 1)

    def test_tampered_command_hash_aborts_atomically_and_can_reapply_after_repair(self):
        expected_hash = self.command.payload_hash
        apps = self._reverse()
        command_model = apps.get_model("emr", "FormSubmissionCommand")
        command_model.objects.filter(pk=self.command.pk).update(payload_hash="0" * 64)

        with self.assertRaisesRegex(RuntimeError, "malformed_series_count=1"):
            self._apply()

        self._assert_correction_tables_rolled_back()
        command_model.objects.filter(pk=self.command.pk).update(
            payload_hash=expected_hash
        )
        target_apps = self._apply()
        self.assertEqual(
            target_apps.get_model(
                "emr", "CorrespondenceSourceCorrection"
            ).objects.count(),
            1,
        )

    def test_missing_finalizer_aborts_atomically_without_fabricating_actor(self):
        actor_id = self.result.workflow_finalized_by_id
        apps = self._reverse()
        historical_submission = apps.get_model("emr", "FormSubmission")
        historical_submission.objects.filter(pk=self.result.pk).update(
            workflow_finalized_by_id=None
        )

        with self.assertRaisesRegex(RuntimeError, "malformed_series_count=1"):
            self._apply()

        self._assert_correction_tables_rolled_back()
        historical_submission.objects.filter(pk=self.result.pk).update(
            workflow_finalized_by_id=actor_id
        )
        target_apps = self._apply()
        correction = target_apps.get_model(
            "emr", "CorrespondenceSourceCorrection"
        ).objects.get()
        self.assertEqual(correction.corrected_by_id, actor_id)

    def test_deleted_amend_command_aborts_atomically_until_ledger_is_restored(self):
        apps = self._reverse()
        command_model = apps.get_model("emr", "FormSubmissionCommand")
        command_model.objects.filter(pk=self.command.pk).update(deleted=True)

        with self.assertRaisesRegex(RuntimeError, "malformed_series_count=1"):
            self._apply()

        self._assert_correction_tables_rolled_back()
        command_model.objects.filter(pk=self.command.pk).update(deleted=False)
        target_apps = self._apply()
        self.assertEqual(
            target_apps.get_model(
                "emr", "CorrespondenceSourceCorrection"
            ).objects.count(),
            1,
        )

    def test_missing_amend_command_aborts_atomically_until_ledger_is_restored(self):
        apps = self._reverse()
        command_model = apps.get_model("emr", "FormSubmissionCommand")
        historical_command = command_model.objects.get(pk=self.command.pk)
        command_values = {
            field.attname: getattr(historical_command, field.attname)
            for field in command_model._meta.concrete_fields  # noqa: SLF001
        }
        command_model.objects.filter(pk=self.command.pk).delete()
        self.assertFalse(command_model.objects.filter(pk=self.command.pk).exists())

        with self.assertRaisesRegex(RuntimeError, "malformed_series_count=1"):
            self._apply()

        self._assert_correction_tables_rolled_back()
        restored_created_date = command_values.pop("created_date")
        restored_modified_date = command_values.pop("modified_date")
        restored = command_model.objects.create(**command_values)
        command_model.objects.filter(pk=restored.pk).update(
            created_date=restored_created_date,
            modified_date=restored_modified_date,
        )
        target_apps = self._apply()
        self.assertEqual(
            target_apps.get_model(
                "emr", "CorrespondenceSourceCorrection"
            ).objects.count(),
            1,
        )

    def test_version_gap_aborts_atomically_until_lineage_is_restored(self):
        expected_version = self.result.resource_version
        expected_hash = self.result.finalized_snapshot_hash
        apps = self._reverse()
        historical_submission = apps.get_model("emr", "FormSubmission")
        gap_result = historical_submission.objects.select_related(
            "encounter",
            "patient",
            "previous_version",
            "questionnaire",
        ).get(pk=self.result.pk)
        gap_result.resource_version = expected_version + 1
        gap_hash = finalized_form_submission_snapshot_hash(gap_result)
        historical_submission.objects.filter(pk=self.result.pk).update(
            resource_version=gap_result.resource_version,
            finalized_snapshot_hash=gap_hash,
        )

        with self.assertRaisesRegex(RuntimeError, "malformed_series_count=1"):
            self._apply()

        self._assert_correction_tables_rolled_back()
        historical_submission.objects.filter(pk=self.result.pk).update(
            resource_version=expected_version,
            finalized_snapshot_hash=expected_hash,
        )
        target_apps = self._apply()
        self.assertEqual(
            target_apps.get_model(
                "emr", "CorrespondenceSourceCorrection"
            ).objects.count(),
            1,
        )
