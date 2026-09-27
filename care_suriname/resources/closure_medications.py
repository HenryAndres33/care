"""Which orders of a consult belong on its final note, and retracting them.

Owner decisions, 27 September 2026:

- an order that is entered-in-error or cancelled counts as never given, so it
  no longer blocks consult closure;
- every other order of the encounter must be a confirmed, traceable order
  linked exactly once to the final note, because that note's PDF is the paper
  record; the closure dialog names each order that is not, so the clinician
  can retract it;
- discarding a note retracts the orders prescribed from it, unless another
  live note of the encounter still carries them.

Retraction marks the native MedicationRequest entered-in-error (CARE's own
retraction status, as the medication card does); nothing is deleted.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from rest_framework.exceptions import PermissionDenied

from care.emr.models.encounter import Encounter
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.questionnaire import QuestionnaireResponse
from care.emr.resources.encounter.constants import CLINICALLY_CLOSED_CHOICES
from care.security.authorization.base import AuthorizationController

IGNORED_MEDICATION_STATUSES = frozenset({"entered_in_error", "cancelled"})
CONFIRMED_MEDICATION_STATUSES = frozenset({"active", "completed"})
RETRACTED_STATUS = "entered_in_error"
MEDICATION_RESPONSE_TYPE = "medication_request"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class ClosureMedicationEvaluation:
    # Orders that are cleanly on the note: id + client_request_id.
    actions: list = field(default_factory=list)
    # (MedicationRequest, reason) for orders the clinician must resolve.
    issues: list = field(default_factory=list)
    # A note link that points at no order CARE can name (deleted or unknown).
    malformed: bool = False
    # The note links at least one order that still counts.
    linked: bool = False

    @property
    def complete(self):
        return not self.malformed and not self.issues


def _linked_medication_id(response):
    value = response.structured_responses
    data = value.get(MEDICATION_RESPONSE_TYPE, {}) if isinstance(value, dict) else {}
    medication_id = data.get("id") if isinstance(data, dict) else None
    return str(medication_id) if medication_id else None


def _issue_reason(medication, link_count, source):
    if link_count == 0:
        return "not_on_note"
    if link_count > 1:
        return "linked_twice"
    if medication.status not in CONFIRMED_MEDICATION_STATUSES:
        return "not_confirmed"
    if medication.intent != "order" or medication.do_not_perform:
        return "not_an_order"
    if (
        medication.patient_id != source.patient_id
        or not medication.client_request_id
        or not isinstance(medication.client_request_payload_hash, str)
        or not _SHA256.match(medication.client_request_payload_hash)
    ):
        return "not_traceable"
    return None


def evaluate_closure_medications(source, encounter, *, lock):
    responses = QuestionnaireResponse._base_manager.filter(  # noqa: SLF001
        form_submission=source,
        structured_response_type=MEDICATION_RESPONSE_TYPE,
    ).order_by("pk")
    medications = MedicationRequest._base_manager.filter(  # noqa: SLF001
        encounter=encounter, deleted=False
    ).order_by("external_id")
    if lock:
        responses = responses.select_for_update(of=("self",))
        medications = medications.select_for_update(of=("self",))
    medications = list(medications)
    ignored = {
        str(item.external_id)
        for item in medications
        if item.status in IGNORED_MEDICATION_STATUSES
    }
    live = [m for m in medications if m.status not in IGNORED_MEDICATION_STATUSES]

    evaluation = ClosureMedicationEvaluation()
    counts = Counter()
    for response in responses:
        medication_id = _linked_medication_id(response)
        if response.deleted or response.status != "completed" or not medication_id:
            evaluation.malformed = True
        elif medication_id not in ignored:
            counts[medication_id] += 1
    if set(counts) - {str(item.external_id) for item in live}:
        evaluation.malformed = True
    evaluation.linked = bool(counts)

    for medication in live:
        reason = _issue_reason(medication, counts[str(medication.external_id)], source)
        if reason:
            evaluation.issues.append((medication, reason))
        else:
            evaluation.actions.append(
                {
                    "id": medication.external_id,
                    "client_request_id": medication.client_request_id,
                }
            )
    return evaluation


def medication_display(medication):
    concept = medication.medication if isinstance(medication.medication, dict) else {}
    return str(concept.get("display") or concept.get("code") or "Onbekend middel")


def retract_medication_requests(medications, actor):
    for medication in medications:
        medication.status = RETRACTED_STATUS
        medication.updated_by = actor
        medication.save(update_fields=["status", "updated_by", "modified_date"])


def retract_orders_of_discarded_note(note, actor):
    """Retract orders prescribed from a note that is being entered in error."""
    linked = {
        medication_id
        for response in QuestionnaireResponse._base_manager.filter(  # noqa: SLF001
            form_submission=note,
            structured_response_type=MEDICATION_RESPONSE_TYPE,
            deleted=False,
        )
        if (medication_id := _linked_medication_id(response))
    }
    if not linked or not note.encounter_id:
        return 0
    encounter = Encounter._base_manager.select_for_update(of=("self",)).get(  # noqa: SLF001
        pk=note.encounter_id
    )
    if encounter.status in CLINICALLY_CLOSED_CHOICES:
        # A closed consult's orders are part of its closure record; leave them.
        return 0
    still_carried = {
        medication_id
        for response in QuestionnaireResponse._base_manager.filter(  # noqa: SLF001
            form_submission__encounter=encounter,
            structured_response_type=MEDICATION_RESPONSE_TYPE,
            deleted=False,
        )
        .exclude(form_submission=note)
        .exclude(form_submission__status="entered_in_error")
        if (medication_id := _linked_medication_id(response))
    }
    orders = list(
        MedicationRequest._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(
            encounter=encounter,
            external_id__in=linked - still_carried,
            deleted=False,
        )
        .exclude(status__in=IGNORED_MEDICATION_STATUSES)
        .order_by("pk")
    )
    if not orders:
        return 0
    if not AuthorizationController.call(
        "can_update_encounter_clinical_data", actor, encounter
    ):
        raise PermissionDenied("Cannot retract the prescriptions of this note")
    retract_medication_requests(orders, actor)
    return len(orders)
