import copy
import io
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from threading import Barrier
from threading import Event as ThreadEvent
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from care.emr.correspondence.delivery import (
    CorrespondenceDispatchNotCurrentError,
)
from care.emr.correspondence.delivery_adapters import (
    CorrespondenceDeliveryAdapterUnavailableError,
    SyntheticCorrespondenceDeliveryAdapter,
)
from care.emr.models.correspondence_correction import (
    CorrespondenceCorrectionOutbox,
    CorrespondenceSourceCorrection,
)
from care.emr.models.correspondence_delivery import (
    CorrespondenceDelivery,
    CorrespondenceDeliveryAttempt,
    CorrespondenceDeliveryEvent,
    CorrespondenceSyntheticProviderInvocation,
    CorrespondenceSyntheticProviderReceipt,
)
from care.emr.models.correspondence_letter import CorrespondenceLetterRevision
from care.emr.models.correspondence_review import CorrespondenceReview
from care.emr.models.report.report_upload import ReportUpload
from care.emr.resources.correspondence import canonical_sha256
from care.emr.resources.correspondence_delivery import (
    correspondence_synthetic_provider_receipt_hash,
    correspondence_synthetic_provider_request_hash,
)
from care.emr.signals.patient.facility_name_identifier import (
    FacilityPatientNameIdentifierConfig,
)
from care.emr.signals.patient.name_identifier import NameIdentifierConfig
from care.emr.signals.patient.phone_number_identifier import (
    PhoneNumberIdentifierConfig,
)
from care.emr.tasks.correspondence_delivery import (
    _claim_pending_attempt,
    _dispatch_claimed_attempt,
    _prepare_claimed_attempt,
    dispatch_correspondence_delivery_attempt,
    reconcile_correspondence_delivery_attempt,
    scan_correspondence_delivery_outbox,
)
from care.emr.tests.test_correspondence_review import CorrespondenceReviewTestMixin
from care.utils.tests.base import CareAPITestBase

SYNTHETIC_PDF = b"%PDF-1.7\nsynthetic-delivery-artifact"


def synthetic_artifact_response(*args, **kwargs):
    del args, kwargs
    return {
        "ContentLength": len(SYNTHETIC_PDF),
        "ContentType": "application/pdf",
        "Body": io.BytesIO(SYNTHETIC_PDF),
    }


