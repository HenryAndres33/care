import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, PositiveInt, StringConstraints

from care.emr.models.questionnaire import FormSubmission
from care.emr.resources.form_submission.spec import FormSubmissionReadSpec


class FormSubmissionCommandSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID4
    expected_version: PositiveInt
    patient: UUID4
    encounter: UUID4 | None = None
    questionnaire: str = Field(min_length=1)


class CreateDraftFormSubmissionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID4
    encounter: UUID4 | None = None
    form_instance_id: UUID4
    patient: UUID4
    questionnaire: str = Field(min_length=1)
    response_dump: dict


class UpdateDraftFormSubmissionSpec(FormSubmissionCommandSpec):
    response_dump: dict


class FinalizeFormSubmissionSpec(FormSubmissionCommandSpec):
    pass


class AmendFormSubmissionSpec(FormSubmissionCommandSpec):
    amendment_type: Literal["amendment", "addendum"]
    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
    ]
    response_dump: dict


class EnterFormSubmissionInErrorSpec(FormSubmissionCommandSpec):
    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
    ]


class FormSubmissionCommandResponseSpec(BaseModel):
    client_request_id: UUID4
    replayed: bool
    form_submission: FormSubmissionReadSpec


def canonical_form_submission_command_hash(
    request_spec: FormSubmissionCommandSpec,
    *,
    command_type: str,
    target_id: UUID,
    actor_id: UUID,
) -> str:
    canonical_input = {
        "actor": actor_id,
        "command": command_type,
        "contract": "form-submission-command-v1",
        "payload": request_spec.model_dump(
            mode="python", exclude={"client_request_id"}
        ),
        "target": target_id,
    }
    return _sha256(canonical_input)


def canonical_form_submission_create_hash(
    request_spec: CreateDraftFormSubmissionSpec,
    *,
    actor_id: UUID,
) -> str:
    canonical_input = {
        "actor": actor_id,
        "command": "create_draft",
        "contract": "form-submission-create-draft-v1",
        "payload": request_spec.model_dump(
            mode="python", exclude={"client_request_id"}
        ),
    }
    return _sha256(canonical_input)


def finalized_form_submission_snapshot_hash(submission: FormSubmission) -> str:
    canonical_snapshot = {
        "amendment_reason": submission.amendment_reason,
        "amendment_type": submission.amendment_type,
        "contract": "form-submission-finalized-snapshot-v1",
        "encounter": (
            submission.encounter.external_id if submission.encounter_id else None
        ),
        "patient": submission.patient.external_id,
        "previous_version": (
            submission.previous_version.external_id
            if submission.previous_version_id
            else None
        ),
        "questionnaire": submission.questionnaire.slug,
        "resource_version": submission.resource_version,
        "response_dump": submission.response_dump,
        "series_id": submission.series_id,
    }
    return _sha256(canonical_snapshot)


def _sha256(value: Any) -> str:
    encoded = json.dumps(
        _normalize(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _normalize(value: Any):  # noqa: PLR0911
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        if value.is_zero():
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    return value
