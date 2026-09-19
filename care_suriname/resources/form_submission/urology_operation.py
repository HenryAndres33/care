from __future__ import annotations

from typing import Any

UROLOGY_OPERATIONS_QUESTIONNAIRE = "urology-operaties"
OPERATION_SCHEMA = "care.urology.operation-documentation"

_REQUIRED_COMPANION_SECTIONS = {
    "turp": {
        "basis",
        "introductie",
        "spc",
        "anatomie",
        "resectie",
        "hemostase",
        "chips",
        "postkatheter",
        "complicaties",
        "beleid",
        "contact",
    },
    "urs": {
        "basis",
        "introductie",
        "toegang",
        "concrement",
        "afronding",
        "complicaties",
        "contact",
    },
}


class InvalidUrologyOperationResponseError(ValueError):
    pass


def validate_urology_operation_response_dump(response_dump: Any) -> None:
    """Reject an operation finalization that lacks explicit clinical review."""

    if not isinstance(response_dump, dict):
        raise InvalidUrologyOperationResponseError(
            "Operation response is not an object"
        )
    content = response_dump.get("content")
    if not isinstance(content, dict):
        raise InvalidUrologyOperationResponseError("Operation content is missing")
    note_text = content.get("noteText")
    values = content.get("values")
    if not isinstance(note_text, str) or not note_text.strip():
        raise InvalidUrologyOperationResponseError("Operation narrative is missing")
    if not isinstance(values, dict):
        raise InvalidUrologyOperationResponseError("Operation values are missing")
    if (
        values.get("operation.schema") != OPERATION_SCHEMA
        or values.get("operation.schemaVersion") != "1"
    ):
        raise InvalidUrologyOperationResponseError("Operation schema is invalid")
    if values.get("operation.clinicalConfirmation") is not True:
        raise InvalidUrologyOperationResponseError(
            "Explicit clinical confirmation is required"
        )

    procedure_key = values.get("operation.procedureKey")
    required_sections = _REQUIRED_COMPANION_SECTIONS.get(procedure_key)
    if required_sections is None:
        return
    raw_confirmed = values.get("operation.confirmedCompanionSectionKeys")
    if not isinstance(raw_confirmed, str):
        raise InvalidUrologyOperationResponseError(
            "Companion section confirmations are missing"
        )
    confirmed = {key.strip() for key in raw_confirmed.split(",") if key.strip()}
    if not required_sections.issubset(confirmed):
        raise InvalidUrologyOperationResponseError(
            "Every Companion section must be explicitly reviewed"
        )
