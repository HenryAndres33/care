from django.db import transaction

from care.emr.correspondence.correction import (
    CorrespondenceCorrectionIntegrityError,
    _append_case_event,
    correction_case_integrity_valid,
    form_submission_series_head_integrity_valid,
    replacement_attempt_integrity_valid,
)
from care.emr.correspondence.delivery import (
    CorrespondenceDeliveryIntegrityError,
    delivery_frozen_integrity_valid,
    latest_delivery_event,
    lock_and_verify_delivery_ledger,
)
from care.emr.models.questionnaire import FormSubmission
from care.emr.resources.correspondence_continuity import (
    correspondence_correction_case_hash,
)
from care.emr.resources.correspondence_replacement import (
    CorrespondenceCorrectionCommandResponseSpec,
    CorrespondenceReplacementWorkflowReadSpec,
    correspondence_correction_command_hash,
)
from care_suriname.models.correspondence_correction import (
    CorrespondenceCorrectionCase,
    CorrespondenceCorrectionCommand,
    CorrespondenceCorrectionEvent,
    CorrespondencePaperReconciliationAttestation,
    FormSubmissionSeriesHead,
)
from care_suriname.models.correspondence_delivery import CorrespondenceDelivery


def serialize_replacement_attempt(attempt):
    if not attempt:
        return None
    return {
        "id": str(attempt.external_id),
        "attempt_number": attempt.attempt_number,
        "supersedes_attempt": (
            str(attempt.supersedes_attempt.external_id)
            if attempt.supersedes_attempt_id
            else None
        ),
        "source_submission": str(attempt.source_submission.external_id),
        "source_version": attempt.source_version,
        "source_snapshot_hash": attempt.source_snapshot_hash,
        "form_artifact": str(attempt.form_artifact.external_id),
        "form_artifact_hash": attempt.form_artifact_hash,
        "source_correction": str(attempt.source_correction.external_id),
        "compilation": str(attempt.compilation.external_id),
        "review": str(attempt.review.external_id),
        "initial_revision": str(attempt.initial_revision.external_id),
        "started_by": str(attempt.started_by.external_id),
        "started_at": attempt.started_at,
        "attempt_hash": attempt.attempt_hash,
    }


def serialize_replacement_workflow(case):
    attempt = case.replacement_attempt if case.replacement_attempt_id else None
    revision = case.replacement_revision if case.replacement_revision_id else None
    artifact = case.replacement_artifact if case.replacement_artifact_id else None
    delivery = case.replacement_delivery if case.replacement_delivery_id else None
    latest = latest_delivery_event(delivery) if delivery else None
    attestation = None
    if attempt:
        attestation = (
            CorrespondencePaperReconciliationAttestation._base_manager.select_related(  # noqa: SLF001
                "attested_by",
                "controlled_copy_artifact",
                "replacement_attempt",
            )
            .filter(case=case, replacement_attempt=attempt, deleted=False)
            .first()
        )
    return {
        "status": case.replacement_status,
        "attempt": serialize_replacement_attempt(attempt),
        "revision": str(revision.external_id) if revision else None,
        "revision_version": revision.resource_version if revision else None,
        "revision_hash": revision.revision_hash if revision else None,
        "revision_status": revision.status if revision else None,
        "artifact": str(artifact.external_id) if artifact else None,
        "artifact_hash": artifact.artifact_sha256 if artifact else None,
        "delivery": str(delivery.external_id) if delivery else None,
        "delivery_state": latest.event_type if latest else None,
        "delivery_certainty": latest.certainty if latest else None,
        "delivery_event_sequence": latest.sequence if latest else None,
        "delivery_event_hash": latest.event_hash if latest else None,
        "can_retry_delivery": bool(latest and latest.event_type == "failed_retryable"),
        "attestation": str(attestation.external_id) if attestation else None,
        "attestation_hash": attestation.attestation_hash if attestation else None,
        "attestation_type": attestation.attestation_type if attestation else None,
        "attested_by": (
            str(attestation.attested_by.external_id) if attestation else None
        ),
        "attested_at": attestation.attested_at if attestation else None,
    }


