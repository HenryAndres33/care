from dataclasses import dataclass
from typing import Literal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from care.emr.resources.correspondence import canonical_sha256
from care.emr.resources.correspondence_delivery import (
    correspondence_synthetic_provider_invocation_hash,
    correspondence_synthetic_provider_receipt_hash,
    correspondence_synthetic_provider_request_hash,
)
from care_suriname.models.correspondence_delivery import (
    CorrespondenceSyntheticProviderInvocation,
    CorrespondenceSyntheticProviderReceipt,
)
from care_suriname.models.correspondence_review import CorrespondenceRecipient


class CorrespondenceDeliveryAdapterUnavailableError(ValueError):
    """No server-approved transport exists for this frozen recipient."""


@dataclass(frozen=True)
class CorrespondenceAdapterOutcome:
    state: Literal[
        "acknowledged",
        "failed_retryable",
        "failed_terminal",
        "outcome_unknown",
    ]
    safe_code: str
    provider_ack_reference: str = ""


class SyntheticCorrespondenceDeliveryAdapter:
    """No-network adapter for local validation and automated tests only."""

    name = "synthetic_no_network"
    version = "1"

    def begin(
        self,
        *,
        provider_idempotency_key: str,
        attempt_number: int,
        delivery_hash: str,
        artifact_sha256: str,
    ) -> bool:
        """Durably fence a provider attempt before the worker releases its claim."""
        request_hash = correspondence_synthetic_provider_request_hash(
            provider_idempotency_key=provider_idempotency_key,
            delivery_hash=delivery_hash,
            artifact_sha256=artifact_sha256,
        )
        return self._record_invocation(
            provider_idempotency_key=provider_idempotency_key,
            attempt_number=attempt_number,
            request_hash=request_hash,
        )

    def deliver(
        self,
        *,
        artifact_bytes: bytes,
        provider_idempotency_key: str,
        attempt_number: int,
        test_mode: str,
        delivery_hash: str,
        artifact_sha256: str,
    ) -> CorrespondenceAdapterOutcome:
        del artifact_bytes
        request_hash = correspondence_synthetic_provider_request_hash(
            provider_idempotency_key=provider_idempotency_key,
            delivery_hash=delivery_hash,
            artifact_sha256=artifact_sha256,
        )
        if not self.begin(
            provider_idempotency_key=provider_idempotency_key,
            attempt_number=attempt_number,
            delivery_hash=delivery_hash,
            artifact_sha256=artifact_sha256,
        ):
            return CorrespondenceAdapterOutcome(
                state="outcome_unknown",
                safe_code="synthetic_invocation_invalid",
            )
        existing = self._receipt(provider_idempotency_key=provider_idempotency_key)
        if existing:
            return self._verified_outcome(existing, request_hash=request_hash)
        if test_mode == "fail_once" and attempt_number == 1:
            outcome = CorrespondenceAdapterOutcome(
                state="failed_retryable",
                safe_code="synthetic_retryable",
            )
        elif test_mode == "fail_terminal":
            outcome = CorrespondenceAdapterOutcome(
                state="failed_terminal",
                safe_code="synthetic_terminal",
            )
        elif test_mode == "outcome_unknown":
            outcome = CorrespondenceAdapterOutcome(
                state="outcome_unknown",
                safe_code="synthetic_unknown",
            )
        elif test_mode not in {"ack", "fail_once"}:
            outcome = CorrespondenceAdapterOutcome(
                state="failed_terminal",
                safe_code="synthetic_mode_invalid",
            )
        else:
            outcome = CorrespondenceAdapterOutcome(
                state="acknowledged",
                safe_code="synthetic_ack",
                provider_ack_reference=f"synthetic:{provider_idempotency_key[:32]}",
            )
        return self._record_outcome(
            provider_idempotency_key=provider_idempotency_key,
            attempt_number=attempt_number,
            request_hash=request_hash,
            outcome=outcome,
        )

    def lookup(
        self,
        *,
        provider_idempotency_key: str,
        attempt_number: int,
        delivery_hash: str,
        artifact_sha256: str,
        test_mode: str,
    ) -> CorrespondenceAdapterOutcome:
        del test_mode
        request_hash = correspondence_synthetic_provider_request_hash(
            provider_idempotency_key=provider_idempotency_key,
            delivery_hash=delivery_hash,
            artifact_sha256=artifact_sha256,
        )
        receipt = self._receipt(provider_idempotency_key=provider_idempotency_key)
        if receipt:
            return self._verified_outcome(receipt, request_hash=request_hash)
        invocation = self._invocation(
            provider_idempotency_key=provider_idempotency_key,
            attempt_number=attempt_number,
        )
        if invocation:
            if not self._verified_invocation(invocation, request_hash=request_hash):
                return CorrespondenceAdapterOutcome(
                    state="outcome_unknown",
                    safe_code="synthetic_invocation_invalid",
                )
            return CorrespondenceAdapterOutcome(
                state="outcome_unknown",
                safe_code="synthetic_invocation_unresolved",
            )
        return CorrespondenceAdapterOutcome(
            state="failed_retryable",
            safe_code="synthetic_not_delivered",
        )

    @staticmethod
    def _receipt(*, provider_idempotency_key):
        return CorrespondenceSyntheticProviderReceipt._base_manager.filter(  # noqa: SLF001
            provider_idempotency_key=provider_idempotency_key,
            deleted=False,
        ).first()

    @staticmethod
    def _invocation(*, provider_idempotency_key, attempt_number):
        return CorrespondenceSyntheticProviderInvocation._base_manager.filter(  # noqa: SLF001
            provider_idempotency_key=provider_idempotency_key,
            attempt_number=attempt_number,
            deleted=False,
        ).first()

    def _record_invocation(
        self,
        *,
        provider_idempotency_key,
        attempt_number,
        request_hash,
    ):
        existing = self._invocation(
            provider_idempotency_key=provider_idempotency_key,
            attempt_number=attempt_number,
        )
        if existing:
            return self._verified_invocation(existing, request_hash=request_hash)
        try:
            with transaction.atomic():
                invocation = CorrespondenceSyntheticProviderInvocation(
                    provider_idempotency_key=provider_idempotency_key,
                    attempt_number=attempt_number,
                    request_hash=request_hash,
                    started_at=timezone.now(),
                    marker_hash="",
                )
                invocation.marker_hash = (
                    correspondence_synthetic_provider_invocation_hash(invocation)
                )
                invocation.save(force_insert=True)
        except IntegrityError:
            invocation = self._invocation(
                provider_idempotency_key=provider_idempotency_key,
                attempt_number=attempt_number,
            )
            if not invocation:
                return False
        return self._verified_invocation(invocation, request_hash=request_hash)

    def _record_outcome(
        self,
        *,
        provider_idempotency_key,
        attempt_number,
        request_hash,
        outcome,
    ):
        if outcome.state in {"failed_retryable", "outcome_unknown"}:
            return outcome
        ack_hash = (
            canonical_sha256(
                {
                    "contract": "correspondence-delivery-ack-reference-v1",
                    "reference": outcome.provider_ack_reference,
                }
            )
            if outcome.provider_ack_reference
            else ""
        )
        try:
            with transaction.atomic():
                receipt = CorrespondenceSyntheticProviderReceipt(
                    provider_idempotency_key=provider_idempotency_key,
                    attempt_number=attempt_number,
                    request_hash=request_hash,
                    outcome_state=outcome.state,
                    safe_code=outcome.safe_code,
                    provider_ack_reference=outcome.provider_ack_reference,
                    provider_ack_hash=ack_hash,
                    recorded_at=timezone.now(),
                    receipt_hash="",
                )
                receipt.receipt_hash = correspondence_synthetic_provider_receipt_hash(
                    receipt
                )
                receipt.save(force_insert=True)
        except IntegrityError:
            receipt = self._receipt(
                provider_idempotency_key=provider_idempotency_key,
            )
            if not receipt:
                raise CorrespondenceDeliveryAdapterUnavailableError from None
        return self._verified_outcome(receipt, request_hash=request_hash)

    @staticmethod
    def _verified_invocation(invocation, *, request_hash):
        return bool(
            invocation.request_hash == request_hash
            and correspondence_synthetic_provider_invocation_hash(invocation)
            == invocation.marker_hash
        )

    @staticmethod
    def _verified_outcome(receipt, *, request_hash):
        expected_ack_hash = (
            canonical_sha256(
                {
                    "contract": "correspondence-delivery-ack-reference-v1",
                    "reference": receipt.provider_ack_reference,
                }
            )
            if receipt.provider_ack_reference
            else ""
        )
        if (
            receipt.request_hash != request_hash
            or receipt.provider_ack_hash != expected_ack_hash
            or (
                receipt.outcome_state == "acknowledged"
                and not receipt.provider_ack_reference
            )
            or (
                receipt.outcome_state != "acknowledged"
                and bool(receipt.provider_ack_reference)
            )
            or correspondence_synthetic_provider_receipt_hash(receipt)
            != receipt.receipt_hash
        ):
            return CorrespondenceAdapterOutcome(
                state="outcome_unknown",
                safe_code="synthetic_receipt_invalid",
            )
        return CorrespondenceAdapterOutcome(
            state=receipt.outcome_state,
            safe_code=receipt.safe_code,
            provider_ack_reference=receipt.provider_ack_reference,
        )


def get_correspondence_delivery_adapter(
    recipient: CorrespondenceRecipient,
) -> SyntheticCorrespondenceDeliveryAdapter:
    """Resolve only server allowlisted adapters; recipient data is never a URL."""
    if not getattr(settings, "CORRESPONDENCE_SYNTHETIC_DELIVERY_ENABLED", False):
        raise CorrespondenceDeliveryAdapterUnavailableError
    if (
        recipient.source_type != "synthetic_test_fixture"
        or recipient.channel_type != "secure_endpoint"
    ):
        raise CorrespondenceDeliveryAdapterUnavailableError
    return SyntheticCorrespondenceDeliveryAdapter()


def synthetic_delivery_mode(recipient: CorrespondenceRecipient) -> str:
    provenance = recipient.source_provenance
    if not isinstance(provenance, dict):
        return "invalid"
    value = provenance.get("delivery_test_mode", "ack")
    return value if isinstance(value, str) else "invalid"
