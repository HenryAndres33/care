import copy
import io
from http import HTTPStatus
from unittest.mock import patch
from uuid import uuid4

from django.urls import reverse
from django.utils import timezone

from care.emr.correspondence.correction import (
    CorrespondenceCorrectionIntegrityError,
    build_correspondence_change_set,
    correction_case_integrity_valid,
    materialize_claimed_correction_outbox,
)
from care.emr.correspondence.delivery import append_delivery_event
from care.emr.models.questionnaire import FormSubmission
from care.emr.models.report.report_upload import ReportUpload
from care.emr.resources.correspondence_continuity import (
    MAX_CORRESPONDENCE_CONTINUITY_CHANGES,
    correspondence_continuity_hash,
    correspondence_correction_case_hash,
    correspondence_correction_event_hash,
)
from care.emr.resources.form_submission.commands import (
    finalized_form_submission_snapshot_hash,
)
from care.emr.tasks.correspondence_correction import (
    _claim_correction_outbox,
    _release_claim,
    _terminal_claim,
    project_correspondence_correction,
    refresh_correspondence_correction_delivery,
    refresh_correspondence_replacement_delivery,
    scan_correspondence_correction_delivery_cases,
)
from care.emr.tasks.correspondence_delivery import (
    dispatch_correspondence_delivery_attempt,
)
from care.emr.tests.test_correspondence_review import CorrespondenceReviewTestMixin
from care.utils.tests.base import CareAPITestBase
from care_suriname.models.correspondence_correction import (
    CorrespondenceCorrectionCase,
    CorrespondenceCorrectionCommand,
    CorrespondenceCorrectionEvent,
    CorrespondenceCorrectionOutbox,
    CorrespondencePaperReconciliationAttestation,
    CorrespondenceReplacementAttempt,
)
from care_suriname.models.correspondence_delivery import CorrespondenceDelivery
from care_suriname.models.correspondence_letter import CorrespondenceLetterRevision
from care_suriname.models.correspondence_review import CorrespondenceReview

SYNTHETIC_PDF = b"%PDF-1.7\nsynthetic-continuity-artifact"


def synthetic_artifact_response(*args, **kwargs):
    del args, kwargs
    return {
        "ContentLength": len(SYNTHETIC_PDF),
        "ContentType": "application/pdf",
        "Body": io.BytesIO(SYNTHETIC_PDF),
    }


