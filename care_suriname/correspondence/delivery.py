import hashlib
import re
from dataclasses import dataclass

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from care.emr.models.encounter import EncounterOrganization
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.organization import FacilityOrganizationUser
from care.emr.models.questionnaire import FormSubmission, QuestionnaireResponse
from care.emr.models.report.report_upload import ReportUpload
from care.security.models import RoleModel
from care_suriname.correspondence.author import (
    InvalidVerifiedAuthorError,
    verified_author_snapshot,
)
from care_suriname.correspondence.correction import (
    FormSubmissionSeriesHeadIntegrityError,
    lock_current_finalized_form_series,
)
from care_suriname.correspondence.review import (
    correspondence_review_hash,
    reviewed_binding_available,
)
from care_suriname.models.correspondence import CorrespondenceCompilation
from care_suriname.models.correspondence_delivery import (
    CorrespondenceDelivery,
    CorrespondenceDeliveryAttempt,
    CorrespondenceDeliveryEvent,
)
from care_suriname.models.correspondence_letter import (
    CorrespondenceLetter,
    CorrespondenceLetterRevision,
)
from care_suriname.models.correspondence_review import (
    CorrespondenceRecipient,
    CorrespondenceReview,
)
from care_suriname.resources.correspondence import canonical_sha256
from care_suriname.resources.correspondence_delivery import (
    correspondence_delivery_attempt_hash,
    correspondence_delivery_event_hash,
    correspondence_delivery_hash,
)
from care_suriname.resources.correspondence_letter import (
    correspondence_letter_body_hash,
    correspondence_letter_revision_hash,
)
from care_suriname.resources.form_submission.commands import (
    finalized_form_submission_snapshot_hash,
)

MAX_DELIVERY_EVENTS = 100
MAX_MEDICATION_SOURCES = 50
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
SAFE_CODE_PATTERN = re.compile(r"^[a-z0-9_:-]{0,64}$")
SAFE_ACK_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")


class CorrespondenceDispatchNotCurrentError(ValueError):
    """The frozen letter cannot be proven current for a new external side effect."""


class CorrespondenceDeliveryIntegrityError(ValueError):
    """The frozen delivery ledger or artifact failed its integrity contract."""


class CorrespondenceDeliveryTransitionError(ValueError):
    """The requested append-only delivery transition is not allowed."""


@dataclass(frozen=True)
class CorrespondenceDispatchContext:
    revision: CorrespondenceLetterRevision
    artifact: ReportUpload
    review: CorrespondenceReview
    recipient: CorrespondenceRecipient


@transaction.atomic
def lock_and_assert_correspondence_dispatch_current(
    revision_id: int,
    *,
    locked_source=None,
) -> CorrespondenceDispatchContext:
    """Lock and prove every source needed before a new delivery side effect."""
    source = locked_source or lock_correspondence_dispatch_source_current(revision_id)
    revision = (
        CorrespondenceLetterRevision._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related("previous_revision", "finalized_by")
        .get(pk=revision_id)
    )
    letter = _lock_instance(CorrespondenceLetter, revision.letter_id)
    review = _lock_instance(CorrespondenceReview, letter.review_id)
    compilation = _lock_instance(CorrespondenceCompilation, review.compilation_id)
    recipient = _lock_instance(CorrespondenceRecipient, review.recipient_id)
    artifact = (
        ReportUpload._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(letter_revision=revision)
        .first()
    )
    if not artifact:
        raise CorrespondenceDispatchNotCurrentError

    patient = _lock_instance(type(letter.patient), letter.patient_id)
    encounter = _lock_instance(type(letter.encounter), letter.encounter_id)
    facility = _lock_instance(type(letter.facility), letter.facility_id)
    department = _lock_instance(type(letter.department), letter.department_id)
    author = _lock_instance(type(letter.author), letter.author_id)
    reason = _lock_instance(
        type(compilation.encounter_reason), compilation.encounter_reason_id
    )
    template = _lock_instance(type(compilation.template), compilation.template_id)
    form_artifact = _lock_instance(
        type(compilation.form_artifact), compilation.form_artifact_id
    )
    if compilation.form_submission_id != source.id:
        raise CorrespondenceDispatchNotCurrentError

    letter.review = review
    letter.patient = patient
    letter.encounter = encounter
    letter.facility = facility
    letter.department = department
    letter.author = author
    revision.letter = letter
    review.compilation = compilation
    review.recipient = recipient
    review.patient = patient
    review.encounter = encounter
    review.facility = facility
    review.department = department
    review.author = author
    compilation.patient = patient
    compilation.encounter = encounter
    compilation.facility = facility
    compilation.department = department
    compilation.author = author
    compilation.encounter_reason = reason
    compilation.template = template
    compilation.form_artifact = form_artifact
    compilation.form_submission = source

    _assert_revision_current(revision, artifact, review)
    _assert_compilation_current(compilation, source, form_artifact)
    _assert_patient_and_encounter_snapshot(compilation)
    _assert_current_author(review)
    _assert_current_medications(compilation, source)
    if not reviewed_binding_available(review, lock_verifier=True):
        raise CorrespondenceDispatchNotCurrentError
    return CorrespondenceDispatchContext(
        revision=revision,
        artifact=artifact,
        review=review,
        recipient=recipient,
    )


