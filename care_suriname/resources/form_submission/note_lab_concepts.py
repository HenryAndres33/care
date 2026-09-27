"""Cancel the lab concepts of a note that is entered in error.

Owner decision, 27 September 2026: a lab result entered from inside a note is a
concept (draft report) until the note is made final. The draft note lists those
concepts in `content.clinicalActions.staged` (frontend
`forms/persistence/formNoteStagedItems.ts`). When the note is discarded, each
concept that is still a draft is cancelled through the governed laboratory
`discard_draft` command, so every laboratory safeguard applies. A concept that
already became a result (finalized elsewhere) is left alone.
"""

from uuid import uuid4

from care.emr.models.diagnostic_report import DiagnosticReport
from care_suriname.resources.laboratory_commands.errors import LaboratoryCommandError
from care_suriname.resources.laboratory_commands.execution import (
    execute_laboratory_command,
)
from care_suriname.resources.laboratory_commands.projection import command_state
from care_suriname.resources.laboratory_commands.specs import (
    LABORATORY_COMMAND_CONTRACT,
    validate_laboratory_command,
)


def _staged_concept_report_ids(note):
    dump = note.response_dump if isinstance(note.response_dump, dict) else {}
    content = dump.get("content") if isinstance(dump.get("content"), dict) else {}
    actions = content.get("clinicalActions")
    staged = actions.get("staged") if isinstance(actions, dict) else None
    if not isinstance(staged, list):
        return []
    return [
        item["reportId"]
        for item in staged
        if isinstance(item, dict)
        and item.get("kind") == "lab-report"
        and item.get("state") == "concept"
        and isinstance(item.get("reportId"), str)
    ]


def discard_lab_concepts_of_discarded_note(note, actor):
    report_ids = _staged_concept_report_ids(note)
    if not report_ids or not note.encounter_id:
        return 0
    reports = DiagnosticReport.objects.filter(
        external_id__in=report_ids,
        encounter_id=note.encounter_id,
        status="preliminary",
    ).select_related("service_request", "patient", "facility", "encounter")
    discarded = 0
    for report in reports:
        command = validate_laboratory_command(
            {
                "contract": LABORATORY_COMMAND_CONTRACT,
                "action": "discard_draft",
                "client_request_id": str(uuid4()),
                "patient": str(report.patient.external_id),
                "facility": str(report.facility.external_id),
                "encounter": str(report.encounter.external_id),
                "service_request_id": str(report.service_request.external_id),
                "report_id": str(report.external_id),
                "expected_version": command_state(report).get("version"),
            }
        )
        try:
            execute_laboratory_command(command, actor)
        except LaboratoryCommandError:
            # Changed meanwhile (finalized, closed visit): not ours to cancel.
            continue
        discarded += 1
    return discarded
