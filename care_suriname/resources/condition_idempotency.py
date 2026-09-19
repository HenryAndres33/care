import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict

from care.emr.resources.condition.spec import ConditionReadSpec, ConditionSpec


class IdempotentDiagnosisCreateSpec(ConditionSpec):
    model_config = ConfigDict(extra="forbid")

    id: UUID4 | None = None
    client_request_id: UUID4


class IdempotentDiagnosisCreateResponseSpec(BaseModel):
    client_request_id: UUID4
    replayed: bool
    diagnosis: ConditionReadSpec


def canonical_diagnosis_hash(
    request_spec: IdempotentDiagnosisCreateSpec,
    *,
    patient_id: UUID,
    actor_id: UUID,
) -> str:
    canonical_input = {
        "actor": actor_id,
        "contract": "diagnosis-idempotent-create-v1",
        "patient": patient_id,
        "payload": request_spec.model_dump(
            mode="python", exclude={"client_request_id", "meta"}
        ),
    }
    encoded = json.dumps(
        _normalize(canonical_input),
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
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    return value
