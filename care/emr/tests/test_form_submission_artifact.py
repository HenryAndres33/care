import copy
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from threading import Barrier
from unittest.mock import patch
from uuid import uuid1, uuid4

from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from rest_framework.test import APIClient

from care.emr.correspondence.correction import create_finalized_form_series_head
from care.emr.models.questionnaire import FormSubmission, Questionnaire
from care.emr.models.report.report_upload import (
    FormSubmissionArtifactCommand,
    ReportUpload,
)
from care.emr.reports.form_submission_artifact import (
    build_form_submission_artifact_html,
    render_form_submission_artifact_pdf,
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
from care.security.permissions.template import TemplatePermissions
from care.utils.tests.base import CareAPITestBase


class TestFormSubmissionArtifactAPI(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(first_name="Ada", last_name="Clinician")
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.patient = self.create_patient(name="Synthetic Patient")
        self.encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
        )
        self.encounter.period = {"start": "2026-07-20T09:30:00Z"}
        self.encounter.external_identifier = "ENC-SYNTHETIC-01"
        self.encounter.save(
            update_fields=["period", "external_identifier", "modified_date"]
        )
        self.questionnaire = baker.make(
            Questionnaire,
            slug="generic-printable-form",
            title="Generic Printable Form",
            version="2026.1",
        )
        self.role = self.create_role_with_permissions(
            [
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_read_encounter.name,
                EncounterPermissions.can_write_encounter.name,
                EncounterPermissions.can_submit_encounter_questionnaire.name,
                PatientPermissions.can_view_clinical_data.name,
            ]
        )
        self.attach_role_facility_organization_user(
            self.organization, self.user, self.role
        )
        self.client.force_authenticate(user=self.user)
        self.submission = self._finalized_submission()

        self.put_patcher = patch.object(
            ReportUpload.files_manager, "put_object", return_value={}
        )
        self.read_patcher = patch.object(
            ReportUpload.files_manager,
            "read_signed_url",
            return_value="https://object.example/authenticated-artifact",
        )
        self.delete_patcher = patch.object(
            ReportUpload.files_manager, "delete_object", return_value={}
        )
        self.render_patcher = patch(
            "care.emr.api.viewsets.form_submission.render_form_submission_artifact_pdf",
            return_value=b"%PDF-1.7\nsynthetic-finalized-form",
        )
        self.put_object = self.put_patcher.start()
        self.read_signed_url = self.read_patcher.start()
        self.delete_object = self.delete_patcher.start()
        self.render_pdf = self.render_patcher.start()
        self.addCleanup(self.put_patcher.stop)
        self.addCleanup(self.read_patcher.stop)
        self.addCleanup(self.delete_patcher.stop)
        self.addCleanup(self.render_patcher.stop)

    def _finalized_submission(self, **overrides):
        values = {
            "questionnaire": self.questionnaire,
            "patient": self.patient,
            "encounter": self.encounter,
            "status": FormSubmissionStatusChoices.submitted.value,
            "response_dump": {
                "score": 7,
                "narrative": "Patient-safe <script>markup</script>",
            },
            "resource_version": 2,
            "workflow_finalized_at": timezone.now(),
            "workflow_finalized_by": self.user,
            "created_by": self.user,
            "updated_by": self.user,
        }
        values.update(overrides)
        submission = FormSubmission(**values)
        submission.finalized_snapshot_hash = finalized_form_submission_snapshot_hash(
            submission
        )
        submission.save(force_insert=True)
        create_finalized_form_series_head(submission=submission, actor=self.user)
        return submission

    def _url(self, submission=None):
        return reverse(
            "form_submission-idempotent-generate-artifact",
            kwargs={
                "external_id": (submission or self.submission).external_id,
            },
        )

    def _payload(self, submission=None, **overrides):
        submission = submission or self.submission
        payload = {
            "client_request_id": str(uuid4()),
            "patient": str(submission.patient.external_id),
            "encounter": str(submission.encounter.external_id),
            "questionnaire": submission.questionnaire.slug,
            "source_version": submission.resource_version,
            "source_snapshot_hash": submission.finalized_snapshot_hash,
        }
        payload.update(overrides)
        return payload

    def _generate(self, payload=None, submission=None):
        return self.client.post(
            self._url(submission),
            payload or self._payload(submission),
            format="json",
        )

    def _amend(self, submission=None):
        submission = submission or self.submission
        return self.client.post(
            reverse(
                "form_submission-idempotent-amend",
                kwargs={"external_id": submission.external_id},
            ),
            {
                "client_request_id": str(uuid4()),
                "expected_version": submission.resource_version,
                "patient": str(submission.patient.external_id),
                "encounter": str(submission.encounter.external_id),
                "questionnaire": submission.questionnaire.slug,
                "amendment_type": "amendment",
                "reason": "Correct source after artifact generation",
                "response_dump": {"score": 8, "narrative": "Corrected"},
            },
            format="json",
        )

    def test_generates_stored_native_report_with_exact_provenance(self):
        payload = self._payload()

        response = self._generate(payload)

        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        self.assertEqual(
            set(response.json()), {"client_request_id", "replayed", "artifact"}
        )
        self.assertFalse(response.json()["replayed"])
        artifact_body = response.json()["artifact"]
        self.assertEqual(artifact_body["patient"], str(self.patient.external_id))
        self.assertEqual(artifact_body["encounter"], str(self.encounter.external_id))
        self.assertEqual(
            artifact_body["form_submission"], str(self.submission.external_id)
        )
        self.assertEqual(artifact_body["source_version"], 2)
        self.assertEqual(
            artifact_body["source_snapshot_hash"],
            self.submission.finalized_snapshot_hash,
        )
        self.assertEqual(artifact_body["mime_type"], "application/pdf")
        self.assertEqual(artifact_body["status"], "completed")
        self.assertEqual(
            artifact_body["download_url"],
            "https://object.example/authenticated-artifact",
        )
        artifact = ReportUpload.objects.get()
        self.assertIsNone(artifact.template)
        self.assertEqual(artifact.report_type, "encounter_report")
        self.assertEqual(artifact.associating_id, str(self.encounter.external_id))
        self.assertEqual(artifact.form_submission, self.submission)
        self.assertEqual(artifact.patient, self.patient)
        self.assertEqual(artifact.encounter, self.encounter)
        self.assertEqual(len(artifact.artifact_sha256), 64)
        self.assertNotIn(self.patient.name, artifact.internal_name)
        self.assertEqual(FormSubmissionArtifactCommand.objects.count(), 1)
        self.put_object.assert_called_once()

        html = self.render_pdf.call_args.args[0]
        self.assertNotIn(str(artifact.external_id), html)
        self.assertNotIn(str(self.patient.external_id), html)
        self.assertNotIn(str(self.encounter.external_id), html)
        self.assertNotIn(self.submission.finalized_snapshot_hash, html)
        self.assertIn("Medisch dossier", html)
        self.assertIn("Synthetic Patient", html)
        self.assertNotIn("ENC-SYNTHETIC-01", html)
        self.assertIn("20-07-2026", html)
        self.assertIn("Generic Printable Form", html)
        self.assertNotIn("2026.1", html)
        self.assertIn("Ada Clinician", html)
        self.assertIn("Definitief vastgelegd", html)
        self.assertIn("Score", html)
        self.assertIn(">7<", html)
        self.assertIn("Patient-safe &lt;script&gt;markup&lt;/script&gt;", html)
        self.assertNotIn("Patient-safe <script>", html)

    def test_exact_retry_replays_same_artifact_without_second_upload(self):
        payload = self._payload()
        created = self._generate(payload)

        replay = self._generate(payload)

        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        self.assertEqual(replay.status_code, HTTPStatus.OK)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            created.json()["artifact"]["id"], replay.json()["artifact"]["id"]
        )
        self.assertEqual(ReportUpload.objects.count(), 1)
        self.assertEqual(FormSubmissionArtifactCommand.objects.count(), 1)
        self.put_object.assert_called_once()

    def test_exact_replay_after_encounter_closure_needs_read_not_new_write(self):
        payload = self._payload()
        self.assertEqual(self._generate(payload).status_code, HTTPStatus.CREATED)
        self.encounter.status = "completed"
        self.encounter.save(update_fields=["status", "modified_date"])

        replay = self._generate(payload)
        new_key = self._generate(self._payload())

        self.assertEqual(replay.status_code, HTTPStatus.OK)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(new_key.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(ReportUpload.objects.count(), 1)
        self.assertEqual(FormSubmissionArtifactCommand.objects.count(), 1)

    def test_new_key_for_same_exact_source_reuses_artifact_and_reserves_key(self):
        first = self._generate(self._payload())
        second = self._generate(self._payload())

        self.assertEqual(first.status_code, HTTPStatus.CREATED)
        self.assertEqual(second.status_code, HTTPStatus.OK)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(
            first.json()["artifact"]["id"], second.json()["artifact"]["id"]
        )
        self.assertEqual(ReportUpload.objects.count(), 1)
        self.assertEqual(FormSubmissionArtifactCommand.objects.count(), 2)
        self.put_object.assert_called_once()

    def test_same_key_different_payload_or_source_is_non_leaking_conflict(self):
        payload = self._payload()
        self.assertEqual(self._generate(payload).status_code, HTTPStatus.CREATED)
        changed = {**payload, "source_snapshot_hash": "a" * 64}

        conflict = self._generate(changed)

        self.assertEqual(conflict.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(conflict.json()["errors"][0]["type"], "idempotency_conflict")
        self.assertNotIn("artifact", conflict.json())

        other_source = self._finalized_submission(response_dump={"other": True})
        other_payload = self._payload(
            other_source, client_request_id=payload["client_request_id"]
        )
        other_conflict = self._generate(other_payload, other_source)
        self.assertEqual(other_conflict.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("artifact", other_conflict.json())
        self.assertEqual(ReportUpload.objects.count(), 1)

    def test_wrong_patient_encounter_and_questionnaire_are_non_leaking_404(self):
        other_patient = self.create_patient()
        other_encounter = self.create_encounter(
            patient=other_patient,
            facility=self.facility,
            organization=self.organization,
        )
        cases = [
            {"patient": str(other_patient.external_id)},
            {"encounter": str(other_encounter.external_id)},
            {"questionnaire": "wrong-questionnaire"},
        ]

        for changed in cases:
            with self.subTest(changed=changed):
                response = self._generate(self._payload(**changed))
                self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
                self.assertNotIn("artifact", response.json())
        self.assertEqual(ReportUpload.objects.count(), 0)

    def test_requires_authentication_and_current_read_plus_report_write(self):
        payload = self._payload()
        self.client.logout()
        unauthenticated = self._generate(payload)

        read_only_user = self.create_user()
        read_role = self.create_role_with_permissions(
            [
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_read_encounter.name,
                PatientPermissions.can_view_clinical_data.name,
            ]
        )
        self.attach_role_facility_organization_user(
            self.organization, read_only_user, read_role
        )
        self.client.force_authenticate(user=read_only_user)
        read_only = self._generate({**payload, "client_request_id": str(uuid4())})

        self.assertEqual(unauthenticated.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(read_only.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(ReportUpload.objects.count(), 0)
        self.put_object.assert_not_called()

    def test_completed_encounter_requires_native_completed_report_permission(self):
        self.encounter.status = "completed"
        self.encounter.save(update_fields=["status", "modified_date"])
        denied = self._generate()

        completed_role = self.create_role_with_permissions(
            [
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_read_encounter.name,
                TemplatePermissions.can_generate_report_for_completed_encounter.name,
            ]
        )
        self.attach_role_facility_organization_user(
            self.organization, self.user, completed_role
        )
        allowed = self._generate(self._payload())

        self.assertEqual(denied.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(allowed.status_code, HTTPStatus.CREATED)

    def test_stale_source_version_or_hash_returns_non_leaking_409(self):
        for changed in [
            {"source_version": self.submission.resource_version + 1},
            {"source_snapshot_hash": "b" * 64},
        ]:
            with self.subTest(changed=changed):
                response = self._generate(self._payload(**changed))
                self.assertEqual(response.status_code, HTTPStatus.CONFLICT)
                self.assertEqual(
                    response.json()["errors"][0]["type"], "artifact_source_stale"
                )
                self.assertNotIn("artifact", response.json())
        self.assertEqual(ReportUpload.objects.count(), 0)

    def test_amendment_preserves_exact_replay_but_blocks_new_artifact_key(self):
        payload = self._payload()
        created = self._generate(payload)
        amended = self._amend()
        exact = self._generate(payload)
        new_key = self._generate({**payload, "client_request_id": str(uuid4())})

        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        self.assertEqual(amended.status_code, HTTPStatus.CREATED, amended.json())
        self.assertEqual(exact.status_code, HTTPStatus.OK)
        self.assertTrue(exact.json()["replayed"])
        self.assertEqual(new_key.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(
            new_key.json()["errors"][0]["type"],
            "artifact_source_stale",
        )
        self.assertEqual(ReportUpload.objects.count(), 1)

    def test_draft_placeholder_malformed_and_patient_only_sources_are_blocked(self):
        draft = baker.make(
            FormSubmission,
            questionnaire=self.questionnaire,
            patient=self.patient,
            encounter=self.encounter,
            status=FormSubmissionStatusChoices.draft.value,
            response_dump={"value": "draft"},
            created_by=self.user,
            updated_by=self.user,
        )
        placeholder = self._finalized_submission(
            response_dump={"value": "{{ unresolved_value }}"}
        )
        malformed = self._finalized_submission(response_dump={})
        patient_only = FormSubmission(
            questionnaire=self.questionnaire,
            patient=self.patient,
            encounter=None,
            status=FormSubmissionStatusChoices.submitted.value,
            response_dump={"value": "final"},
            workflow_finalized_at=timezone.now(),
            workflow_finalized_by=self.user,
            created_by=self.user,
            updated_by=self.user,
        )
        patient_only.finalized_snapshot_hash = finalized_form_submission_snapshot_hash(
            patient_only
        )
        patient_only.save(force_insert=True)

        cases = [
            (
                draft,
                self._payload(draft, source_snapshot_hash="0" * 64),
            ),
            (placeholder, self._payload(placeholder)),
            (malformed, self._payload(malformed)),
            (
                patient_only,
                {
                    "client_request_id": str(uuid4()),
                    "patient": str(self.patient.external_id),
                    "encounter": str(self.encounter.external_id),
                    "questionnaire": self.questionnaire.slug,
                    "source_version": patient_only.resource_version,
                    "source_snapshot_hash": patient_only.finalized_snapshot_hash,
                },
            ),
        ]
        for source, payload in cases:
            with self.subTest(source=source.external_id):
                response = self._generate(payload, source)
                expected = (
                    HTTPStatus.CONFLICT
                    if source.status == FormSubmissionStatusChoices.draft.value
                    else HTTPStatus.UNPROCESSABLE_ENTITY
                )
                self.assertEqual(response.status_code, expected)
        self.assertEqual(ReportUpload.objects.count(), 0)

    def test_tampered_finalized_snapshot_is_blocked(self):
        FormSubmission._base_manager.filter(pk=self.submission.pk).update(  # noqa: SLF001
            response_dump={"tampered": True}
        )

        response = self._generate()

        self.assertEqual(response.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(ReportUpload.objects.count(), 0)

    def test_command_is_strict_uuid_v4_and_does_not_accept_browser_title(self):
        extra = self._payload(title="Patient supplied title")
        wrong_uuid = self._payload(client_request_id=str(uuid1()))

        self.assertEqual(self._generate(extra).status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(self._generate(wrong_uuid).status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(ReportUpload.objects.count(), 0)

    def test_storage_or_ledger_failure_rolls_back_and_reports_retry_semantics(self):
        self.put_object.side_effect = RuntimeError("synthetic storage failure")
        storage_failure = self._generate()

        self.assertEqual(storage_failure.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(
            storage_failure.json()["errors"][0]["type"],
            "artifact_generation_failed",
        )
        self.assertIn(
            "same client_request_id", storage_failure.json()["errors"][0]["msg"]
        )
        self.assertEqual(ReportUpload.objects.count(), 0)
        self.assertEqual(FormSubmissionArtifactCommand.objects.count(), 0)
        self.delete_object.assert_called_once()

        self.put_object.side_effect = None
        self.put_object.reset_mock()
        self.delete_object.reset_mock()
        with patch.object(
            FormSubmissionArtifactCommand.objects,
            "create",
            side_effect=RuntimeError("synthetic ledger failure"),
        ):
            ledger_failure = self._generate(self._payload())
        self.assertEqual(ledger_failure.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(ReportUpload.objects.count(), 0)
        self.assertEqual(FormSubmissionArtifactCommand.objects.count(), 0)
        self.delete_object.assert_called_once()

    def test_download_url_failure_preserves_artifact_for_exact_recovery(self):
        payload = self._payload()
        self.read_signed_url.side_effect = RuntimeError("synthetic signing outage")

        unavailable = self._generate(payload)

        self.assertEqual(unavailable.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(
            unavailable.json()["errors"][0]["type"],
            "artifact_download_unavailable",
        )
        self.assertEqual(ReportUpload.objects.count(), 1)
        self.assertEqual(FormSubmissionArtifactCommand.objects.count(), 1)
        self.read_signed_url.side_effect = None
        recovered = self._generate(payload)
        self.assertEqual(recovered.status_code, HTTPStatus.OK)
        self.assertTrue(recovered.json()["replayed"])
        self.assertEqual(ReportUpload.objects.count(), 1)
        self.put_object.assert_called_once()

    def test_deleted_or_archived_artifact_replay_fails_closed(self):
        payload = self._payload()
        created = self._generate(payload)
        artifact_id = created.json()["artifact"]["id"]
        ReportUpload._base_manager.filter(external_id=artifact_id).update(  # noqa: SLF001
            deleted=True
        )

        replay = self._generate(payload)

        self.assertEqual(replay.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("artifact", replay.json())
        self.assertEqual(FormSubmissionArtifactCommand._base_manager.count(), 1)  # noqa: SLF001
        self.put_object.assert_called_once()

    def test_deleted_source_replay_fails_closed_and_keeps_key_reserved(self):
        payload = self._payload()
        self.assertEqual(self._generate(payload).status_code, HTTPStatus.CREATED)
        FormSubmission._base_manager.filter(pk=self.submission.pk).update(  # noqa: SLF001
            deleted=True
        )

        replay = self._generate(payload)

        self.assertEqual(replay.status_code, HTTPStatus.CONFLICT)
        self.assertNotIn("artifact", replay.json())
        self.assertEqual(FormSubmissionArtifactCommand._base_manager.count(), 1)  # noqa: SLF001
        self.assertEqual(ReportUpload._base_manager.count(), 1)  # noqa: SLF001
        self.put_object.assert_called_once()

    def test_artifact_is_downloadable_only_through_authenticated_report_api(self):
        created = self._generate()
        artifact_id = created.json()["artifact"]["id"]
        url = reverse("template-reports-detail", kwargs={"external_id": artifact_id})

        authenticated = self.client.get(url)
        self.client.logout()
        unauthenticated = self.client.get(url)

        self.assertEqual(authenticated.status_code, HTTPStatus.OK)
        self.assertEqual(
            authenticated.json()["read_signed_url"],
            "https://object.example/authenticated-artifact",
        )
        self.assertEqual(
            authenticated.json()["form_submission"], str(self.submission.external_id)
        )
        self.assertEqual(unauthenticated.status_code, HTTPStatus.FORBIDDEN)

    def test_artifact_cannot_be_archived_or_mutated(self):
        created = self._generate()
        artifact = ReportUpload.objects.get(
            external_id=created.json()["artifact"]["id"]
        )
        archive_url = reverse(
            "template-reports-archive",
            kwargs={"external_id": artifact.external_id},
        )

        archive = self.client.post(
            archive_url, {"archive_reason": "not allowed"}, format="json"
        )
        artifact.name = "changed"

        self.assertEqual(archive.status_code, HTTPStatus.CONFLICT)
        with self.assertRaises(DjangoValidationError):
            artifact.save()

    def test_named_constraints_reserve_keys_and_exact_sources_across_soft_delete(self):
        self._generate()
        artifact_constraints = {
            item.name
            for item in ReportUpload._meta.constraints  # noqa: SLF001
        }
        command_constraints = {
            item.name
            for item in FormSubmissionArtifactCommand._meta.constraints  # noqa: SLF001
        }

        self.assertIn(
            ReportUpload.FORM_ARTIFACT_SOURCE_CONSTRAINT_NAME,
            artifact_constraints,
        )
        self.assertIn(
            FormSubmissionArtifactCommand.IDEMPOTENCY_CONSTRAINT_NAME,
            command_constraints,
        )

    def test_renderer_html_is_deterministic_and_hides_server_identifiers(self):
        generated_at = timezone.now()
        first = build_form_submission_artifact_html(
            artifact_id=uuid4(),
            submission=self.submission,
            generated_at=generated_at,
        )
        artifact_id = uuid4()
        second = build_form_submission_artifact_html(
            artifact_id=artifact_id,
            submission=self.submission,
            generated_at=generated_at,
        )
        repeated = build_form_submission_artifact_html(
            artifact_id=artifact_id,
            submission=self.submission,
            generated_at=generated_at,
        )

        self.assertEqual(first, second)
        self.assertEqual(second, repeated)
        self.assertNotIn(str(self.patient.external_id), second)
        self.assertNotIn(str(self.encounter.external_id), second)
        self.assertNotIn(str(artifact_id), second)
        self.assertNotIn(self.submission.finalized_snapshot_hash, second)
        self.assertIn("Medisch dossier", second)

        first_pdf = render_form_submission_artifact_pdf(second)
        second_pdf = render_form_submission_artifact_pdf(second)
        self.assertTrue(first_pdf.startswith(b"%PDF"))
        self.assertGreater(len(first_pdf), 1_000)
        self.assertEqual(first_pdf, second_pdf)

    def test_renderer_prefers_clean_custom_form_narrative(self):
        self.submission.response_dump = {
            "content": {
                "noteText": "BPH-consult\nIPSS: 12\nMedicatie: Tamsulosine",
                "narrativePreview": "duplicate preview",
                "values": {"bph.ipss.total": 12},
                "clinicalActions": {"internal": "must not print"},
                "customFormDefinition": {"schema": "must not print"},
            },
            "identity": {"patientId": str(self.patient.external_id)},
            "source_snapshot_hash": "f" * 64,
        }

        html = build_form_submission_artifact_html(
            artifact_id=uuid4(),
            submission=self.submission,
            generated_at=timezone.now(),
        )

        self.assertIn("BPH-consult", html)
        self.assertIn("IPSS: 12", html)
        self.assertIn("Medicatie: Tamsulosine", html)
        self.assertNotIn("duplicate preview", html)
        self.assertNotIn("must not print", html)
        self.assertNotIn(str(self.patient.external_id), html)
        self.assertNotIn("source_snapshot_hash", html)

    def test_renderer_uses_operation_report_title_for_urology_operations(self):
        self.questionnaire.slug = "urology-operaties"
        self.questionnaire.title = "Urologie operatieverslag"
        self.questionnaire.save(update_fields=["slug", "title", "modified_date"])

        html = build_form_submission_artifact_html(
            artifact_id=uuid4(),
            submission=self.submission,
            generated_at=timezone.now(),
        )

        self.assertIn("<title>Operatieverslag</title>", html)
        self.assertIn("<h1>Operatieverslag</h1>", html)
        self.assertNotIn("<h1>Medisch dossier</h1>", html)


class TestFormSubmissionArtifactConcurrency(TransactionTestCase):
    fake = CareAPITestBase.fake
    reset_sequences = True

    def setUp(self):
        cache.clear()
        FacilityPatientNameIdentifierConfig.CACHED_CONFIG.clear()
        NameIdentifierConfig.CACHED_CONFIG.clear()
        PhoneNumberIdentifierConfig.CACHED_CONFIG.clear()
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
        self.questionnaire = baker.make(
            Questionnaire,
            slug="concurrent-printable-form",
            title="Concurrent Printable Form",
            version="1",
        )
        role = CareAPITestBase.create_role_with_permissions(
            self,
            [
                EncounterPermissions.can_read_encounter_clinical_data.name,
                EncounterPermissions.can_read_encounter.name,
                EncounterPermissions.can_write_encounter.name,
                EncounterPermissions.can_submit_encounter_questionnaire.name,
                PatientPermissions.can_view_clinical_data.name,
            ],
        )
        CareAPITestBase.attach_role_facility_organization_user(
            self, self.organization, self.user, role
        )
        self.submission = FormSubmission(
            questionnaire=self.questionnaire,
            patient=self.patient,
            encounter=self.encounter,
            status=FormSubmissionStatusChoices.submitted.value,
            response_dump={"concurrent": "value"},
            resource_version=2,
            workflow_finalized_at=timezone.now(),
            workflow_finalized_by=self.user,
            created_by=self.user,
            updated_by=self.user,
        )
        self.submission.finalized_snapshot_hash = (
            finalized_form_submission_snapshot_hash(self.submission)
        )
        self.submission.save(force_insert=True)
        create_finalized_form_series_head(submission=self.submission, actor=self.user)
        self.url = reverse(
            "form_submission-idempotent-generate-artifact",
            kwargs={"external_id": self.submission.external_id},
        )
        self.storage_patchers = [
            patch.object(ReportUpload.files_manager, "put_object", return_value={}),
            patch.object(
                ReportUpload.files_manager,
                "read_signed_url",
                return_value="https://object.example/artifact",
            ),
            patch.object(ReportUpload.files_manager, "delete_object", return_value={}),
            patch(
                "care.emr.api.viewsets.form_submission."
                "render_form_submission_artifact_pdf",
                return_value=b"%PDF-1.7\nconcurrent",
            ),
        ]
        self.mocks = [patcher.start() for patcher in self.storage_patchers]
        for patcher in self.storage_patchers:
            self.addCleanup(patcher.stop)

    def _payload(self, request_id):
        return {
            "client_request_id": str(request_id),
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "questionnaire": self.questionnaire.slug,
            "source_version": self.submission.resource_version,
            "source_snapshot_hash": self.submission.finalized_snapshot_hash,
        }

    def _amend_request(self):
        return (
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
                "reason": "Concurrent correction",
                "response_dump": {"concurrent": "corrected"},
            },
        )

    def _post_concurrently(self, requests):
        barrier = Barrier(2)

        def post_request(request):
            url, payload = (
                request if isinstance(request, tuple) else (self.url, request)
            )
            close_old_connections()
            client = APIClient()
            client.force_authenticate(user=self.user)
            barrier.wait()
            response = client.post(url, copy.deepcopy(payload), format="json")
            close_old_connections()
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(post_request, requests))

    def test_concurrent_exact_same_key_creates_one_artifact_and_command(self):
        payload = self._payload(uuid4())

        responses = self._post_concurrently([payload, payload])

        self.assertEqual(sorted(code for code, _ in responses), [200, 201])
        self.assertEqual(
            sorted(body["replayed"] for _, body in responses), [False, True]
        )
        self.assertEqual(ReportUpload.objects.count(), 1)
        self.assertEqual(FormSubmissionArtifactCommand.objects.count(), 1)
        self.assertEqual(self.mocks[0].call_count, 1)

    def test_concurrent_distinct_keys_for_same_source_reuse_one_artifact(self):
        responses = self._post_concurrently(
            [self._payload(uuid4()), self._payload(uuid4())]
        )

        self.assertEqual(sorted(code for code, _ in responses), [200, 201])
        artifact_ids = {body["artifact"]["id"] for _, body in responses}
        self.assertEqual(len(artifact_ids), 1)
        self.assertEqual(ReportUpload.objects.count(), 1)
        self.assertEqual(FormSubmissionArtifactCommand.objects.count(), 2)
        self.assertEqual(self.mocks[0].call_count, 1)

    def test_amendment_and_artifact_generation_have_only_serial_outcomes(self):
        responses = self._post_concurrently(
            [
                self._amend_request(),
                (self.url, self._payload(uuid4())),
            ]
        )

        self.assertEqual(responses[0][0], HTTPStatus.CREATED)
        self.assertIn(
            responses[1][0],
            {HTTPStatus.CREATED, HTTPStatus.CONFLICT},
        )
        expected_artifacts = 1 if responses[1][0] == HTTPStatus.CREATED else 0
        self.assertEqual(ReportUpload.objects.count(), expected_artifacts)
        self.assertEqual(
            FormSubmissionArtifactCommand.objects.count(), expected_artifacts
        )

    def test_concurrent_same_key_different_sources_is_non_leaking_conflict(self):
        other = FormSubmission(
            questionnaire=self.questionnaire,
            patient=self.patient,
            encounter=self.encounter,
            status=FormSubmissionStatusChoices.submitted.value,
            response_dump={"concurrent": "other source"},
            resource_version=2,
            workflow_finalized_at=timezone.now(),
            workflow_finalized_by=self.user,
            created_by=self.user,
            updated_by=self.user,
        )
        other.finalized_snapshot_hash = finalized_form_submission_snapshot_hash(other)
        other.save(force_insert=True)
        create_finalized_form_series_head(submission=other, actor=self.user)
        request_id = uuid4()
        other_url = reverse(
            "form_submission-idempotent-generate-artifact",
            kwargs={"external_id": other.external_id},
        )
        other_payload = {
            **self._payload(request_id),
            "source_snapshot_hash": other.finalized_snapshot_hash,
        }

        responses = self._post_concurrently(
            [
                (self.url, self._payload(request_id)),
                (other_url, other_payload),
            ]
        )

        self.assertEqual(sorted(code for code, _ in responses), [201, 409])
        conflict = next(body for code, body in responses if code == HTTPStatus.CONFLICT)
        self.assertNotIn("artifact", conflict)
        self.assertEqual(ReportUpload.objects.count(), 1)
        self.assertEqual(FormSubmissionArtifactCommand.objects.count(), 1)
