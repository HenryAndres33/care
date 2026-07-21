import re
from datetime import datetime
from typing import Literal

from pydantic import UUID4, BaseModel, ConfigDict, Field, PositiveInt

from care.emr.resources.correspondence import Sha256, canonical_sha256
from care.emr.resources.correspondence_replacement import (
    CorrespondenceReplacementWorkflowReadSpec,
)

CORRESPONDENCE_CONTINUITY_CONTRACT_VERSION = 2
MAX_CORRESPONDENCE_CONTINUITY_CHANGES = 200
MAX_CORRESPONDENCE_CONTINUITY_VALUE_CHARACTERS = 4000
SAFE_CORRESPONDENCE_REFERENCE = re.compile(r"^[A-Za-z0-9._:/~-]{1,255}$")


class CorrespondenceContinuityQuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compilation: UUID4
    patient: UUID4
    encounter: UUID4


class CorrespondenceContinuityContextSpec(BaseModel):
    compilation: UUID4
    department: UUID4
    encounter: UUID4
    facility: UUID4
    patient: UUID4


class CorrespondenceContinuitySourceSpec(BaseModel):
    form_artifact: UUID4
    form_artifact_hash: Sha256
    form_series: UUID4
    form_source_hash: Sha256
    form_submission: UUID4
    form_version: PositiveInt


class CorrespondenceHistoricalChainSpec(BaseModel):
    artifact: UUID4 | None
    artifact_hash: Sha256 | None
    compilation: UUID4
    compilation_hash: Sha256
    delivery: UUID4 | None
    delivery_certainty: (
        Literal[
            "not_attempted",
            "attempting",
            "acknowledged",
            "not_delivered",
            "unknown",
        ]
        | None
    )
    delivery_hash: Sha256 | None
    delivery_state: (
        Literal[
            "dispatch_pending",
            "dispatching",
            "acknowledged",
            "failed_retryable",
            "failed_terminal",
            "outcome_unknown",
        ]
        | None
    )
    review: UUID4 | None
    review_hash: Sha256 | None
    revision: UUID4 | None
    revision_hash: Sha256 | None
    revision_version: PositiveInt | None


class CorrespondenceCorrectionChangeSpec(BaseModel):
    current_value: str | None = Field(max_length=4000)
    display_label: str = Field(min_length=1, max_length=255)
    field_reference: str = Field(
        min_length=1,
        max_length=255,
        pattern=SAFE_CORRESPONDENCE_REFERENCE.pattern,
    )
    previous_value: str | None = Field(max_length=4000)


class CorrespondenceCorrectionCaseSpec(BaseModel):
    amendment_author: UUID4
    amendment_occurred_at: datetime
    amendment_reason: str = Field(min_length=1, max_length=4000)
    amendment_type: Literal["addendum", "amendment"]
    case_hash: Sha256
    created_at: datetime
    id: UUID4
    notification_status: Literal[
        "not_required",
        "required",
        "pending",
        "acknowledged",
        "failed",
        "unknown",
    ]
    paper_reconciliation_status: Literal[
        "not_required",
        "required",
        "acknowledged",
    ]
    replacement: CorrespondenceReplacementWorkflowReadSpec
    resolution_mode: (
        Literal["replacement_acknowledged", "original_not_delivered"] | None
    )
    resolved_at: datetime | None
    resource_version: PositiveInt
    status: Literal["open", "resolved"]


class CorrespondenceContinuityActionPolicySpec(BaseModel):
    blocker_codes: list[
        Literal[
            "correction_required",
            "delivery_outcome_unresolved",
            "historical_only",
            "integrity_failed",
            "permission_required",
            "replacement_unavailable",
            "source_stale",
            "source_unavailable",
            "verification_required",
        ]
    ] = Field(max_length=20)
    can_draft: bool
    can_finalize: bool
    can_generate_controlled_print_copy: bool
    can_read_historical: bool
    can_retry_original: bool
    can_review: bool
    can_send_original: bool
    can_start_replacement: bool


