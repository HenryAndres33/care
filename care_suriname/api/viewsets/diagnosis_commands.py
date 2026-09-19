"""Idempotent diagnosis command actions; native CRUD and authorization are inherited."""

from django.db import IntegrityError, transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from care.emr.models.condition import Condition
from care.emr.models.encounter import Encounter
from care.emr.models.patient import Patient
from care.emr.resources.condition.spec import ConditionReadSpec, ConditionSpec
from care.utils.shortcuts import get_object_or_404
from care_suriname.resources.condition_idempotency import (
    IdempotentDiagnosisCreateResponseSpec,
    IdempotentDiagnosisCreateSpec,
    canonical_diagnosis_hash,
)


class DiagnosisCommandActions:
    def _idempotency_response(self, request_spec, patient, payload_hash):
        diagnosis = (
            Condition._base_manager.select_related(  # noqa: SLF001
                "patient", "encounter", "created_by", "updated_by"
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if not diagnosis:
            return None
        context_matches = all(
            [
                not diagnosis.deleted,
                diagnosis.patient_id == patient.id,
                diagnosis.created_by_id == self.request.user.id,
                diagnosis.client_request_payload_hash == payload_hash,
            ]
        )
        if not context_matches:
            return Response(
                {
                    "errors": [
                        {
                            "type": "idempotency_conflict",
                            "msg": (
                                "client_request_id was already used with different "
                                "request data or context"
                            ),
                        }
                    ]
                },
                status=status.HTTP_409_CONFLICT,
            )
        self.authorize_retrieve(diagnosis)
        return Response(
            {
                "client_request_id": str(request_spec.client_request_id),
                "diagnosis": ConditionReadSpec.serialize(diagnosis).to_json(),
                "replayed": True,
            }
        )

    @extend_schema(
        request=IdempotentDiagnosisCreateSpec,
        responses={
            200: IdempotentDiagnosisCreateResponseSpec,
            201: IdempotentDiagnosisCreateResponseSpec,
        },
    )
    @action(detail=False, methods=["POST"], url_path="idempotent-create")
    def idempotent_create(self, request, *args, **kwargs):
        request_spec = IdempotentDiagnosisCreateSpec.model_validate(request.data)
        patient = get_object_or_404(
            Patient, external_id=self.kwargs["patient_external_id"]
        )
        payload_hash = canonical_diagnosis_hash(
            request_spec,
            patient_id=patient.external_id,
            actor_id=request.user.external_id,
        )
        if response := self._idempotency_response(request_spec, patient, payload_hash):
            return response

        try:
            with transaction.atomic():
                patient = get_object_or_404(
                    Patient.objects.select_for_update(),
                    external_id=self.kwargs["patient_external_id"],
                )
                encounter = get_object_or_404(
                    Encounter.objects.select_related("patient").select_for_update(),
                    external_id=request_spec.encounter,
                )
                if encounter.patient_id != patient.id:
                    raise ValidationError(
                        "Patient external ID mismatch with encounter's patient"
                    )
                if response := self._idempotency_response(
                    request_spec, patient, payload_hash
                ):
                    return response
                self.authorize_create(request_spec)

                duplicate = Condition.objects.filter(
                    patient=patient,
                    category=request_spec.category.value,
                    code__system=request_spec.code.system,
                    code__code=request_spec.code.code,
                    clinical_status__in=["active", "recurrence", "relapse"],
                ).exclude(verification_status__in=["entered_in_error", "refuted"])
                if duplicate.exists():
                    return Response(
                        {
                            "errors": [
                                {
                                    "type": "duplicate_diagnosis",
                                    "msg": "This diagnosis is already active.",
                                }
                            ]
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

                create_spec = ConditionSpec.model_validate(
                    request_spec.model_dump(exclude={"client_request_id", "meta", "id"})
                )
                create_spec._context = {"is_create": True}  # noqa: SLF001
                diagnosis = create_spec.de_serialize()
                diagnosis.encounter = encounter
                diagnosis.patient = patient
                diagnosis.client_request_id = request_spec.client_request_id
                diagnosis.client_request_payload_hash = payload_hash
                self.perform_create(diagnosis)
        except IntegrityError:
            response = self._idempotency_response(request_spec, patient, payload_hash)
            if response:
                return response
            raise

        return Response(
            {
                "client_request_id": str(request_spec.client_request_id),
                "diagnosis": ConditionReadSpec.serialize(diagnosis).to_json(),
                "replayed": False,
            },
            status=status.HTTP_201_CREATED,
        )