class TestCorrespondenceContinuityAPI(
    CorrespondenceReviewTestMixin,
    CareAPITestBase,
):
    def setUp(self):
        super().setUp()
        self.build_review_context()
        self.continuity_url = reverse("correspondence-continuity-list")

    def _get(self, **overrides):
        query = {
            "compilation": str(self.compilation.external_id),
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
        }
        query.update(overrides)
        return self.client.get(self.continuity_url, query)

    def _amend(self):
        response = self._amend_source()
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.json())
        return FormSubmission.objects.get(
            external_id=response.json()["form_submission"]["id"]
        )

    def _synthetic_current(self, response_dump):
        current = copy.copy(self.submission)
        current.pk = self.submission.pk + 1_000_000
        current.external_id = uuid4()
        current.resource_version = self.submission.resource_version + 1
        current.previous_version = self.submission
        current.amendment_type = "amendment"
        current.amendment_reason = "Synthetic bounded diff"
        current.response_dump = response_dump
        current.workflow_finalized_at = timezone.now()
        current.workflow_finalized_by = self.user
        current.finalized_snapshot_hash = finalized_form_submission_snapshot_hash(
            current
        )
        return current

    def test_current_contract_is_exact_and_exposes_only_actionable_review(self):
        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.OK, response.json())
        body = response.json()
        self.assertEqual(
            set(body),
            {
                "action_policy",
                "authoritative_source",
                "changes",
                "checked_at",
                "context",
                "continuity_hash",
                "continuity_id",
                "contract_version",
                "correction_case",
                "frozen_source",
                "historical_chain",
                "required_action",
                "resource_version",
                "source_state",
            },
        )
        self.assertEqual(body["source_state"], "current")
        self.assertEqual(body["required_action"], "none")
        self.assertEqual(body["authoritative_source"], body["frozen_source"])
        self.assertTrue(body["action_policy"]["can_review"])
        self.assertFalse(body["action_policy"]["can_draft"])
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("Authorization", response["Vary"])
        self.assertEqual(body["continuity_hash"], correspondence_continuity_hash(body))
        self.assertEqual(
            response["ETag"],
            f'"{body["continuity_id"]}:{body["resource_version"]}:'
            f'{body["continuity_hash"]}"',
        )

    def test_pending_projection_returns_retryable_503_then_unavailable_regeneration(
        self,
    ):
        self._amend()

        pending = self._get()
        self.assertEqual(pending.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(pending["Retry-After"], "1")
        self.assertEqual(pending["Cache-Control"], "no-store")

        outbox = CorrespondenceCorrectionOutbox.objects.get()
        project_correspondence_correction(str(outbox.external_id))
        projected = self._get()

        self.assertEqual(projected.status_code, HTTPStatus.OK, projected.json())
        body = projected.json()
        self.assertEqual(body["source_state"], "unavailable")
        self.assertEqual(body["required_action"], "regenerate_unsent")
        self.assertIsNone(body["authoritative_source"])
        self.assertIsNone(body["correction_case"])
        self.assertEqual(
            body["changes"][0]["field_reference"],
            "form.resource_version",
        )
        self.assertIn(
            "form.responses/measurement",
            {change["field_reference"] for change in body["changes"]},
        )

    def test_failed_terminal_outbox_for_unsent_branch_forces_integrity_state(self):
        self._amend()
        outbox = CorrespondenceCorrectionOutbox.objects.get()
        outbox_id, token = _claim_correction_outbox(str(outbox.external_id))
        self.assertTrue(
            _terminal_claim(
                outbox_id,
                token,
                safe_code="synthetic_projection_failure",
            )
        )

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.OK, response.json())
        self.assertEqual(response.json()["source_state"], "integrity_failed")
        self.assertEqual(response.json()["required_action"], "none")
        self.assertIsNone(response.json()["authoritative_source"])

    def test_stale_worker_token_cannot_release_a_reclaimed_lease(self):
        self._amend()
        outbox = CorrespondenceCorrectionOutbox.objects.get()
        outbox_id, first_token = _claim_correction_outbox(str(outbox.external_id))
        CorrespondenceCorrectionOutbox.objects.filter(pk=outbox_id).update(
            lease_expires_at=timezone.now()
        )
        reclaimed_id, second_token = _claim_correction_outbox(str(outbox.external_id))

        self.assertEqual(reclaimed_id, outbox_id)
        self.assertNotEqual(first_token, second_token)
        self.assertFalse(
            _release_claim(
                outbox_id,
                first_token,
                safe_code="stale_worker",
                delay_seconds=1,
            )
        )
        self.assertTrue(
            _release_claim(
                outbox_id,
                second_token,
                safe_code="current_worker",
                delay_seconds=1,
            )
        )

    def test_per_series_claims_wait_for_the_predecessor(self):
        first_result = self._amend()
        self.submission = first_result
        self._amend()
        outboxes = list(
            CorrespondenceCorrectionOutbox.objects.select_related(
                "source_correction"
            ).order_by("source_correction__sequence")
        )

        self.assertIsNone(_claim_correction_outbox(str(outboxes[1].external_id)))
        project_correspondence_correction(str(outboxes[0].external_id))
        second_claim = _claim_correction_outbox(str(outboxes[1].external_id))
        self.assertIsNotNone(second_claim)
        self.assertTrue(
            _release_claim(
                *second_claim,
                safe_code="ordered_claim_verified",
                delay_seconds=1,
            )
        )

    def test_inactive_template_disables_review_without_corrupting_history(self):
        type(self.template)._base_manager.filter(pk=self.template.pk).update(  # noqa: SLF001
            status="retired"
        )

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.OK, response.json())
        self.assertEqual(response.json()["source_state"], "current")
        self.assertFalse(response.json()["action_policy"]["can_review"])

    def test_diff_escapes_json_pointer_segments_without_reference_collisions(self):
        current = self._synthetic_current(
            {
                "a/b": "flat",
                "a": {"b": "nested"},
            }
        )

        changes = build_correspondence_change_set(
            self.submission,
            current,
        ).display_changes
        references = {change["field_reference"] for change in changes}

        self.assertIn("form.responses/a~1b", references)
        self.assertIn("form.responses/a/b", references)
        self.assertEqual(len(references), len(changes))

    def test_diff_overflow_marker_is_reserved_unique_and_display_is_bounded(self):
        current = self._synthetic_current(
            {f"field_{index:03d}": index for index in range(250)}
        )

        changes = build_correspondence_change_set(
            self.submission,
            current,
        ).display_changes
        references = [change["field_reference"] for change in changes]

        self.assertEqual(len(changes), MAX_CORRESPONDENCE_CONTINUITY_CHANGES)
        self.assertEqual(references[-1], "continuity.synthetic/overflow")
        self.assertEqual(len(references), len(set(references)))

    def test_deep_legacy_snapshot_is_rejected_before_recursive_diff(self):
        nested = "bounded"
        for index in range(25):
            nested = {f"level_{index}": nested}
        current = self._synthetic_current({"deep": nested})

        with self.assertRaises(CorrespondenceCorrectionIntegrityError):
            build_correspondence_change_set(self.submission, current)

    def test_draft_policy_covers_create_revise_and_finalize_semantics(self):
        bound = self._bind()
        self.assertEqual(bound.status_code, HTTPStatus.CREATED, bound.json())
        review = CorrespondenceReview.objects.get()
        created = self.client.post(
            reverse("correspondence-letter-idempotent-create"),
            {
                "client_request_id": str(uuid4()),
                "review_binding": str(review.external_id),
                "review_hash": review.review_hash,
                "patient": str(self.patient.external_id),
                "encounter": str(self.encounter.external_id),
                "facility": str(self.facility.external_id),
                "department": str(self.organization.external_id),
                "author": str(self.user.external_id),
                "body": "Synthetic draft",
            },
            format="json",
        )
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.OK, response.json())
        self.assertTrue(response.json()["action_policy"]["can_draft"])
        self.assertTrue(response.json()["action_policy"]["can_finalize"])

    def test_inactive_bound_recipient_disables_draft_and_finalize(self):
        bound = self._bind()
        self.assertEqual(bound.status_code, HTTPStatus.CREATED, bound.json())
        type(self.recipient)._base_manager.filter(pk=self.recipient.pk).update(  # noqa: SLF001
            active=False
        )

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.OK, response.json())
        self.assertFalse(response.json()["action_policy"]["can_draft"])
        self.assertFalse(response.json()["action_policy"]["can_finalize"])

    def test_missing_series_head_maps_to_integrity_conflict(self):
        from care_suriname.models.correspondence_correction import (
            FormSubmissionSeriesHead,
        )

        FormSubmissionSeriesHead.objects.filter(
            series_id=self.submission.series_id
        ).delete()

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_tampered_series_head_maps_to_integrity_conflict(self):
        from care_suriname.models.correspondence_correction import (
            FormSubmissionSeriesHead,
        )

        FormSubmissionSeriesHead.objects.filter(
            series_id=self.submission.series_id
        ).update(head_hash="0" * 64)

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_wrong_patient_or_encounter_is_non_leaking_404(self):
        for query in [
            {"patient": str(uuid4())},
            {"encounter": str(uuid4())},
        ]:
            with self.subTest(query=query):
                response = self._get(**query)
                self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_missing_extra_or_invalid_query_is_bounded_400_and_never_cached(self):
        cases = [
            {},
            {
                "compilation": "not-a-uuid",
                "patient": str(self.patient.external_id),
                "encounter": str(self.encounter.external_id),
            },
            {
                "compilation": str(self.compilation.external_id),
                "patient": str(self.patient.external_id),
                "encounter": str(self.encounter.external_id),
                "extra": "forbidden",
            },
        ]
        for query in cases:
            with self.subTest(query=query):
                response = self.client.get(self.continuity_url, query)
                self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertIn("Authorization", response["Vary"])

    def test_user_without_clinical_access_is_forbidden(self):
        unauthorized = self.create_user(is_active=True, verified=True)
        self.client.force_authenticate(user=unauthorized)

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)