def command_result_snapshot(case, command_type):
    replacement = CorrespondenceReplacementWorkflowReadSpec.model_validate(
        serialize_replacement_workflow(case)
    ).model_dump(mode="json")
    return {
        "case": str(case.external_id),
        "case_hash": case.case_hash,
        "case_status": case.status,
        "case_version": case.resource_version,
        "command_type": command_type,
        "notification_status": case.notification_status,
        "paper_reconciliation_status": case.paper_reconciliation_status,
        "replacement": replacement,
        "replacement_status": case.replacement_status,
        "resolution_mode": case.resolution_mode,
    }


def command_response(command, *, replayed):
    payload = {
        "client_request_id": str(command.client_request_id),
        "replayed": replayed,
        **command.result_snapshot,
    }
    return CorrespondenceCorrectionCommandResponseSpec.model_validate(
        payload
    ).model_dump(mode="json")


def commit_case_command(
    *,
    case,
    request_spec,
    payload_hash,
    actor,
    event_type,
    safe_code,
    target_attempt=None,
    result_attempt=None,
    result_revision=None,
    result_artifact=None,
    result_delivery=None,
    result_attestation=None,
):
    previous_hash = request_spec.expected_case_hash
    case.resource_version = request_spec.expected_case_version + 1
    case.updated_by = actor
    case.case_hash = correspondence_correction_case_hash(case)
    case.save()
    snapshot = command_result_snapshot(case, request_spec.command_type)
    command = CorrespondenceCorrectionCommand(
        client_request_id=request_spec.client_request_id,
        payload_hash=payload_hash,
        command_hash="",
        command_type=request_spec.command_type,
        expected_case_version=request_spec.expected_case_version,
        expected_case_hash=request_spec.expected_case_hash,
        actor=actor,
        case=case,
        target_attempt=target_attempt,
        result_attempt=result_attempt,
        result_revision=result_revision,
        result_artifact=result_artifact,
        result_delivery=result_delivery,
        result_attestation=result_attestation,
        resulting_case_version=case.resource_version,
        resulting_case_hash=case.case_hash,
        result_snapshot=snapshot,
        created_by=actor,
        updated_by=actor,
    )
    command.command_hash = correspondence_correction_command_hash(command)
    command.save(force_insert=True)
    _append_case_event(
        case,
        event_type=event_type,
        safe_code=safe_code,
        previous_case_hash=previous_hash,
        replacement_attempt=result_attempt or target_attempt,
        command=command,
        actor=actor,
    )
    return command