def lock_correspondence_dispatch_source_current(revision_id: int) -> FormSubmission:
    """Resolve without locks, then acquire canonical source-head/source locks first."""
    try:
        reference = (
            CorrespondenceLetterRevision._base_manager.select_related(  # noqa: SLF001
                "letter__review__compilation__form_submission"
            )
            .get(pk=revision_id)
            .letter.review.compilation.form_submission
        )
        _head, current = lock_current_finalized_form_series(reference)
    except (
        AttributeError,
        ObjectDoesNotExist,
        FormSubmissionSeriesHeadIntegrityError,
    ) as exc:
        raise CorrespondenceDispatchNotCurrentError from exc
    if current.pk != reference.pk:
        raise CorrespondenceDispatchNotCurrentError
    return current


def read_and_verify_correspondence_artifact(artifact: ReportUpload) -> bytes:
    if (
        artifact.deleted
        or artifact.is_archived
        or not artifact.upload_completed
        or artifact.meta.get("mime_type") != "application/pdf"
    ):
        raise CorrespondenceDeliveryIntegrityError
    try:
        response = artifact.files_manager.get_object(artifact)
        content_length = response.get("ContentLength")
        if content_length is not None and (
            not isinstance(content_length, int) or content_length > MAX_ARTIFACT_BYTES
        ):
            raise CorrespondenceDeliveryIntegrityError
        content_type = response.get("ContentType")
        body = response["Body"].read(MAX_ARTIFACT_BYTES + 1)
    except CorrespondenceDeliveryIntegrityError:
        raise
    except Exception as exc:
        raise CorrespondenceDeliveryIntegrityError from exc
    if (
        content_type != "application/pdf"
        or len(body) > MAX_ARTIFACT_BYTES
        or not body.startswith(b"%PDF")
        or hashlib.sha256(body).hexdigest() != artifact.artifact_sha256
    ):
        raise CorrespondenceDeliveryIntegrityError
    return body


def delivery_frozen_integrity_valid(delivery: CorrespondenceDelivery) -> bool:
    """Validate frozen history without requiring current recipient/source availability."""
    try:
        if not _delivery_frozen_snapshot_valid(delivery):
            return False
        attempts = list(
            CorrespondenceDeliveryAttempt._base_manager.filter(delivery=delivery)  # noqa: SLF001
            .select_related("requested_by", "previous_terminal_event")
            .order_by("attempt_number")
        )
        events = list(
            CorrespondenceDeliveryEvent._base_manager.filter(delivery=delivery)  # noqa: SLF001
            .select_related("attempt", "actor", "previous_event", "delivery")
            .order_by("sequence")
        )
        return _delivery_chain_valid(delivery, attempts, events)
    except (AttributeError, TypeError, ValueError):
        return False


def lock_and_verify_delivery_ledger(delivery: CorrespondenceDelivery):
    """Lock and validate the complete append-only ledger before a side effect."""
    if not _delivery_frozen_snapshot_valid(delivery):
        raise CorrespondenceDeliveryIntegrityError
    attempts = list(
        CorrespondenceDeliveryAttempt._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(delivery=delivery)
        .select_related("requested_by", "previous_terminal_event", "delivery")
        .order_by("attempt_number")
    )
    events = list(
        CorrespondenceDeliveryEvent._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(delivery=delivery)
        .select_related("attempt", "actor", "previous_event", "delivery")
        .order_by("sequence")
    )
    if not _delivery_chain_valid(delivery, attempts, events):
        raise CorrespondenceDeliveryIntegrityError
    return attempts, events


