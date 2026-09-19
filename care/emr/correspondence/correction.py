import json
import re
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from care.emr.correspondence.letter import (
    correspondence_revision_artifact_status,
    correspondence_revision_frozen_integrity_valid,
)
from care.emr.correspondence.review import (
    reviewed_binding_frozen_integrity_valid,
)
from care.emr.correspondence.source import (
    compilation_frozen_integrity_valid as source_compilation_frozen_integrity_valid,
)
from care.emr.models.questionnaire import FormSubmission
from care.emr.models.report.report_upload import ReportUpload
from care.emr.reports.form_submission_artifact import (
    MalformedFinalizedSnapshotError,
    validate_response_dump,
)
from care.emr.resources.correspondence import canonical_sha256
from care.emr.resources.correspondence_continuity import (
    MAX_CORRESPONDENCE_CONTINUITY_CHANGES,
    MAX_CORRESPONDENCE_CONTINUITY_VALUE_CHARACTERS,
    SAFE_CORRESPONDENCE_REFERENCE,
    correspondence_correction_case_hash,
    correspondence_correction_change_set_hash,
    correspondence_correction_event_hash,
)
from care.emr.resources.correspondence_correction import (
    correspondence_source_correction_hash,
    form_submission_series_head_hash,
    form_submission_series_head_snapshot_hash,
)
from care.emr.resources.correspondence_replacement import (
    correspondence_correction_command_hash,
    correspondence_paper_attestation_hash,
    correspondence_replacement_attempt_hash,
)
from care.emr.resources.form_submission.commands import (
    finalized_form_submission_snapshot_hash,
)
from care.emr.resources.form_submission.spec import FormSubmissionStatusChoices
from care_suriname.models.correspondence import CorrespondenceCompilation
from care_suriname.models.correspondence_correction import (
    CorrespondenceCorrectionCase,
    CorrespondenceCorrectionCommand,
    CorrespondenceCorrectionEvent,
    CorrespondenceCorrectionOutbox,
    CorrespondencePaperReconciliationAttestation,
    CorrespondenceReplacementAttempt,
    CorrespondenceSourceCorrection,
    FormSubmissionSeriesHead,
)
from care_suriname.models.correspondence_delivery import CorrespondenceDelivery
from care_suriname.models.correspondence_letter import (
    CorrespondenceLetter,
    CorrespondenceLetterRevision,
)
from care_suriname.models.correspondence_review import CorrespondenceReview

MAX_AFFECTED_CORRESPONDENCE_BRANCHES = 500
MAX_DISPLAY_LABEL_CHARACTERS = 255
SHA256_HEX_LENGTH = 64
SAFE_CORRECTION_CODE = re.compile(r"^[a-z0-9_:-]{1,64}$")
DELIVERY_CERTAINTY = {
    "dispatch_pending": "not_attempted",
    "dispatching": "attempting",
    "acknowledged": "acknowledged",
    "failed_retryable": "not_delivered",
    "failed_terminal": "not_delivered",
    "outcome_unknown": "unknown",
}


class CorrespondenceCorrectionIntegrityError(ValueError):
    pass


class CorrespondenceCorrectionPendingError(ValueError):
    pass


@dataclass(frozen=True)
class CorrespondenceChangeSet:
    display_changes: list[dict]
    full_hash: str


class FormSubmissionSeriesHeadIntegrityError(ValueError):
    pass


def create_finalized_form_series_head(*, submission, actor):
    """Create the first finalized source head while the draft row is locked."""
    if (
        not _finalized_submission_integrity_valid(submission)
        or submission.workflow_finalized_by_id != actor.id
    ):
        raise FormSubmissionSeriesHeadIntegrityError
    if FormSubmissionSeriesHead._base_manager.filter(  # noqa: SLF001
        series_id=submission.series_id
    ).exists():
        raise FormSubmissionSeriesHeadIntegrityError
    head = FormSubmissionSeriesHead(
        series_id=submission.series_id,
        current_submission=submission,
        current_version=submission.resource_version,
        current_snapshot_hash=submission.finalized_snapshot_hash,
        advanced_at=submission.workflow_finalized_at,
        advanced_by=actor,
        head_hash="",
        created_by=actor,
        updated_by=actor,
    )
    head.head_hash = form_submission_series_head_hash(head)
    head.save(force_insert=True)
    return head


def lock_current_finalized_form_series(target):
    """Lock head first, then current source, and prove the route target is current."""
    head = (
        FormSubmissionSeriesHead._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related("advanced_by")
        .filter(series_id=target.series_id, deleted=False)
        .first()
    )
    if not head:
        raise FormSubmissionSeriesHeadIntegrityError
    current = (
        FormSubmission._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "questionnaire",
            "patient",
            "encounter",
            "previous_version",
            "created_by",
            "updated_by",
            "workflow_finalized_by",
        )
        .get(pk=head.current_submission_id)
    )
    head.current_submission = current
    if not form_submission_series_head_integrity_valid(head, current=current):
        raise FormSubmissionSeriesHeadIntegrityError
    return head, current


