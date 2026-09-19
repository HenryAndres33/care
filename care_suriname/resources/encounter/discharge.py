from datetime import datetime
from typing import Annotated, Literal

from pydantic import UUID4, BaseModel, ConfigDict, StringConstraints, field_validator

from care.emr.resources.encounter.constants import DischargeDispositionChoices
from care_suriname.resources.correspondence import canonical_sha256

DischargeAdvice = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=4000),
]


class EncounterDischargeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discharge_disposition: DischargeDispositionChoices
    discharged_at: datetime
    discharge_summary_advice: DischargeAdvice = ""
    release_bed: bool = True

    @field_validator("discharged_at")
    @classmethod
    def validate_discharged_at_is_timezone_aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("discharged_at must be timezone aware")
        return value


class EncounterDischargeCommandSpec(EncounterDischargeSpec):
    client_request_id: UUID4


class DischargeDocumentationSnapshot(BaseModel):
    summary_submission: UUID4
    summary_version: int
    summary_hash: str
    letter_revision: UUID4
    letter_hash: str


class EncounterDischargeResultSpec(BaseModel):
    encounter: UUID4
    status: Literal["discharged"]
    status_history_entry: dict
    discharge_disposition: DischargeDispositionChoices
    discharged_at: datetime
    discharge_summary_advice: str
    bed_released: bool
    released_location: UUID4 | None
    documentation: DischargeDocumentationSnapshot | None = None
    warning_codes: list[str] = []


class EncounterDischargeCommandResponseSpec(BaseModel):
    client_request_id: UUID4
    replayed: bool
    discharge: EncounterDischargeResultSpec


class EncounterDischargePreflightResponseSpec(BaseModel):
    ready: bool
    blocker_codes: list[str]
    warning_codes: list[str]
    checked_at: datetime


class EncounterDischargeConflictResponseSpec(BaseModel):
    blocker_codes: list[str]


def encounter_discharge_payload_hash(
    request_spec: EncounterDischargeCommandSpec,
    *,
    actor_id,
    encounter_id,
) -> str:
    return canonical_sha256(
        {
            "actor": actor_id,
            "command": "idempotent-discharge",
            "contract": "encounter-discharge-command-v1",
            "encounter": encounter_id,
            "payload": request_spec.model_dump(
                mode="python",
                exclude={"client_request_id"},
            ),
        }
    )


def encounter_discharge_command_hash(value: dict) -> str:
    return canonical_sha256(
        {
            "command": value,
            "contract": "encounter-discharge-command-record-v1",
        }
    )