class TestCorrespondenceDeliveryAPI(
    CorrespondenceReviewTestMixin,
    CareAPITestBase,
):
    def _recipient(self, **overrides):
        values = {
            "source_type": "synthetic_test_fixture",
            "channel_identifier": "synthetic:no-network",
            "source_provenance": {
                "governance": "synthetic-test-only",
                "evidence_reference": "SYNTHETIC-DELIVERY-1",
                "delivery_test_mode": "ack",
            },
        }
        values.update(overrides)
        return super()._recipient(**values)

    def setUp(self):
        super().setUp()
        self.build_review_context()
        response = self._bind()
        if response.status_code != HTTPStatus.CREATED:
            raise AssertionError(response.json())
        self.review = CorrespondenceReview.objects.get()
        self.put_patcher = patch.object(
            ReportUpload.files_manager, "put_object", return_value={}
        )
        self.render_patcher = patch(
            "care_suriname.api.viewsets.correspondence_letter.render_correspondence_letter_pdf",
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
        self.put_patcher.start()
        self.render_patcher.start()
        self.get_patcher.start()
        self.enqueue = self.enqueue_patcher.start()
        self.addCleanup(self.put_patcher.stop)
        self.addCleanup(self.render_patcher.stop)
        self.addCleanup(self.get_patcher.stop)
        self.addCleanup(self.enqueue_patcher.stop)
        self.revision = self._finalized_revision()
        self.artifact = ReportUpload.objects.get(correspondence_revision=self.revision)
        self.send_url = reverse("correspondence-delivery-idempotent-send")

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
                "body": "Dear colleague,\n\nSynthetic clinical correspondence.",
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

    def _send_payload(self, **overrides):
        payload = {
            "client_request_id": str(uuid4()),
            "correspondence_revision": str(self.revision.external_id),
            "resource_version": self.revision.resource_version,
            "revision_hash": self.revision.revision_hash,
            "artifact": str(self.artifact.external_id),
            "artifact_sha256": self.artifact.artifact_sha256,
            **self._context(),
            "recipient": str(self.review.recipient.external_id),
            "recipient_version": self.review.recipient_version,
            "recipient_hash": self.review.recipient_hash,
            "confirmed": True,
        }
        payload.update(overrides)
        return payload

    def _send(self, payload=None):
        return self.client.post(
            self.send_url,
            payload or self._send_payload(),
            format="json",
        )

    def _dispatch(self, delivery):
        attempt = delivery.attempts.get(attempt_number=1)
        dispatch_correspondence_delivery_attempt(str(attempt.external_id))
        return CorrespondenceDeliveryEvent.objects.filter(delivery=delivery).latest(
            "sequence"
        )

    def test_explicit_confirmation_is_required_before_any_ledger_write(self):
        payload = self._send_payload()
        payload.pop("confirmed")
        response = self._send(payload)

        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertFalse(CorrespondenceDelivery.objects.exists())
        self.assertFalse(CorrespondenceDeliveryAttempt.objects.exists())

    def test_empty_exact_list_accepts_bounded_pagination_contract(self):
        response = self.client.get(
            reverse("correspondence-delivery-list"),
            {
                "patient": str(self.patient.external_id),
                "encounter": str(self.encounter.external_id),
                "correspondence_revision": str(self.revision.external_id),
                "limit": 1,
                "offset": 0,
            },
        )

        self.assertEqual(response.status_code, HTTPStatus.OK, response.json())
        self.assertEqual(response.json(), {"count": 0, "results": []})

        invalid = self.client.get(
            reverse("correspondence-delivery-list"),
            {
                "patient": str(self.patient.external_id),
                "encounter": str(self.encounter.external_id),
                "correspondence_revision": str(self.revision.external_id),
                "limit": 201,
                "offset": 0,
            },
        )
        self.assertEqual(invalid.status_code, HTTPStatus.BAD_REQUEST)

    def test_send_is_durable_strictly_idempotent_and_acknowledged_by_no_network_adapter(
        self,
    ):
        payload = self._send_payload()
        created = self._send(payload)

        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        self.assertEqual(created.json()["delivery"]["state"], "dispatch_pending")
        delivery = CorrespondenceDelivery.objects.get()
        provider_key = delivery.provider_idempotency_key
        with patch("socket.create_connection") as network:
            latest = self._dispatch(delivery)
        self.assertFalse(network.called)
        self.assertEqual(latest.event_type, "acknowledged")
        self.assertEqual(latest.certainty, "acknowledged")
        self.assertEqual(delivery.provider_idempotency_key, provider_key)

        replay = self._send(copy.deepcopy(payload))
        self.assertEqual(replay.status_code, HTTPStatus.OK, replay.json())
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(CorrespondenceDelivery.objects.count(), 1)
        self.assertEqual(CorrespondenceDeliveryAttempt.objects.count(), 1)

    def test_same_key_with_changed_payload_conflicts_without_new_side_effect(self):
        payload = self._send_payload()
        created = self._send(payload)
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        payload["recipient_hash"] = "0" * 64

        conflict = self._send(payload)

        self.assertEqual(conflict.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(CorrespondenceDelivery.objects.count(), 1)
        self.assertEqual(CorrespondenceDeliveryAttempt.objects.count(), 1)

    def test_retry_requires_exact_retryable_terminal_event_and_is_idempotent(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        with patch(
            "care.emr.tasks.correspondence_delivery.synthetic_delivery_mode",
            return_value="fail_once",
        ):
            dispatch_correspondence_delivery_attempt(str(attempt.external_id))
        failed = delivery.events.latest("sequence")
        self.assertEqual(failed.event_type, "failed_retryable")

        retry_url = reverse(
            "correspondence-delivery-idempotent-retry",
            kwargs={"external_id": delivery.external_id},
        )
        retry_payload = {
            "client_request_id": str(uuid4()),
            "expected_event_sequence": failed.sequence,
            "expected_event_hash": failed.event_hash,
            "confirmed": True,
        }
        stale_payload = {**retry_payload, "client_request_id": str(uuid4())}
        stale_payload["expected_event_hash"] = "0" * 64
        stale = self.client.post(retry_url, stale_payload, format="json")
        self.assertEqual(stale.status_code, HTTPStatus.CONFLICT)

        retry = self.client.post(retry_url, retry_payload, format="json")
        self.assertEqual(retry.status_code, HTTPStatus.CREATED, retry.json())
        second = delivery.attempts.get(attempt_number=2)
        with patch(
            "care.emr.tasks.correspondence_delivery.synthetic_delivery_mode",
            return_value="fail_once",
        ):
            dispatch_correspondence_delivery_attempt(str(second.external_id))
        self.assertEqual(delivery.events.latest("sequence").event_type, "acknowledged")

        replay = self.client.post(retry_url, retry_payload, format="json")
        self.assertEqual(replay.status_code, HTTPStatus.OK, replay.json())
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(delivery.attempts.count(), 2)

    def test_outcome_unknown_blocks_blind_retry(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        with patch(
            "care.emr.tasks.correspondence_delivery.synthetic_delivery_mode",
            return_value="outcome_unknown",
        ):
            dispatch_correspondence_delivery_attempt(str(attempt.external_id))
        unknown = delivery.events.latest("sequence")

        response = self.client.post(
            reverse(
                "correspondence-delivery-idempotent-retry",
                kwargs={"external_id": delivery.external_id},
            ),
            {
                "client_request_id": str(uuid4()),
                "expected_event_sequence": unknown.sequence,
                "expected_event_hash": unknown.event_hash,
                "confirmed": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(delivery.attempts.count(), 1)
        with patch(
            "care.emr.correspondence.delivery_adapters."
            "SyntheticCorrespondenceDeliveryAdapter.lookup",
            side_effect=RuntimeError("synthetic lookup failure"),
        ):
            for _ in range(7):
                reconcile_correspondence_delivery_attempt(
                    str(attempt.external_id),
                    automatic=True,
                )
        self.assertEqual(
            delivery.events.filter(safe_code="reconcile_lookup_started").count(),
            5,
        )

    def test_outcome_unknown_can_resolve_by_late_receipt_lookup_without_resend(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        with patch(
            "care.emr.correspondence.delivery_adapters."
            "SyntheticCorrespondenceDeliveryAdapter.deliver",
            side_effect=RuntimeError("synthetic provider ambiguity"),
        ):
            dispatch_correspondence_delivery_attempt(str(attempt.external_id))
        self.assertEqual(
            delivery.events.latest("sequence").event_type,
            "outcome_unknown",
        )
        ack_reference = f"synthetic:{delivery.provider_idempotency_key[:32]}"
        receipt = CorrespondenceSyntheticProviderReceipt(
            provider_idempotency_key=delivery.provider_idempotency_key,
            attempt_number=attempt.attempt_number,
            request_hash=correspondence_synthetic_provider_request_hash(
                provider_idempotency_key=delivery.provider_idempotency_key,
                delivery_hash=delivery.delivery_hash,
                artifact_sha256=delivery.artifact_sha256,
            ),
            outcome_state="acknowledged",
            safe_code="synthetic_late_ack",
            provider_ack_reference=ack_reference,
            provider_ack_hash=canonical_sha256(
                {
                    "contract": "correspondence-delivery-ack-reference-v1",
                    "reference": ack_reference,
                }
            ),
            recorded_at=timezone.now(),
            receipt_hash="",
        )
        receipt.receipt_hash = correspondence_synthetic_provider_receipt_hash(receipt)
        receipt.save(force_insert=True)

        with patch(
            "care.emr.correspondence.delivery_adapters."
            "SyntheticCorrespondenceDeliveryAdapter.deliver"
        ) as deliver:
            reconcile_correspondence_delivery_attempt(str(attempt.external_id))

        self.assertFalse(deliver.called)
        self.assertEqual(delivery.events.latest("sequence").event_type, "acknowledged")
        with patch(
            "care.emr.tasks.correspondence_delivery."
            "reconcile_correspondence_delivery_attempt.delay"
        ) as queued:
            scan_correspondence_delivery_outbox()
        self.assertFalse(queued.called)

    def test_crash_before_provider_receipt_is_definitely_not_delivered(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        self.assertEqual(_claim_pending_attempt(str(attempt.external_id)), attempt.id)

        with patch(
            "care.emr.correspondence.delivery_adapters."
            "SyntheticCorrespondenceDeliveryAdapter.deliver"
        ) as deliver:
            reconcile_correspondence_delivery_attempt(str(attempt.external_id))

        self.assertFalse(deliver.called)
        latest = delivery.events.latest("sequence")
        self.assertEqual(latest.event_type, "failed_retryable")
        self.assertEqual(latest.certainty, "not_delivered")

    def test_crash_after_provider_receipt_recovers_ack_without_resending(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        self.assertEqual(_claim_pending_attempt(str(attempt.external_id)), attempt.id)
        prepared = _prepare_claimed_attempt(attempt.id)
        prepared["adapter"].deliver(
            artifact_bytes=prepared["artifact_bytes"],
            provider_idempotency_key=prepared["provider_idempotency_key"],
            attempt_number=prepared["attempt_number"],
            test_mode=prepared["test_mode"],
            delivery_hash=prepared["delivery_hash"],
            artifact_sha256=prepared["artifact_sha256"],
        )

        with patch(
            "care.emr.correspondence.delivery_adapters."
            "SyntheticCorrespondenceDeliveryAdapter.deliver"
        ) as deliver:
            reconcile_correspondence_delivery_attempt(str(attempt.external_id))

        self.assertFalse(deliver.called)
        self.assertEqual(delivery.events.latest("sequence").event_type, "acknowledged")

    def test_worker_rechecks_authorization_after_claim_before_provider(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        self.assertEqual(_claim_pending_attempt(str(attempt.external_id)), attempt.id)

        with (
            patch(
                "care.emr.tasks.correspondence_delivery.write_report_authorizer",
                side_effect=PermissionDenied("revoked"),
            ),
            patch(
                "care.emr.correspondence.delivery_adapters."
                "SyntheticCorrespondenceDeliveryAdapter.deliver"
            ) as deliver,
        ):
            _dispatch_claimed_attempt(attempt.id)

        self.assertFalse(deliver.called)
        self.assertEqual(
            delivery.events.latest("sequence").event_type,
            "failed_terminal",
        )
        self.assertEqual(
            delivery.events.latest("sequence").safe_code,
            "authorization_revoked",
        )
        self.assertEqual(
            delivery.events.latest("sequence").certainty,
            "not_delivered",
        )

    def test_claim_records_distinct_adapter_unavailable_terminal_reason(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)

        with patch(
            "care.emr.tasks.correspondence_delivery."
            "get_correspondence_delivery_adapter",
            side_effect=CorrespondenceDeliveryAdapterUnavailableError,
        ):
            claimed = _claim_pending_attempt(str(attempt.external_id))

        self.assertIsNone(claimed)
        latest = delivery.events.latest("sequence")
        self.assertEqual(latest.event_type, "failed_terminal")
        self.assertEqual(latest.safe_code, "adapter_unavailable")
        self.assertEqual(latest.certainty, "not_delivered")

    def test_claim_records_distinct_authorization_revoked_terminal_reason(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)

        with patch(
            "care.emr.tasks.correspondence_delivery._assert_dispatch_authorized",
            side_effect=PermissionDenied("revoked"),
        ):
            claimed = _claim_pending_attempt(str(attempt.external_id))

        self.assertIsNone(claimed)
        latest = delivery.events.latest("sequence")
        self.assertEqual(latest.event_type, "failed_terminal")
        self.assertEqual(latest.safe_code, "authorization_revoked")
        self.assertEqual(latest.certainty, "not_delivered")

    def test_claim_records_distinct_source_not_current_terminal_reason(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)

        with patch(
            "care.emr.tasks.correspondence_delivery."
            "lock_and_assert_correspondence_dispatch_current",
            side_effect=CorrespondenceDispatchNotCurrentError,
        ):
            claimed = _claim_pending_attempt(str(attempt.external_id))

        self.assertIsNone(claimed)
        latest = delivery.events.latest("sequence")
        self.assertEqual(latest.event_type, "failed_terminal")
        self.assertEqual(latest.safe_code, "source_not_current")
        self.assertEqual(latest.certainty, "not_delivered")

    def test_preflight_records_distinct_source_not_current_terminal_reason(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        self.assertEqual(_claim_pending_attempt(str(attempt.external_id)), attempt.id)

        with (
            patch(
                "care.emr.tasks.correspondence_delivery."
                "lock_and_assert_correspondence_dispatch_current",
                side_effect=CorrespondenceDispatchNotCurrentError,
            ),
            patch(
                "care.emr.correspondence.delivery_adapters."
                "SyntheticCorrespondenceDeliveryAdapter.deliver"
            ) as deliver,
        ):
            _dispatch_claimed_attempt(attempt.id)

        self.assertFalse(deliver.called)
        latest = delivery.events.latest("sequence")
        self.assertEqual(latest.event_type, "failed_terminal")
        self.assertEqual(latest.safe_code, "source_not_current")
        self.assertEqual(latest.certainty, "not_delivered")

    def test_preflight_records_distinct_adapter_unavailable_terminal_reason(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        self.assertEqual(_claim_pending_attempt(str(attempt.external_id)), attempt.id)

        with (
            patch(
                "care.emr.tasks.correspondence_delivery."
                "get_correspondence_delivery_adapter",
                side_effect=CorrespondenceDeliveryAdapterUnavailableError,
            ),
            patch(
                "care.emr.correspondence.delivery_adapters."
                "SyntheticCorrespondenceDeliveryAdapter.deliver"
            ) as deliver,
        ):
            _dispatch_claimed_attempt(attempt.id)

        self.assertFalse(deliver.called)
        latest = delivery.events.latest("sequence")
        self.assertEqual(latest.event_type, "failed_terminal")
        self.assertEqual(latest.safe_code, "adapter_unavailable")
        self.assertEqual(latest.certainty, "not_delivered")

    def test_attempt_tamper_before_claim_blocks_provider_and_ledger_append(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        CorrespondenceDeliveryAttempt._base_manager.filter(pk=attempt.pk).update(  # noqa: SLF001
            payload_hash="0" * 64
        )

        with patch(
            "care.emr.correspondence.delivery_adapters."
            "SyntheticCorrespondenceDeliveryAdapter.deliver"
        ) as deliver:
            dispatch_correspondence_delivery_attempt(str(attempt.external_id))

        self.assertFalse(deliver.called)
        self.assertEqual(delivery.events.count(), 1)
        self.assertEqual(
            delivery.events.latest("sequence").event_type,
            "dispatch_pending",
        )

    def test_attempt_tamper_after_claim_blocks_provider(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        self.assertEqual(_claim_pending_attempt(str(attempt.external_id)), attempt.id)
        CorrespondenceDeliveryAttempt._base_manager.filter(pk=attempt.pk).update(  # noqa: SLF001
            client_request_id=uuid4()
        )

        with patch(
            "care.emr.correspondence.delivery_adapters."
            "SyntheticCorrespondenceDeliveryAdapter.deliver"
        ) as deliver:
            _dispatch_claimed_attempt(attempt.id)

        self.assertFalse(deliver.called)
        self.assertEqual(delivery.events.latest("sequence").event_type, "dispatching")

    def test_transaction_rolls_back_if_ledger_creation_fails(self):
        with patch(
            "care_suriname.api.viewsets.correspondence_delivery."
            "CorrespondenceDeliveryViewSet._create_attempt",
            side_effect=RuntimeError("synthetic failure"),
        ):
            response = self._send()

        self.assertEqual(response.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertFalse(CorrespondenceDelivery.objects.exists())
        self.assertFalse(CorrespondenceDeliveryEvent.objects.exists())

    def test_write_authorization_failure_creates_no_delivery(self):
        with patch(
            "care_suriname.api.viewsets.correspondence_delivery.write_report_authorizer",
            side_effect=PermissionDenied("denied"),
        ):
            response = self._send()

        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)
        self.assertFalse(CorrespondenceDelivery.objects.exists())

    def test_worker_error_log_contains_only_safe_ids_and_error_class(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        with (
            patch(
                "care.emr.correspondence.delivery_adapters."
                "SyntheticCorrespondenceDeliveryAdapter.deliver",
                side_effect=RuntimeError("secret patient payload"),
            ),
            patch("care.emr.tasks.correspondence_delivery.logger.warning") as warning,
        ):
            dispatch_correspondence_delivery_attempt(str(attempt.external_id))

        logged = " ".join(str(value) for value in warning.call_args.args)
        self.assertIn(str(delivery.external_id), logged)
        self.assertIn("RuntimeError", logged)
        self.assertNotIn(self.patient.name, logged)
        self.assertNotIn("secret patient payload", logged)
        self.assertEqual(
            delivery.events.latest("sequence").event_type, "outcome_unknown"
        )

    def test_historical_retrieve_checks_frozen_ledger_not_live_currentness(self):
        created = self._send()
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        self._dispatch(delivery)
        self.patient.name = "Changed after delivery"
        self.patient.save(update_fields=["name"])

        response = self.client.get(
            reverse(
                "correspondence-delivery-detail",
                kwargs={"external_id": delivery.external_id},
            )
        )

        self.assertEqual(response.status_code, HTTPStatus.OK, response.json())
        self.assertEqual(response.json()["state"], "acknowledged")


class TestCorrespondenceDeliveryConcurrency(
    CorrespondenceReviewTestMixin,
    TransactionTestCase,
):
    fake = CareAPITestBase.fake
    reset_sequences = True

    def _recipient(self, **overrides):
        values = {
            "source_type": "synthetic_test_fixture",
            "channel_identifier": "synthetic:no-network",
            "source_provenance": {
                "governance": "synthetic-test-only",
                "evidence_reference": "SYNTHETIC-CONCURRENCY-1",
                "delivery_test_mode": "ack",
            },
        }
        values.update(overrides)
        return super()._recipient(**values)

    def setUp(self):
        cache.clear()
        FacilityPatientNameIdentifierConfig.CACHED_CONFIG.clear()
        NameIdentifierConfig.CACHED_CONFIG.clear()
        PhoneNumberIdentifierConfig.CACHED_CONFIG.clear()
        self.client = APIClient()
        self.build_review_context()
        bound = self._bind()
        if bound.status_code != HTTPStatus.CREATED:
            raise AssertionError(bound.json())
        self.review = CorrespondenceReview.objects.get()
        patches = [
            patch.object(ReportUpload.files_manager, "put_object", return_value={}),
            patch(
                "care_suriname.api.viewsets.correspondence_letter."
                "render_correspondence_letter_pdf",
                return_value=SYNTHETIC_PDF,
            ),
            patch.object(
                ReportUpload.files_manager,
                "get_object",
                side_effect=synthetic_artifact_response,
            ),
            patch(
                "care_suriname.api.viewsets.correspondence_delivery."
                "dispatch_correspondence_delivery_attempt.delay"
            ),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.revision = self._finalize_letter()
        self.artifact = ReportUpload.objects.get(correspondence_revision=self.revision)
        self.send_url = reverse("correspondence-delivery-idempotent-send")

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

    def _finalize_letter(self):
        created = self.client.post(
            reverse("correspondence-letter-idempotent-create"),
            {
                "client_request_id": str(uuid4()),
                **self._context(),
                "body": "Synthetic concurrency letter",
            },
            format="json",
        )
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
        return CorrespondenceLetterRevision.objects.get(
            external_id=finalized.json()["correspondence"]["id"]
        )

    def _concurrent_send_payload(self, client_request_id):
        return {
            "client_request_id": client_request_id,
            "correspondence_revision": str(self.revision.external_id),
            "resource_version": self.revision.resource_version,
            "revision_hash": self.revision.revision_hash,
            "artifact": str(self.artifact.external_id),
            "artifact_sha256": self.artifact.artifact_sha256,
            **self._context(),
            "recipient": str(self.review.recipient.external_id),
            "recipient_version": self.review.recipient_version,
            "recipient_hash": self.review.recipient_hash,
            "confirmed": True,
        }

    def _post_concurrently(self, payloads):
        barrier = Barrier(2)

        def post(payload):
            close_old_connections()
            client = APIClient()
            client.force_authenticate(user=self.user)
            barrier.wait()
            response = client.post(
                self.send_url,
                copy.deepcopy(payload),
                format="json",
            )
            close_old_connections()
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(post, payloads))

    def _amend_source(self, *, reason):
        client = APIClient()
        client.force_authenticate(user=self.user)
        return client.post(
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
                "reason": reason,
                "response_dump": {"measurement": 45},
            },
            format="json",
        )

    def test_concurrent_exact_send_creates_one_delivery_attempt_and_event(self):
        payload = self._concurrent_send_payload(str(uuid4()))

        responses = self._post_concurrently([payload, payload])

        self.assertEqual(sorted(responses), [HTTPStatus.OK, HTTPStatus.CREATED])
        self.assertEqual(CorrespondenceDelivery.objects.count(), 1)
        self.assertEqual(CorrespondenceDeliveryAttempt.objects.count(), 1)
        self.assertEqual(CorrespondenceDeliveryEvent.objects.count(), 1)

    def test_concurrent_distinct_send_keys_never_duplicate_the_delivery(self):
        responses = self._post_concurrently(
            [
                self._concurrent_send_payload(str(uuid4())),
                self._concurrent_send_payload(str(uuid4())),
            ]
        )

        self.assertEqual(
            sorted(responses),
            [HTTPStatus.CREATED, HTTPStatus.CONFLICT],
        )
        self.assertEqual(CorrespondenceDelivery.objects.count(), 1)
        self.assertEqual(CorrespondenceDeliveryAttempt.objects.count(), 1)

    def test_slow_provider_and_scanner_never_create_false_retryable_or_duplicate_send(
        self,
    ):
        created = self.client.post(
            self.send_url,
            self._concurrent_send_payload(str(uuid4())),
            format="json",
        )
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        provider_started = ThreadEvent()
        provider_release = ThreadEvent()
        original_record = (
            SyntheticCorrespondenceDeliveryAdapter._record_outcome  # noqa: SLF001 - intentional provider-race test seam
        )

        def slow_record(adapter, **kwargs):
            provider_started.set()
            if not provider_release.wait(timeout=10):
                raise RuntimeError("synthetic provider release timeout")
            return original_record(adapter, **kwargs)

        def run_worker():
            close_old_connections()
            dispatch_correspondence_delivery_attempt(str(attempt.external_id))
            close_old_connections()

        with (
            patch.object(
                SyntheticCorrespondenceDeliveryAdapter,
                "_record_outcome",
                new=slow_record,
            ),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            worker = executor.submit(run_worker)
            self.assertTrue(provider_started.wait(timeout=10))
            reconcile_correspondence_delivery_attempt(str(attempt.external_id))
            self.assertEqual(
                delivery.events.latest("sequence").event_type,
                "outcome_unknown",
            )
            self.assertFalse(
                delivery.events.filter(event_type="failed_retryable").exists()
            )
            provider_release.set()
            worker.result(timeout=10)

        self.assertEqual(delivery.events.latest("sequence").event_type, "acknowledged")
        reconcile_correspondence_delivery_attempt(str(attempt.external_id))
        self.assertEqual(delivery.events.latest("sequence").event_type, "acknowledged")
        self.assertEqual(CorrespondenceSyntheticProviderReceipt.objects.count(), 1)
        self.assertEqual(CorrespondenceDeliveryAttempt.objects.count(), 1)

    def test_scanner_cannot_terminalize_worker_paused_before_deliver_entry(self):
        created = self.client.post(
            self.send_url,
            self._concurrent_send_payload(str(uuid4())),
            format="json",
        )
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        provider_entry = ThreadEvent()
        provider_release = ThreadEvent()
        original_deliver = SyntheticCorrespondenceDeliveryAdapter.deliver

        def paused_before_deliver(adapter, **kwargs):
            provider_entry.set()
            if not provider_release.wait(timeout=10):
                raise RuntimeError("synthetic provider release timeout")
            return original_deliver(adapter, **kwargs)

        def run_worker():
            close_old_connections()
            dispatch_correspondence_delivery_attempt(str(attempt.external_id))
            close_old_connections()

        with (
            patch.object(
                SyntheticCorrespondenceDeliveryAdapter,
                "deliver",
                new=paused_before_deliver,
            ),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            worker = executor.submit(run_worker)
            self.assertTrue(provider_entry.wait(timeout=10))
            self.assertEqual(
                CorrespondenceSyntheticProviderInvocation.objects.filter(
                    provider_idempotency_key=delivery.provider_idempotency_key,
                    attempt_number=attempt.attempt_number,
                ).count(),
                1,
            )
            reconcile_correspondence_delivery_attempt(str(attempt.external_id))
            self.assertEqual(
                delivery.events.latest("sequence").event_type,
                "outcome_unknown",
            )
            self.assertFalse(
                delivery.events.filter(event_type="failed_retryable").exists()
            )
            provider_release.set()
            worker.result(timeout=10)

        self.assertEqual(delivery.events.latest("sequence").event_type, "acknowledged")
        self.assertEqual(CorrespondenceSyntheticProviderReceipt.objects.count(), 1)
        self.assertEqual(CorrespondenceDeliveryAttempt.objects.count(), 1)

    def test_amendment_and_dispatch_serialize_without_deadlock_or_stale_send(self):
        created = self.client.post(
            self.send_url,
            self._concurrent_send_payload(str(uuid4())),
            format="json",
        )
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        barrier = Barrier(2)

        def run_worker():
            close_old_connections()
            barrier.wait()
            dispatch_correspondence_delivery_attempt(str(attempt.external_id))
            close_old_connections()

        def run_amendment():
            close_old_connections()
            client = APIClient()
            client.force_authenticate(user=self.user)
            barrier.wait()
            response = client.post(
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
                    "reason": "Concurrent source correction",
                    "response_dump": {"measurement": 45},
                },
                format="json",
            )
            close_old_connections()
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            worker = executor.submit(run_worker)
            amendment = executor.submit(run_amendment)
            worker.result(timeout=20)
            amendment_status, amendment_body = amendment.result(timeout=20)

        self.assertEqual(
            amendment_status,
            HTTPStatus.CREATED,
            amendment_body,
        )
        self.assertEqual(CorrespondenceSourceCorrection.objects.count(), 1)
        self.assertEqual(CorrespondenceCorrectionOutbox.objects.count(), 1)
        latest = delivery.events.latest("sequence")
        self.assertIn(latest.event_type, {"acknowledged", "failed_terminal"})
        if latest.event_type == "acknowledged":
            self.assertEqual(latest.safe_code, "synthetic_ack")
            self.assertEqual(CorrespondenceSyntheticProviderInvocation.objects.count(), 1)
        else:
            self.assertEqual(latest.safe_code, "source_not_current")
            self.assertEqual(CorrespondenceSyntheticProviderInvocation.objects.count(), 0)

    def test_amendment_and_idempotent_send_have_only_two_safe_serial_outcomes(self):
        barrier = Barrier(2)

        def run_send():
            close_old_connections()
            client = APIClient()
            client.force_authenticate(user=self.user)
            barrier.wait()
            response = client.post(
                self.send_url,
                self._concurrent_send_payload(str(uuid4())),
                format="json",
            )
            close_old_connections()
            return response.status_code, response.json()

        def run_amendment():
            close_old_connections()
            barrier.wait()
            response = self._amend_source(reason="Concurrent with confirmed send")
            close_old_connections()
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            send = executor.submit(run_send)
            amendment = executor.submit(run_amendment)
            send_status, send_body = send.result(timeout=20)
            amendment_status, amendment_body = amendment.result(timeout=20)

        self.assertEqual(amendment_status, HTTPStatus.CREATED, amendment_body)
        self.assertIn(send_status, {HTTPStatus.CREATED, HTTPStatus.CONFLICT}, send_body)
        self.assertEqual(CorrespondenceSourceCorrection.objects.count(), 1)
        self.assertEqual(CorrespondenceCorrectionOutbox.objects.count(), 1)
        if send_status == HTTPStatus.CREATED:
            delivery = CorrespondenceDelivery.objects.get()
            attempt = delivery.attempts.get(attempt_number=1)
            dispatch_correspondence_delivery_attempt(str(attempt.external_id))
            latest = delivery.events.latest("sequence")
            self.assertEqual(latest.event_type, "failed_terminal")
            self.assertEqual(latest.safe_code, "source_not_current")
        else:
            self.assertFalse(CorrespondenceDelivery.objects.exists())
        self.assertFalse(CorrespondenceSyntheticProviderInvocation.objects.exists())
        self.assertFalse(CorrespondenceSyntheticProviderReceipt.objects.exists())

    def test_amendment_after_worker_claim_blocks_locked_preflight_and_provider(self):
        created = self.client.post(
            self.send_url,
            self._concurrent_send_payload(str(uuid4())),
            format="json",
        )
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        self.assertEqual(_claim_pending_attempt(str(attempt.external_id)), attempt.id)
        amended = self._amend_source(reason="Committed after worker claim")
        self.assertEqual(amended.status_code, HTTPStatus.CREATED, amended.json())

        with (
            patch.object(SyntheticCorrespondenceDeliveryAdapter, "begin") as begin,
            patch.object(SyntheticCorrespondenceDeliveryAdapter, "deliver") as deliver,
        ):
            _dispatch_claimed_attempt(attempt.id)

        self.assertFalse(begin.called)
        self.assertFalse(deliver.called)
        latest = delivery.events.latest("sequence")
        self.assertEqual(latest.event_type, "failed_terminal")
        self.assertEqual(latest.safe_code, "source_not_current")
        self.assertFalse(CorrespondenceSyntheticProviderInvocation.objects.exists())
        self.assertFalse(CorrespondenceSyntheticProviderReceipt.objects.exists())

    def test_amendment_after_provider_start_preserves_ack_and_queues_correction(self):
        created = self.client.post(
            self.send_url,
            self._concurrent_send_payload(str(uuid4())),
            format="json",
        )
        self.assertEqual(created.status_code, HTTPStatus.CREATED, created.json())
        delivery = CorrespondenceDelivery.objects.get()
        attempt = delivery.attempts.get(attempt_number=1)
        provider_started = ThreadEvent()
        provider_release = ThreadEvent()
        original_record = SyntheticCorrespondenceDeliveryAdapter._record_outcome  # noqa: SLF001

        def slow_record(adapter, **kwargs):
            provider_started.set()
            if not provider_release.wait(timeout=10):
                raise RuntimeError("synthetic provider release timeout")
            return original_record(adapter, **kwargs)

        def run_worker():
            close_old_connections()
            dispatch_correspondence_delivery_attempt(str(attempt.external_id))
            close_old_connections()

        with (
            patch.object(
                SyntheticCorrespondenceDeliveryAdapter,
                "_record_outcome",
                new=slow_record,
            ),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            worker = executor.submit(run_worker)
            self.assertTrue(provider_started.wait(timeout=10))
            try:
                amended = self._amend_source(reason="Committed after provider start")
                self.assertEqual(
                    amended.status_code,
                    HTTPStatus.CREATED,
                    amended.json(),
                )
            finally:
                provider_release.set()
            worker.result(timeout=20)

        latest = delivery.events.latest("sequence")
        self.assertEqual(latest.event_type, "acknowledged")
        self.assertEqual(latest.safe_code, "synthetic_ack")
        self.assertEqual(CorrespondenceSyntheticProviderInvocation.objects.count(), 1)
        self.assertEqual(CorrespondenceSyntheticProviderReceipt.objects.count(), 1)
        self.assertEqual(CorrespondenceSourceCorrection.objects.count(), 1)
        self.assertEqual(CorrespondenceCorrectionOutbox.objects.count(), 1)
