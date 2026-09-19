from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from care.emr.api.viewsets.scheduling.schedule import get_schedulable_resource
from care.emr.models.encounter import Encounter
from care.emr.resources.scheduling.slot.spec import COMPLETED_STATUS_CHOICES
from care.security.authorization import AuthorizationController
from care.utils.shortcuts import get_object_or_404
from care_suriname.models.operation_plan import OperationPlan
from care_suriname.resources.scheduling.operation_plan import (
    OperationPlanSpec,
    OperationProgrammeSpec,
    OperationReportSlotSpec,
    serialize_planned_booking,
)
from care_suriname.workflow_capabilities import require_workflow_mutations_enabled


class OperationPlanMixin:
    def _operation_booking(self, *, lock=False):
        queryset = self.get_queryset().select_related(
            "token_slot__resource__facility",
            "token_slot__resource__user",
            "operation_plan__encounter",
        )
        if lock:
            queryset = queryset.select_for_update(of=("self",))
        booking = get_object_or_404(queryset, external_id=self.kwargs["external_id"])
        self.authorize_retrieve(booking)
        if booking.token_slot.resource.resource_type != "practitioner":
            raise ValidationError("Operation planning requires a practitioner booking")
        return booking

    @action(detail=False, methods=["get"], url_path="operation-programme")
    def operation_programme(self, request, *args, **kwargs):
        spec = OperationProgrammeSpec.model_validate(request.query_params.dict())
        resource = get_schedulable_resource(
            "practitioner", spec.surgeon, self.get_facility_obj()
        )
        if not resource or not AuthorizationController.call(
            "can_list_booking", resource, request.user
        ):
            raise PermissionDenied("Operation programme access denied")
        start = datetime.combine(spec.day, time.min, ZoneInfo("America/Paramaribo"))
        queryset = (
            self.get_queryset()
            .filter(
                token_slot__resource=resource,
                token_slot__start_datetime__gte=start,
                token_slot__start_datetime__lt=start + timedelta(days=1),
            )
            .select_related(
                "token_slot__resource__facility",
                "token_slot__resource__user",
                "operation_plan__encounter",
            )
            .order_by("token_slot__start_datetime", "id")
        )
        return Response(
            {
                "count": queryset.count(),
                "results": [
                    serialize_planned_booking(item)
                    for item in queryset[spec.offset : spec.offset + spec.limit]
                ],
            },
            headers={"Cache-Control": "no-store"},
        )

    @action(detail=True, methods=["get", "post"], url_path="operation-plan")
    def operation_plan(self, request, *args, **kwargs):
        with transaction.atomic():
            booking = self._operation_booking(lock=request.method == "POST")
            if request.method == "POST":
                spec = OperationPlanSpec.model_validate(request.data)
                self.authorize_update({}, booking)
                require_workflow_mutations_enabled(
                    booking.token_slot.resource.facility.external_id
                )
                if booking.status in COMPLETED_STATUS_CHOICES:
                    raise ValidationError("Terminal bookings cannot be planned")
                plan = getattr(booking, "operation_plan", None)
                if (plan.revision if plan else 0) != spec.expected_revision:
                    raise ValidationError("The plan changed; reload before saving")
                if plan and plan.encounter_id:
                    raise ValidationError(
                        "Documentation has started; the original plan is preserved"
                    )
                if plan is None:
                    plan = OperationPlan(booking=booking, created_by=request.user)
                else:
                    plan.revision += 1
                plan.procedure_key = spec.procedure_key
                plan.procedure_label = spec.procedure_label
                plan.updated_by = request.user
                plan.revisions = [
                    *plan.revisions,
                    {
                        "revision": plan.revision,
                        "procedure_key": plan.procedure_key,
                        "procedure_label": plan.procedure_label,
                        "actor": str(request.user.external_id),
                        "at": timezone.now().isoformat(),
                    },
                ]
                plan.save()
                booking.operation_plan = plan
            return Response(
                serialize_planned_booking(booking),
                headers={"Cache-Control": "no-store"},
            )

    @action(detail=True, methods=["post"], url_path="operation-report-slot")
    def operation_report_slot(self, request, *args, **kwargs):
        spec = OperationReportSlotSpec.model_validate(request.data)
        # Clinical lifecycle operations lock encounter first, then booking.
        with transaction.atomic():
            encounter = get_object_or_404(
                Encounter.objects.select_for_update(),
                external_id=spec.encounter_id,
                deleted=False,
            )
            for permission in (
                "can_view_encounter_clinical_data",
                "can_update_encounter_obj",
            ):
                if not AuthorizationController.call(
                    permission, request.user, encounter
                ):
                    raise PermissionDenied("Clinical documentation access denied")
            booking = self._operation_booking(lock=True)
            plan = getattr(booking, "operation_plan", None)
            if not plan or plan.revision != spec.expected_revision:
                raise ValidationError("The plan changed; reload before opening")
            if (
                encounter.patient_id != booking.patient_id
                or encounter.facility_id != booking.token_slot.resource.facility_id
            ):
                raise ValidationError("Patient or facility does not match")
            if plan.encounter_id and plan.encounter_id != encounter.id:
                raise ValidationError(
                    "The operation already belongs to another encounter"
                )
            if not plan.encounter_id:
                require_workflow_mutations_enabled(encounter.facility.external_id)
                if (
                    encounter.status not in ("in_progress", "on_hold")
                    or booking.status in COMPLETED_STATUS_CHOICES
                ):
                    raise ValidationError(
                        "An active encounter and booking are required"
                    )
                plan.encounter = encounter
                plan.updated_by = request.user
                plan.save(update_fields=["encounter", "updated_by", "updated_at"])
            return Response(
                serialize_planned_booking(booking),
                headers={"Cache-Control": "no-store"},
            )
