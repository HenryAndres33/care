from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    model_validator,
)

from care.emr.resources.correspondence import (
    MedicationActionSourceSpec,
    canonical_sha256,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

CONSULT_CLOSE_POLICY_ID = "care.standard.consult-close"
EMERGENCY_CLOSE_POLICY_ID = "care.standard.emergency-close"
CONSULT_CLOSE_POLICY_VERSION = 1
CONSULT_CLOSE_PREFLIGHT_VERSION = 1
CONSULT_CLOSE_POLICY_HASH = canonical_sha256(
    {
        "contract": "care-standard-consult-close-policy-v1",
        "requirements": [
            "current-finalized-form",
            "verified-printable-artifact",
            "declared-medication-outcome",
            "declared-correspondence-outcome",
            "active-encounter-token-booking",
            "explicit-confirmation",
            "server-revalidated-preflight",
        ],
    }
)

MedicationOutcome = Literal["completed", "not_required"]
CorrespondenceOutcome = Literal[
    "not_required",
    "delivery_acknowledged",
    "correction_resolved",
]


class ConsultClosePreflightSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient: UUID4
    facility: UUID4
    department: UUID4
    form_submission: UUID4
    medication_outcome: MedicationOutcome
    correspondence_outcome: CorrespondenceOutcome
    correspondence_compilation: UUID4 | None = None

    @model_validator(mode="after")
    def validate_correspondence_shape(self):
        needs_compilation = self.correspondence_outcome == "delivery_acknowledged"
        if needs_compilation != (self.correspondence_compilation is not None):
            raise ValueError(
                "correspondence_compilation is required only for acknowledged delivery"
            )
        return self


class CorrespondenceCloseEvidenceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: CorrespondenceOutcome
    compilation: UUID4 | None = None
    delivery: UUID4 | None = None
    event_sequence: PositiveInt | None = None
    event_hash: Sha256 | None = None
    correction_case: UUID4 | None = None
    case_version: PositiveInt | None = None
    case_hash: Sha256 | None = None

    @model_validator(mode="after")
    def validate_outcome_shape(self):
        delivery_fields = [self.delivery, self.event_sequence, self.event_hash]
        case_fields = [self.correction_case, self.case_version, self.case_hash]
        if self.outcome == "not_required":
            if self.compilation is not None or any(delivery_fields) or any(case_fields):
                raise ValueError("not_required forbids correspondence evidence")
        elif self.outcome == "delivery_acknowledged":
            if self.compilation is None or not all(delivery_fields) or any(case_fields):
                raise ValueError(
                    "delivery_acknowledged requires exact delivery evidence"
                )
        elif self.outcome == "correction_resolved" and (
            self.compilation is None or not all(case_fields) or any(delivery_fields)
        ):
            raise ValueError("correction_resolved requires exact correction evidence")
        return self


class ConsultCloseCommandCandidateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    encounter: UUID4
    patient: UUID4
    facility: UUID4
    department: UUID4
    token: UUID4 | None
    appointment: UUID4 | None
    expected_encounter_status: Literal["in_progress"]
    expected_encounter_modified_at: datetime
    expected_token_status: Literal["IN_PROGRESS"] | None
    expected_token_modified_at: datetime | None
    expected_booking_status: Literal["in_consultation"] | None
    expected_booking_modified_at: datetime | None
    policy_id: Literal[CONSULT_CLOSE_POLICY_ID, EMERGENCY_CLOSE_POLICY_ID]
    policy_version: Literal[CONSULT_CLOSE_POLICY_VERSION]
    policy_hash: Sha256
    preflight_version: Literal[CONSULT_CLOSE_PREFLIGHT_VERSION]
    preflight_hash: Sha256
    form_submission: UUID4
    form_source_version: PositiveInt
    form_source_hash: Sha256
    form_artifact: UUID4
    form_artifact_hash: Sha256
    medication_outcome: MedicationOutcome
    medication_actions: list[MedicationActionSourceSpec] = Field(max_length=50)
    correspondence_outcome: CorrespondenceOutcome
    correspondence_compilation: UUID4 | None = None
    correspondence_delivery: UUID4 | None = None
    correspondence_delivery_event_sequence: PositiveInt | None = None
    correspondence_delivery_event_hash: Sha256 | None = None
    correspondence_case: UUID4 | None = None
    correspondence_case_version: PositiveInt | None = None
    correspondence_case_hash: Sha256 | None = None

    @model_validator(mode="after")
    def validate_policy_and_medication_shape(self):
        scheduling = [
            self.token,
            self.appointment,
            self.expected_token_status,
            self.expected_token_modified_at,
            self.expected_booking_status,
            self.expected_booking_modified_at,
        ]
        if self.policy_id == EMERGENCY_CLOSE_POLICY_ID:
            if any(value is not None for value in scheduling):
                raise ValueError(
                    "Unscheduled emergency must not contain queue evidence"
                )
        elif any(value is None for value in scheduling):
            raise ValueError("Booked consultation requires complete queue evidence")
        has_actions = bool(self.medication_actions)
        if (self.medication_outcome == "completed") != has_actions:
            raise ValueError("medication outcome does not match medication actions")
        ids = [item.id for item in self.medication_actions]
        request_ids = [item.client_request_id for item in self.medication_actions]
        if len(ids) != len(set(ids)) or len(request_ids) != len(set(request_ids)):
            raise ValueError("medication_actions contains duplicate evidence")
        CorrespondenceCloseEvidenceSpec.model_validate(
            {
                "outcome": self.correspondence_outcome,
                "compilation": self.correspondence_compilation,
                "delivery": self.correspondence_delivery,
                "event_sequence": self.correspondence_delivery_event_sequence,
                "event_hash": self.correspondence_delivery_event_hash,
                "correction_case": self.correspondence_case,
                "case_version": self.correspondence_case_version,
                "case_hash": self.correspondence_case_hash,
            }
        )
        return self


class ConsultCloseCommandSpec(ConsultCloseCommandCandidateSpec):
    client_request_id: UUID4
    confirmed: Literal[True]


class ConsultClosureReadSpec(BaseModel):
    id: UUID4
    closure_number: PositiveInt
    previous_closure: UUID4 | None
    encounter: UUID4
    patient: UUID4
    facility: UUID4
    department: UUID4
    token: UUID4 | None
    appointment: UUID4 | None
    status: Literal["completed"]
    encounter_status: Literal["completed"]
    token_status: Literal["FULFILLED", "not_required"]
    booking_status: Literal["fulfilled", "not_required"]
    policy_id: str
    policy_version: PositiveInt
    policy_hash: Sha256
    preflight_version: PositiveInt
    preflight_hash: Sha256
    form_submission: UUID4
    form_source_version: PositiveInt
    form_source_hash: Sha256
    form_artifact: UUID4
    form_artifact_hash: Sha256
    medication_outcome: MedicationOutcome
    medication_actions: list[MedicationActionSourceSpec]
    correspondence_outcome: CorrespondenceOutcome
    correspondence_compilation: UUID4 | None = None
    correspondence_delivery: UUID4 | None = None
    correspondence_delivery_event_sequence: PositiveInt | None = None
    correspondence_delivery_event_hash: Sha256 | None = None
    correspondence_case: UUID4 | None = None
    correspondence_case_version: PositiveInt | None = None
    correspondence_case_hash: Sha256 | None = None
    closed_at: datetime
    closed_by: UUID4
    closure_hash: Sha256
    recovery_status: Literal["not_required"]


class ConsultCloseCommandResponseSpec(BaseModel):
    client_request_id: UUID4
    replayed: bool
    closure: ConsultClosureReadSpec


class ConsultClosePreflightResponseSpec(BaseModel):
    ready: bool
    blocker_codes: list[str]
    command_candidate: ConsultCloseCommandCandidateSpec | None
    checked_at: datetime
    preflight_version: Literal[CONSULT_CLOSE_PREFLIGHT_VERSION]
    preflight_hash: Sha256 | None


class ConsultClosureRecoveryReadSpec(BaseModel):
    id: UUID4
    status: Literal["pending", "resolved"]
    safe_code: str
    recovery_hash: Sha256 | None
    created_at: datetime
    resolved_at: datetime | None


class ConsultClosureRecoveryResolveSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID4
    recovery: UUID4
    expected_recovery_hash: Sha256
    confirmed: Literal[True]


class ConsultClosureRecoveryResolveResponseSpec(BaseModel):
    client_request_id: UUID4
    replayed: bool
    recovery: ConsultClosureRecoveryReadSpec


class ConsultClosureStateResponseSpec(BaseModel):
    encounter: UUID4
    closure: ConsultClosureReadSpec | None
    recovery: ConsultClosureRecoveryReadSpec | None


def consult_close_preflight_hash(candidate: dict) -> str:
    return canonical_sha256(
        {
            "candidate": candidate,
            "contract": "consult-close-preflight-v1",
        }
    )


def consult_close_policy_hash(
    required_questionnaire_slugs: list[str], policy_id=CONSULT_CLOSE_POLICY_ID
) -> str:
    booked_hash = canonical_sha256(
        {
            "base_policy_hash": CONSULT_CLOSE_POLICY_HASH,
            "contract": "care-standard-consult-close-department-policy-v1",
            "required_questionnaire_slugs": sorted(required_questionnaire_slugs),
        }
    )
    if policy_id == EMERGENCY_CLOSE_POLICY_ID:
        return canonical_sha256(
            {
                "booked_evidence_policy": booked_hash,
                "contract": "unscheduled-emergency-close-v1",
            }
        )
    return booked_hash


def consult_closure_recovery_hash(value: dict) -> str:
    return canonical_sha256(
        {
            "contract": "consult-closure-recovery-v1",
            "recovery": value,
        }
    )


def consult_closure_recovery_resolution_payload_hash(
    request_spec: ConsultClosureRecoveryResolveSpec,
    *,
    actor_id,
    encounter_id,
) -> str:
    return canonical_sha256(
        {
            "actor": actor_id,
            "command": "resolve-consult-closure-recovery",
            "contract": "consult-closure-recovery-resolution-payload-v1",
            "encounter": encounter_id,
            "payload": request_spec.model_dump(
                mode="python",
                exclude={"client_request_id"},
            ),
        }
    )


def consult_closure_recovery_resolution_hash(value: dict) -> str:
    return canonical_sha256(
        {
            "contract": "consult-closure-recovery-resolution-v1",
            "resolution": value,
        }
    )


def consult_close_payload_hash(
    request_spec: ConsultCloseCommandSpec,
    *,
    actor_id,
    encounter_id,
) -> str:
    return canonical_sha256(
        {
            "actor": actor_id,
            "command": "idempotent-close",
            "contract": "consult-close-command-v1",
            "encounter": encounter_id,
            "payload": request_spec.model_dump(
                mode="python",
                exclude={"client_request_id"},
            ),
        }
    )


def consult_closure_snapshot_hash(value: dict) -> str:
    return canonical_sha256(
        {
            "closure": value,
            "contract": "consult-closure-snapshot-v1",
        }
    )


def consult_closure_command_hash(value: dict) -> str:
    return canonical_sha256(
        {
            "command": value,
            "contract": "consult-closure-command-record-v1",
        }
    )
