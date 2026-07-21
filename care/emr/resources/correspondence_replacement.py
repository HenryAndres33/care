from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, PositiveInt, model_validator

from care.emr.resources.correspondence import (
    MedicationActionSourceSpec,
    Sha256,
    canonical_sha256,
)
from care.emr.resources.correspondence_letter import (
    LetterBody,
    normalize_letter_body,
)

CorrectionCommandType = Literal[
    "start_replacement",
    "revise_replacement",
    "finalize_replacement",
    "send_replacement",
    "retry_replacement",
    "attest_paper",
    "resolve",
]


class CorrespondenceCorrectionCommandSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID4
    command_type: CorrectionCommandType
    expected_case_version: PositiveInt
    expected_case_hash: Sha256

    attempt: UUID4 | None = None
    target_revision: UUID4 | None = None
    target_revision_version: PositiveInt | None = None
    target_revision_hash: Sha256 | None = None
    body: LetterBody | None = None
    controlled_copy_artifact: UUID4 | None = None
    controlled_copy_artifact_hash: Sha256 | None = None
    delivery: UUID4 | None = None
    delivery_event_sequence: PositiveInt | None = None
    delivery_event_hash: Sha256 | None = None
    confirmed: Literal[True] | None = None
    resolution_mode: (
        Literal["replacement_acknowledged", "original_not_delivered"] | None
    ) = None
    attestation_type: Literal["corrected_copy_filed_prior_copy_reconciled"] | None = (
        None
    )

    patient: UUID4 | None = None
    encounter: UUID4 | None = None
    facility: UUID4 | None = None
    department: UUID4 | None = None
    encounter_reason: UUID4 | None = None
    form_submission: UUID4 | None = None
    form_source_version: PositiveInt | None = None
    form_source_hash: Sha256 | None = None
    form_artifact: UUID4 | None = None
    form_artifact_hash: Sha256 | None = None
    medication_actions: list[MedicationActionSourceSpec] | None = Field(
        default=None,
        max_length=50,
    )
    template: UUID4 | None = None
    template_version: PositiveInt | None = None
    template_hash: Sha256 | None = None
    author: UUID4 | None = None
    recipient: UUID4 | None = None
    recipient_version: PositiveInt | None = None
    recipient_hash: Sha256 | None = None

    @model_validator(mode="after")
    def validate_command_shape(self):
        common = {
            "client_request_id",
            "command_type",
            "expected_case_version",
            "expected_case_hash",
        }
        command_fields = {
            "start_replacement": {
                "patient",
                "encounter",
                "facility",
                "department",
                "encounter_reason",
                "form_submission",
                "form_source_version",
                "form_source_hash",
                "form_artifact",
                "form_artifact_hash",
                "medication_actions",
                "template",
                "template_version",
                "template_hash",
                "author",
                "recipient",
                "recipient_version",
                "recipient_hash",
            },
            "revise_replacement": {
                "attempt",
                "target_revision",
                "target_revision_version",
                "target_revision_hash",
                "body",
            },
            "finalize_replacement": {
                "attempt",
                "target_revision",
                "target_revision_version",
                "target_revision_hash",
                "confirmed",
            },
            "send_replacement": {
                "attempt",
                "target_revision",
                "target_revision_version",
                "target_revision_hash",
                "controlled_copy_artifact",
                "controlled_copy_artifact_hash",
                "confirmed",
            },
            "retry_replacement": {
                "attempt",
                "delivery",
                "delivery_event_sequence",
                "delivery_event_hash",
                "confirmed",
            },
            "attest_paper": {
                "attempt",
                "controlled_copy_artifact",
                "controlled_copy_artifact_hash",
                "attestation_type",
                "confirmed",
            },
            "resolve": {
                "resolution_mode",
                "confirmed",
            },
        }
        required = command_fields[self.command_type]
        if (
            self.command_type == "resolve"
            and self.resolution_mode == "replacement_acknowledged"
        ):
            required = required | {"attempt"}
        supplied = {
            field_name
            for field_name in self.model_fields
            if field_name not in common and getattr(self, field_name) is not None
        }
        if supplied != required:
            missing = sorted(required - supplied)
            unexpected = sorted(supplied - required)
            message = (
                f"Invalid {self.command_type} fields; missing={missing}, "
                f"unexpected={unexpected}"
            )
            raise ValueError(message)
        if self.medication_actions is not None:
            ids = [item.id for item in self.medication_actions]
            if len(ids) != len(set(ids)):
                raise ValueError("medication_actions contains duplicate IDs")
        if self.body is not None:
            self.body = normalize_letter_body(self.body)
        if (
            self.command_type == "resolve"
            and self.resolution_mode == "original_not_delivered"
            and self.attempt is not None
        ):
            raise ValueError(
                "original_not_delivered resolution must not identify a replacement attempt"
            )
        if (
            self.command_type == "resolve"
            and self.resolution_mode == "replacement_acknowledged"
            and self.attempt is None
        ):
            raise ValueError(
                "replacement_acknowledged resolution requires a replacement attempt"
            )
        return self


