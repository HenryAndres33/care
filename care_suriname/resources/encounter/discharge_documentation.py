"""Inspect the current admission summary and its finalized letter at discharge.

Lock the form head before the encounter, matching FormSubmission amendments.
Call inside the same transaction as discharge. Missing documentation is recorded
as a warning; the native encounter/bed safety checks remain authoritative.
"""

from care.emr.models.questionnaire import FormSubmission
from care_suriname.correspondence.correction import (
    FormSubmissionSeriesHeadIntegrityError,
    lock_current_finalized_form_series,
)
from care_suriname.correspondence.letter import correspondence_revision_actionable
from care_suriname.models.admission_documentation import AdmissionDocumentation
from care_suriname.models.correspondence_letter import CorrespondenceLetterRevision

MAX_DISCHARGE_LETTERS = 50


def lock_discharge_documentation(encounter):
    summary = _lock_summary(encounter)
    if summary is None:
        return ["discharge_summary_required"], None
    return _matching_letter(encounter, summary)


def _lock_summary(encounter):
    slot = AdmissionDocumentation.objects.filter(
        admission=encounter, slot="discharge"
    ).first()
    if not slot:
        return None
    original = FormSubmission.objects.filter(
        external_id=slot.form_instance_id,
        encounter=encounter,
        patient_id=encounter.patient_id,
        questionnaire__slug="urology-medisch-dossier",
        deleted=False,
    ).first()
    if not original:
        return None
    try:
        _head, summary = lock_current_finalized_form_series(original)
    except FormSubmissionSeriesHeadIntegrityError:
        return None
    if (
        summary.status != "submitted"
        or summary.encounter_id != encounter.id
        or summary.patient_id != encounter.patient_id
    ):
        return None
    return summary


def _matching_letter(encounter, summary):
    # Bound work in pathological histories; excessive candidates fail closed.
    revisions = list(
        CorrespondenceLetterRevision.objects.filter(
            letter__encounter=encounter,
            letter__patient_id=encounter.patient_id,
            letter__facility_id=encounter.facility_id,
            letter__review__compilation__form_submission=summary,
            status="finalized",
            deleted=False,
        )
        .select_related("letter__review__compilation", "letter__review__recipient")
        .order_by("-finalized_at")[: MAX_DISCHARGE_LETTERS + 1]
    )
    if len(revisions) > MAX_DISCHARGE_LETTERS:
        return ["discharge_letter_unavailable"], None
    for revision in revisions:
        if correspondence_revision_actionable(revision, expected_status="finalized"):
            return [], {
                "summary_submission": str(summary.external_id),
                "summary_version": summary.resource_version,
                "summary_hash": summary.finalized_snapshot_hash,
                "letter_revision": str(revision.external_id),
                "letter_hash": revision.revision_hash,
            }
    return [
        "discharge_letter_unavailable" if revisions else "discharge_letter_required"
    ], None
