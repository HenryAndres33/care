from copy import deepcopy

from care.emr.models.observation import Observation
from care_suriname.resources.laboratory_commands.errors import conflict
from care_suriname.resources.laboratory_commands.hashing import canonical_sha256
from care_suriname.resources.laboratory_commands.observations import (
    observation_to_row,
)

COMMAND_META_KEY = "laboratory_command"


def command_state(report) -> dict:
    return (report.meta or {}).get("care_suriname", {}).get(COMMAND_META_KEY, {})


def set_command_state(report, state: dict):
    meta = deepcopy(report.meta or {})
    namespace = dict(meta.get("care_suriname", {}))
    namespace[COMMAND_META_KEY] = state
    meta["care_suriname"] = namespace
    report.meta = meta


def report_source(report) -> dict:
    source = command_state(report).get("source")
    if not source:
        raise conflict("aggregate_drift", "Laboratory report source is missing.")
    return source


def report_audit(report) -> dict:
    audit = command_state(report).get("audit")
    if not audit:
        raise conflict("aggregate_drift", "Laboratory report audit is missing.")
    return audit


def aggregate_fingerprint(report) -> str:
    service_request = report.service_request
    observations = Observation._base_manager.filter(  # noqa: SLF001
        diagnostic_report=report
    ).order_by("external_id")
    return canonical_sha256(
        {
            "contract": "laboratory-report-aggregate-v1",
            "service_request": {
                "id": service_request.external_id,
                "deleted": service_request.deleted,
                "patient": service_request.patient.external_id,
                "facility": service_request.facility.external_id,
                "encounter": service_request.encounter.external_id,
                "status": service_request.status,
                "intent": service_request.intent,
                "priority": service_request.priority,
                "category": service_request.category,
                "title": service_request.title,
                "code": service_request.code,
                "note": service_request.note,
                "occurance": service_request.occurance,
                "requester": (
                    service_request.requester.external_id
                    if service_request.requester_id
                    else None
                ),
                "meta": service_request.meta,
            },
            "report": {
                "id": report.external_id,
                "deleted": report.deleted,
                "patient": report.patient.external_id,
                "facility": report.facility.external_id,
                "encounter": report.encounter.external_id,
                "status": report.status,
                "category": report.category,
                "code": report.code,
                "note": report.note,
                "conclusion": report.conclusion,
                "meta": _clinical_report_meta(report.meta),
            },
            "observations": [
                _observation_fingerprint_data(item) for item in observations
            ],
        }
    )


def verify_aggregate(report):
    state = command_state(report)
    stored = state.get("aggregate_fingerprint")
    if not stored or stored != aggregate_fingerprint(report):
        raise conflict(
            "aggregate_drift",
            "Native laboratory resources changed outside the command path.",
        )


def report_payload(report, *, include_history=False) -> dict:
    observations = Observation._base_manager.filter(  # noqa: SLF001
        diagnostic_report=report,
        deleted=False,
    ).order_by("created_date", "external_id")
    if not include_history:
        observations = observations.exclude(status="entered_in_error")
    return {
        "id": str(report.external_id),
        "service_request_id": str(report.service_request.external_id),
        "patient": str(report.patient.external_id),
        "facility": str(report.facility.external_id),
        "encounter": str(report.encounter.external_id),
        "status": report.status,
        "service_request_status": report.service_request.status,
        "source": report_source(report),
        "audit": report_audit(report),
        "rows": [observation_to_row(item) for item in observations],
    }


def _clinical_report_meta(meta):
    cleaned = deepcopy(meta or {})
    namespace = cleaned.get("care_suriname")
    if isinstance(namespace, dict):
        command = namespace.get(COMMAND_META_KEY)
        if isinstance(command, dict):
            command.pop("aggregate_fingerprint", None)
            command.pop("last_command", None)
    return cleaned


def _observation_fingerprint_data(observation):
    return {
        "id": observation.external_id,
        "deleted": observation.deleted,
        "status": observation.status,
        "patient": observation.patient.external_id,
        "encounter": observation.encounter.external_id,
        "definition": (
            observation.observation_definition.external_id
            if observation.observation_definition_id
            else None
        ),
        "category": observation.category,
        "main_code": observation.main_code,
        "subject_type": observation.subject_type,
        "subject_id": observation.subject_id,
        "effective_datetime": observation.effective_datetime,
        "data_entered_by": (
            observation.data_entered_by.external_id
            if observation.data_entered_by_id
            else None
        ),
        "value_type": observation.value_type,
        "value": observation.value,
        "note": observation.note,
        "body_site": observation.body_site,
        "method": observation.method,
        "reference_range": observation.reference_range,
        "interpretation": observation.interpretation,
        "parent": observation.parent,
        "meta": observation.meta,
    }
