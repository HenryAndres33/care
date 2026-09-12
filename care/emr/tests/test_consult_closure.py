import io
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from rest_framework.exceptions import ValidationError

from care.emr.api.viewsets.consult_closure import ConsultClosureViewSet
from care.emr.api.viewsets.encounter import EncounterViewSet
from care.emr.api.viewsets.form_submission import FormSubmissionViewSet
from care.emr.api.viewsets.location import FacilityLocationEncounterViewSet
from care.emr.api.viewsets.medication_request import MedicationRequestViewSet
from care.emr.api.viewsets.scheduling.booking import TokenBookingViewSet
from care.emr.api.viewsets.scheduling.token import TokenViewSet
from care.emr.models.consult_closure import (
    ConsultClosure,
    ConsultClosureCommand,
    ConsultClosureRecoveryTask,
)
from care.emr.models.correspondence import CorrespondenceCompilation
from care.emr.models.correspondence_correction import (
    CorrespondenceCorrectionCase,
    CorrespondenceCorrectionOutbox,
)
from care.emr.models.correspondence_delivery import CorrespondenceDelivery
from care.emr.models.correspondence_letter import CorrespondenceLetterRevision
from care.emr.models.correspondence_review import CorrespondenceReview
from care.emr.models.device import Device
from care.emr.models.location import FacilityLocation, FacilityLocationEncounter
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.questionnaire import FormSubmission
from care.emr.models.report.report_upload import ReportUpload
from care.emr.models.scheduling.booking import TokenBooking, TokenSlot
from care.emr.models.scheduling.schedule import SchedulableResource
from care.emr.models.scheduling.token import (
    Token,
    TokenCategory,
    TokenQueue,
    TokenSubQueue,
)
from care.emr.resources.scheduling.slot.spec import BookingStatusChoices
from care.emr.resources.scheduling.token.spec import TokenStatusOptions
from care.emr.tasks.correspondence_correction import project_correspondence_correction
from care.emr.tasks.correspondence_delivery import (
    dispatch_correspondence_delivery_attempt,
)
from care.emr.tests.test_correspondence_compilation import (
    CorrespondenceCompilationTestMixin,
)
from care.emr.tests.test_correspondence_review import CorrespondenceReviewTestMixin
from care.utils.tests.base import CareAPITestBase

SYNTHETIC_PDF = b"%PDF-1.7\nconsult-closure-correspondence"
REQUIRED_FORMS = {"urology department": ["generic-correspondence-form"]}

CANDIDATE_KEYS = {
    "appointment",
    "correspondence_case",
    "correspondence_case_hash",
    "correspondence_case_version",
    "correspondence_compilation",
    "correspondence_delivery",
    "correspondence_delivery_event_hash",
    "correspondence_delivery_event_sequence",
    "correspondence_outcome",
    "department",
    "encounter",
    "expected_booking_modified_at",
    "expected_booking_status",
    "expected_encounter_modified_at",
    "expected_encounter_status",
    "expected_token_modified_at",
    "expected_token_status",
    "facility",
    "form_artifact",
    "form_artifact_hash",
    "form_source_hash",
    "form_source_version",
    "form_submission",
    "medication_actions",
    "medication_outcome",
    "patient",
    "policy_hash",
    "policy_id",
    "policy_version",
    "preflight_hash",
    "preflight_version",
    "token",
}

CLOSURE_KEYS = {
    "appointment",
    "booking_status",
    "closed_at",
    "closed_by",
    "closure_hash",
    "closure_number",
    "correspondence_case",
    "correspondence_case_hash",
    "correspondence_case_version",
    "correspondence_compilation",
    "correspondence_delivery",
    "correspondence_delivery_event_hash",
    "correspondence_delivery_event_sequence",
    "correspondence_outcome",
    "department",
    "encounter",
    "encounter_status",
    "facility",
    "form_artifact",
    "form_artifact_hash",
    "form_source_hash",
    "form_source_version",
    "form_submission",
    "id",
    "medication_actions",
    "medication_outcome",
    "patient",
    "policy_hash",
    "policy_id",
    "policy_version",
    "preflight_hash",
    "preflight_version",
    "previous_closure",
    "recovery_status",
    "status",
    "token",
    "token_status",
}

RECOVERY_KEYS = {
    "created_at",
    "id",
    "recovery_hash",
    "resolved_at",
    "safe_code",
    "status",
}


