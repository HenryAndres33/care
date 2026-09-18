from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.urls import reverse
from django.utils import timezone

from care.emr.models.admission_documentation import AdmissionDocumentation
from care.emr.models.correspondence_letter import CorrespondenceLetterRevision
from care.emr.models.correspondence_review import CorrespondenceReview
from care.emr.models.encounter_discharge import EncounterDischargeCommand
from care.emr.models.questionnaire import FormSubmission
from care.emr.models.report.report_upload import ReportUpload
from care.emr.tests.test_correspondence_review import CorrespondenceReviewTestMixin
from care.security.models import PermissionModel, RolePermission
from care.security.permissions.encounter import EncounterPermissions
from care.utils.tests.base import CareAPITestBase


class DischargeDocumentationTests(CorrespondenceReviewTestMixin, CareAPITestBase):
    def _finalized_submission(self, **overrides):
        self.questionnaire.slug = "urology-medisch-dossier"
        self.questionnaire.save()
        self.encounter.encounter_class = "imp"
        self.encounter.status = "in_progress"
        self.encounter.hospitalization = {"admit_source": "outp"}
        self.encounter.status_history = {"history": []}
        self.encounter.save()
        return super()._finalized_submission(**overrides)

    def setUp(self):
        super().setUp()
        self.build_review_context()
        permission, _ = PermissionModel.objects.get_or_create(
            slug=EncounterPermissions.can_write_encounter_clinical_data.name
        )
        RolePermission.objects.create(role=self.role, permission=permission)
        self.assertEqual(self._bind().status_code, 201)
        self.review = CorrespondenceReview.objects.get()
        self.slot = AdmissionDocumentation.objects.create(
            admission=self.encounter,
            slot="discharge",
            form_instance_id=self.submission.external_id,
            created_by=self.user,
        )
        self.payload = {
            "client_request_id": str(uuid4()),
            "discharge_disposition": "home",
            "discharged_at": (timezone.now() - timedelta(seconds=1)).isoformat(),
            "discharge_summary_advice": "DEMO-SIM discharge test only",
            "release_bed": True,
        }
        for method, value in (
            ("put_object", {}),
            ("read_signed_url", "https://object.example/letter.pdf"),
            ("delete_object", {}),
        ):
            mock = patch.object(ReportUpload.files_manager, method, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        render = patch(
            "care_suriname.api.viewsets.correspondence_letter.render_correspondence_letter_pdf",
            return_value=b"%PDF-1.7\nsynthetic-discharge-letter",
        )
        render.start()
        self.addCleanup(render.stop)

    def letter(self, *, finalize=True):
        context = {
            "review_binding": str(self.review.external_id),
            "review_hash": self.review.review_hash,
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "facility": str(self.facility.external_id),
            "department": str(self.organization.external_id),
            "author": str(self.user.external_id),
        }
        response = self.client.post(
            reverse("correspondence-letter-idempotent-create"),
            {
                **context,
                "client_request_id": str(uuid4()),
                "body": "DEMO-SIM discharge letter. No actual care.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        if not finalize:
            return None
        draft = response.data["correspondence"]
        response = self.client.post(
            reverse(
                "correspondence-letter-idempotent-finalize",
                kwargs={"external_id": draft["id"]},
            ),
            {
                **context,
                "client_request_id": str(uuid4()),
                "expected_version": draft["resource_version"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return CorrespondenceLetterRevision.objects.get(
            external_id=response.data["correspondence"]["id"]
        )

    def discharge(self, *, preflight=False):
        payload = dict(self.payload)
        if preflight:
            payload.pop("client_request_id")
        return self.client.post(
            reverse(
                "encounter-preflight-discharge"
                if preflight
                else "encounter-idempotent-discharge",
                kwargs={"external_id": self.encounter.external_id},
            ),
            payload,
            format="json",
        )

    def assert_warned_and_closed(self, code):
        preflight = self.discharge(preflight=True)
        self.assertEqual(preflight.status_code, 200, preflight.data)
        self.assertTrue(preflight.data["ready"])
        self.assertEqual(preflight.data["blocker_codes"], [])
        self.assertEqual(preflight.data["warning_codes"], [code])
        response = self.discharge()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["discharge"]["warning_codes"], [code])
        self.assertIsNone(response.data["discharge"]["documentation"])
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.status, "discharged")
        command = EncounterDischargeCommand.objects.get()
        self.assertEqual(command.result_snapshot["warning_codes"], [code])

    def test_missing_summary_warns_and_allows_close(self):
        self.slot.form_instance_id = uuid4()
        self.slot.save()
        self.assert_warned_and_closed("discharge_summary_required")

    def test_draft_letter_warns_and_allows_close(self):
        self.letter(finalize=False)
        self.assert_warned_and_closed("discharge_letter_required")

    def test_final_letter_allows_close_and_exact_replay(self):
        revision = self.letter()
        preflight = self.discharge(preflight=True)
        self.assertTrue(preflight.data["ready"], preflight.data)
        self.assertEqual(preflight.data["warning_codes"], [])
        result = self.discharge()
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data["discharge"]["warning_codes"], [])
        self.assertEqual(
            result.data["discharge"]["documentation"]["letter_revision"],
            str(revision.external_id),
        )
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.status, "discharged")
        replay = self.discharge()
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data["discharge"], result.data["discharge"])
        self.assertEqual(EncounterDischargeCommand.objects.count(), 1)
        reopened = self.client.get(
            reverse(
                "correspondence-letter-detail",
                kwargs={"external_id": revision.external_id},
            )
        )
        self.assertEqual(reopened.status_code, 200, reopened.data)
        self.assertEqual(reopened.data["artifact_status"], "available")

    def test_archived_pdf_after_preflight_warns_on_commit(self):
        revision = self.letter()
        preflight = self.discharge(preflight=True)
        self.assertTrue(preflight.data["ready"])
        self.assertEqual(preflight.data["warning_codes"], [])
        ReportUpload.objects.filter(letter_revision=revision).update(is_archived=True)
        response = self.discharge()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            response.data["discharge"]["warning_codes"],
            ["discharge_letter_unavailable"],
        )
        self.assertIsNone(response.data["discharge"]["documentation"])

    def test_different_summary_letter_warns_and_allows_close(self):
        self.letter()
        other = self._finalized_submission(
            response_dump={"assessment": "Another summary"}
        )
        self.slot.form_instance_id = other.external_id
        self.slot.save()
        self.assert_warned_and_closed("discharge_letter_required")

    def test_entered_in_error_summary_warns_and_allows_close(self):
        self.letter()
        FormSubmission.objects.filter(pk=self.submission.pk).update(
            status="entered_in_error"
        )
        self.assert_warned_and_closed("discharge_summary_required")