def advance_finalized_form_series(*, head, previous, result, actor):
    """Advance the source and atomically enqueue its immutable correction fact."""
    if (
        head.current_submission_id != previous.id
        or head.current_version != previous.resource_version
        or head.current_snapshot_hash != previous.finalized_snapshot_hash
        or not form_submission_series_head_integrity_valid(head, current=previous)
        or result.previous_version_id != previous.id
        or result.series_id != head.series_id
        or result.resource_version != previous.resource_version + 1
        or not _finalized_submission_integrity_valid(result)
        or result.workflow_finalized_by_id != actor.id
    ):
        raise FormSubmissionSeriesHeadIntegrityError

    advanced_at = result.workflow_finalized_at
    head.current_submission = result
    head.current_version = result.resource_version
    head.current_snapshot_hash = result.finalized_snapshot_hash
    head.advanced_at = advanced_at
    head.advanced_by = actor
    head.updated_by = actor
    head.head_hash = form_submission_series_head_hash(head)
    head.save(
        update_fields=[
            "current_submission",
            "current_version",
            "current_snapshot_hash",
            "advanced_at",
            "advanced_by",
            "updated_by",
            "head_hash",
            "modified_date",
        ]
    )

    previous_sequence = (
        CorrespondenceSourceCorrection._base_manager.filter(source_head=head)  # noqa: SLF001
        .order_by("-sequence")
        .values_list("sequence", flat=True)
        .first()
    )
    correction = CorrespondenceSourceCorrection(
        source_head=head,
        sequence=(previous_sequence or 0) + 1,
        source_head_hash=head.head_hash,
        previous_submission=previous,
        new_submission=result,
        previous_version=previous.resource_version,
        new_version=result.resource_version,
        previous_snapshot_hash=previous.finalized_snapshot_hash,
        new_snapshot_hash=result.finalized_snapshot_hash,
        amendment_type=result.amendment_type,
        reason=result.amendment_reason,
        corrected_by=actor,
        corrected_at=advanced_at,
        correction_hash="",
        created_by=actor,
        updated_by=actor,
    )
    correction.correction_hash = correspondence_source_correction_hash(correction)
    correction.save(force_insert=True)
    CorrespondenceCorrectionOutbox.objects.create(
        source_correction=correction,
        status="pending",
        attempt_count=0,
        available_at=advanced_at,
        created_by=actor,
        updated_by=actor,
    )
    return correction


def form_submission_series_head_integrity_valid(head, *, current=None):
    current = current or head.current_submission
    try:
        return all(
            [
                not head.deleted,
                not current.deleted,
                head.series_id == current.series_id,
                head.current_submission_id == current.id,
                head.current_version == current.resource_version,
                head.current_snapshot_hash == current.finalized_snapshot_hash,
                head.advanced_by_id == current.workflow_finalized_by_id,
                head.updated_by_id == head.advanced_by_id,
                head.advanced_at == current.workflow_finalized_at,
                _finalized_submission_integrity_valid(
                    current,
                    allow_entered_in_error=True,
                ),
                form_submission_series_head_hash(head) == head.head_hash,
            ]
        )
    except (AttributeError, TypeError, ValueError):
        return False