class TestCorrespondenceContinuityDeliveredBranch(
    CorrespondenceReviewTestMixin,
    CareAPITestBase,
):
    def _recipient(self, **overrides):
        values = {
            "source_type": "synthetic_test_fixture",
            "channel_identifier": "synthetic:no-network",
            "source_provenance": {
                "governance": "synthetic-test-only",
                "evidence_reference": "SYNTHETIC-CONTINUITY-1",
                "delivery_test_mode": "ack",
            },
        }
        values.update(overrides)
        return super()._recipient(**values)

    def setUp(self):
        super().setUp()
        self.build_review_context()
        bound = self._bind()
        if bound.status_code != HTTPStatus.CREATED:
            raise AssertionError(bound.json())
        self.review = CorrespondenceReview.objects.get()
        self.put_patcher = patch.object(
            ReportUpload.files_manager,
            "put_object",
            return_value={},
        )
        self.render_patcher = patch(
            "care_suriname.api.viewsets.correspondence_letter."
            "render_correspondence_letter_pdf",
            return_value=SYNTHETIC_PDF,
        )
        self.correction_render_patcher = patch(
            "care_suriname.api.viewsets.correspondence_correction_case."
            "render_correspondence_letter_pdf",
            return_value=SYNTHETIC_PDF,
        )
        self.get_patcher = patch.object(
            ReportUpload.files_manager,
            "get_object",
            side_effect=synthetic_artifact_response,
        )
        self.enqueue_patcher = patch(
            "care_suriname.api.viewsets.correspondence_delivery."
            "dispatch_correspondence_delivery_attempt.delay"
        )
        self.refresh_patcher = patch(
            "care.emr.tasks.correspondence_correction."
            "refresh_correspondence_correction_delivery.delay"
        )
        self.replacement_refresh_patcher = patch(
            "care.emr.tasks.correspondence_correction."
            "refresh_correspondence_replacement_delivery.delay"
        )
        self.put_patcher.start()
        self.render_patcher.start()
        self.correction_render_patcher.start()
        self.get_patcher.start()
        self.enqueue_patcher.start()
        self.refresh_patcher.start()
        self.replacement_refresh_patcher.start()
        self.addCleanup(self.put_patcher.stop)
        self.addCleanup(self.render_patcher.stop)
        self.addCleanup(self.correction_render_patcher.stop)
        self.addCleanup(self.get_patcher.stop)
        self.addCleanup(self.enqueue_patcher.stop)
        self.addCleanup(self.refresh_patcher.stop)
        self.addCleanup(self.replacement_refresh_patcher.stop)
        self.revision = self._finalized_revision()
        self.letter_artifact = ReportUpload.objects.get(letter_revision=self.revision)
        sent = self.client.post(
            reverse("correspondence-delivery-idempotent-send"),
            self._send_payload(),
            format="json",
        )
        if sent.status_code != HTTPStatus.CREATED:
            raise AssertionError(sent.json())
        self.delivery = CorrespondenceDelivery.objects.get()
        self.continuity_url = reverse("correspondence-continuity-list")

    def _context(self):
        return {
            "review_binding": str(self.review.external_id),
            "review_hash": self.review.review_hash,
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "facility": str(self.facility.external_id),
            "department": str(self.organization.external_id),
            "author": str(self.user.external_id),
        }

    def _finalized_revision(self):
        created = self.client.post(
            reverse("correspondence-letter-idempotent-create"),
            {
                "client_request_id": str(uuid4()),
                **self._context(),
                "body": "Synthetic delivered correspondence",
            },
            format="json",
        )
        if created.status_code != HTTPStatus.CREATED:
            raise AssertionError(created.json())
        draft = CorrespondenceLetterRevision.objects.get(
            external_id=created.json()["correspondence"]["id"]
        )
        finalized = self.client.post(
            reverse(
                "correspondence-letter-idempotent-finalize",
                kwargs={"external_id": draft.external_id},
            ),
            {
                "client_request_id": str(uuid4()),
                **self._context(),
                "expected_version": draft.resource_version,
            },
            format="json",
        )
        if finalized.status_code != HTTPStatus.CREATED:
            raise AssertionError(finalized.json())
        return CorrespondenceLetterRevision.objects.get(
            external_id=finalized.json()["correspondence"]["id"]
        )

    def _send_payload(self):
        return {
            "client_request_id": str(uuid4()),
            "correspondence_revision": str(self.revision.external_id),
            "resource_version": self.revision.resource_version,
            "revision_hash": self.revision.revision_hash,
            "artifact": str(self.letter_artifact.external_id),
            "artifact_sha256": self.letter_artifact.artifact_sha256,
            **self._context(),
            "recipient": str(self.review.recipient.external_id),
            "recipient_version": self.review.recipient_version,
            "recipient_hash": self.review.recipient_hash,
            "confirmed": True,
        }

    def _dispatch(self, mode):
        attempt = self.delivery.attempts.get(attempt_number=1)
        with patch(
            "care.emr.tasks.correspondence_delivery.synthetic_delivery_mode",
            return_value=mode,
        ):
            dispatch_correspondence_delivery_attempt(str(attempt.external_id))
        return self.delivery.events.latest("sequence")

    def _amend_and_project(self):
        amended = self._amend_source()
        self.assertEqual(amended.status_code, HTTPStatus.CREATED, amended.json())
        current = FormSubmission.objects.get(
            external_id=amended.json()["form_submission"]["id"]
        )
        self._artifact(current)
        outbox = CorrespondenceCorrectionOutbox.objects.get()
        project_correspondence_correction(str(outbox.external_id))
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, "completed")
        return current

    def _get(self):
        return self.client.get(
            self.continuity_url,
            {
                "compilation": str(self.compilation.external_id),
                "patient": str(self.patient.external_id),
                "encounter": str(self.encounter.external_id),
            },
        )

    def _correction_command(self, case, payload):
        return self.client.post(
            reverse(
                "correspondence-correction-case-idempotent-command",
                kwargs={"external_id": case.external_id},
            ),
            payload,
            format="json",
        )

    def _start_replacement_payload(self, case, current):
        artifact = ReportUpload.objects.get(form_submission=current)
        return {
            "client_request_id": str(uuid4()),
            "command_type": "start_replacement",
            "expected_case_version": case.resource_version,
            "expected_case_hash": case.case_hash,
            "patient": str(self.patient.external_id),
            "encounter": str(self.encounter.external_id),
            "facility": str(self.facility.external_id),
            "department": str(self.organization.external_id),
            "encounter_reason": str(self.reason.external_id),
            "form_submission": str(current.external_id),
            "form_source_version": current.resource_version,
            "form_source_hash": current.finalized_snapshot_hash,
            "form_artifact": str(artifact.external_id),
            "form_artifact_hash": artifact.artifact_sha256,
            "medication_actions": [
                {
                    "id": str(self.medication.external_id),
                    "client_request_id": str(self.medication.client_request_id),
                }
            ],
            "template": str(self.template.external_id),
            "template_version": self.template.resource_version,
            "template_hash": self.template.content_hash,
            "author": str(self.user.external_id),
            "recipient": str(self.review.recipient.external_id),
            "recipient_version": self.review.recipient_version,
            "recipient_hash": self.review.recipient_hash,
        }

    def _finalize_and_send_replacement(self, case, started):
        attempt_id = started["replacement"]["attempt"]["id"]
        finalized = self._correction_command(
            case,
            {
                "client_request_id": str(uuid4()),
                "command_type": "finalize_replacement",
                "expected_case_version": started["case_version"],
                "expected_case_hash": started["case_hash"],
                "attempt": attempt_id,
                "target_revision": started["replacement"]["revision"],
                "target_revision_version": started["replacement"]["revision_version"],
                "target_revision_hash": started["replacement"]["revision_hash"],
                "confirmed": True,
            },
        )
        self.assertEqual(finalized.status_code, HTTPStatus.CREATED, finalized.json())
        state = finalized.json()
        sent = self._correction_command(
            case,
            {
                "client_request_id": str(uuid4()),
                "command_type": "send_replacement",
                "expected_case_version": state["case_version"],
                "expected_case_hash": state["case_hash"],
                "attempt": attempt_id,
                "target_revision": state["replacement"]["revision"],
                "target_revision_version": state["replacement"]["revision_version"],
                "target_revision_hash": state["replacement"]["revision_hash"],
                "controlled_copy_artifact": state["replacement"]["artifact"],
                "controlled_copy_artifact_hash": state["replacement"]["artifact_hash"],
                "confirmed": True,
            },
        )
        self.assertEqual(sent.status_code, HTTPStatus.CREATED, sent.json())
        return sent.json()

    def test_acknowledged_delivery_opens_hashed_system_case_and_requires_correction(
        self,
    ):
        self.assertEqual(self._dispatch("ack").event_type, "acknowledged")
        self._amend_and_project()

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.OK, response.json())
        body = response.json()
        self.assertEqual(body["source_state"], "stale")
        self.assertEqual(body["required_action"], "send_correction")
        self.assertIsNotNone(body["authoritative_source"])
        self.assertEqual(body["correction_case"]["notification_status"], "required")
        case = CorrespondenceCorrectionCase.objects.get()
        event = CorrespondenceCorrectionEvent.objects.get(case=case)
        self.assertIsNone(case.created_by)
        self.assertIsNone(case.updated_by)
        self.assertIsNone(event.actor)
        self.assertIsNone(event.created_by)
        self.assertTrue(correction_case_integrity_valid(case))

    def test_replacement_lifecycle_retries_without_resending_original_and_resolves(  # noqa: PLR0915
        self,
    ):
        self.assertEqual(self._dispatch("ack").event_type, "acknowledged")
        current = self._amend_and_project()
        case = CorrespondenceCorrectionCase.objects.get()
        original_attempt = self.delivery.attempts.get()
        start_payload = self._start_replacement_payload(case, current)

        started = self._correction_command(case, start_payload)
        replayed = self._correction_command(case, start_payload)

        self.assertEqual(started.status_code, HTTPStatus.CREATED, started.json())
        self.assertEqual(replayed.status_code, HTTPStatus.OK, replayed.json())
        self.assertTrue(replayed.json()["replayed"])
        self.assertEqual(CorrespondenceReplacementAttempt.objects.count(), 1)
        self.assertEqual(CorrespondenceCorrectionCommand.objects.count(), 1)
        state = started.json()
        attempt_id = state["replacement"]["attempt"]["id"]
        finalized = self._correction_command(
            case,
            {
                "client_request_id": str(uuid4()),
                "command_type": "finalize_replacement",
                "expected_case_version": state["case_version"],
                "expected_case_hash": state["case_hash"],
                "attempt": attempt_id,
                "target_revision": state["replacement"]["revision"],
                "target_revision_version": state["replacement"]["revision_version"],
                "target_revision_hash": state["replacement"]["revision_hash"],
                "confirmed": True,
            },
        )
        self.assertEqual(finalized.status_code, HTTPStatus.CREATED, finalized.json())
        state = finalized.json()
        self.assertEqual(state["paper_reconciliation_status"], "required")
        sent = self._correction_command(
            case,
            {
                "client_request_id": str(uuid4()),
                "command_type": "send_replacement",
                "expected_case_version": state["case_version"],
                "expected_case_hash": state["case_hash"],
                "attempt": attempt_id,
                "target_revision": state["replacement"]["revision"],
                "target_revision_version": state["replacement"]["revision_version"],
                "target_revision_hash": state["replacement"]["revision_hash"],
                "controlled_copy_artifact": state["replacement"]["artifact"],
                "controlled_copy_artifact_hash": state["replacement"]["artifact_hash"],
                "confirmed": True,
            },
        )
        self.assertEqual(sent.status_code, HTTPStatus.CREATED, sent.json())
        replacement = CorrespondenceDelivery.objects.get(
            correction_case_reference=case.external_id
        )
        self.assertEqual(replacement.supersedes_id, self.delivery.id)
        self.assertEqual(CorrespondenceDelivery.objects.count(), 2)
        first_replacement_attempt = replacement.attempts.get(attempt_number=1)
        with patch(
            "care.emr.tasks.correspondence_delivery.synthetic_delivery_mode",
            return_value="fail_once",
        ):
            dispatch_correspondence_delivery_attempt(
                str(first_replacement_attempt.external_id)
            )
        refresh_correspondence_replacement_delivery(str(replacement.external_id))
        continuity = self._get()
        self.assertEqual(continuity.status_code, HTTPStatus.OK, continuity.json())
        case_state = continuity.json()["correction_case"]
        workflow = case_state["replacement"]
        self.assertTrue(workflow["can_retry_delivery"])
        retried = self._correction_command(
            case,
            {
                "client_request_id": str(uuid4()),
                "command_type": "retry_replacement",
                "expected_case_version": case_state["resource_version"],
                "expected_case_hash": case_state["case_hash"],
                "attempt": attempt_id,
                "delivery": workflow["delivery"],
                "delivery_event_sequence": workflow["delivery_event_sequence"],
                "delivery_event_hash": workflow["delivery_event_hash"],
                "confirmed": True,
            },
        )
        self.assertEqual(retried.status_code, HTTPStatus.CREATED, retried.json())
        self.assertEqual(CorrespondenceDelivery.objects.count(), 2)
        self.assertEqual(self.delivery.attempts.count(), 1)
        self.assertEqual(self.delivery.attempts.get().id, original_attempt.id)
        second_replacement_attempt = replacement.attempts.get(attempt_number=2)
        with patch(
            "care.emr.tasks.correspondence_delivery.synthetic_delivery_mode",
            return_value="fail_once",
        ):
            dispatch_correspondence_delivery_attempt(
                str(second_replacement_attempt.external_id)
            )
        refresh_correspondence_replacement_delivery(str(replacement.external_id))
        continuity = self._get()
        self.assertEqual(continuity.status_code, HTTPStatus.OK, continuity.json())
        case_state = continuity.json()["correction_case"]
        self.assertEqual(case_state["replacement"]["status"], "acknowledged")
        state = retried.json()
        attested = self._correction_command(
            case,
            {
                "client_request_id": str(uuid4()),
                "command_type": "attest_paper",
                "expected_case_version": case_state["resource_version"],
                "expected_case_hash": case_state["case_hash"],
                "attempt": attempt_id,
                "controlled_copy_artifact": state["replacement"]["artifact"],
                "controlled_copy_artifact_hash": state["replacement"]["artifact_hash"],
                "attestation_type": "corrected_copy_filed_prior_copy_reconciled",
                "confirmed": True,
            },
        )
        self.assertEqual(attested.status_code, HTTPStatus.CREATED, attested.json())
        state = attested.json()
        resolved = self._correction_command(
            case,
            {
                "client_request_id": str(uuid4()),
                "command_type": "resolve",
                "expected_case_version": state["case_version"],
                "expected_case_hash": state["case_hash"],
                "attempt": attempt_id,
                "resolution_mode": "replacement_acknowledged",
                "confirmed": True,
            },
        )
        self.assertEqual(resolved.status_code, HTTPStatus.CREATED, resolved.json())
        self.assertEqual(resolved.json()["case_status"], "resolved")
        self.assertEqual(resolved.json()["resolution_mode"], "replacement_acknowledged")
        self.assertEqual(
            CorrespondencePaperReconciliationAttestation.objects.count(), 1
        )

    def test_terminal_replacement_failure_allows_one_new_immutable_same_source_attempt(
        self,
    ):
        self.assertEqual(self._dispatch("ack").event_type, "acknowledged")
        current = self._amend_and_project()
        case = CorrespondenceCorrectionCase.objects.get()
        first_start = self._correction_command(
            case,
            self._start_replacement_payload(case, current),
        )
        self.assertEqual(
            first_start.status_code, HTTPStatus.CREATED, first_start.json()
        )
        started = first_start.json()
        duplicate_payload = self._start_replacement_payload(case, current)
        duplicate_payload.update(
            {
                "expected_case_version": started["case_version"],
                "expected_case_hash": started["case_hash"],
            }
        )
        duplicate = self._correction_command(case, duplicate_payload)
        self.assertEqual(duplicate.status_code, HTTPStatus.CONFLICT, duplicate.json())
        self.assertEqual(
            duplicate.json()["error"],
            "correspondence_replacement_already_started",
        )
        sent_state = self._finalize_and_send_replacement(case, started)
        replacement = CorrespondenceDelivery.objects.get(
            correction_case_reference=case.external_id
        )
        attested = self._correction_command(
            case,
            {
                "client_request_id": str(uuid4()),
                "command_type": "attest_paper",
                "expected_case_version": sent_state["case_version"],
                "expected_case_hash": sent_state["case_hash"],
                "attempt": sent_state["replacement"]["attempt"]["id"],
                "controlled_copy_artifact": sent_state["replacement"]["artifact"],
                "controlled_copy_artifact_hash": sent_state["replacement"][
                    "artifact_hash"
                ],
                "attestation_type": "corrected_copy_filed_prior_copy_reconciled",
                "confirmed": True,
            },
        )
        self.assertEqual(attested.status_code, HTTPStatus.CREATED, attested.json())
        first_delivery_attempt = replacement.attempts.get(attempt_number=1)
        with patch(
            "care.emr.tasks.correspondence_delivery.synthetic_delivery_mode",
            return_value="fail_terminal",
        ):
            dispatch_correspondence_delivery_attempt(
                str(first_delivery_attempt.external_id)
            )
        refresh_correspondence_replacement_delivery(str(replacement.external_id))
        continuity = self._get()
        self.assertEqual(continuity.status_code, HTTPStatus.OK, continuity.json())
        case_state = continuity.json()["correction_case"]
        self.assertEqual(case_state["replacement"]["status"], "failed")
        self.assertEqual(
            case_state["replacement"]["delivery_state"],
            "failed_terminal",
        )
        self.assertTrue(continuity.json()["action_policy"]["can_start_replacement"])
        restart_payload = self._start_replacement_payload(case, current)
        restart_payload.update(
            {
                "expected_case_version": case_state["resource_version"],
                "expected_case_hash": case_state["case_hash"],
            }
        )
        restarted = self._correction_command(case, restart_payload)
        self.assertEqual(restarted.status_code, HTTPStatus.CREATED, restarted.json())
        attempts = list(
            CorrespondenceReplacementAttempt.objects.order_by("attempt_number")
        )
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[1].supersedes_attempt_id, attempts[0].id)
        self.assertEqual(
            attempts[1].source_submission_id,
            attempts[0].source_submission_id,
        )
        self.assertEqual(CorrespondenceDelivery.objects.count(), 2)
        self.assertEqual(self.delivery.attempts.count(), 1)
        case.refresh_from_db()
        self.assertTrue(correction_case_integrity_valid(case))
        original_source_hash = attempts[0].source_snapshot_hash
        CorrespondenceReplacementAttempt._base_manager.filter(pk=attempts[0].pk).update(  # noqa: SLF001
            source_snapshot_hash="0" * 64
        )
        self.assertFalse(correction_case_integrity_valid(case))
        CorrespondenceReplacementAttempt._base_manager.filter(pk=attempts[0].pk).update(  # noqa: SLF001
            source_snapshot_hash=original_source_hash
        )
        self.assertTrue(correction_case_integrity_valid(case))
        paper_attestation = CorrespondencePaperReconciliationAttestation.objects.get()
        original_attestation_hash = paper_attestation.attestation_hash
        CorrespondencePaperReconciliationAttestation._base_manager.filter(  # noqa: SLF001
            pk=paper_attestation.pk
        ).update(attestation_hash="0" * 64)
        self.assertFalse(correction_case_integrity_valid(case))
        CorrespondencePaperReconciliationAttestation._base_manager.filter(  # noqa: SLF001
            pk=paper_attestation.pk
        ).update(attestation_hash=original_attestation_hash)
        self.assertTrue(correction_case_integrity_valid(case))
        replacement.events.filter(event_type="failed_terminal").update(
            event_hash="0" * 64
        )
        self.assertFalse(correction_case_integrity_valid(case))

    def test_crashed_worker_is_fenced_and_reclaimed_projector_materializes_once(self):
        self.assertEqual(self._dispatch("ack").event_type, "acknowledged")
        amended = self._amend_source()
        self.assertEqual(amended.status_code, HTTPStatus.CREATED, amended.json())
        current = FormSubmission.objects.get(
            external_id=amended.json()["form_submission"]["id"]
        )
        self._artifact(current)
        outbox = CorrespondenceCorrectionOutbox.objects.get()
        outbox_id, worker_a = _claim_correction_outbox(str(outbox.external_id))
        CorrespondenceCorrectionOutbox.objects.filter(pk=outbox_id).update(
            lease_expires_at=timezone.now()
        )
        reclaimed_id, worker_b = _claim_correction_outbox(str(outbox.external_id))

        self.assertEqual(reclaimed_id, outbox_id)
        self.assertFalse(
            materialize_claimed_correction_outbox(
                outbox_id=outbox_id,
                claim_token=worker_a,
            )
        )
        self.assertFalse(CorrespondenceCorrectionCase.objects.exists())
        self.assertTrue(
            materialize_claimed_correction_outbox(
                outbox_id=outbox_id,
                claim_token=worker_b,
            )
        )
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, "completed")
        self.assertEqual(outbox.claim_token, worker_b)
        self.assertEqual(CorrespondenceCorrectionCase.objects.count(), 1)
        self.assertEqual(CorrespondenceCorrectionEvent.objects.count(), 1)

    def test_definitely_not_delivered_branch_requires_regeneration_without_case(self):
        latest = self._dispatch("fail_terminal")
        self.assertEqual(latest.event_type, "failed_terminal")
        self.assertEqual(latest.certainty, "not_delivered")
        self._amend_and_project()

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.OK, response.json())
        self.assertEqual(response.json()["required_action"], "regenerate_unsent")
        self.assertIsNone(response.json()["correction_case"])
        self.assertFalse(CorrespondenceCorrectionCase.objects.exists())

    def test_unknown_delivery_opens_case_and_requires_resolution_before_replacement(
        self,
    ):
        latest = self._dispatch("outcome_unknown")
        self.assertEqual(latest.event_type, "outcome_unknown")
        self._amend_and_project()

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.OK, response.json())
        self.assertEqual(
            response.json()["required_action"],
            "resolve_delivery_outcome",
        )
        self.assertIsNotNone(response.json()["correction_case"])
        self.assertFalse(response.json()["action_policy"]["can_start_replacement"])

    def test_completed_projection_without_required_ack_case_is_repaired(self):
        self.assertEqual(self._dispatch("ack").event_type, "acknowledged")
        amended = self._amend_source()
        self.assertEqual(amended.status_code, HTTPStatus.CREATED, amended.json())
        current = FormSubmission.objects.get(
            external_id=amended.json()["form_submission"]["id"]
        )
        self._artifact(current)
        outbox = CorrespondenceCorrectionOutbox.objects.get()
        outbox_id, token = _claim_correction_outbox(str(outbox.external_id))
        CorrespondenceCorrectionOutbox.objects.filter(
            pk=outbox_id,
            claim_token=token,
        ).update(
            status="completed",
            completed_at=timezone.now(),
            safe_code="synthetic_missing_case",
        )

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertFalse(CorrespondenceCorrectionCase.objects.exists())
        refresh_correspondence_correction_delivery(str(self.delivery.external_id))
        repaired = self._get()
        self.assertEqual(repaired.status_code, HTTPStatus.OK, repaired.json())
        self.assertEqual(CorrespondenceCorrectionCase.objects.count(), 1)

    def test_archived_final_artifact_disables_live_send_retry_but_keeps_history(self):
        ReportUpload._base_manager.filter(pk=self.letter_artifact.pk).update(  # noqa: SLF001
            is_archived=True
        )

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.OK, response.json())
        self.assertEqual(response.json()["source_state"], "current")
        self.assertTrue(response.json()["action_policy"]["can_read_historical"])
        self.assertFalse(response.json()["action_policy"]["can_send_original"])
        self.assertFalse(response.json()["action_policy"]["can_retry_original"])

    def test_tampered_case_event_fails_closed_without_hiding_frozen_history(self):
        self.assertEqual(self._dispatch("ack").event_type, "acknowledged")
        self._amend_and_project()
        event = CorrespondenceCorrectionEvent.objects.get()
        CorrespondenceCorrectionEvent._base_manager.filter(pk=event.pk).update(  # noqa: SLF001
            event_hash="0" * 64
        )

        response = self._get()

        self.assertEqual(response.status_code, HTTPStatus.OK, response.json())
        self.assertEqual(response.json()["source_state"], "integrity_failed")
        self.assertIsNotNone(response.json()["historical_chain"])
        self.assertIsNone(response.json()["correction_case"])

    def test_periodic_repair_converges_existing_case_after_dropped_broker_enqueue(self):
        self.assertEqual(
            self._dispatch("outcome_unknown").event_type, "outcome_unknown"
        )
        self._amend_and_project()
        attempt = self.delivery.attempts.get(attempt_number=1)
        append_delivery_event(
            delivery=self.delivery,
            attempt=attempt,
            event_type="acknowledged",
            certainty="acknowledged",
            safe_code="late_acknowledgement",
            provider_ack_reference="SYNTHETIC-LATE-ACK",
            provider_ack_at=timezone.now(),
        )

        pending = self._get()
        self.assertEqual(pending.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        with patch(
            "care.emr.tasks.correspondence_correction."
            "refresh_correspondence_correction_delivery.delay"
        ) as queued:
            scan_correspondence_correction_delivery_cases()
        queued.assert_called_with(str(self.delivery.external_id))

        refresh_correspondence_correction_delivery(str(self.delivery.external_id))
        repaired = self._get()
        self.assertEqual(repaired.status_code, HTTPStatus.OK, repaired.json())
        self.assertEqual(repaired.json()["required_action"], "send_correction")
        self.assertEqual(
            repaired.json()["correction_case"]["notification_status"],
            "required",
        )

    def test_periodic_repair_creates_missing_case_after_late_ack_without_resend(self):
        failed = self._dispatch("fail_once")
        self.assertEqual(failed.event_type, "failed_retryable")
        retry = self.client.post(
            reverse(
                "correspondence-delivery-idempotent-retry",
                kwargs={"external_id": self.delivery.external_id},
            ),
            {
                "client_request_id": str(uuid4()),
                "expected_event_sequence": failed.sequence,
                "expected_event_hash": failed.event_hash,
                "confirmed": True,
            },
            format="json",
        )
        self.assertEqual(retry.status_code, HTTPStatus.CREATED, retry.json())
        retry_attempt = self.delivery.attempts.get(attempt_number=2)
        append_delivery_event(
            delivery=self.delivery,
            attempt=retry_attempt,
            event_type="dispatching",
            certainty="attempting",
            safe_code="synthetic_dispatching",
        )
        amended = self._amend_source()
        self.assertEqual(amended.status_code, HTTPStatus.CREATED, amended.json())
        current = FormSubmission.objects.get(
            external_id=amended.json()["form_submission"]["id"]
        )
        self._artifact(current)
        outbox = CorrespondenceCorrectionOutbox.objects.get()
        outbox_id, token = _claim_correction_outbox(str(outbox.external_id))
        CorrespondenceCorrectionOutbox.objects.filter(
            pk=outbox_id,
            claim_token=token,
        ).update(
            status="completed",
            completed_at=timezone.now(),
            safe_code="synthetic_missing_case",
        )
        self.assertFalse(CorrespondenceCorrectionCase.objects.exists())
        append_delivery_event(
            delivery=self.delivery,
            attempt=retry_attempt,
            event_type="acknowledged",
            certainty="acknowledged",
            safe_code="late_acknowledgement",
            provider_ack_reference="SYNTHETIC-LATE-AFTER-FAIL",
            provider_ack_at=timezone.now(),
        )

        pending = self._get()
        self.assertEqual(pending.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        with patch(
            "care.emr.tasks.correspondence_correction."
            "refresh_correspondence_correction_delivery.delay"
        ) as queued:
            scan_correspondence_correction_delivery_cases()
        queued.assert_called_with(str(self.delivery.external_id))
        with patch(
            "care.emr.correspondence.delivery_adapters."
            "SyntheticCorrespondenceDeliveryAdapter.deliver"
        ) as provider_deliver:
            refresh_correspondence_correction_delivery(str(self.delivery.external_id))
        provider_deliver.assert_not_called()
        repaired = self._get()

        self.assertEqual(repaired.status_code, HTTPStatus.OK, repaired.json())
        self.assertEqual(repaired.json()["required_action"], "send_correction")
        self.assertEqual(CorrespondenceCorrectionCase.objects.count(), 1)

    def test_hash_valid_stale_notification_is_503_then_converges(self):
        self.assertEqual(
            self._dispatch("outcome_unknown").event_type, "outcome_unknown"
        )
        self._amend_and_project()
        case = CorrespondenceCorrectionCase.objects.get()
        event = CorrespondenceCorrectionEvent.objects.get(case=case)
        case.notification_status = "not_required"
        case.case_hash = correspondence_correction_case_hash(case)
        CorrespondenceCorrectionCase.objects.filter(pk=case.pk).update(
            notification_status=case.notification_status,
            case_hash=case.case_hash,
        )
        event.resulting_case_hash = case.case_hash
        event.event_hash = correspondence_correction_event_hash(event)
        CorrespondenceCorrectionEvent._base_manager.filter(pk=event.pk).update(  # noqa: SLF001
            resulting_case_hash=event.resulting_case_hash,
            event_hash=event.event_hash,
        )

        pending = self._get()
        self.assertEqual(pending.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        with patch(
            "care.emr.tasks.correspondence_correction."
            "refresh_correspondence_correction_delivery.delay"
        ) as queued:
            scan_correspondence_correction_delivery_cases()
        queued.assert_called_with(str(self.delivery.external_id))

        refresh_correspondence_correction_delivery(str(self.delivery.external_id))
        repaired = self._get()
        self.assertEqual(repaired.status_code, HTTPStatus.OK, repaired.json())
        self.assertEqual(
            repaired.json()["correction_case"]["notification_status"],
            "unknown",
        )
