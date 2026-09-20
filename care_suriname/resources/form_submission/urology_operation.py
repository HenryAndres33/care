from __future__ import annotations

from typing import Any

UROLOGY_OPERATIONS_QUESTIONNAIRE = "urology-operaties"
OPERATION_SCHEMA = "care.urology.operation-documentation"


class InvalidUrologyOperationResponseError(ValueError):
    pass


def validate_urology_operation_response_dump(response_dump: Any) -> None:
    """Reject an operation finalization whose content cannot stand as a record.

    Finalize/amend commands set ``operation.clinicalConfirmation`` themselves;
    the clinician's explicit act is pressing finalize, not a separate checkbox.
    """

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
            "Finalized operation content must carry clinicalConfirmation"
        )
