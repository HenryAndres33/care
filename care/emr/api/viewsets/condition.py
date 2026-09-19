from django.db import IntegrityError, transaction
from django_filters import CharFilter, FilterSet, UUIDFilter
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters as rest_framework_filters
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from care.emr.api.viewsets.base import EMRModelViewSet, EMRQuestionnaireResponseMixin
from care.emr.api.viewsets.encounter_authz_base import EncounterBasedAuthorizationBase
from care.emr.models.condition import Condition
from care.emr.models.encounter import Encounter
from care.emr.models.patient import Patient
from care.emr.registries.system_questionnaire.system_questionnaire import (
    InternalQuestionnaireRegistry,
)
from care.emr.resources.condition.spec import (
    CategoryChoices,
    ConditionReadSpec,
    ConditionSpec,
    ConditionUpdateSpec,
)
from care.emr.resources.questionnaire.spec import SubjectType
from care.security.authorization import AuthorizationController
from care.utils.filters.multiselect import MultiSelectFilter
from care.utils.shortcuts import get_object_or_404
from care_suriname.resources.condition_idempotency import (
    IdempotentDiagnosisCreateResponseSpec,
    IdempotentDiagnosisCreateSpec,
    canonical_diagnosis_hash,
)


class ValidateEncounterMixin:
    """
    Mixin to validate encounter and its relationship with the patient.
    """

    def validate_data(self, instance, model_obj=None):
        # Ensure the encounter exists and matches the patient's external ID
        if model_obj:
            encounter = model_obj.encounter
        else:
            encounter = get_object_or_404(Encounter, external_id=instance.encounter)

        if str(encounter.patient.external_id) != self.kwargs["patient_external_id"]:
            raise ValidationError(
                "Patient external ID mismatch with encounter's patient"
            )


class ConditionFilters(FilterSet):
    encounter = UUIDFilter(field_name="encounter__external_id")
    clinical_status = MultiSelectFilter(field_name="clinical_status")
    exclude_clinical_status = MultiSelectFilter(
        field_name="clinical_status", exclude=True
    )
    verification_status = MultiSelectFilter(field_name="verification_status")
    exclude_verification_status = MultiSelectFilter(
        field_name="verification_status", exclude=True
    )

    severity = CharFilter(field_name="severity", lookup_expr="iexact")
    name = CharFilter(field_name="code__display", lookup_expr="icontains")
    category = MultiSelectFilter(field_name="category")


class SymptomViewSet(
    ValidateEncounterMixin,
    EncounterBasedAuthorizationBase,
    EMRQuestionnaireResponseMixin,
    EMRModelViewSet,
):
    database_model = Condition
    pydantic_model = ConditionSpec
    pydantic_read_model = ConditionReadSpec
    pydantic_update_model = ConditionUpdateSpec
    # Filters
    filterset_class = ConditionFilters
    filter_backends = [
        DjangoFilterBackend,
        rest_framework_filters.OrderingFilter,
    ]
    ordering_fields = ["created_date", "modified_date"]
    # Questionnaire Spec
    questionnaire_type = "symptom"
    questionnaire_title = "Symptom"
    questionnaire_description = "Symptom"
    questionnaire_subject_type = SubjectType.patient.value

    def perform_create(self, instance):
        instance.category = CategoryChoices.problem_list_item.value
        super().perform_create(instance)

    def get_queryset(self):
        # Check if the user has read access to the patient and their EMR Data
        self.authorize_read_encounter()
        return (
            super()
            .get_queryset()
            .filter(
                patient__external_id=self.kwargs["patient_external_id"],
                category=CategoryChoices.problem_list_item.value,
            )
            .select_related("patient", "encounter", "created_by", "updated_by")
        )


InternalQuestionnaireRegistry.register(SymptomViewSet)


class DiagnosisViewSet(
    ValidateEncounterMixin,
    EncounterBasedAuthorizationBase,
    EMRQuestionnaireResponseMixin,
    EMRModelViewSet,
):
    database_model = Condition
    pydantic_model = ConditionSpec
    pydantic_read_model = ConditionReadSpec
    pydantic_update_model = ConditionUpdateSpec

    # Filters
    filterset_class = ConditionFilters
    filter_backends = [
        DjangoFilterBackend,
        rest_framework_filters.OrderingFilter,
    ]
    ordering_fields = ["created_date", "modified_date"]
    # Questionnaire Spec
    questionnaire_type = "diagnosis"
    questionnaire_title = "Diagnosis"
    questionnaire_description = "Diagnosis"
    questionnaire_subject_type = SubjectType.patient.value

    def get_queryset(self):
        # Check if the user has read access to the patient and their EMR Data
        self.authorize_read_encounter()
        return (
            super()
            .get_queryset()
            .filter(patient__external_id=self.kwargs["patient_external_id"])
            .select_related("patient", "encounter", "created_by", "updated_by")
        )

    def authorize_update(self, request_obj, model_instance):
        if not AuthorizationController.call(
            "can_update_encounter_clinical_data",
            self.request.user,
            model_instance.encounter,
        ):
            raise PermissionDenied("You do not have permission to update encounter")

    def authorize_retrieve(self, model_instance):
        if AuthorizationController.call(
            "can_view_clinical_data", self.request.user, model_instance.patient
        ):
            return
        if not AuthorizationController.call(
            "can_view_encounter_clinical_data",
            self.request.user,
            model_instance.encounter,
        ):
            raise PermissionDenied("Permission denied to user")

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


InternalQuestionnaireRegistry.register(DiagnosisViewSet)