def source_correction_integrity_valid(correction):
    try:
        previous = correction.previous_submission
        result = correction.new_submission
        return all(
            [
                not correction.deleted,
                not correction.source_head.deleted,
                not previous.deleted,
                not result.deleted,
                correction.source_head.series_id == previous.series_id,
                previous.series_id == result.series_id,
                previous.patient_id == result.patient_id,
                previous.encounter_id == result.encounter_id,
                previous.questionnaire_id == result.questionnaire_id,
                correction.previous_version == previous.resource_version,
                correction.new_version == result.resource_version,
                correction.new_version == correction.previous_version + 1,
                correction.previous_snapshot_hash == previous.finalized_snapshot_hash,
                correction.new_snapshot_hash == result.finalized_snapshot_hash,
                result.previous_version_id == previous.id,
                correction.amendment_type == result.amendment_type,
                correction.reason == result.amendment_reason,
                correction.corrected_by_id == result.workflow_finalized_by_id,
                correction.created_by_id == correction.corrected_by_id,
                correction.updated_by_id == correction.corrected_by_id,
                correction.corrected_at == result.workflow_finalized_at,
                _finalized_submission_integrity_valid(previous),
                _finalized_submission_integrity_valid(result),
                correction.source_head_hash
                == form_submission_series_head_snapshot_hash(
                    series_id=result.series_id,
                    current_submission_id=result.external_id,
                    current_version=result.resource_version,
                    current_snapshot_hash=result.finalized_snapshot_hash,
                    advanced_at=result.workflow_finalized_at,
                    advanced_by_id=result.workflow_finalized_by.external_id,
                ),
                correspondence_source_correction_hash(correction)
                == correction.correction_hash,
            ]
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _finalized_submission_integrity_valid(
    submission,
    *,
    allow_entered_in_error=False,
):
    status_is_valid = submission.status == FormSubmissionStatusChoices.submitted.value
    if allow_entered_in_error:
        status_is_valid = status_is_valid or bool(
            submission.status == FormSubmissionStatusChoices.entered_in_error.value
            and submission.entered_in_error_at
            and submission.entered_in_error_by_id
            and submission.entered_in_error_reason.strip()
        )
    return bool(
        status_is_valid
        and submission.workflow_finalized_at
        and submission.workflow_finalized_by_id
        and submission.finalized_snapshot_hash
        and finalized_form_submission_snapshot_hash(submission)
        == submission.finalized_snapshot_hash
    )


def build_correspondence_change_set(
    frozen: FormSubmission,
    current: FormSubmission,
) -> CorrespondenceChangeSet:
    """Build a bounded deterministic display diff and hash the complete diff."""
    try:
        validate_response_dump(frozen.response_dump)
        validate_response_dump(current.response_dump)
    except (MalformedFinalizedSnapshotError, RecursionError) as exc:
        raise CorrespondenceCorrectionIntegrityError from exc
    if not _finalized_submission_integrity_valid(
        frozen
    ) or not _finalized_submission_integrity_valid(current):
        raise CorrespondenceCorrectionIntegrityError
    if any(
        [
            frozen.series_id != current.series_id,
            frozen.patient_id != current.patient_id,
            frozen.encounter_id != current.encounter_id,
            frozen.questionnaire_id != current.questionnaire_id,
            frozen.resource_version >= current.resource_version,
        ]
    ):
        raise CorrespondenceCorrectionIntegrityError

    changes = [
        {
            "current_value": str(current.resource_version),
            "display_label": "Form resource version",
            "field_reference": "form.resource_version",
            "previous_value": str(frozen.resource_version),
        }
    ]
    if frozen.amendment_type != current.amendment_type:
        changes.append(
            _change(
                "form.amendment_type",
                "Amendment type",
                frozen.amendment_type or None,
                current.amendment_type or None,
            )
        )
    if frozen.amendment_reason != current.amendment_reason:
        changes.append(
            _change(
                "form.amendment_reason",
                "Amendment reason",
                frozen.amendment_reason or None,
                current.amendment_reason or None,
            )
        )

    leaves: list[tuple[tuple[str, ...], object]] = []
    current_leaves: list[tuple[tuple[str, ...], object]] = []
    _flatten_json(frozen.response_dump, (), leaves)
    _flatten_json(current.response_dump, (), current_leaves)
    before = dict(leaves)
    after = dict(current_leaves)
    for path in sorted(set(before) | set(after)):
        previous = before.get(path, _MISSING)
        value = after.get(path, _MISSING)
        if previous == value:
            continue
        changes.append(
            _change(
                _safe_field_reference(path),
                _display_label(path),
                _display_value(previous),
                _display_value(value),
            )
        )

    references = [change["field_reference"] for change in changes]
    if len(references) != len(set(references)):
        raise CorrespondenceCorrectionIntegrityError
    full_hash = correspondence_correction_change_set_hash(changes)
    if len(changes) <= MAX_CORRESPONDENCE_CONTINUITY_CHANGES:
        display = changes
    else:
        kept = changes[: MAX_CORRESPONDENCE_CONTINUITY_CHANGES - 1]
        kept.append(
            {
                "current_value": (
                    f"{len(changes) - len(kept)} additional change(s); "
                    f"complete change-set SHA-256 {full_hash}"
                ),
                "display_label": "Additional source changes",
                "field_reference": "continuity.synthetic/overflow",
                "previous_value": None,
            }
        )
        display = kept
    return CorrespondenceChangeSet(display_changes=display, full_hash=full_hash)


class _Missing:
    pass


_MISSING = _Missing()


def _flatten_json(value, path, result):
    if isinstance(value, dict):
        if not value:
            result.append((path, {}))
            return
        for key in sorted(value):
            _flatten_json(value[key], (*path, str(key)), result)
        return
    if isinstance(value, list):
        if not value:
            result.append((path, []))
            return
        for index, item in enumerate(value):
            _flatten_json(item, (*path, str(index)), result)
        return
    result.append((path, value))


def _safe_field_reference(path):
    encoded = tuple(segment.replace("~", "~0").replace("/", "~1") for segment in path)
    candidate = "form.responses/" + "/".join(encoded or ("root",))
    if SAFE_CORRESPONDENCE_REFERENCE.fullmatch(candidate):
        return candidate
    return "form.responses/" + canonical_sha256({"path": list(path)})


def _display_label(path):
    value = " / ".join(path) if path else "Form responses"
    value = " ".join(value.split())
    if value and len(value) <= MAX_DISPLAY_LABEL_CHARACTERS:
        return value
    return f"Clinical field {canonical_sha256({'path': list(path)})[:12]}"


def _display_value(value):
    if isinstance(value, _Missing):
        return None
    if value is None:
        return "null"
    if isinstance(value, str):
        rendered = value
    else:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    if len(rendered) <= MAX_CORRESPONDENCE_CONTINUITY_VALUE_CHARACTERS:
        return rendered
    digest = canonical_sha256({"value": value})
    return f"Value too large for inline display; SHA-256 {digest}"


def _change(field_reference, display_label, previous_value, current_value):
    if previous_value == current_value:
        raise CorrespondenceCorrectionIntegrityError
    return {
        "current_value": current_value,
        "display_label": display_label,
        "field_reference": field_reference,
        "previous_value": previous_value,
    }


def compilation_frozen_integrity_valid(compilation) -> bool:
    """Compatibility wrapper around the canonical frozen-source validator."""
    return source_compilation_frozen_integrity_valid(compilation)


def review_frozen_integrity_valid(review) -> bool:
    return reviewed_binding_frozen_integrity_valid(review)


def final_revision_frozen_integrity_valid(revision, artifact) -> bool:
    return bool(
        correspondence_revision_frozen_integrity_valid(revision)
        and correspondence_revision_artifact_status(
            revision,
            artifact=artifact,
        )
        != "integrity_failed"
    )


def correction_case_integrity_valid(case) -> bool:
    try:
        if any(
            [
                case.deleted,
                case.source_head.series_id != case.frozen_submission.series_id,
                case.source_head.series_id != case.current_submission.series_id,
                case.original_compilation.form_submission_id
                != case.frozen_submission_id,
                case.original_review.compilation_id != case.original_compilation_id,
                case.original_delivery.review_id != case.original_review_id,
                case.frozen_version != case.frozen_submission.resource_version,
                case.frozen_snapshot_hash
                != case.frozen_submission.finalized_snapshot_hash,
                case.current_version != case.current_submission.resource_version,
                case.current_snapshot_hash
                != case.current_submission.finalized_snapshot_hash,
                case.latest_source_correction.new_submission_id
                != case.current_submission_id,
                case.source_head_hash != case.latest_source_correction.source_head_hash,
                case.latest_source_correction_hash
                != case.latest_source_correction.correction_hash,
                not source_correction_integrity_valid(case.latest_source_correction),
                build_correspondence_change_set(
                    case.frozen_submission,
                    case.current_submission,
                ).full_hash
                != case.change_set_hash,
                correspondence_correction_case_hash(case) != case.case_hash,
            ]
        ):
            return False
        attempts = list(
            CorrespondenceReplacementAttempt._base_manager.filter(case=case)  # noqa: SLF001
            .select_related(
                "case__source_head",
                "source_head",
                "source_correction__source_head",
                "source_submission",
                "form_artifact",
                "compilation",
                "review",
                "initial_revision__letter__review",
                "started_by",
                "created_by",
                "updated_by",
                "supersedes_attempt",
            )
            .order_by("attempt_number")
        )
        if not _replacement_attempt_history_integrity_valid(case, attempts):
            return False
        attempt_ids = {attempt.id for attempt in attempts}
        events = list(
            CorrespondenceCorrectionEvent._base_manager.filter(case=case)  # noqa: SLF001
            .select_related(
                "actor",
                "case",
                "delivery_event",
                "command__actor",
                "command__case",
                "command__target_attempt",
                "command__result_attempt",
                "command__result_revision",
                "command__result_artifact",
                "command__result_delivery",
                "command__result_attestation",
                "previous_event",
                "replacement_attempt",
                "source_correction",
            )
            .order_by("sequence")
        )
        if not events or len(events) != case.resource_version:
            return False
        previous = None
        ledger_command_ids = set()
        for index, event in enumerate(events, start=1):
            if any(
                [
                    event.deleted,
                    event.sequence != index,
                    event.previous_event_id != (previous.id if previous else None),
                    event.previous_event_hash
                    != (previous.event_hash if previous else ""),
                    event.previous_case_hash
                    != (previous.resulting_case_hash if previous else ""),
                    correspondence_correction_event_hash(event) != event.event_hash,
                    event.replacement_attempt_id
                    and event.replacement_attempt_id not in attempt_ids,
                    event.command_id
                    and not correction_command_integrity_valid(event.command),
                    event.command_id and event.command.case_id != case.id,
                    event.command_id
                    and event.command.target_attempt_id
                    and event.command.target_attempt_id not in attempt_ids,
                    event.command_id
                    and event.command.result_attempt_id
                    and event.command.result_attempt_id not in attempt_ids,
                    event.command_id
                    and event.command.result_attestation_id
                    and (
                        event.command.result_attestation.case_id != case.id
                        or event.command.result_attestation.replacement_attempt_id
                        not in attempt_ids
                    ),
                ]
            ):
                return False
            if event.command_id:
                ledger_command_ids.add(event.command_id)
            previous = event
        persisted_command_ids = set(
            CorrespondenceCorrectionCommand._base_manager.filter(case=case).values_list(  # noqa: SLF001
                "id", flat=True
            )
        )
        commands = [event.command for event in events if event.command_id]
        return bool(
            previous.resulting_case_hash == case.case_hash
            and ledger_command_ids == persisted_command_ids
            and _historical_attestations_integrity_valid(
                case,
                attempt_ids,
                commands,
            )
            and _historical_replacement_deliveries_integrity_valid(
                case,
                attempt_ids,
                commands,
                events,
            )
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _replacement_attempt_history_integrity_valid(case, attempts) -> bool:
    if not attempts:
        return case.replacement_attempt_id is None
    previous = None
    for expected_number, attempt in enumerate(attempts, start=1):
        if any(
            [
                attempt.case_id != case.id,
                attempt.attempt_number != expected_number,
                attempt.supersedes_attempt_id
                != (previous.id if previous is not None else None),
                not replacement_attempt_integrity_valid(attempt),
            ]
        ):
            return False
        previous = attempt
    return case.replacement_attempt_id == attempts[-1].id


def replacement_attempt_integrity_valid(attempt) -> bool:
    try:
        return all(
            [
                not attempt.deleted,
                attempt.case.source_head_id == attempt.source_head_id,
                attempt.source_correction.source_head_id == attempt.source_head_id,
                attempt.source_correction.new_submission_id
                == attempt.source_submission_id,
                attempt.source_version == attempt.source_submission.resource_version,
                attempt.source_snapshot_hash
                == attempt.source_submission.finalized_snapshot_hash,
                attempt.source_head_hash == attempt.source_correction.source_head_hash,
                attempt.source_correction_hash
                == attempt.source_correction.correction_hash,
                attempt.form_artifact.form_submission_id
                == attempt.source_submission_id,
                attempt.form_artifact_hash == attempt.form_artifact.artifact_sha256,
                attempt.compilation.form_submission_id == attempt.source_submission_id,
                attempt.compilation.form_artifact_id == attempt.form_artifact_id,
                attempt.review.compilation_id == attempt.compilation_id,
                attempt.initial_revision.letter.review_id == attempt.review_id,
                attempt.started_by_id == attempt.created_by_id,
                attempt.started_by_id == attempt.updated_by_id,
                correspondence_replacement_attempt_hash(attempt)
                == attempt.attempt_hash,
            ]
        )
    except (AttributeError, TypeError, ValueError):
        return False


def paper_attestation_integrity_valid(attestation) -> bool:
    try:
        attempt = attestation.replacement_attempt
        artifact = attestation.controlled_copy_artifact
        revision = getattr(artifact, "letter_revision", None)
        return all(
            [
                not attestation.deleted,
                attempt.case_id == attestation.case_id,
                revision is not None,
                not artifact.deleted,
                artifact.upload_completed,
                final_revision_frozen_integrity_valid(revision, artifact),
                revision.letter.review_id == attempt.review_id,
                artifact.source_version == revision.resource_version,
                artifact.source_snapshot_hash == revision.revision_hash,
                artifact.meta.get("artifact_kind") == "controlled_correction_copy",
                artifact.meta.get("correction_case")
                == str(attestation.case.external_id),
                artifact.meta.get("replacement_attempt") == str(attempt.external_id),
                attestation.artifact_hash == artifact.artifact_sha256,
                attestation.attestation_type
                == "corrected_copy_filed_prior_copy_reconciled",
                attestation.attested_by_id == attestation.created_by_id,
                attestation.attested_by_id == attestation.updated_by_id,
                correspondence_paper_attestation_hash(attestation)
                == attestation.attestation_hash,
            ]
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _historical_attestations_integrity_valid(case, attempt_ids, commands) -> bool:
    attestations = list(
        CorrespondencePaperReconciliationAttestation._base_manager.filter(  # noqa: SLF001
            case=case
        ).select_related(
            "case",
            "replacement_attempt__review",
            "controlled_copy_artifact__letter_revision__letter__review",
            "attested_by",
            "created_by",
            "updated_by",
        )
    )
    persisted_ids = {attestation.id for attestation in attestations}
    referenced_ids = {
        command.result_attestation_id
        for command in commands
        if command.result_attestation_id
    }
    return bool(
        persisted_ids == referenced_ids
        and all(
            attestation.replacement_attempt_id in attempt_ids
            and paper_attestation_integrity_valid(attestation)
            for attestation in attestations
        )
    )


def _historical_replacement_deliveries_integrity_valid(  # noqa: PLR0911
    case,
    attempt_ids,
    commands,
    case_events,
) -> bool:
    from care.emr.correspondence.delivery import (
        CorrespondenceDeliveryIntegrityError,
        lock_and_verify_delivery_ledger,
    )

    send_commands = [
        command
        for command in commands
        if command.command_type == "send_replacement"
        and command.result_attempt_id
        and command.result_delivery_id
    ]
    delivery_attempt = {
        command.result_delivery_id: command.result_attempt_id
        for command in send_commands
    }
    if len(delivery_attempt) != len(send_commands):
        return False
    persisted_delivery_ids = set(
        CorrespondenceDelivery._base_manager.filter(  # noqa: SLF001
            correction_case_reference=case.external_id,
            deleted=False,
        ).values_list("id", flat=True)
    )
    if persisted_delivery_ids != set(delivery_attempt):
        return False
    for command in commands:
        if (
            command.result_delivery_id
            and command.result_delivery_id != case.original_delivery_id
            and (
                command.result_delivery_id not in delivery_attempt
                or command.result_attempt_id
                != delivery_attempt[command.result_delivery_id]
            )
        ):
            return False
    deliveries = list(
        CorrespondenceDelivery._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "artifact",
            "recipient",
            "revision__letter__review__compilation__form_artifact",
            "review",
            "supersedes",
        )
        .filter(pk__in=persisted_delivery_ids)
        .order_by("pk")
    )
    if len(deliveries) != len(persisted_delivery_ids):
        return False
    ledger_event_attempt = {}
    for delivery in deliveries:
        attempt_id = delivery_attempt[delivery.id]
        if any(
            [
                attempt_id not in attempt_ids,
                delivery.correction_case_reference != case.external_id,
                delivery.supersedes_id != case.original_delivery_id,
                delivery.review_id
                != next(
                    command.result_attempt.review_id
                    for command in send_commands
                    if command.result_delivery_id == delivery.id
                ),
            ]
        ):
            return False
        try:
            _, delivery_events = lock_and_verify_delivery_ledger(delivery)
        except CorrespondenceDeliveryIntegrityError:
            return False
        for delivery_event in delivery_events:
            ledger_event_attempt[delivery_event.id] = attempt_id
    for event in case_events:
        if (
            event.replacement_attempt_id
            and event.delivery_event_id
            and ledger_event_attempt.get(event.delivery_event_id)
            != event.replacement_attempt_id
        ):
            return False
    return True


def correction_command_integrity_valid(command) -> bool:
    try:
        return all(
            [
                not command.deleted,
                command.actor_id == command.created_by_id,
                command.actor_id == command.updated_by_id,
                command.resulting_case_version == command.expected_case_version + 1,
                command.resulting_case_hash == command.result_snapshot.get("case_hash"),
                command.resulting_case_version
                == command.result_snapshot.get("case_version"),
                correspondence_correction_command_hash(command) == command.command_hash,
            ]
        )
    except (AttributeError, TypeError, ValueError):
        return False


@transaction.atomic
def materialize_claimed_correction_outbox(*, outbox_id, claim_token) -> bool:
    """Materialize one claimed correction and fence stale workers by token."""
    outbox_reference = (
        CorrespondenceCorrectionOutbox._base_manager.filter(pk=outbox_id)  # noqa: SLF001
        .values(
            "source_correction__new_version",
            "source_correction__source_head_id",
        )
        .first()
    )
    if not outbox_reference:
        return False
    head = (
        FormSubmissionSeriesHead._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related("advanced_by", "current_submission__workflow_finalized_by")
        .get(pk=outbox_reference["source_correction__source_head_id"])
    )
    current_head_source = (
        FormSubmission._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "patient",
            "encounter",
            "questionnaire",
            "previous_version",
            "created_by",
            "updated_by",
            "workflow_finalized_by",
        )
        .get(pk=head.current_submission_id)
    )
    head.current_submission = current_head_source
    if not form_submission_series_head_integrity_valid(
        head, current=current_head_source
    ):
        raise CorrespondenceCorrectionIntegrityError
    existing_cases = list(
        CorrespondenceCorrectionCase._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .only("pk", "original_compilation_id")
        .filter(
            original_compilation__form_submission__series_id=head.series_id,
            original_compilation__form_source_version__lt=outbox_reference[
                "source_correction__new_version"
            ],
        )
        .order_by("pk")[: MAX_AFFECTED_CORRESPONDENCE_BRANCHES + 1]
    )
    if len(existing_cases) > MAX_AFFECTED_CORRESPONDENCE_BRANCHES:
        raise CorrespondenceCorrectionIntegrityError
    existing_by_compilation = {
        case.original_compilation_id: case for case in existing_cases
    }
    outbox = (
        CorrespondenceCorrectionOutbox._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "source_correction__corrected_by",
            "source_correction__new_submission__workflow_finalized_by",
            "source_correction__previous_submission__workflow_finalized_by",
            "source_correction__source_head__advanced_by",
        )
        .filter(
            pk=outbox_id,
            status="processing",
            claim_token=claim_token,
        )
        .first()
    )
    if not outbox:
        return False
    correction = outbox.source_correction
    if (
        correction.source_head_id != head.id
        or correction.new_version != outbox_reference["source_correction__new_version"]
        or not source_correction_integrity_valid(correction)
    ):
        raise CorrespondenceCorrectionIntegrityError
    predecessor_incomplete = CorrespondenceCorrectionOutbox._base_manager.filter(  # noqa: SLF001
        source_correction__source_head=head,
        source_correction__sequence__lt=correction.sequence,
    ).exclude(status="completed")
    if predecessor_incomplete.exists():
        raise CorrespondenceCorrectionPendingError

    compilations = list(
        CorrespondenceCompilation._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "patient",
            "encounter",
            "facility",
            "department",
            "form_submission__patient",
            "form_submission__encounter",
            "form_submission__questionnaire",
            "form_submission__workflow_finalized_by",
            "form_artifact",
            "template",
            "author",
        )
        .filter(
            form_submission__series_id=head.series_id,
            form_source_version__lt=correction.new_version,
        )
        .order_by("pk")[: MAX_AFFECTED_CORRESPONDENCE_BRANCHES + 1]
    )
    if len(compilations) > MAX_AFFECTED_CORRESPONDENCE_BRANCHES:
        raise CorrespondenceCorrectionIntegrityError
    for compilation in compilations:
        _materialize_compilation_correction(
            head=head,
            correction=correction,
            compilation=compilation,
            existing_reference=existing_by_compilation.get(compilation.id),
        )

    outbox.status = "completed"
    outbox.completed_at = timezone.now()
    outbox.safe_code = "materialized"
    outbox.updated_by = None
    outbox.save(
        update_fields=[
            "status",
            "completed_at",
            "safe_code",
            "updated_by",
            "modified_date",
        ]
    )
    return True


