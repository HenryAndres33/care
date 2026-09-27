"""Derive consult-closure outcomes from the saved record.

Owner decision, 27 September 2026: the closure dialog no longer asks the
clinician what happened with medication and the GP letter; CARE can see both.
A derived outcome is then checked by the same evidence rules as a declared one,
so an unfinished prescription or letter still keeps the consult open.
"""

from care_suriname.models.correspondence_letter import CorrespondenceLetterRevision
from care_suriname.resources.closure_medications import evaluate_closure_medications


def finalized_paper_letter_revisions(source):
    """Finalized GP-letter revisions with an available PDF for this note."""
    return CorrespondenceLetterRevision._base_manager.filter(  # noqa: SLF001
        letter__review__compilation__form_submission=source,
        letter__review__compilation__deleted=False,
        letter__review__deleted=False,
        letter__deleted=False,
        status="finalized",
        deleted=False,
        final_artifact__deleted=False,
        final_artifact__is_archived=False,
        final_artifact__upload_completed=True,
    )


def derive_medication_outcome(source, encounter):
    evaluation = evaluate_closure_medications(source, encounter, lock=False)
    prescribed = evaluation.actions or evaluation.issues or evaluation.linked
    return "completed" if prescribed else "not_required"


def derive_correspondence_outcome(source):
    if finalized_paper_letter_revisions(source).exists():
        return "paper_prepared"
    return "not_required"


def with_derived_outcomes(request_spec, source, encounter):
    """Fill outcomes the request left out; declared outcomes stay as sent."""
    updates = {}
    if request_spec.medication_outcome is None:
        updates["medication_outcome"] = derive_medication_outcome(source, encounter)
    if request_spec.correspondence_outcome is None:
        updates["correspondence_outcome"] = derive_correspondence_outcome(source)
    return request_spec.model_copy(update=updates) if updates else request_spec
