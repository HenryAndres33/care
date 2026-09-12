from datetime import date
from typing import Literal

from pydantic import UUID4, BaseModel, ConfigDict, Field
from rest_framework.exceptions import ValidationError

from care.emr.models.operation_plan import OperationPlan


class OperationPlanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    procedure_key: Literal[
        "turp", "turbt", "urs", "open-nephrectomy", "cystoscopy", "other"
    ]
    procedure_label: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=0)


class OperationReportSlotSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    encounter_id: UUID4
    expected_revision: int = Field(ge=1)


class OperationProgrammeSpec(BaseModel):
    day: date
    surgeon: UUID4
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=25, ge=1, le=100)


def serialize_operation_plan(plan):
    return {
        "procedure_key": plan.procedure_key,
        "procedure_label": plan.procedure_label,
        "revision": plan.revision,
        "encounter_id": str(plan.encounter.external_id) if plan.encounter_id else None,
        "form_instance_id": str(plan.form_instance_id) if plan.encounter_id else None,
    }


def serialize_planned_booking(booking):
    resource = booking.token_slot.resource
    plan = getattr(booking, "operation_plan", None)
    return {
        "booking_id": str(booking.external_id),
        "facility_id": str(resource.facility.external_id),
        "patient_id": str(booking.patient.external_id),
        "patient_name": booking.patient.name,
        "surgeon_id": str(resource.user.external_id),
        "surgeon_name": resource.user.get_full_name(),
        "start": booking.token_slot.start_datetime.isoformat(),
        "end": booking.token_slot.end_datetime.isoformat(),
        "status": booking.status,
        "plan": serialize_operation_plan(plan) if plan else None,
    }


def validate_planned_form_identity(form_instance_id, questionnaire, patient, encounter):
    plan = (
        OperationPlan.objects.filter(form_instance_id=form_instance_id)
        .select_related("booking")
        .first()
    )
    if plan and (
        not plan.encounter_id
        or encounter is None
        or plan.encounter_id != encounter.id
        or plan.booking.patient_id != patient.id
        or questionnaire.slug != "urology-operaties"
    ):
        raise ValidationError("The reserved operation report context does not match")