def _materialize_compilation_correction(
    *,
    head,
    correction,
    compilation,
    existing_reference,
):
    if not compilation_frozen_integrity_valid(compilation):
        raise CorrespondenceCorrectionIntegrityError
    review = (
        CorrespondenceReview._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "author",
            "recipient",
            "patient",
            "facility",
            "compilation__form_submission__questionnaire",
            "compilation__form_artifact",
        )
        .filter(compilation=compilation)
        .first()
    )
    if not review:
        return
    if not review_frozen_integrity_valid(review):
        raise CorrespondenceCorrectionIntegrityError
    letter = (
        CorrespondenceLetter._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(review=review)
        .first()
    )
    if not letter:
        return
    revision = (
        CorrespondenceLetterRevision._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related("letter__review", "previous_revision", "finalized_by")
        .filter(letter=letter, status="finalized")
        .first()
    )
    if not revision:
        return
    artifact = (
        ReportUpload._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(letter_revision=revision)
        .first()
    )
    if not final_revision_frozen_integrity_valid(revision, artifact):
        raise CorrespondenceCorrectionIntegrityError
    delivery = (
        CorrespondenceDelivery._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "artifact",
            "recipient",
            "revision__letter__review__compilation__form_artifact",
            "review",
        )
        .filter(revision=revision)
        .first()
    )
    if not delivery:
        return
    from care.emr.correspondence.delivery import (
        CorrespondenceDeliveryIntegrityError,
        latest_delivery_event,
        lock_and_verify_delivery_ledger,
    )

    try:
        lock_and_verify_delivery_ledger(delivery)
    except CorrespondenceDeliveryIntegrityError as exc:
        raise CorrespondenceCorrectionIntegrityError from exc
    latest = latest_delivery_event(delivery, lock=True)
    if not latest:
        raise CorrespondenceCorrectionIntegrityError
    existing = (
        CorrespondenceCorrectionCase._base_manager.select_related(  # noqa: SLF001
            *_case_related_fields()
        ).get(pk=existing_reference.pk)
        if existing_reference
        else None
    )
    if latest.certainty == "not_delivered" and existing is None:
        return
    _create_or_advance_case(
        existing=existing,
        head=head,
        correction=correction,
        compilation=compilation,
        review=review,
        delivery=delivery,
        latest_delivery_event=latest,
    )