def _delivery_frozen_snapshot_valid(delivery):
    revision = delivery.revision
    letter = revision.letter
    review = delivery.review
    artifact = delivery.artifact
    return not any(
        [
            delivery.deleted,
            revision.deleted,
            letter.deleted,
            review.deleted,
            artifact.deleted,
            revision.status != "finalized",
            correspondence_letter_body_hash(revision.body) != revision.body_hash,
            correspondence_letter_revision_hash(revision) != revision.revision_hash,
            correspondence_review_hash(review) != review.review_hash,
            revision.final_artifact_id != artifact.id,
            delivery.revision_id != revision.id,
            delivery.review_id != letter.review_id,
            delivery.review_hash != review.review_hash,
            delivery.revision_hash != revision.revision_hash,
            delivery.revision_version != revision.resource_version,
            delivery.artifact_sha256 != artifact.artifact_sha256,
            delivery.recipient_id != review.recipient_id,
            delivery.recipient_version != review.recipient_version,
            delivery.recipient_hash != review.recipient_hash,
            delivery.patient_id != letter.patient_id,
            delivery.encounter_id != letter.encounter_id,
            delivery.facility_id != letter.facility_id,
            delivery.department_id != letter.department_id,
            delivery.author_id != letter.author_id,
            delivery.channel_type
            != review.recipient_snapshot.get("channel", {}).get("type"),
            correspondence_delivery_hash(delivery) != delivery.delivery_hash,
        ]
    )


def latest_delivery_event(delivery, *, lock=False):
    query = CorrespondenceDeliveryEvent._base_manager.filter(  # noqa: SLF001
        delivery=delivery
    ).select_related("attempt", "actor", "previous_event", "delivery")
    if lock:
        query = query.select_for_update(of=("self",))
    return query.order_by("-sequence").first()


def append_delivery_event(
    *,
    delivery: CorrespondenceDelivery,
    attempt: CorrespondenceDeliveryAttempt,
    event_type: str,
    certainty: str,
    actor=None,
    safe_code: str = "",
    provider_ack_reference: str = "",
    provider_ack_at=None,
) -> CorrespondenceDeliveryEvent:
    if not SAFE_CODE_PATTERN.fullmatch(safe_code):
        raise CorrespondenceDeliveryTransitionError
    if provider_ack_reference and not SAFE_ACK_PATTERN.fullmatch(
        provider_ack_reference
    ):
        raise CorrespondenceDeliveryTransitionError
    previous = latest_delivery_event(delivery, lock=True)
    if not _transition_allowed(previous, attempt, event_type):
        raise CorrespondenceDeliveryTransitionError
    if previous and previous.sequence >= MAX_DELIVERY_EVENTS:
        raise CorrespondenceDeliveryTransitionError
    ack_hash = (
        canonical_sha256(
            {
                "contract": "correspondence-delivery-ack-reference-v1",
                "reference": provider_ack_reference,
            }
        )
        if provider_ack_reference
        else ""
    )
    event = CorrespondenceDeliveryEvent(
        delivery=delivery,
        attempt=attempt,
        sequence=previous.sequence + 1 if previous else 1,
        event_type=event_type,
        certainty=certainty,
        occurred_at=timezone.now(),
        actor_type="user" if actor else "system",
        actor=actor,
        safe_code=safe_code,
        provider_ack_reference=provider_ack_reference,
        provider_ack_hash=ack_hash,
        provider_ack_at=provider_ack_at,
        previous_event=previous,
        previous_event_hash=previous.event_hash if previous else "",
        created_by=actor,
        updated_by=actor,
    )
    event.event_hash = correspondence_delivery_event_hash(event)
    event.save(force_insert=True)
    if event_type in {
        "acknowledged",
        "failed_retryable",
        "failed_terminal",
        "outcome_unknown",
    }:
        from care_suriname.tasks.correspondence_correction import (
            refresh_correspondence_correction_delivery,
            refresh_correspondence_replacement_delivery,
        )

        delivery_external_id = str(delivery.external_id)
        refresh_task = (
            refresh_correspondence_replacement_delivery
            if delivery.correction_case_reference
            else refresh_correspondence_correction_delivery
        )
        transaction.on_commit(
            lambda: refresh_task.delay(delivery_external_id),
            robust=True,
        )
    return event


