from django.db import transaction
from pydantic import UUID4, BaseModel, ConfigDict
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from care.emr.models.emergency_admission import EmergencyAdmission
from care.emr.models.encounter import ACTIVE_INPATIENT_STATUSES, Encounter
from care.emr.workflow_capabilities import require_workflow_mutations_enabled
from care.security.authorization import AuthorizationController
from care.utils.shortcuts import get_object_or_404


class EmergencyAdmissionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    admission_id: UUID4


def authorize(user, encounter, *, write=False):
    permissions = ["can_view_encounter_clinical_data"]
    if write:
        permissions.append("can_update_encounter_obj")
    if any(
        not AuthorizationController.call(permission, user, encounter)
        for permission in permissions
    ):
        raise PermissionDenied("Emergency admission access denied")


def response_for(link):
    response = Response(
        {
            "handoff": (
                {
                    "emergency_id": str(link.emergency.external_id),
                    "admission_id": str(link.admission.external_id),
                    "patient_id": str(link.emergency.patient.external_id),
                    "facility_id": str(link.emergency.facility.external_id),
                    "created_at": link.created_at.isoformat(),
                }
                if link
                else None
            )
        }
    )
    response["Cache-Control"] = "no-store"
    return response


class EmergencyAdmissionMixin:
    @action(detail=True, methods=["get", "post"], url_path="admission-handoff")
    def admission_handoff(self, request, *args, **kwargs):
        emergency = get_object_or_404(
            Encounter.objects.all(),
            external_id=self.kwargs["external_id"],
            deleted=False,
        )
        authorize(request.user, emergency, write=request.method == "POST")
        if request.method == "GET":
            link = (
                EmergencyAdmission.objects.select_related(
                    "emergency__patient", "emergency__facility", "admission"
                )
                .filter(emergency=emergency)
                .first()
            )
            if link:
                if link.admission.deleted:
                    raise ValidationError("Admission unavailable")
                authorize(request.user, link.admission)
            return response_for(link)

        spec = EmergencyAdmissionSpec.model_validate(request.data)
        # Deterministic encounter lock order; ordinary updates/discharge use
        # the same row locks. One-to-one constraint backs up retry safety.
        with transaction.atomic():
            rows = list(
                Encounter.objects.select_for_update()
                .filter(
                    external_id__in=[emergency.external_id, spec.admission_id],
                    deleted=False,
                )
                .order_by("pk")
            )
            emergency = next((row for row in rows if row.pk == emergency.pk), None)
            if emergency is None:
                raise ValidationError("Emergency unavailable")
            admission = next(
                (row for row in rows if row.external_id == spec.admission_id), None
            )
            if admission is None:
                raise ValidationError("Admission unavailable")
            authorize(request.user, emergency, write=True)
            authorize(request.user, admission, write=True)
            if (
                emergency.encounter_class != "emer"
                or admission.encounter_class != "imp"
                or emergency.patient_id != admission.patient_id
                or emergency.facility_id != admission.facility_id
            ):
                raise ValidationError("Emergency admission context mismatch")
            existing = EmergencyAdmission.objects.filter(emergency=emergency).first()
            if existing:
                if existing.admission_id != admission.pk:
                    return Response({"detail": "Emergency already linked"}, status=409)
                return response_for(existing)
            if any(row.status not in ACTIVE_INPATIENT_STATUSES for row in rows):
                raise ValidationError("Both encounters must be active")
            require_workflow_mutations_enabled(emergency.facility.external_id)
            link = EmergencyAdmission.objects.create(
                emergency=emergency, admission=admission, created_by=request.user
            )
            return response_for(link)