def _create_or_advance_case(
    *,
    existing,
    head,
    correction,
    compilation,
    review,
    delivery,
    latest_delivery_event,
):
    current = correction.new_submission
    frozen = compilation.form_submission
    changes = build_correspondence_change_set(frozen, current)
    notification = _notification_status(latest_delivery_event)
    if existing:
        if not correction_case_integrity_valid(existing):
            raise CorrespondenceCorrectionIntegrityError
        if existing.latest_source_correction_id == correction.id:
            return existing
        if (
            existing.status == "resolved"
            or existing.replacement_status == "acknowledged"
        ):
            return existing
        if (
            existing.latest_source_correction.sequence >= correction.sequence
            or existing.current_submission_id != correction.previous_submission_id
        ):
            raise CorrespondenceCorrectionIntegrityError
        previous_hash = existing.case_hash
        existing.current_submission = current
        existing.current_version = correction.new_version
        existing.current_snapshot_hash = correction.new_snapshot_hash
        existing.latest_source_correction = correction
        existing.latest_source_correction_hash = correction.correction_hash
        existing.source_head_hash = correction.source_head_hash
        existing.change_set_hash = changes.full_hash
        existing.delivery_state = latest_delivery_event.event_type
        existing.delivery_certainty = latest_delivery_event.certainty
        existing.notification_status = notification
        existing.resource_version += 1
        existing.updated_by = None
        existing.case_hash = correspondence_correction_case_hash(existing)
        existing.save(
            update_fields=[
                "current_submission",
                "current_version",
                "current_snapshot_hash",
                "latest_source_correction",
                "latest_source_correction_hash",
                "source_head_hash",
                "change_set_hash",
                "delivery_state",
                "delivery_certainty",
                "notification_status",
                "resource_version",
                "updated_by",
                "case_hash",
                "modified_date",
            ]
        )
        _append_case_event(
            existing,
            event_type="source_advanced",
            safe_code="source_advanced",
            previous_case_hash=previous_hash,
            source_correction=correction,
        )
        return existing

    case = CorrespondenceCorrectionCase(
        source_head=head,
        source_head_hash=correction.source_head_hash,
        original_compilation=compilation,
        original_review=review,
        original_delivery=delivery,
        frozen_submission=frozen,
        frozen_version=frozen.resource_version,
        frozen_snapshot_hash=frozen.finalized_snapshot_hash,
        current_submission=current,
        current_version=correction.new_version,
        current_snapshot_hash=correction.new_snapshot_hash,
        latest_source_correction=correction,
        latest_source_correction_hash=correction.correction_hash,
        change_set_hash=changes.full_hash,
        delivery_state=latest_delivery_event.event_type,
        delivery_certainty=latest_delivery_event.certainty,
        notification_status=notification,
        paper_reconciliation_status="required",
        replacement_status="not_started",
        replacement_delivery_state=None,
        replacement_delivery_certainty=None,
        status="open",
        resolution_mode=None,
        resource_version=1,
        case_hash="",
        created_by=None,
        updated_by=None,
    )
    case.case_hash = correspondence_correction_case_hash(case)
    case.save(force_insert=True)
    _append_case_event(
        case,
        event_type="opened",
        safe_code=f"case_opened_{latest_delivery_event.event_type}",
        previous_case_hash="",
        source_correction=correction,
    )
    return case