def _assert_revision_current(revision, artifact, review):
    letter = revision.letter
    if any(
        [
            revision.deleted,
            letter.deleted,
            revision.status != "finalized",
            revision.finalized_by_id != letter.author_id,
            revision.source_review_hash != review.review_hash,
            letter.review_hash != review.review_hash,
            correspondence_letter_body_hash(revision.body) != revision.body_hash,
            correspondence_letter_revision_hash(revision) != revision.revision_hash,
            artifact.deleted,
            artifact.is_archived,
            not artifact.upload_completed,
            revision.final_artifact_id != artifact.id,
            artifact.patient_id != letter.patient_id,
            artifact.encounter_id != letter.encounter_id,
            artifact.source_version != revision.resource_version,
            artifact.source_snapshot_hash != revision.revision_hash,
            artifact.report_type != "encounter_report",
            artifact.meta.get("mime_type") != "application/pdf",
            not _valid_sha256(artifact.artifact_sha256),
        ]
    ):
        raise CorrespondenceDispatchNotCurrentError


def _assert_compilation_current(compilation, source, form_artifact):
    expected_hash = canonical_sha256(
        {
            "compiled_at": compilation.compiled_at,
            "compiled_html": compilation.compiled_html,
            "compiled_text": compilation.compiled_text,
            "compilation": compilation.external_id,
            "provenance": compilation.source_provenance,
        }
    )
    if any(
        [
            compilation.deleted,
            compilation.status != "compiled",
            compilation.compiled_hash != expected_hash,
            source.deleted,
            source.status != "submitted",
            source.resource_version != compilation.form_source_version,
            source.finalized_snapshot_hash != compilation.form_source_hash,
            finalized_form_submission_snapshot_hash(source)
            != source.finalized_snapshot_hash,
            form_artifact.deleted,
            form_artifact.is_archived,
            not form_artifact.upload_completed,
            form_artifact.form_submission_id != source.id,
            form_artifact.source_version != source.resource_version,
            form_artifact.source_snapshot_hash != source.finalized_snapshot_hash,
            form_artifact.artifact_sha256 != compilation.form_artifact_hash,
        ]
    ):
        raise CorrespondenceDispatchNotCurrentError


def _assert_patient_and_encounter_snapshot(compilation):
    provenance = compilation.source_provenance
    patient = provenance.get("patient") if isinstance(provenance, dict) else None
    encounter = provenance.get("encounter") if isinstance(provenance, dict) else None
    facility = provenance.get("facility") if isinstance(provenance, dict) else None
    department = provenance.get("department") if isinstance(provenance, dict) else None
    if not all(
        isinstance(item, dict) for item in [patient, encounter, facility, department]
    ):
        raise CorrespondenceDispatchNotCurrentError
    current_identifiers = list(compilation.patient.instance_identifiers or [])
    facility_identifiers = compilation.patient.facility_identifiers or {}
    current_identifiers.extend(
        facility_identifiers.get(str(compilation.facility_id), [])
        or facility_identifiers.get(compilation.facility_id, [])
    )
    current_identifiers = [
        item
        for item in current_identifiers
        if isinstance(item, dict) and str(item.get("value") or "").strip()
    ]
    current_patient = {
        "date_of_birth": str(
            compilation.patient.date_of_birth or compilation.patient.year_of_birth
        ),
        "id": str(compilation.patient.external_id),
        "identifiers": current_identifiers,
        "name": compilation.patient.name,
    }
    if any(
        [
            compilation.patient.deleted,
            compilation.encounter.deleted,
            compilation.facility.deleted,
            not compilation.facility.is_active,
            compilation.department.deleted,
            not compilation.department.active,
            compilation.department.facility_id != compilation.facility_id,
            compilation.encounter.facility_id != compilation.facility_id,
            patient != current_patient,
            encounter.get("id") != str(compilation.encounter.external_id),
            encounter.get("date")
            != str((compilation.encounter.period or {}).get("start")),
            encounter.get("reason_id") != str(compilation.encounter_reason.external_id),
            encounter.get("reason") != compilation.encounter_reason.display,
            compilation.encounter_reason.deleted,
            compilation.encounter_reason.status != "active",
            compilation.encounter_reason.id not in compilation.encounter.tags,
            facility
            != {
                "id": str(compilation.facility.external_id),
                "name": compilation.facility.name,
            },
            department
            != {
                "id": str(compilation.department.external_id),
                "name": compilation.department.name,
            },
        ]
    ):
        raise CorrespondenceDispatchNotCurrentError
    department_links = list(
        EncounterOrganization._base_manager.select_for_update(of=("self",)).filter(  # noqa: SLF001
            encounter=compilation.encounter,
            organization=compilation.department,
        )[:2]
    )
    if len(department_links) != 1:
        raise CorrespondenceDispatchNotCurrentError


