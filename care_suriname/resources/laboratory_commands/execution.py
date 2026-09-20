from django.db import transaction
from rest_framework.exceptions import PermissionDenied

from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.observation import Observation
from care.emr.models.service_request import ServiceRequest
from care.security.authorization.base import AuthorizationController
from care_suriname.resources.form_submission.note_labs import LAB_CATEGORY
from care_suriname.resources.laboratory_commands.catalogue import (
    validate_definition,
)
from care_suriname.resources.laboratory_commands.errors import conflict
from care_suriname.resources.laboratory_commands.hashing import (
    canonical_laboratory_command_hash,
)
from care_suriname.resources.laboratory_commands.integrity import (
    lock_stored_definitions,
    prelock_report_resources,
    validate_corrected_collection_groups,
    validate_stored_definitions,
)
from care_suriname.resources.laboratory_commands.locking import (
    authorize_mutation,
    authorize_patient_reference,
    lock_command_context,
    lock_definitions,
    lock_observations,
)
from care_suriname.resources.laboratory_commands.observations import apply_result_row
from care_suriname.resources.laboratory_commands.projection import (
    command_state,
    verify_aggregate,
)
from care_suriname.resources.laboratory_commands.receipts import (
    command_response,
    record_success,
    replay_or_conflict,
    report_title,
    set_source,
    shared_observation_occurrence,
    shared_occurrence,
    source_note,
    store_initial_state,
    update_audit,
)
from care_suriname.resources.laboratory_commands.specs import (
    ExternalLaboratorySource,
)


def execute_laboratory_command(command, actor) -> tuple[dict, int]:
    authorize_patient_reference(actor, command.patient)
    payload_hash = canonical_laboratory_command_hash(
        command, actor_id=actor.external_id
    )
    with transaction.atomic():
        return _execute_locked(command, actor, payload_hash)


def _execute_locked(command, actor, payload_hash):
    context = lock_command_context(
        command,
        require_existing=command.action != "create_draft",
    )
    creating = command.action == "create_draft" and context.report is None
    authorize_mutation(actor, context, creating=creating)
    if context.report:
        prelock_report_resources(context.report, command)
        verify_aggregate(context.report)
        replay = replay_or_conflict(context.report, command, actor, payload_hash)
        if replay:
            return replay, 200
    if command.action == "create_draft":
        report = _create_draft(context, command, actor)
    elif command.action == "update_draft":
        report = _update_draft(context, command, actor)
    elif command.action == "finalize":
        report = _finalize(context, actor)
    else:
        report = _correct(context, command, actor)
    version = record_success(report, command, actor, payload_hash)
    data = command_response(report, command.client_request_id, version, replayed=False)
    return data, 201 if command.action == "create_draft" else 200


def _create_draft(context, command, actor):
    if context.report or context.service_request:
        raise conflict("row_identity_conflict", "Laboratory aggregate ID collision.")
    service_request = ServiceRequest(
        external_id=command.service_request_id,
        facility=context.encounter.facility,
        patient=context.patient,
        encounter=context.encounter,
        title=report_title(command.source.label),
        category="laboratory",
        status="draft",
        intent="order",
        priority="routine",
        code=None,
        note=source_note(command.source.label),
        occurance=shared_occurrence(command.rows),
        requester=actor,
        created_by=actor,
        updated_by=actor,
    )
    for permission in ("can_write_service_request", "can_write_diagnostic_report"):
        if not AuthorizationController.call(permission, actor, service_request):
            raise PermissionDenied("Cannot create this laboratory report")
    service_request.save(force_insert=True)
    report = DiagnosticReport.objects.create(
        external_id=command.report_id,
        facility=context.encounter.facility,
        patient=context.patient,
        encounter=context.encounter,
        service_request=service_request,
        status="preliminary",
        category=LAB_CATEGORY,
        code=None,
        note=source_note(command.source.label),
        created_by=actor,
        updated_by=actor,
    )
    store_initial_state(report, command.source, actor)
    _apply_snapshot(report, command.rows, command.source, actor)
    return report