class CorrespondenceContinuityReadSpec(BaseModel):
    action_policy: CorrespondenceContinuityActionPolicySpec
    authoritative_source: CorrespondenceContinuitySourceSpec | None
    changes: list[CorrespondenceCorrectionChangeSpec] = Field(
        max_length=MAX_CORRESPONDENCE_CONTINUITY_CHANGES
    )
    checked_at: datetime
    context: CorrespondenceContinuityContextSpec
    continuity_hash: Sha256
    continuity_id: UUID4
    contract_version: Literal[2]
    correction_case: CorrespondenceCorrectionCaseSpec | None
    frozen_source: CorrespondenceContinuitySourceSpec
    historical_chain: CorrespondenceHistoricalChainSpec
    required_action: Literal[
        "none",
        "regenerate_unsent",
        "resolve_delivery_outcome",
        "send_correction",
    ]
    resource_version: PositiveInt
    source_state: Literal[
        "current",
        "stale",
        "unavailable",
        "integrity_failed",
        "unknown",
    ]


def correspondence_correction_change_set_hash(changes: list[dict]) -> str:
    return canonical_sha256(
        {
            "changes": changes,
            "contract": "correspondence-correction-change-set-v1",
        }
    )


def correspondence_correction_case_hash(case) -> str:
    return canonical_sha256(
        {
            "case": case.external_id,
            "change_set_hash": case.change_set_hash,
            "contract": "correspondence-correction-case-v1",
            "current_snapshot_hash": case.current_snapshot_hash,
            "current_submission": case.current_submission.external_id,
            "current_version": case.current_version,
            "delivery_certainty": case.delivery_certainty,
            "delivery_state": case.delivery_state,
            "frozen_snapshot_hash": case.frozen_snapshot_hash,
            "frozen_submission": case.frozen_submission.external_id,
            "frozen_version": case.frozen_version,
            "latest_source_correction": case.latest_source_correction.external_id,
            "latest_source_correction_hash": case.latest_source_correction_hash,
            "notification_status": case.notification_status,
            "original_compilation": case.original_compilation.external_id,
            "original_delivery": case.original_delivery.external_id,
            "original_review": case.original_review.external_id,
            "paper_reconciliation_status": case.paper_reconciliation_status,
            "replacement_artifact": (
                case.replacement_artifact.external_id
                if case.replacement_artifact_id
                else None
            ),
            "replacement_compilation": (
                case.replacement_compilation.external_id
                if case.replacement_compilation_id
                else None
            ),
            "replacement_delivery": (
                case.replacement_delivery.external_id
                if case.replacement_delivery_id
                else None
            ),
            "replacement_delivery_certainty": case.replacement_delivery_certainty,
            "replacement_delivery_state": case.replacement_delivery_state,
            "replacement_review": (
                case.replacement_review.external_id
                if case.replacement_review_id
                else None
            ),
            "replacement_revision": (
                case.replacement_revision.external_id
                if case.replacement_revision_id
                else None
            ),
            "replacement_status": case.replacement_status,
            "replacement_attempt": (
                case.replacement_attempt.external_id
                if case.replacement_attempt_id
                else None
            ),
            "resolution_mode": case.resolution_mode,
            "resolved_at": case.resolved_at,
            "resolved_by": (
                case.resolved_by.external_id if case.resolved_by_id else None
            ),
            "resource_version": case.resource_version,
            "series_id": case.source_head.series_id,
            "source_head_hash": case.source_head_hash,
            "status": case.status,
        }
    )


def correspondence_correction_event_hash(event) -> str:
    return canonical_sha256(
        {
            "actor": event.actor.external_id if event.actor_id else None,
            "actor_type": event.actor_type,
            "case": event.case.external_id,
            "contract": "correspondence-correction-event-v1",
            "delivery_event": (
                event.delivery_event.external_id if event.delivery_event_id else None
            ),
            "event": event.external_id,
            "event_type": event.event_type,
            "command": event.command.external_id if event.command_id else None,
            "occurred_at": event.occurred_at,
            "previous_case_hash": event.previous_case_hash,
            "previous_event": (
                event.previous_event.external_id if event.previous_event_id else None
            ),
            "previous_event_hash": event.previous_event_hash,
            "resulting_case_hash": event.resulting_case_hash,
            "safe_code": event.safe_code,
            "sequence": event.sequence,
            "source_correction": (
                event.source_correction.external_id
                if event.source_correction_id
                else None
            ),
            "replacement_attempt": (
                event.replacement_attempt.external_id
                if event.replacement_attempt_id
                else None
            ),
        }
    )


def correspondence_continuity_hash(payload: dict) -> str:
    frozen = {key: value for key, value in payload.items() if key != "checked_at"}
    frozen.pop("continuity_hash", None)
    return canonical_sha256(
        {
            "continuity": frozen,
            "contract": "correspondence-continuity-v2",
        }
    )
