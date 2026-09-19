from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from care.emr.models.encounter import ACTIVE_INPATIENT_STATUSES, Encounter
from care.emr.workflow_capabilities import require_workflow_mutations_enabled
from care.security.authorization import AuthorizationController
from care.utils.shortcuts import get_object_or_404
from care_suriname.models.admission_documentation import AdmissionDocumentation


class AdmissionDocumentationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["admission", "visit", "discharge"]
    visit_date: date | None = None


class AdmissionDocumentationMixin:
    @action(detail=True, methods=["post"], url_path="documentation-slot")
    def documentation_slot(self, request, *args, **kwargs):
        spec = AdmissionDocumentationSpec.model_validate(request.data)
        today = timezone.localdate(timezone=ZoneInfo("America/Paramaribo"))
        if spec.kind != "visit" and spec.visit_date is not None:
            raise ValidationError("Only ward visits accept a visit date")
        visit_date = spec.visit_date or today
        slot = f"visit:{visit_date.isoformat()}" if spec.kind == "visit" else spec.kind

        # Same lock as discharge: reservation cannot race past clinical closure.
        with transaction.atomic():
            admission = get_object_or_404(
                Encounter.objects.select_for_update(),
                external_id=self.kwargs["external_id"],
                deleted=False,
            )
            for permission in (
                "can_view_encounter_clinical_data",
                "can_update_encounter_obj",
            ):
                if not AuthorizationController.call(
                    permission, request.user, admission
                ):
                    raise PermissionDenied("Admission documentation access denied")
            if admission.encounter_class != "imp":
                raise ValidationError("An inpatient admission is required")
            existing = AdmissionDocumentation.objects.filter(
                admission=admission, slot=slot
            ).first()
            if existing is None:
                if admission.status not in ACTIVE_INPATIENT_STATUSES:
                    raise ValidationError("The admission is closed")
                if spec.kind == "visit" and visit_date != today:
                    raise ValidationError(
                        "New ward visits must use today's Suriname date"
                    )
                require_workflow_mutations_enabled(admission.facility.external_id)
                existing = AdmissionDocumentation.objects.create(
                    admission=admission, slot=slot, created_by=request.user
                )
            response = Response(
                {
                    "admission_id": str(admission.external_id),
                    "patient_id": str(admission.patient.external_id),
                    "facility_id": str(admission.facility.external_id),
                    "form_instance_id": str(existing.form_instance_id),
                    "slot": existing.slot,
                }
            )
            response["Cache-Control"] = "no-store"
            return response