def _assert_current_author(review):
    memberships = list(
        FacilityOrganizationUser._base_manager.select_for_update(of=("self",)).filter(  # noqa: SLF001
            deleted=False,
            organization=review.department,
            user=review.author,
        )[:2]
    )
    if len(memberships) != 1:
        raise CorrespondenceDispatchNotCurrentError
    role = (
        RoleModel._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(
            pk=memberships[0].role_id,
            deleted=False,
            is_archived=False,
        )
        .first()
    )
    if not role:
        raise CorrespondenceDispatchNotCurrentError
    try:
        current = verified_author_snapshot(
            user=review.author,
            membership=memberships[0],
            role=role,
            facility=review.facility,
            department=review.department,
        )
    except InvalidVerifiedAuthorError as exc:
        raise CorrespondenceDispatchNotCurrentError from exc
    if current != review.author_snapshot:
        raise CorrespondenceDispatchNotCurrentError


def _assert_current_medications(compilation, source):
    snapshots = compilation.medication_sources
    if not isinstance(snapshots, list) or len(snapshots) > MAX_MEDICATION_SOURCES:
        raise CorrespondenceDispatchNotCurrentError
    ids = [item.get("id") for item in snapshots if isinstance(item, dict)]
    if len(ids) != len(snapshots) or len(set(ids)) != len(ids):
        raise CorrespondenceDispatchNotCurrentError
    medications = list(
        MedicationRequest._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related("requested_product", "requester")
        .filter(external_id__in=ids)
    )
    by_id = {str(item.external_id): item for item in medications}
    if set(by_id) != set(ids):
        raise CorrespondenceDispatchNotCurrentError
    linked_ids = []
    links = list(
        QuestionnaireResponse._base_manager.select_for_update(of=("self",)).filter(  # noqa: SLF001
            form_submission=source,
            structured_response_type="medication_request",
        )
    )
    for link in links:
        structured = link.structured_responses
        action = (
            structured.get("medication_request")
            if isinstance(structured, dict)
            else None
        )
        if (
            link.deleted
            or link.status != "completed"
            or link.patient_id != source.patient_id
            or link.encounter_id != source.encounter_id
            or not isinstance(action, dict)
            or set(action) != {"id", "submit_type"}
            or action.get("submit_type") != "CREATE"
        ):
            raise CorrespondenceDispatchNotCurrentError
        linked_ids.append(str(action.get("id")))
    if len(linked_ids) != len(set(linked_ids)) or set(linked_ids) != set(ids):
        raise CorrespondenceDispatchNotCurrentError
    current = [_medication_snapshot(by_id[item_id]) for item_id in ids]
    if current != snapshots:
        raise CorrespondenceDispatchNotCurrentError


