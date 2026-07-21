import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict

from care.emr.resources.medication.request.spec import (
    MedicationRequestReadSpec,
    MedicationRequestSpec,
)


class IdempotentMedicationRequestCreateSpec(MedicationRequestSpec):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID4
    form_submission: UUID4 | None = None


class IdempotentMedicationRequestCreateResponseSpec(BaseModel):
    client_request_id: UUID4
    replayed: bool
    medication_request: MedicationRequestReadSpec


def canonical_medication_request_hash(
    request_spec: IdempotentMedicationRequestCreateSpec,
    *,
    patient_id: UUID,
    actor_id: UUID,
) -> str:
    payload = request_spec.model_dump(
        mode="python",
        exclude={"client_request_id", "meta"},
    )
    canonical_input = {
        "actor": actor_id,
        "contract": "medication-request-idempotent-create-v1",
        "patient": patient_id,
        "payload": payload,
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
