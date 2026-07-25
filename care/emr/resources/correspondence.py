import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    model_validator,
)

from care.emr.models.correspondence import CorrespondenceCompilation
from care.emr.resources.base import EMRResource

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class MedicationActionSourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID4
    client_request_id: UUID4


class CompileCorrespondenceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID4
    patient: UUID4
    encounter: UUID4
    facility: UUID4
    department: UUID4
    encounter_reason: UUID4
    form_submission: UUID4
    form_source_version: PositiveInt
    form_source_hash: Sha256
    form_artifact: UUID4
    form_artifact_hash: Sha256
    medication_actions: list[MedicationActionSourceSpec] = Field(max_length=50)
    template: UUID4
    template_version: PositiveInt
    template_hash: Sha256
    author: UUID4

    @model_validator(mode="after")
    def validate_medication_action_ids_are_unique(self):
        ids = [item.id for item in self.medication_actions]
        if len(ids) != len(set(ids)):
            raise ValueError("medication_actions contains duplicate IDs")
        return self


class CorrespondenceCompilationReadSpec(EMRResource):
    __model__ = CorrespondenceCompilation

    id: UUID4
    status: str
    patient: UUID4
    encounter: UUID4
    facility: UUID4
    department: UUID4
    encounter_reason: UUID4
    form_submission: UUID4
    form_artifact: UUID4
    form_source_version: PositiveInt
    form_source_hash: Sha256
    form_artifact_hash: Sha256
    template: UUID4
    template_version: PositiveInt
    template_hash: Sha256
    author: UUID4
    medication_sources: list[dict]
    source_provenance: dict
    compiled_text: str
    compiled_html: str
    compiled_hash: Sha256
    compiled_at: datetime

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        mapping["id"] = obj.external_id
        for field in [
            "patient",
            "encounter",
            "facility",
            "department",
            "encounter_reason",
            "form_submission",
            "form_artifact",
            "template",
            "author",
        ]:
            mapping[field] = getattr(obj, field).external_id


class CompileCorrespondenceResponseSpec(BaseModel):
    client_request_id: UUID4
    replayed: bool
    compilation: CorrespondenceCompilationReadSpec


def canonical_correspondence_command_hash(
    request_spec: CompileCorrespondenceSpec,
    *,
    actor_id: UUID,
) -> str:
    value = {
        "actor": actor_id,
        "command": "compile_correspondence",
        "contract": "correspondence-compilation-command-v2",
        "payload": request_spec.model_dump(mode="python"),
    }
    return canonical_sha256(value)


def canonical_correspondence_command_hash_v1(
    request_spec: CompileCorrespondenceSpec,
    *,
    actor_id: UUID,
) -> str:
    """Recompute the former hash only to replay pre-v2 committed commands."""

    value = {
        "actor": actor_id,
        "command": "compile_correspondence",
        "contract": "correspondence-compilation-command-v1",
        "payload": request_spec.model_dump(
            mode="python", exclude={"client_request_id"}
        ),
    }
    return canonical_sha256(value)


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    message = f"Unsupported canonical value: {type(value).__name__}"
    raise TypeError(message)