@override_settings(CONSULT_CLOSE_REQUIRED_FORMS_BY_DEPARTMENT=REQUIRED_FORMS)
class ConsultClosureWorkflowTests(
    CorrespondenceCompilationTestMixin,
    CareAPITestBase,
):
    def setUp(self):
        super().setUp()
        self.build_context()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.client.force_authenticate(user=self.user)
        self._build_active_queue_context()
        self.preflight_url = reverse(
            "consult-closure-preflight",
            kwargs={"external_id": self.encounter.external_id},
        )
        self.close_url = reverse(
            "consult-closure-idempotent-close",
            kwargs={"external_id": self.encounter.external_id},
        )
        self.read_url = reverse(
            "consult-closure-detail",
            kwargs={"external_id": self.encounter.external_id},
        )

    def _build_active_queue_context(self):
        self.resource = baker.make(
            SchedulableResource,
            facility=self.facility,
            user=self.user,
            resource_type="practitioner",
        )
        self.slot = baker.make(
            TokenSlot,
            resource=self.resource,
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(minutes=30),
        )
        self.booking = baker.make(
            TokenBooking,
            token_slot=self.slot,
            patient=self.patient,
            booked_by=self.user,
            status=BookingStatusChoices.in_consultation.value,
            associated_encounter=self.encounter,
        )
        queue = baker.make(
            TokenQueue,
            facility=self.facility,
            resource=self.resource,
            date=timezone.localdate(),
        )
        category = baker.make(
            TokenCategory,
            facility=self.facility,
            resource_type="practitioner",
        )
        self.token = baker.make(
            Token,
            facility=self.facility,
            patient=self.patient,
            queue=queue,
            category=category,
            status=TokenStatusOptions.IN_PROGRESS.value,
            booking=self.booking,
        )
        self.subqueue = baker.make(
            TokenSubQueue,
            facility=self.facility,
            resource=self.resource,
            status="open",
            current_token=self.token,
        )
        self.booking.token = self.token
        self.booking.save(update_fields=["token", "modified_date"])
        self.encounter.status = "in_progress"
        self.encounter.appointment = self.booking
        self.encounter.save(update_fields=["status", "appointment", "modified_date"])

    def _unscheduled_emergency(self):
        self.encounter.encounter_class = "emer"
        self.encounter.appointment = None
        self.encounter.save(
            update_fields=["encounter_class", "appointment", "modified_date"]
        )
        self.booking.associated_encounter = None
        self.booking.save(update_fields=["associated_encounter", "modified_date"])

    def test_unscheduled_emergency_close_replay_read_keeps_admission_open(self):
        self._unscheduled_emergency()
        admission = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            encounter_class="imp",
            status="in_progress",
        )
        from care.emr.models.emergency_admission import EmergencyAdmission

        EmergencyAdmission.objects.create(
            emergency=self.encounter, admission=admission, created_by=self.user
        )
        candidate = self._ready_candidate()
        self.assertEqual(candidate["policy_id"], "care.standard.emergency-close")
        self.assertIsNone(candidate["appointment"])
        self.assertIsNone(candidate["token"])
        response, payload = self._close(candidate)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["closure"]["booking_status"], "not_required")
        replay = self.client.post(self.close_url, payload, format="json")
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertTrue(replay.data["replayed"])
        read = self.client.get(self.read_url)
        self.assertEqual(read.status_code, 200, read.data)
        self.assertEqual(read.data["closure"], response.data["closure"])
        admission.refresh_from_db()
        self.booking.refresh_from_db()
        self.token.refresh_from_db()
        self.assertEqual(admission.status, "in_progress")
        self.assertEqual(self.booking.status, "in_consultation")
        self.assertEqual(self.token.status, "IN_PROGRESS")
        self.assertEqual(ConsultClosure.objects.count(), 1)

    def test_unbooked_ambulatory_still_requires_appointment(self):
        self._unscheduled_emergency()
        self.encounter.encounter_class = "amb"
        self.encounter.save()
        response = self.client.post(
            self.preflight_url, self._preflight_body(), format="json"
        )
        self.assertIn("appointment_missing", response.data["blocker_codes"])

    def test_booked_emergency_retains_queue_checks(self):
        self.encounter.encounter_class = "emer"
        self.encounter.save()
        self.assertEqual(
            self._ready_candidate()["policy_id"], "care.standard.consult-close"
        )
        self.token.status = "FULFILLED"
        self.token.save()
        response = self.client.post(
            self.preflight_url, self._preflight_body(), format="json"
        )
        self.assertIn("token_state_stale", response.data["blocker_codes"])

    def test_emergency_class_change_invalidates_preflight(self):
        self._unscheduled_emergency()
        candidate = self._ready_candidate()
        self.encounter.encounter_class = "amb"
        self.encounter.save()
        response, _ = self._close(candidate)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertFalse(ConsultClosure.objects.exists())

    def test_emergency_still_requires_valid_pdf(self):
        self._unscheduled_emergency()
        ReportUpload.objects.filter(form_submission=self.submission).update(
            is_archived=True
        )
        response = self.client.post(
            self.preflight_url, self._preflight_body(), format="json"
        )
        self.assertIn("form_artifact_invalid", response.data["blocker_codes"])

    def test_emergency_close_rolls_back_on_ledger_failure(self):
        self._unscheduled_emergency()
        candidate = self._ready_candidate()
        with patch.object(
            ConsultClosureCommand, "save", side_effect=RuntimeError("synthetic failure")
        ):
            response, _ = self._close(candidate)
        self.assertEqual(response.status_code, 503)
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.status, "in_progress")
        self.assertFalse(ConsultClosure.objects.exists())

    def _preflight_body(self, **overrides):
        body = {
            "patient": str(self.patient.external_id),
            "facility": str(self.facility.external_id),
            "department": str(self.organization.external_id),
            "form_submission": str(self.submission.external_id),
            "medication_outcome": "completed",
            "correspondence_outcome": "not_required",
            "correspondence_compilation": None,
        }
        body.update(overrides)
        return body

    def _ready_candidate(self):
        response = self.client.post(
            self.preflight_url,
            self._preflight_body(),
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["ready"], response.data)
        self.assertEqual(set(response.data["command_candidate"]), CANDIDATE_KEYS)
        return response.data["command_candidate"]

    def _close(self, candidate=None, client_request_id=None):
        payload = {
            **(candidate or self._ready_candidate()),
            "client_request_id": str(client_request_id or uuid4()),
            "confirmed": True,
        }
        return self.client.post(self.close_url, payload, format="json"), payload

    def _resolve_recovery_body(self, recovery, **overrides):
        if isinstance(recovery, dict):
            recovery_id = recovery["id"]
            recovery_hash = recovery["recovery_hash"]
        else:
            recovery_id = recovery.external_id
            recovery_hash = recovery.recovery_hash
        body = {
            "client_request_id": str(uuid4()),
            "recovery": str(recovery_id),
            "expected_recovery_hash": recovery_hash,
            "confirmed": True,
        }
        body.update(overrides)
        return body

    def _correspondence_context(self):
        return {
            "review_binding": str(self.review.external_id),
            "review_hash": self.review.review_hash,
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "facility": str(self.facility.external_id),
            "department": str(self.organization.external_id),
            "author": str(self.user.external_id),
        }

    @staticmethod
    def _synthetic_artifact_response(*args, **kwargs):
        del args, kwargs
        return {
            "ContentLength": len(SYNTHETIC_PDF),
            "ContentType": "application/pdf",
            "Body": io.BytesIO(SYNTHETIC_PDF),
        }

    def _finalized_letter_revision(self):
        context = self._correspondence_context()
        created = self.client.post(
            reverse("correspondence-letter-idempotent-create"),
            {
                "client_request_id": str(uuid4()),
                **context,
                "body": "Synthetic delivered correspondence",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        draft = CorrespondenceLetterRevision.objects.get(
            external_id=created.data["correspondence"]["id"]
        )
        finalized = self.client.post(
            reverse(
                "correspondence-letter-idempotent-finalize",
                kwargs={"external_id": draft.external_id},
            ),
            {
                "client_request_id": str(uuid4()),
                **context,
                "expected_version": draft.resource_version,
            },
            format="json",
        )
        self.assertEqual(finalized.status_code, 201, finalized.data)
        return CorrespondenceLetterRevision.objects.get(
            external_id=finalized.data["correspondence"]["id"]
        )

    def _build_open_correspondence_case(self):
        compiled = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(compiled.status_code, 201, compiled.data)
        self.compilation = CorrespondenceCompilation.objects.get()
        self.recipient = CorrespondenceReviewTestMixin._recipient(  # noqa: SLF001
            self,
            source_type="synthetic_test_fixture",
            channel_identifier="synthetic:no-network",
            source_provenance={
                "governance": "synthetic-test-only",
                "evidence_reference": "SYNTHETIC-CLOSE-1",
                "delivery_test_mode": "ack",
            },
        )
        self.review_url = reverse("correspondence-review-idempotent-bind")
        bound = CorrespondenceReviewTestMixin._bind(  # noqa: SLF001
            self,
            {
                "client_request_id": str(uuid4()),
                "compilation": str(self.compilation.external_id),
                "compilation_hash": self.compilation.compiled_hash,
                "patient": str(self.patient.external_id),
                "encounter": str(self.encounter.external_id),
                "facility": str(self.facility.external_id),
                "department": str(self.organization.external_id),
                "author": str(self.user.external_id),
                "recipient": str(self.recipient.external_id),
                "recipient_version": self.recipient.resource_version,
                "recipient_hash": self.recipient.content_hash,
            },
        )
        self.assertEqual(bound.status_code, 201, bound.data)
        self.review = CorrespondenceReview.objects.get()

        with (
            patch.object(ReportUpload.files_manager, "put_object", return_value={}),
            patch.object(
                ReportUpload.files_manager,
                "get_object",
                side_effect=self._synthetic_artifact_response,
            ),
            patch(
                "care.emr.api.viewsets.correspondence_letter."
                "render_correspondence_letter_pdf",
                return_value=SYNTHETIC_PDF,
            ),
            patch(
                "care.emr.api.viewsets.correspondence_delivery."
                "dispatch_correspondence_delivery_attempt.delay"
            ),
        ):
            revision = self._finalized_letter_revision()
            letter_artifact = ReportUpload.objects.get(correspondence_revision=revision)
            sent = self.client.post(
                reverse("correspondence-delivery-idempotent-send"),
                {
                    "client_request_id": str(uuid4()),
                    "correspondence_revision": str(revision.external_id),
                    "resource_version": revision.resource_version,
                    "revision_hash": revision.revision_hash,
                    "artifact": str(letter_artifact.external_id),
                    "artifact_sha256": letter_artifact.artifact_sha256,
                    **self._correspondence_context(),
                    "recipient": str(self.recipient.external_id),
                    "recipient_version": self.recipient.resource_version,
                    "recipient_hash": self.recipient.content_hash,
                    "confirmed": True,
                },
                format="json",
            )
        self.assertEqual(sent.status_code, 201, sent.data)
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        with patch(
            "care.emr.tasks.correspondence_delivery.synthetic_delivery_mode",
            return_value="ack",
        ):
            dispatch_correspondence_delivery_attempt(str(attempt.external_id))

        amended = self._amend_source()
        self.assertEqual(amended.status_code, 201, amended.data)
        current = FormSubmission.objects.get(
            external_id=amended.data["form_submission"]["id"]
        )
        self._artifact(current)
        outbox = CorrespondenceCorrectionOutbox.objects.get()
        with patch(
            "care.emr.tasks.correspondence_correction."
            "refresh_correspondence_correction_delivery.delay"
        ):
            project_correspondence_correction(str(outbox.external_id))
        self.submission = current
        self.artifact = ReportUpload.objects.get(form_submission=current)
        return CorrespondenceCorrectionCase.objects.get(), delivery

    def test_preflight_close_exact_replay_and_reload_contract(self):
        candidate = self._ready_candidate()
        response, payload = self._close(candidate)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data["replayed"])
        self.assertEqual(set(response.data["closure"]), CLOSURE_KEYS)

        replay = self.client.post(self.close_url, payload, format="json")
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertTrue(replay.data["replayed"])
        self.assertEqual(replay.data["closure"], response.data["closure"])

        self.encounter.refresh_from_db()
        self.booking.refresh_from_db()
        self.token.refresh_from_db()
        self.subqueue.refresh_from_db()
        self.assertEqual(self.encounter.status, "completed")
        self.assertIsNone(self.encounter.current_location_id)
        self.assertEqual(self.booking.status, BookingStatusChoices.fulfilled.value)
        self.assertEqual(self.token.status, TokenStatusOptions.FULFILLED.value)
        self.assertIsNone(self.subqueue.current_token_id)
        self.assertEqual(ConsultClosure.objects.count(), 1)
        self.assertEqual(ConsultClosureCommand.objects.count(), 1)

        reload_response = self.client.get(self.read_url)
        self.assertEqual(reload_response.status_code, 200, reload_response.data)
        self.assertEqual(reload_response.data["closure"], response.data["closure"])
        self.assertIsNone(reload_response.data["recovery"])

    def test_authoritative_reload_creates_one_pending_recovery_on_state_drift(self):
        response, _payload = self._close()
        self.assertEqual(response.status_code, 201, response.data)
        Token._base_manager.filter(pk=self.token.pk).update(  # noqa: SLF001
            status=TokenStatusOptions.IN_PROGRESS.value
        )

        first = self.client.get(self.read_url)
        second = self.client.get(self.read_url)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertIsNone(first.data["closure"])
        self.assertEqual(first.data["recovery"]["status"], "pending")
        self.assertEqual(set(first.data["recovery"]), RECOVERY_KEYS)
        self.assertEqual(len(first.data["recovery"]["recovery_hash"]), 64)
        self.assertEqual(
            first.data["recovery"]["safe_code"],
            "closure_integrity_failed",
        )
        self.assertEqual(second.data["recovery"], first.data["recovery"])
        self.assertEqual(
            ConsultClosureRecoveryTask.objects.filter(status="pending").count(),
            1,
        )

    def test_authoritative_reload_retries_stale_prelock_closure_snapshot(self):
        response, _payload = self._close()
        self.assertEqual(response.status_code, 201, response.data)
        closure = ConsultClosure.objects.get()

        with patch.object(
            ConsultClosureViewSet,
            "_latest_closure_reference",
            side_effect=[None, closure],
        ) as reference_read:
            reloaded = self.client.get(self.read_url)

        self.assertEqual(reloaded.status_code, 200, reloaded.data)
        self.assertEqual(reloaded.data["closure"]["id"], str(closure.external_id))
        self.assertIsNone(reloaded.data["recovery"])
        self.assertEqual(reference_read.call_count, 2)
        self.assertFalse(ConsultClosureRecoveryTask.objects.exists())

    def test_recovery_resolution_succeeds_and_exact_replay_is_idempotent(self):
        response, _payload = self._close()
        self.assertEqual(response.status_code, 201, response.data)
        Token._base_manager.filter(pk=self.token.pk).update(  # noqa: SLF001
            status=TokenStatusOptions.IN_PROGRESS.value
        )
        drifted = self.client.get(self.read_url)
        self.assertIsNone(drifted.data["closure"])
        recovery = ConsultClosureRecoveryTask.objects.get(status="pending")
        Token._base_manager.filter(pk=self.token.pk).update(  # noqa: SLF001
            status=TokenStatusOptions.FULFILLED.value
        )
        resolve_url = reverse(
            "consult-closure-idempotent-resolve-recovery",
            kwargs={"external_id": self.encounter.external_id},
        )
        self.assertEqual(set(drifted.data["recovery"]), RECOVERY_KEYS)
        body = self._resolve_recovery_body(drifted.data["recovery"])

        resolved = self.client.post(resolve_url, body, format="json")
        replayed = self.client.post(resolve_url, body, format="json")

        self.assertEqual(resolved.status_code, 201, resolved.data)
        self.assertFalse(resolved.data["replayed"])
        self.assertEqual(resolved.data["recovery"]["status"], "resolved")
        self.assertEqual(set(resolved.data["recovery"]), RECOVERY_KEYS)
        self.assertEqual(
            resolved.data["recovery"]["recovery_hash"],
            drifted.data["recovery"]["recovery_hash"],
        )
        self.assertEqual(replayed.status_code, 200, replayed.data)
        self.assertTrue(replayed.data["replayed"])
        self.assertEqual(replayed.data["recovery"], resolved.data["recovery"])
        recovery.refresh_from_db()
        self.assertEqual(recovery.status, "resolved")
        self.assertEqual(
            str(recovery.resolution_request_id),
            body["client_request_id"],
        )
        self.assertEqual(len(recovery.resolution_payload_hash), 64)
        self.assertEqual(len(recovery.resolution_hash), 64)
        reloaded = self.client.get(self.read_url)
        self.assertIsNotNone(reloaded.data["closure"])
        self.assertEqual(reloaded.data["recovery"]["status"], "resolved")

    def test_recovery_resolution_refuses_while_authoritative_state_is_corrupt(self):
        response, _payload = self._close()
        self.assertEqual(response.status_code, 201, response.data)
        ConsultClosureCommand._base_manager.update(command_hash="0" * 64)  # noqa: SLF001
        drifted = self.client.get(self.read_url)
        self.assertIsNone(drifted.data["closure"])
        recovery = ConsultClosureRecoveryTask.objects.get(status="pending")
        resolve_url = reverse(
            "consult-closure-idempotent-resolve-recovery",
            kwargs={"external_id": self.encounter.external_id},
        )

        refused = self.client.post(
            resolve_url,
            self._resolve_recovery_body(drifted.data["recovery"]),
            format="json",
        )

        self.assertEqual(refused.status_code, 409, refused.data)
        self.assertEqual(refused.data["blocker_codes"], ["closure_integrity_failed"])
        recovery.refresh_from_db()
        self.assertEqual(recovery.status, "pending")
        self.assertIsNone(recovery.resolved_at)

    def test_required_form_and_complete_medication_set_fail_closed(self):
        with override_settings(
            CONSULT_CLOSE_REQUIRED_FORMS_BY_DEPARTMENT={
                "urology department": ["different-required-form"]
            }
        ):
            wrong_form = self.client.post(
                self.preflight_url,
                self._preflight_body(),
                format="json",
            )
        self.assertFalse(wrong_form.data["ready"])
        self.assertIn("form_not_finalized", wrong_form.data["blocker_codes"])

        baker.make(
            type(self.medication),
            patient=self.patient,
            encounter=self.encounter,
            status="active",
            intent="order",
            do_not_perform=False,
            client_request_id=uuid4(),
            client_request_payload_hash="c" * 64,
        )
        incomplete = self.client.post(
            self.preflight_url,
            self._preflight_body(),
            format="json",
        )
        self.assertFalse(incomplete.data["ready"])
        self.assertIn("medication_incomplete", incomplete.data["blocker_codes"])

    def test_close_rolls_back_every_state_when_ledger_insert_fails(self):
        candidate = self._ready_candidate()
        with patch.object(ConsultClosure, "save", side_effect=IntegrityError("forced")):
            response, _payload = self._close(candidate)
        self.assertEqual(response.status_code, 409, response.data)
        self.encounter.refresh_from_db()
        self.booking.refresh_from_db()
        self.token.refresh_from_db()
        self.subqueue.refresh_from_db()
        self.assertEqual(self.encounter.status, "in_progress")
        self.assertEqual(
            self.booking.status,
            BookingStatusChoices.in_consultation.value,
        )
        self.assertEqual(self.token.status, TokenStatusOptions.IN_PROGRESS.value)
        self.assertEqual(self.subqueue.current_token_id, self.token.id)
        self.assertFalse(ConsultClosure.objects.exists())

    def test_completed_encounter_form_amendment_is_deferred_fail_closed(self):
        response, _payload = self._close()
        self.assertEqual(response.status_code, 201, response.data)
        view = FormSubmissionViewSet()
        view.request = type("Request", (), {"user": self.user})()
        with transaction.atomic(), self.assertRaises(ValidationError):
            view._lock_and_authorize_write_context(  # noqa: SLF001
                self.submission,
                "amend",
            )

    def test_terminal_transitions_require_close_and_closure_restart_is_rejected(self):
        with self.assertRaises(ValidationError):
            EncounterViewSet().validate_data(
                SimpleNamespace(status="completed"),
                self.encounter,
            )
        with self.assertRaises(ValidationError):
            TokenBookingViewSet().validate_data(
                SimpleNamespace(status=BookingStatusChoices.fulfilled.value),
                self.booking,
            )
        with self.assertRaises(ValidationError):
            TokenViewSet().validate_data(
                SimpleNamespace(
                    status=TokenStatusOptions.FULFILLED.value,
                    sub_queue=None,
                ),
                self.token,
            )

        closed, _payload = self._close()
        self.assertEqual(closed.status_code, 201, closed.data)
        restarted = self.client.post(
            reverse(
                "encounter-restart",
                kwargs={"external_id": self.encounter.external_id},
            ),
            format="json",
        )
        self.assertEqual(restarted.status_code, 400, restarted.data)
        self.assertIn("consult-closure", str(restarted.data).lower())

    def test_correspondence_series_blocks_not_required_and_derives_resolved_lineage(
        self,
    ):
        case, delivery = self._build_open_correspondence_case()
        blocked = self.client.post(
            self.preflight_url,
            self._preflight_body(correspondence_outcome="not_required"),
            format="json",
        )
        self.assertEqual(blocked.status_code, 200, blocked.data)
        self.assertFalse(blocked.data["ready"])
        self.assertIn("correspondence_incomplete", blocked.data["blocker_codes"])
        self.assertEqual(case.status, "open")

        CorrespondenceCorrectionCase._base_manager.filter(pk=case.pk).update(  # noqa: SLF001
            status="resolved",
            resource_version=case.resource_version + 1,
            resolution_mode="original_not_delivered",
            resolved_at=timezone.now(),
            resolved_by=self.user,
            delivery_certainty="not_delivered",
            notification_status="not_required",
            paper_reconciliation_status="not_required",
            case_hash="d" * 64,
        )
        case.refresh_from_db()
        with (
            patch(
                "care.emr.api.viewsets.consult_closure.correction_case_integrity_valid",
                return_value=True,
            ),
            patch(
                "care.emr.api.viewsets.consult_closure.latest_delivery_event",
                return_value=SimpleNamespace(certainty="not_delivered"),
            ),
        ):
            resolved = self.client.post(
                self.preflight_url,
                self._preflight_body(
                    correspondence_outcome="correction_resolved",
                    correspondence_compilation=None,
                ),
                format="json",
            )
        self.assertEqual(resolved.status_code, 200, resolved.data)
        self.assertTrue(resolved.data["ready"], resolved.data)
        candidate = resolved.data["command_candidate"]
        self.assertEqual(
            str(candidate["correspondence_compilation"]),
            str(self.compilation.external_id),
        )
        self.assertEqual(str(candidate["correspondence_case"]), str(case.external_id))
        self.assertEqual(candidate["correspondence_case_hash"], case.case_hash)
        self.assertIsNone(candidate["correspondence_delivery"])
        self.assertEqual(delivery.id, case.original_delivery_id)

    def test_corrupted_command_hash_is_integrity_failure_not_idempotency_conflict(self):
        response, payload = self._close()
        self.assertEqual(response.status_code, 201, response.data)
        ConsultClosureCommand._base_manager.update(command_hash="0" * 64)  # noqa: SLF001

        replay = self.client.post(self.close_url, payload, format="json")
        self.assertEqual(replay.status_code, 409, replay.data)
        self.assertEqual(replay.data["blocker_codes"], ["closure_integrity_failed"])
        self.assertNotIn("idempotency_conflict", replay.data["blocker_codes"])

    def test_terminal_encounter_rejects_new_device_location_and_medication_mutation(
        self,
    ):
        response, _payload = self._close()
        self.assertEqual(response.status_code, 201, response.data)
        self.encounter.refresh_from_db()

        device = baker.make(
            Device,
            facility=self.facility,
            status="active",
            availability_status="available",
            manufacturer="Synthetic",
        )
        device_response = self.client.post(
            reverse(
                "device-associate-encounter",
                kwargs={
                    "facility_external_id": self.facility.external_id,
                    "external_id": device.external_id,
                },
            ),
            {"encounter": str(self.encounter.external_id)},
            format="json",
        )
        self.assertEqual(device_response.status_code, 400, device_response.data)

        location = baker.make(
            FacilityLocation,
            facility=self.facility,
            status="active",
            operational_status="operational",
            system_availability_status="available",
            name="Synthetic room",
            description="Synthetic",
            mode="instance",
            form="ro",
        )
        location_association = FacilityLocationEncounter(
            location=location,
            encounter=self.encounter,
            status="active",
            start_datetime=timezone.now(),
        )
        location_view = FacilityLocationEncounterViewSet()
        location_view.kwargs = {
            "facility_external_id": self.facility.external_id,
            "location_external_id": location.external_id,
        }
        location_view.request = SimpleNamespace(user=self.user)
        with self.assertRaises(ValidationError):
            location_view.perform_create(location_association)

        medication = MedicationRequest(
            patient=self.patient,
            encounter=self.encounter,
            status="active",
            intent="order",
        )
        medication_view = MedicationRequestViewSet()
        medication_view.request = SimpleNamespace(user=self.user)
        with self.assertRaises(ValidationError):
            medication_view.perform_create(medication)
