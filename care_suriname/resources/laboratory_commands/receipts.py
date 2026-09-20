from copy import deepcopy

from django.utils import timezone

from care_suriname.resources.laboratory_commands.errors import conflict
from care_suriname.resources.laboratory_commands.projection import (
    aggregate_fingerprint,
    command_state,
    report_payload,
    set_command_state,
)
from care_suriname.resources.laboratory_commands.responses import (
    LaboratoryCommandResponse,
)
from care_suriname.resources.laboratory_commands.specs import (
    LABORATORY_COMMAND_CONTRACT,
)


def record_success(report, command, actor, payload_hash):
    state = deepcopy(command_state(report))
    version = int(state.get("version", 0)) + 1
    state.update(
        {
            "contract": "v1",
            "version": version,
            "last_command": {
                "id": str(command.client_request_id),
                "payload_hash": payload_hash,
                "actor": str(actor.external_id),
                "action": command.action,
                "result_version": version,
            },
        }
    )
    set_command_state(report, state)
    report.save(update_fields=["meta", "modified_date"])
    state["aggregate_fingerprint"] = aggregate_fingerprint(report)
    set_command_state(report, state)
    report.save(update_fields=["meta", "modified_date"])
    return version


def replay_or_conflict(report, command, actor, payload_hash):
    state = command_state(report)
    latest = state.get("last_command", {})
    if latest.get("id") == str(command.client_request_id):
        expected = {
            "id": str(command.client_request_id),
            "payload_hash": payload_hash,
            "actor": str(actor.external_id),
            "action": command.action,
            "result_version": state.get("version"),
        }
        if latest != expected:
            raise conflict("idempotency_conflict", "Command ID was reused.")
        return command_response(
            report,
            command.client_request_id,
            state["version"],
            replayed=True,
        )
    if command.expected_version != state.get("version"):
        raise conflict("version_conflict", "Expected report version is stale.")
    return None


def command_response(report, client_request_id, version, *, replayed):
    payload = {
        "contract": LABORATORY_COMMAND_CONTRACT,
        "client_request_id": client_request_id,
        "replayed": replayed,
        "command_result_version": version,
        "report": report_payload(report),
    }
    return LaboratoryCommandResponse.model_validate(payload).model_dump(mode="json")


def store_initial_state(report, source, actor):
    set_command_state(
        report,
        {
            "contract": "v1",
            "version": 0,
            "source": source.model_dump(mode="json"),
            "audit": {
                "created_by": actor_snapshot(actor),
                "created_at": report.created_date.isoformat(),
                "finalized_by": None,
                "finalized_at": None,
                "latest_corrected_by": None,
                "latest_corrected_at": None,
            },
        },
    )
    report.save(update_fields=["meta", "modified_date"])


def set_source(report, source):
    state = deepcopy(command_state(report))
    state["source"] = source.model_dump(mode="json")
    set_command_state(report, state)
    report.save(update_fields=["meta", "modified_date"])


def update_audit(report, prefix, actor):
    state = deepcopy(command_state(report))
    audit = dict(state["audit"])
    audit[f"{prefix}_by"] = actor_snapshot(actor)
    audit[f"{prefix}_at"] = timezone.now().isoformat()
    state["audit"] = audit
    set_command_state(report, state)
    report.save(update_fields=["meta", "modified_date"])


def shared_occurrence(rows):
    known = [row.collected_at.value for row in rows if row.collected_at.kind == "known"]
    return known[0] if len(known) == len(rows) and len(set(known)) == 1 else None


def shared_observation_occurrence(observations):
    dates = [item.effective_datetime for item in observations]
    return dates[0] if dates and all(dates) and len(set(dates)) == 1 else None


def report_title(source_label):
    return f"Extern labrapport — {source_label}"


def source_note(source_label):
    return f"Extern laboratorium: {source_label}."


def actor_snapshot(actor):
    display = actor.full_name.strip() or actor.username
    return {"id": str(actor.external_id), "display": display}
