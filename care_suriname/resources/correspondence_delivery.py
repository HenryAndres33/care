from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, PositiveInt

from care_suriname.resources.correspondence import Sha256, canonical_sha256


class SendCorrespondenceDeliverySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID4
    correspondence_revision: UUID4
    resource_version: PositiveInt
    revision_hash: Sha256
    artifact: UUID4
    artifact_sha256: Sha256
    review_binding: UUID4
    review_hash: Sha256
    patient: UUID4
    encounter: UUID4
    facility: UUID4
    department: UUID4
    author: UUID4
    recipient: UUID4
    recipient_version: PositiveInt
    recipient_hash: Sha256
    confirmed: Literal[True]


class RetryCorrespondenceDeliverySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID4
    expected_event_sequence: PositiveInt
    expected_event_hash: Sha256
    confirmed: Literal[True]


class CorrespondenceDeliveryListSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient: UUID4
    encounter: UUID4
    correspondence_revision: UUID4 | None = None
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=10_000)


class CorrespondenceDeliveryEventReadSpec(BaseModel):
    id: UUID4
    attempt: UUID4
    attempt_number: PositiveInt
    sequence: PositiveInt
    event_type: Literal[
        "dispatch_pending",
        "dispatching",
        "acknowledged",
        "failed_retryable",
        "failed_terminal",
        "outcome_unknown",
    ]
    certainty: Literal[
        "not_attempted",
        "attempting",
        "acknowledged",
        "not_delivered",
        "unknown",
    ]
    occurred_at: datetime
    actor_type: Literal["user", "system"]
    actor: UUID4 | None
    safe_code: str
    provider_ack_reference: str | None
    provider_ack_hash: Sha256 | None
    provider_ack_at: datetime | None
    previous_event: UUID4 | None
    event_hash: Sha256


class CorrespondenceDeliveryAttemptReadSpec(BaseModel):
    id: UUID4
    attempt_number: PositiveInt
    command_type: Literal["send", "retry"]
    requested_by: UUID4
    requested_at: datetime
    attempt_hash: Sha256
    previous_terminal_event: UUID4 | None
    events: list[CorrespondenceDeliveryEventReadSpec]


class CorrespondenceDeliveryReadSpec(BaseModel):
    id: UUID4
    correspondence_revision: UUID4
    resource_version: PositiveInt
    revision_hash: Sha256
    artifact: UUID4
    artifact_sha256: Sha256
    review_binding: UUID4
    review_hash: Sha256
    patient: UUID4
    encounter: UUID4
    facility: UUID4
    department: UUID4
    author: UUID4
    recipient: UUID4
    recipient_version: PositiveInt
    recipient_hash: Sha256
    channel_type: str
    adapter_name: str
    adapter_version: str
    delivery_hash: Sha256
    supersedes: UUID4 | None
    correction_case_reference: UUID4 | None
    state: str
    certainty: str
    latest_event_sequence: PositiveInt
    latest_event_hash: Sha256
    can_retry: bool
    attempts: list[CorrespondenceDeliveryAttemptReadSpec]


class CorrespondenceDeliveryCommandResponseSpec(BaseModel):
    client_request_id: UUID4
    replayed: bool
    delivery: CorrespondenceDeliveryReadSpec


def canonical_delivery_command_hash(
    request_spec: SendCorrespondenceDeliverySpec | RetryCorrespondenceDeliverySpec,
    *,
    command_type: str,
    actor_id: UUID,
    delivery_id: UUID | None,
) -> str:
    return canonical_sha256(
        {
            "actor": actor_id,
            "command": command_type,
            "contract": "correspondence-delivery-command-v1",
            "delivery": delivery_id,
            "payload": request_spec.model_dump(
                mode="python",
                exclude={"client_request_id"},
            ),
        }
    )


def correspondence_delivery_provider_key(delivery_id: UUID) -> str:
    return canonical_sha256(
        {
            "contract": "correspondence-delivery-provider-key-v1",
            "delivery": delivery_id,
        }
    )