class CorrespondenceReplacementAttemptReadSpec(BaseModel):
    id: UUID4
    attempt_number: PositiveInt
    supersedes_attempt: UUID4 | None
    source_submission: UUID4
    source_version: PositiveInt
    source_snapshot_hash: Sha256
    form_artifact: UUID4
    form_artifact_hash: Sha256
    source_correction: UUID4
    compilation: UUID4
    review: UUID4
    initial_revision: UUID4
    started_by: UUID4
    started_at: datetime
    attempt_hash: Sha256


class CorrespondenceReplacementWorkflowReadSpec(BaseModel):
    status: Literal[
        "not_started",
        "compiling",
        "draft",
        "finalized",
        "delivery_pending",
        "acknowledged",
        "failed",
    ]
    attempt: CorrespondenceReplacementAttemptReadSpec | None
    revision: UUID4 | None
    revision_version: PositiveInt | None
    revision_hash: Sha256 | None
    revision_status: Literal["draft", "finalized"] | None
    artifact: UUID4 | None
    artifact_hash: Sha256 | None
    delivery: UUID4 | None
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
    delivery_event_sequence: PositiveInt | None
    delivery_event_hash: Sha256 | None
    can_retry_delivery: bool
    attestation: UUID4 | None
    attestation_hash: Sha256 | None
    attestation_type: Literal["corrected_copy_filed_prior_copy_reconciled"] | None
    attested_by: UUID4 | None
    attested_at: datetime | None


class CorrespondenceCorrectionCommandResponseSpec(BaseModel):
    client_request_id: UUID4
    replayed: bool
    command_type: CorrectionCommandType
    case: UUID4
    case_version: PositiveInt
    case_hash: Sha256
    case_status: Literal["open", "resolved"]
    resolution_mode: (
        Literal["replacement_acknowledged", "original_not_delivered"] | None
    )
    replacement_status: Literal[
        "not_started",
        "compiling",
        "draft",
        "finalized",
        "delivery_pending",
        "acknowledged",
        "failed",
    ]
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


def canonical_correction_command_payload_hash(
    request_spec: CorrespondenceCorrectionCommandSpec,
    *,
    actor_id: UUID,
    case_id: UUID,
) -> str:
    return canonical_sha256(
        {
            "actor": actor_id,
            "case": case_id,
            "command": request_spec.command_type,
            "contract": "correspondence-correction-command-payload-v1",
            "payload": request_spec.model_dump(
                mode="python",
                exclude={"client_request_id"},
            ),
        }
    )


def correspondence_replacement_attempt_hash(attempt) -> str:
    return canonical_sha256(
        {
            "attempt": attempt.external_id,
            "attempt_number": attempt.attempt_number,
            "case": attempt.case.external_id,
            "compilation": attempt.compilation.external_id,
            "contract": "correspondence-replacement-attempt-v1",
            "form_artifact": attempt.form_artifact.external_id,
            "form_artifact_hash": attempt.form_artifact_hash,
            "initial_revision": attempt.initial_revision.external_id,
            "review": attempt.review.external_id,
            "source_correction": attempt.source_correction.external_id,
            "source_correction_hash": attempt.source_correction_hash,
            "source_head_hash": attempt.source_head_hash,
            "source_snapshot_hash": attempt.source_snapshot_hash,
            "source_submission": attempt.source_submission.external_id,
            "source_version": attempt.source_version,
            "started_at": attempt.started_at,
            "started_by": attempt.started_by.external_id,
            "supersedes_attempt": (
                attempt.supersedes_attempt.external_id
                if attempt.supersedes_attempt_id
                else None
            ),
        }
    )


def correspondence_paper_attestation_hash(attestation) -> str:
    return canonical_sha256(
        {
            "artifact": attestation.controlled_copy_artifact.external_id,
            "artifact_hash": attestation.artifact_hash,
            "attestation": attestation.external_id,
            "attestation_type": attestation.attestation_type,
            "attested_at": attestation.attested_at,
            "attested_by": attestation.attested_by.external_id,
            "case": attestation.case.external_id,
            "contract": "correspondence-paper-reconciliation-attestation-v1",
            "replacement_attempt": attestation.replacement_attempt.external_id,
        }
    )


def correspondence_correction_command_hash(command) -> str:
    return canonical_sha256(
        {
            "actor": command.actor.external_id,
            "case": command.case.external_id,
            "client_request_id": command.client_request_id,
            "command": command.command_type,
            "contract": "correspondence-correction-command-v1",
            "expected_case_hash": command.expected_case_hash,
            "expected_case_version": command.expected_case_version,
            "payload_hash": command.payload_hash,
            "result_artifact": (
                command.result_artifact.external_id
                if command.result_artifact_id
                else None
            ),
            "result_attempt": (
                command.result_attempt.external_id
                if command.result_attempt_id
                else None
            ),
            "result_attestation": (
                command.result_attestation.external_id
                if command.result_attestation_id
                else None
            ),
            "result_delivery": (
                command.result_delivery.external_id
                if command.result_delivery_id
                else None
            ),
            "result_revision": (
                command.result_revision.external_id
                if command.result_revision_id
                else None
            ),
            "resulting_case_hash": command.resulting_case_hash,
            "resulting_case_version": command.resulting_case_version,
            "result_snapshot": command.result_snapshot,
            "target_attempt": (
                command.target_attempt.external_id
                if command.target_attempt_id
                else None
            ),
        }
    )