def _update_draft(context, command, actor):
    report = context.report
    if report.status != "preliminary" or context.service_request.status != "draft":
        raise conflict("state_conflict", "Only a draft report can be updated.")
    source = command.source
    report.note = source_note(source.label)
    report.updated_by = actor
    report.save(update_fields=["note", "updated_by", "modified_date"])
    request = context.service_request
    request.title = report_title(source.label)
    request.note = source_note(source.label)
    request.occurance = shared_occurrence(command.rows)
    request.updated_by = actor
    request.save(
        update_fields=["title", "note", "occurance", "updated_by", "modified_date"]
    )
    report.service_request = request
    set_source(report, source)
    _apply_snapshot(report, command.rows, source, actor)
    return report


def _finalize(context, actor):
    report = context.report
    if report.status != "preliminary" or context.service_request.status != "draft":
        raise conflict("state_conflict", "Only a draft report can be finalized.")
    definitions = lock_stored_definitions(report)
    observations = [item for item in lock_observations(report) if not item.deleted]
    if not observations:
        raise conflict("state_conflict", "An empty report cannot be finalized.")
    validate_stored_definitions(report, observations, definitions)
    report.status = "final"
    report.updated_by = actor
    report.save(update_fields=["status", "updated_by", "modified_date"])
    context.service_request.status = "completed"
    context.service_request.updated_by = actor
    context.service_request.save(
        update_fields=["status", "updated_by", "modified_date"]
    )
    report.service_request = context.service_request
    update_audit(report, "finalized", actor)
    return report


def _correct(context, command, actor):
    report = context.report
    if report.status != "final" or context.service_request.status != "completed":
        raise conflict("state_conflict", "Only a final report can be corrected.")
    definitions = lock_definitions(
        report.facility,
        [replacement.row for replacement in command.replacements],
    )
    existing = {item.external_id: item for item in lock_observations(report)}
    validate_corrected_collection_groups(existing, command)
    source = ExternalLaboratorySource.model_validate(command_state(report)["source"])
    for replacement in command.replacements:
        target = existing.get(replacement.replaces_observation_id)
        if not target or target.deleted or target.status not in {"final", "amended"}:
            raise conflict(
                "correction_conflict",
                "Correction target is not the current visible result.",
            )
        if Observation._base_manager.filter(  # noqa: SLF001
            external_id=replacement.row.row_id
        ).exists():
            raise conflict(
                "row_identity_conflict", "Replacement row ID already exists."
            )
        definition = definitions[replacement.row.definition.id]
        validate_definition(
            definition, replacement.row.definition, replacement.row.value
        )
        target.status = "entered_in_error"
        target.updated_by = actor
        target.save(update_fields=["status", "updated_by", "modified_date"])
        apply_result_row(
            Observation(),
            row=replacement.row,
            definition=definition,
            report=report,
            source=source,
            actor=actor,
            status="amended",
            parent=target.external_id,
            correction_reason=command.reason,
        )
    current = Observation._base_manager.filter(  # noqa: SLF001
        diagnostic_report=report,
        deleted=False,
    ).exclude(status="entered_in_error")
    context.service_request.occurance = shared_observation_occurrence(current)
    context.service_request.updated_by = actor
    context.service_request.save(
        update_fields=["occurance", "updated_by", "modified_date"]
    )
    report.service_request = context.service_request
    update_audit(report, "latest_corrected", actor)
    return report


def _apply_snapshot(report, rows, source, actor):
    definitions = lock_definitions(report.facility, rows)
    existing = {item.external_id: item for item in lock_observations(report)}
    row_ids = {row.row_id for row in rows}
    collisions = Observation._base_manager.filter(  # noqa: SLF001
        external_id__in=row_ids
    ).exclude(diagnostic_report=report)
    if collisions.exists():
        raise conflict("row_identity_conflict", "Laboratory row ID already exists.")
    for row in rows:
        definition = definitions[row.definition.id]
        validate_definition(definition, row.definition, row.value)
        apply_result_row(
            existing.get(row.row_id, Observation()),
            row=row,
            definition=definition,
            report=report,
            source=source,
            actor=actor,
        )
    for external_id, observation in existing.items():
        if external_id not in row_ids and not observation.deleted:
            observation.deleted = True
            observation.updated_by = actor
            observation.save(update_fields=["deleted", "updated_by", "modified_date"])
