"""Consult-closure medication repair (owner decision, 27 September 2026).

`GET  consult-closure/<encounter>/medication-issues/?form_submission=<note>`
names each order that keeps the consult open, with the same rule the closure
preflight uses (`resources/closure_medications.py`).

`POST consult-closure/<encounter>/medication-retractions/` retracts one order
of that open consult (marks it entered-in-error, as the medication card does).
Retracting an already retracted/cancelled order is answered as a replay.

Mounted at `/api/care_suriname/` (`urls.py`); the closure preflight contract
itself is unchanged, so older frontends keep working.
"""

from django.db import transaction
from pydantic import UUID4, BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from care.emr.models.encounter import Encounter
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.questionnaire import FormSubmission
from care.emr.resources.encounter.constants import CLINICALLY_CLOSED_CHOICES
from care.security.authorization.base import AuthorizationController
from care.utils.shortcuts import get_object_or_404
from care_suriname.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care_suriname.resources.closure_medications import (
    IGNORED_MEDICATION_STATUSES,
    evaluate_closure_medications,
    medication_display,
    retract_medication_requests,
)
from care_suriname.workflow_capabilities import require_workflow_mutations_enabled


def authorize_consult_closure_read(user, encounter):
    if AuthorizationController.call(
        "can_view_clinical_data", user, encounter.patient
    ) or (
        AuthorizationController.call("can_view_encounter_obj", user, encounter)
        and AuthorizationController.call(
            "can_view_encounter_clinical_data", user, encounter
        )
    ):
        return
    raise PermissionDenied("Permission denied for consult close")


def _encounter(encounter_id, *, lock=False):
    queryset = Encounter._base_manager.select_related("patient", "facility")  # noqa: SLF001
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return get_object_or_404(queryset, external_id=encounter_id, deleted=False)


class _RetractionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    medication_request: UUID4


class ClosureMedicationIssuesView(ClinicalNoStoreResponseMixin, APIView):
    def get(self, request, encounter_id):
        encounter = _encounter(encounter_id)
        authorize_consult_closure_read(request.user, encounter)
        form_submission = request.query_params.get("form_submission")
        try:
            source = get_object_or_404(
                FormSubmission._base_manager,  # noqa: SLF001
                external_id=form_submission,
                encounter=encounter,
                deleted=False,
            )
        except (ValueError, ValidationError) as error:
            raise ValidationError("A valid form_submission is required") from error
        evaluation = evaluate_closure_medications(source, encounter, lock=False)
        return Response(
            {
                "encounter": encounter.external_id,
                "form_submission": source.external_id,
                "issues": [
                    {
                        "id": medication.external_id,
                        "display": medication_display(medication),
                        "status": medication.status,
                        "reason": reason,
                    }
                    for medication, reason in evaluation.issues
                ],
                "unnamed_link_problem": evaluation.malformed,
            }
        )


class ClosureMedicationRetractionView(ClinicalNoStoreResponseMixin, APIView):
    def post(self, request, encounter_id):
        try:
            spec = _RetractionSpec.model_validate(request.data)
        except PydanticValidationError as error:
            raise ValidationError("medication_request is required") from error
        with transaction.atomic():
            encounter = _encounter(encounter_id, lock=True)
            authorize_consult_closure_read(request.user, encounter)
            if encounter.status in CLINICALLY_CLOSED_CHOICES:
                return Response(
                    {"code": "encounter_closed"}, status=status.HTTP_409_CONFLICT
                )
            if not AuthorizationController.call(
                "can_update_encounter_clinical_data", request.user, encounter
            ):
                raise PermissionDenied("Cannot retract prescriptions here")
            require_workflow_mutations_enabled(encounter.facility.external_id)
            medication = get_object_or_404(
                MedicationRequest._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
                external_id=spec.medication_request,
                encounter=encounter,
                deleted=False,
            )
            replayed = medication.status in IGNORED_MEDICATION_STATUSES
            if not replayed:
                retract_medication_requests([medication], request.user)
        return Response(
            {
                "id": medication.external_id,
                "status": medication.status,
                "replayed": replayed,
            }
        )