def correspondence_delivery_hash(delivery) -> str:
    return canonical_sha256(
        {
            "adapter_name": delivery.adapter_name,
            "adapter_version": delivery.adapter_version,
            "artifact": delivery.artifact.external_id,
            "artifact_sha256": delivery.artifact_sha256,
            "author": delivery.author.external_id,
            "channel_type": delivery.channel_type,
            "contract": "correspondence-delivery-snapshot-v1",
            "correction_case_reference": delivery.correction_case_reference,
            "delivery": delivery.external_id,
            "department": delivery.department.external_id,
            "encounter": delivery.encounter.external_id,
            "facility": delivery.facility.external_id,
            "patient": delivery.patient.external_id,
            "provider_idempotency_key": delivery.provider_idempotency_key,
            "recipient": delivery.recipient.external_id,
            "recipient_hash": delivery.recipient_hash,
            "recipient_version": delivery.recipient_version,
            "review_binding": delivery.review.external_id,
            "review_hash": delivery.review_hash,
            "revision": delivery.revision.external_id,
            "revision_hash": delivery.revision_hash,
            "revision_version": delivery.revision_version,
            "supersedes": (
                delivery.supersedes.external_id if delivery.supersedes_id else None
            ),
        }
    )


def correspondence_delivery_event_hash(event) -> str:
    return canonical_sha256(
        {
            "actor": event.actor.external_id if event.actor_id else None,
            "actor_type": event.actor_type,
            "attempt": event.attempt.external_id,
            "attempt_number": event.attempt.attempt_number,
            "attempt_hash": event.attempt.attempt_hash,
            "certainty": event.certainty,
            "contract": "correspondence-delivery-event-v1",
            "delivery": event.delivery.external_id,
            "delivery_hash": event.delivery.delivery_hash,
            "event": event.external_id,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "previous_event": (
                event.previous_event.external_id if event.previous_event_id else None
            ),
            "previous_event_hash": event.previous_event_hash,
            "provider_ack_at": event.provider_ack_at,
            "provider_ack_hash": event.provider_ack_hash,
            "provider_ack_reference": event.provider_ack_reference,
            "safe_code": event.safe_code,
            "sequence": event.sequence,
        }
    )


def correspondence_delivery_attempt_hash(attempt) -> str:
    return canonical_sha256(
        {
            "adapter_name": attempt.adapter_name,
            "adapter_version": attempt.adapter_version,
            "attempt": attempt.external_id,
            "attempt_number": attempt.attempt_number,
            "client_request_id": attempt.client_request_id,
            "command_type": attempt.command_type,
            "contract": "correspondence-delivery-attempt-v1",
            "delivery": attempt.delivery.external_id,
            "delivery_hash": attempt.delivery.delivery_hash,
            "payload_hash": attempt.payload_hash,
            "previous_terminal_event": (
                attempt.previous_terminal_event.external_id
                if attempt.previous_terminal_event_id
                else None
            ),
            "provider_idempotency_key": attempt.provider_idempotency_key,
            "requested_at": attempt.requested_at,
            "requested_by": attempt.requested_by.external_id,
            "created_by": (
                attempt.created_by.external_id if attempt.created_by_id else None
            ),
            "updated_by": (
                attempt.updated_by.external_id if attempt.updated_by_id else None
            ),
        }
    )


def correspondence_synthetic_provider_request_hash(
    *,
    provider_idempotency_key: str,
    delivery_hash: str,
    artifact_sha256: str,
) -> str:
    return canonical_sha256(
        {
            "artifact_sha256": artifact_sha256,
            "contract": "correspondence-synthetic-provider-request-v1",
            "delivery_hash": delivery_hash,
            "provider_idempotency_key": provider_idempotency_key,
        }
    )


def correspondence_synthetic_provider_invocation_hash(invocation) -> str:
    return canonical_sha256(
        {
            "attempt_number": invocation.attempt_number,
            "contract": "correspondence-synthetic-provider-invocation-v1",
            "invocation": invocation.external_id,
            "marker_hash_version": 1,
            "provider_idempotency_key": invocation.provider_idempotency_key,
            "request_hash": invocation.request_hash,
            "started_at": invocation.started_at,
        }
    )


def correspondence_synthetic_provider_receipt_hash(receipt) -> str:
    return canonical_sha256(
        {
            "attempt_number": receipt.attempt_number,
            "contract": "correspondence-synthetic-provider-receipt-v1",
            "outcome_state": receipt.outcome_state,
            "provider_ack_hash": receipt.provider_ack_hash,
            "provider_ack_reference": receipt.provider_ack_reference,
            "provider_idempotency_key": receipt.provider_idempotency_key,
            "receipt": receipt.external_id,
            "recorded_at": receipt.recorded_at,
            "request_hash": receipt.request_hash,
            "safe_code": receipt.safe_code,
        }
    )