@transaction.atomic
def refresh_replacement_case_for_delivery(  # noqa: PLR0912, PLR0915
    delivery_id,
) -> bool:
    reference = (
        CorrespondenceDelivery._base_manager.filter(  # noqa: SLF001
            pk=delivery_id,
            deleted=False,
            correction_case_reference__isnull=False,
        )
        .values(
            "correction_case_reference",
            "review__compilation__form_submission__series_id",
        )
        .first()
    )
    if not reference:
        return False
    head = (
        FormSubmissionSeriesHead._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related("advanced_by", "current_submission__workflow_finalized_by")
        .get(series_id=reference["review__compilation__form_submission__series_id"])
    )
    current = (
        FormSubmission._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related("workflow_finalized_by")
        .get(pk=head.current_submission_id)
    )
    head.current_submission = current
    if not form_submission_series_head_integrity_valid(head, current=current):
        raise CorrespondenceCorrectionIntegrityError

    case_id = (
        CorrespondenceCorrectionCase._base_manager.filter(  # noqa: SLF001
            external_id=reference["correction_case_reference"],
        )
        .values_list("pk", flat=True)
        .get()
    )
    case = (
        CorrespondenceCorrectionCase._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "source_head",
            "frozen_submission__workflow_finalized_by",
            "current_submission__workflow_finalized_by",
            "latest_source_correction__source_head",
            "latest_source_correction__corrected_by",
            "original_compilation__form_submission__questionnaire",
            "original_compilation__form_artifact",
            "original_review__author",
            "original_review__recipient",
            "original_delivery",
            "replacement_attempt__source_head",
            "replacement_attempt__source_correction",
            "replacement_attempt__source_submission",
            "replacement_attempt__form_artifact",
            "replacement_attempt__compilation",
            "replacement_attempt__review",
            "replacement_attempt__initial_revision__letter__review",
            "replacement_attempt__started_by",
            "replacement_compilation",
            "replacement_review",
            "replacement_revision",
            "replacement_artifact",
            "replacement_delivery",
            "resolved_by",
        )
        .get(pk=case_id)
    )
    if not correction_case_integrity_valid(case):
        raise CorrespondenceCorrectionIntegrityError
    delivery_ids = list(
        CorrespondenceDelivery._base_manager.filter(  # noqa: SLF001
            correction_case_reference=reference["correction_case_reference"],
            deleted=False,
        ).values_list("pk", flat=True)
    )
    locked_deliveries = {
        item.pk: item
        for item in CorrespondenceDelivery._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "artifact",
            "recipient",
            "revision__letter__review__compilation__form_artifact",
            "review",
            "supersedes",
        )
        .filter(pk__in={case.original_delivery_id, *delivery_ids})
        .order_by("pk")
    }
    delivery = locked_deliveries.get(delivery_id)
    if not delivery:
        return False
    command = (
        CorrespondenceCorrectionCommand._base_manager.select_related(  # noqa: SLF001
            "target_attempt",
            "result_attempt",
        )
        .filter(
            case=case,
            command_type="send_replacement",
            result_delivery=delivery,
        )
        .first()
    )
    attempt = command.result_attempt if command else None
    if (
        not attempt
        or not replacement_attempt_integrity_valid(attempt)
        or delivery.supersedes_id != case.original_delivery_id
        or delivery.correction_case_reference != case.external_id
        or not delivery_frozen_integrity_valid(delivery)
    ):
        raise CorrespondenceCorrectionIntegrityError
    try:
        lock_and_verify_delivery_ledger(delivery)
    except CorrespondenceDeliveryIntegrityError as exc:
        raise CorrespondenceCorrectionIntegrityError from exc
    latest = latest_delivery_event(delivery, lock=True)
    if not latest:
        raise CorrespondenceCorrectionIntegrityError
    if CorrespondenceCorrectionEvent._base_manager.filter(  # noqa: SLF001
        case=case,
        delivery_event=latest,
    ).exists():
        return False

    previous_hash = case.case_hash
    if case.replacement_attempt_id == attempt.id and case.status == "open":
        case.replacement_delivery_state = latest.event_type
        case.replacement_delivery_certainty = latest.certainty
        if latest.event_type == "acknowledged":
            case.replacement_status = "acknowledged"
            case.notification_status = "acknowledged"
        elif latest.event_type == "failed_terminal":
            case.replacement_status = "failed"
            case.notification_status = "failed"
        elif latest.event_type == "failed_retryable":
            case.replacement_status = "delivery_pending"
            case.notification_status = "pending"
        elif latest.event_type == "outcome_unknown":
            case.replacement_status = "delivery_pending"
            case.notification_status = "unknown"
        else:
            case.replacement_status = "delivery_pending"
            case.notification_status = "pending"
    case.resource_version += 1
    case.updated_by = None
    case.case_hash = correspondence_correction_case_hash(case)
    case.save()
    _append_case_event(
        case,
        event_type="replacement_delivery_classified",
        safe_code=f"replacement_{latest.event_type}",
        previous_case_hash=previous_hash,
        delivery_event=latest,
        replacement_attempt=attempt,
    )
    return True