def _medication_snapshot(medication):
    if medication.requested_product_id:
        display = medication.requested_product.name
    elif isinstance(medication.medication, dict):
        display = medication.medication.get("display") or medication.medication.get(
            "code"
        )
    else:
        display = None
    dosage = medication.dosage_instruction
    if (
        medication.deleted
        or medication.status not in {"active", "completed"}
        or medication.intent != "order"
        or medication.do_not_perform
        or not display
        or not isinstance(dosage, list)
    ):
        raise CorrespondenceDispatchNotCurrentError
    dosage_text = "; ".join(
        str(item.get("text"))
        for item in dosage
        if isinstance(item, dict) and item.get("text")
    )
    if not dosage_text:
        import json

        dosage_text = json.dumps(
            dosage,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    authored_on = medication.authored_on
    if authored_on and authored_on.tzinfo is not None:
        from datetime import UTC

        authored_on_value = (
            authored_on.astimezone(UTC).isoformat().replace("+00:00", "Z")
        )
    else:
        authored_on_value = authored_on.isoformat() if authored_on else None
    return {
        "authored_on": authored_on_value,
        "display": str(display),
        "dosage_instruction": dosage,
        "dosage_text": dosage_text,
        "id": str(medication.external_id),
        "intent": medication.intent,
        "payload_hash": medication.client_request_payload_hash,
        "requester": (
            str(medication.requester.external_id) if medication.requester_id else None
        ),
        "status": medication.status,
    }


def _delivery_chain_valid(delivery, attempts, events):  # noqa: PLR0911, PLR0912
    if not attempts or not events or len(events) > MAX_DELIVERY_EVENTS:
        return False
    if [attempt.attempt_number for attempt in attempts] != list(
        range(1, len(attempts) + 1)
    ):
        return False
    attempt_by_id = {attempt.id: attempt for attempt in attempts}
    referenced_attempt_ids = set()
    previous = None
    for expected_sequence, event in enumerate(events, start=1):
        attempt = attempt_by_id.get(event.attempt_id)
        expected_certainty = {
            "dispatch_pending": "not_attempted",
            "dispatching": "attempting",
            "acknowledged": "acknowledged",
            "failed_retryable": "not_delivered",
            "failed_terminal": "not_delivered",
            "outcome_unknown": "unknown",
        }.get(event.event_type)
        if (
            not attempt
            or attempt.deleted
            or not _valid_sha256(attempt.payload_hash)
            or correspondence_delivery_attempt_hash(attempt) != attempt.attempt_hash
            or attempt.adapter_name != delivery.adapter_name
            or attempt.adapter_version != delivery.adapter_version
            or attempt.requested_by_id != delivery.author_id
            or attempt.created_by_id != attempt.requested_by_id
            or attempt.updated_by_id != attempt.requested_by_id
            or event.deleted
            or event.sequence != expected_sequence
            or event.certainty != expected_certainty
            or not SAFE_CODE_PATTERN.fullmatch(event.safe_code)
            or not _transition_allowed(previous, attempt, event.event_type)
            or event.delivery_id != delivery.id
            or attempt.delivery_id != delivery.id
            or attempt.provider_idempotency_key != delivery.provider_idempotency_key
            or event.previous_event_id != (previous.id if previous else None)
            or event.previous_event_hash != (previous.event_hash if previous else "")
            or correspondence_delivery_event_hash(event) != event.event_hash
        ):
            return False
        if attempt.attempt_number == 1:
            if attempt.command_type != "send" or attempt.previous_terminal_event_id:
                return False
        elif attempt.command_type != "retry" or not attempt.previous_terminal_event_id:
            return False
        referenced_attempt_ids.add(attempt.id)
        if event.provider_ack_reference:
            expected_ack_hash = canonical_sha256(
                {
                    "contract": "correspondence-delivery-ack-reference-v1",
                    "reference": event.provider_ack_reference,
                }
            )
            if event.provider_ack_hash != expected_ack_hash:
                return False
        elif event.provider_ack_hash:
            return False
        if event.event_type == "acknowledged":
            if not event.provider_ack_reference or not event.provider_ack_at:
                return False
        elif event.provider_ack_reference or event.provider_ack_at:
            return False
        previous = event
    return referenced_attempt_ids == set(attempt_by_id)


def _transition_allowed(previous, attempt, event_type):
    if previous is None:
        return attempt.attempt_number == 1 and event_type == "dispatch_pending"
    if event_type == "dispatch_pending":
        return bool(
            previous.event_type == "failed_retryable"
            and attempt.attempt_number == previous.attempt.attempt_number + 1
            and attempt.previous_terminal_event_id == previous.id
        )
    if event_type == "dispatching":
        return (
            previous.event_type == "dispatch_pending"
            and previous.attempt_id == attempt.id
        )
    if event_type in {
        "acknowledged",
        "failed_retryable",
        "failed_terminal",
        "outcome_unknown",
    }:
        return bool(
            previous.event_type in {"dispatching", "outcome_unknown"}
            and previous.attempt_id == attempt.id
        )
    return False


def _lock_instance(model, pk):
    return model._base_manager.select_for_update(of=("self",)).get(pk=pk)  # noqa: SLF001


def _valid_sha256(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))
