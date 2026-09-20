import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from care_suriname.resources.laboratory_commands.specs import LaboratoryCommand


def canonical_laboratory_command_hash(
    request_spec: LaboratoryCommand,
    *,
    actor_id: UUID,
) -> str:
    canonical_input = {
        "actor": actor_id,
        "action": request_spec.action,
        "context": {
            "patient": request_spec.patient,
            "facility": request_spec.facility,
            "encounter": request_spec.encounter,
            "service_request": request_spec.service_request_id,
            "report": request_spec.report_id,
        },
        "contract": request_spec.contract,
        "payload": request_spec.model_dump(
            mode="python",
            exclude={"client_request_id"},
        ),
    }
    return canonical_sha256(canonical_input)


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        _normalize(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _normalize(value: Any):
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    return value