def _append_case_event(
    case,
    *,
    event_type,
    safe_code,
    previous_case_hash,
    source_correction=None,
    delivery_event=None,
    replacement_attempt=None,
    command=None,
    actor=None,
):
    if not SAFE_CORRECTION_CODE.fullmatch(safe_code):
        raise CorrespondenceCorrectionIntegrityError
    previous = (
        CorrespondenceCorrectionEvent._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(case=case)
        .order_by("-sequence")
        .first()
    )
    sequence = previous.sequence + 1 if previous else 1
    if sequence != case.resource_version:
        raise CorrespondenceCorrectionIntegrityError
    event = CorrespondenceCorrectionEvent(
        case=case,
        sequence=sequence,
        event_type=event_type,
        source_correction=source_correction,
        delivery_event=delivery_event,
        replacement_attempt=replacement_attempt,
        command=command,
        occurred_at=timezone.now(),
        actor_type="user" if actor else "system",
        actor=actor,
        safe_code=safe_code,
        previous_event=previous,
        previous_event_hash=previous.event_hash if previous else "",
        previous_case_hash=previous_case_hash,
        resulting_case_hash=case.case_hash,
        event_hash="",
        created_by=actor,
        updated_by=actor,
    )
    event.event_hash = correspondence_correction_event_hash(event)
    event.save(force_insert=True)
    return event


