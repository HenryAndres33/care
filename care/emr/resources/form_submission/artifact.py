import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, PositiveInt, StringConstraints

SnapshotHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class GenerateFormSubmissionArtifactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID4
    patient: UUID4
    encounter: UUID4
    questionnaire: str = Field(min_length=1)
    source_version: PositiveInt
    source_snapshot_hash: SnapshotHash


class FormSubmissionArtifactReadSpec(BaseModel):
    id: UUID4
    patient: UUID4
    encounter: UUID4
    form_submission: UUID4
    source_version: PositiveInt
    source_snapshot_hash: SnapshotHash
    artifact_sha256: SnapshotHash
    generated_at: datetime
    generated_by: UUID4
    status: str
    mime_type: str
    download_url: str


class FormSubmissionArtifactCommandResponseSpec(BaseModel):
    client_request_id: UUID4
    replayed: bool
    artifact: FormSubmissionArtifactReadSpec


def canonical_artifact_command_hash(
    request_spec: GenerateFormSubmissionArtifactSpec,
    *,
    source_id: UUID,
    actor_id: UUID,
) -> str:
    value = {
        "actor": actor_id,
        "command": "generate_form_submission_artifact",
        "contract": "form-submission-artifact-command-v1",
        "payload": request_spec.model_dump(
            mode="python", exclude={"client_request_id"}
        ),
        "source": source_id,
    }
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


def has_unresolved_placeholder(value: Any) -> bool:
    patterns = (
        re.compile(r"{{[^{}]+}}"),
        re.compile(r"{%[^%]+%}"),
        re.compile(r"\$\{[^{}]+}"),
        re.compile(r"<<[^<>]+>>"),
        re.compile(r"\[\[(?:placeholder|todo|required)(?::[^\]]*)?]]", re.IGNORECASE),
    )

    def scan(item):
        if isinstance(item, dict):
            return any(scan(key) or scan(child) for key, child in item.items())
        if isinstance(item, list):
            return any(scan(child) for child in item)
        if isinstance(item, str):
            return any(pattern.search(item) for pattern in patterns)
        return False

    return scan(value)
