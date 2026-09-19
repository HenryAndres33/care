from care.emr.correspondence.review import (
    reviewed_binding_available,
    reviewed_binding_frozen_integrity_valid,
)
from care.emr.models.report.report_upload import ReportUpload
from care.emr.resources.correspondence_letter import (
    correspondence_letter_body_hash,
    correspondence_letter_revision_hash,
)
from care.emr.resources.form_submission.artifact import has_unresolved_placeholder
from care_suriname.models.correspondence_letter import CorrespondenceLetterRevision

SHA256_HEX_LENGTH = 64


def correspondence_revision_frozen_integrity_valid(revision) -> bool:
    try:
        letter = revision.letter
        review = letter.review
        audit_valid = (
            bool(revision.finalized_at and revision.finalized_by_id)
            if revision.status == "finalized"
            else bool(
                revision.status == "draft"
                and not revision.finalized_at
                and not revision.finalized_by_id
            )
        )
        return all(
            [
                not revision.deleted,
                not letter.deleted,
                reviewed_binding_frozen_integrity_valid(review),
                letter.review_hash == review.review_hash,
                revision.source_review_hash == review.review_hash,
                letter.patient_id == review.patient_id,
                letter.encounter_id == review.encounter_id,
                letter.facility_id == review.facility_id,
                letter.department_id == review.department_id,
                letter.author_id == review.author_id,
                correspondence_letter_body_hash(revision.body) == revision.body_hash,
                correspondence_letter_revision_hash(revision) == revision.revision_hash,
                audit_valid,
            ]
        )
    except (AttributeError, TypeError, ValueError):
        return False


def correspondence_revision_artifact_status(revision, *, artifact=None) -> str:
    if revision.status != "finalized":
        return "not_applicable"
    artifact = artifact or _artifact_for_revision(revision)
    if not correspondence_artifact_frozen_integrity_valid(artifact, revision):
        return "integrity_failed"
    if artifact.deleted or artifact.is_archived or not artifact.upload_completed:
        return "unavailable"
    return "available"


def correspondence_revision_actionable(revision, *, expected_status: str) -> bool:
    """Validate live actionability; authorization for the requesting actor is separate."""
    if expected_status not in {"draft", "finalized"}:
        raise ValueError("expected_status must be draft or finalized")
    if (
        revision.status != expected_status
        or not correspondence_revision_frozen_integrity_valid(revision)
        or not reviewed_binding_available(revision.letter.review)
    ):
        return False
    latest_id = (
        CorrespondenceLetterRevision._base_manager.filter(  # noqa: SLF001
            letter_id=revision.letter_id,
            deleted=False,
        )
        .order_by("-resource_version")
        .values_list("id", flat=True)
        .first()
    )
    if latest_id != revision.id:
        return False
    if expected_status == "draft":
        return bool(
            _artifact_for_revision(revision) is None
            and not has_unresolved_placeholder(revision.body)
        )
    return correspondence_revision_artifact_status(revision) == "available"


def correspondence_artifact_frozen_integrity_valid(artifact, revision) -> bool:
    return bool(
        artifact
        and revision.final_artifact_id == artifact.id
        and artifact.patient_id == revision.letter.patient_id
        and artifact.encounter_id == revision.letter.encounter_id
        and artifact.source_version == revision.resource_version
        and artifact.source_snapshot_hash == revision.revision_hash
        and artifact.report_type == "encounter_report"
        and len(artifact.artifact_sha256) == SHA256_HEX_LENGTH
    )


def _artifact_for_revision(revision):
    return (
        ReportUpload._base_manager.select_related("generated_by")  # noqa: SLF001
        .filter(letter_revision=revision)
        .first()
    )