def _notification_status(event):
    return _notification_status_values(event.event_type, event.certainty)


def _notification_status_values(delivery_state, delivery_certainty):
    if delivery_state == "acknowledged":
        return "required"
    if delivery_certainty == "not_delivered":
        return "not_required"
    return "unknown"


def _case_related_fields():
    return [
        "source_head",
        "original_compilation__form_submission__questionnaire",
        "original_compilation__form_artifact",
        "original_review__author",
        "original_review__recipient",
        "original_delivery",
        "frozen_submission__patient",
        "frozen_submission__encounter",
        "frozen_submission__questionnaire",
        "frozen_submission__workflow_finalized_by",
        "current_submission__patient",
        "current_submission__encounter",
        "current_submission__questionnaire",
        "current_submission__workflow_finalized_by",
        "latest_source_correction__corrected_by",
        "latest_source_correction__source_head",
        "latest_source_correction__previous_submission__workflow_finalized_by",
        "latest_source_correction__new_submission__workflow_finalized_by",
        "replacement_compilation",
        "replacement_attempt",
        "replacement_review",
        "replacement_revision",
        "replacement_artifact",
        "replacement_delivery",
        "resolved_by",
    ]


@transaction.atomic
def refresh_correction_case_for_delivery(  # noqa: PLR0912, PLR0915
    delivery_id,
) -> bool:
    """Project a newly appended delivery outcome without ever resending it."""
    reference = (
        CorrespondenceDelivery._base_manager.filter(  # noqa: SLF001
            pk=delivery_id,
            deleted=False,
        )
        .values(
            "review__compilation_id",
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
    case_reference = (
        CorrespondenceCorrectionCase._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .only("pk")
        .filter(original_delivery_id=delivery_id)
        .first()
    )
    compilation = (
        CorrespondenceCompilation._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "patient",
            "encounter",
            "facility",
            "department",
            "form_submission__questionnaire",
            "form_submission__workflow_finalized_by",
            "form_artifact",
            "template",
            "author",
        )
        .get(pk=reference["review__compilation_id"])
    )
    if not compilation_frozen_integrity_valid(compilation):
        raise CorrespondenceCorrectionIntegrityError
    review = (
        CorrespondenceReview._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "author",
            "recipient",
            "patient",
            "facility",
            "compilation__form_submission__questionnaire",
            "compilation__form_artifact",
        )
        .get(compilation=compilation)
    )
    if not review_frozen_integrity_valid(review):
        raise CorrespondenceCorrectionIntegrityError
    letter = CorrespondenceLetter._base_manager.select_for_update(of=("self",)).get(  # noqa: SLF001
        review=review
    )
    revision = (
        CorrespondenceLetterRevision._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related("letter__review", "previous_revision", "finalized_by")
        .get(letter=letter, status="finalized")
    )
    artifact = (
        ReportUpload._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(letter_revision=revision)
        .first()
    )
    if not final_revision_frozen_integrity_valid(revision, artifact):
        raise CorrespondenceCorrectionIntegrityError
    delivery = (
        CorrespondenceDelivery._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "artifact",
            "recipient",
            "revision__letter__review__compilation__form_artifact",
            "review",
        )
        .get(pk=delivery_id, revision=revision)
    )
    case = (
        CorrespondenceCorrectionCase._base_manager.select_related(  # noqa: SLF001
            *_case_related_fields()
        ).get(pk=case_reference.pk)
        if case_reference
        else None
    )
    if case and not correction_case_integrity_valid(case):
        raise CorrespondenceCorrectionIntegrityError
    from care.emr.correspondence.delivery import (
        CorrespondenceDeliveryIntegrityError,
        latest_delivery_event,
        lock_and_verify_delivery_ledger,
    )

    try:
        lock_and_verify_delivery_ledger(delivery)
    except CorrespondenceDeliveryIntegrityError as exc:
        raise CorrespondenceCorrectionIntegrityError from exc
    latest = latest_delivery_event(delivery, lock=True)
    if not latest:
        raise CorrespondenceCorrectionIntegrityError
    if not case:
        corrections = _completed_source_correction_chain(
            head=head,
            frozen=compilation.form_submission,
            current=current,
        )
        if latest.certainty == "not_delivered":
            return False
        return bool(
            _create_or_advance_case(
                existing=None,
                head=head,
                correction=corrections[-1],
                compilation=compilation,
                review=review,
                delivery=delivery,
                latest_delivery_event=latest,
            )
        )
    expected_notification = _notification_status(latest)
    notification_matches = bool(
        case.replacement_status != "not_started"
        or case.notification_status == expected_notification
    )
    if (
        latest.event_type == case.delivery_state
        and latest.certainty == case.delivery_certainty
        and notification_matches
    ):
        return False
    previous_hash = case.case_hash
    case.delivery_state = latest.event_type
    case.delivery_certainty = latest.certainty
    if case.replacement_status == "not_started":
        case.notification_status = expected_notification
    case.resource_version += 1
    case.updated_by = None
    case.case_hash = correspondence_correction_case_hash(case)
    update_fields = [
        "delivery_state",
        "delivery_certainty",
        "resource_version",
        "updated_by",
        "case_hash",
        "modified_date",
    ]
    if case.replacement_status == "not_started":
        update_fields.append("notification_status")
    case.save(update_fields=update_fields)
    _append_case_event(
        case,
        event_type="delivery_classified",
        safe_code=f"delivery_{latest.event_type}",
        previous_case_hash=previous_hash,
        delivery_event=latest,
    )
    return True


def _completed_source_correction_chain(*, head, frozen, current):
    corrections = list(
        CorrespondenceSourceCorrection._base_manager.select_related(  # noqa: SLF001
            "corrected_by",
            "source_head__advanced_by",
            "previous_submission__workflow_finalized_by",
            "new_submission__workflow_finalized_by",
        )
        .filter(
            source_head=head,
            new_version__gt=frozen.resource_version,
            new_version__lte=current.resource_version,
        )
        .order_by("sequence")
    )
    previous = frozen
    for correction in corrections:
        if (
            correction.previous_submission_id != previous.id
            or not source_correction_integrity_valid(correction)
        ):
            raise CorrespondenceCorrectionIntegrityError
        previous = correction.new_submission
    if not corrections or previous.id != current.id:
        raise CorrespondenceCorrectionIntegrityError
    statuses = list(
        CorrespondenceCorrectionOutbox._base_manager.filter(  # noqa: SLF001
            source_correction__in=corrections
        ).values_list("status", flat=True)
    )
    if len(statuses) != len(corrections) or any(
        status == "failed_terminal" for status in statuses
    ):
        raise CorrespondenceCorrectionIntegrityError
    if any(status in {"pending", "processing"} for status in statuses):
        raise CorrespondenceCorrectionPendingError
    if not all(status == "completed" for status in statuses):
        raise CorrespondenceCorrectionIntegrityError
    return corrections


def authoritative_form_artifact(source):
    artifact = (
        ReportUpload._base_manager.filter(  # noqa: SLF001
            form_submission=source,
            deleted=False,
            is_archived=False,
            upload_completed=True,
        )
        .order_by("pk")
        .first()
    )
    if not artifact:
        return None
    if any(
        [
            artifact.patient_id != source.patient_id,
            artifact.encounter_id != source.encounter_id,
            artifact.source_version != source.resource_version,
            artifact.source_snapshot_hash != source.finalized_snapshot_hash,
            len(artifact.artifact_sha256) != SHA256_HEX_LENGTH,
        ]
    ):
        raise CorrespondenceCorrectionIntegrityError
    return artifact
