from django.db import IntegrityError, models, transaction
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import filters as rest_framework_filters
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from care.emr.api.viewsets.base import EMRModelViewSet, EMRQuestionnaireResponseMixin
from care.emr.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care.emr.api.viewsets.encounter_authz_base import EncounterBasedAuthorizationBase
from care.emr.models.encounter import Encounter
from care.emr.models.medication_request import MedicationRequest
from care.emr.registries.system_questionnaire.system_questionnaire import (
    InternalQuestionnaireRegistry,
)
from care.emr.resources.encounter.constants import COMPLETED_CHOICES
from care.emr.resources.inventory.product_knowledge.spec import ProductTypeOptions
from care.emr.resources.medication.request.idempotency import (
    IdempotentMedicationRequestCreateResponseSpec,
    IdempotentMedicationRequestCreateSpec,
    canonical_medication_request_hash,
)
from care.emr.resources.medication.request.spec import (
    MedicationRequestReadSpec,
    MedicationRequestSpec,
    MedicationRequestUpdateSpec,
    resolve_created_prescription,
)
from care.emr.resources.questionnaire.spec import SubjectType
from care.emr.workflow_capabilities import require_workflow_mutations_enabled
from care.security.authorization import AuthorizationController
from care.users.models import User
from care.utils.filters.multiselect import MultiSelectFilter
from care.utils.filters.null_filter import NullFilter
from care.utils.shortcuts import get_object_or_404


class MedicationFilter(filters.BooleanFilter):
    def filter(self, qs, value):
        if value:
            return qs.filter(
                models.Q(
                    requested_product__product_type__iexact=ProductTypeOptions.medication.value
                )
                | models.Q(requested_product__isnull=True)
            )
        return qs


class MedicationRequestFilter(filters.FilterSet):
    encounter = filters.UUIDFilter(field_name="encounter__external_id")
    status = MultiSelectFilter(field_name="status")
    name = filters.CharFilter(field_name="medication__display", lookup_expr="icontains")
    encounter_class = filters.CharFilter(
        field_name="encounter__class", lookup_expr="iexact"
    )
    priority = filters.CharFilter(lookup_expr="iexact")
    dispense_status = MultiSelectFilter(field_name="dispense_status")
    exclude_dispense_status = MultiSelectFilter(
        field_name="dispense_status", exclude=True
    )
    dispense_status_isnull = NullFilter(field_name="dispense_status")
    facility = filters.UUIDFilter(field_name="encounter__facility__external_id")
    prescription = filters.UUIDFilter(field_name="prescription__external_id")
    product_type = filters.CharFilter(
        field_name="requested_product__product_type", lookup_expr="iexact"
    )
    medications_only = MedicationFilter()


class MedicationRequestViewSet(
    ClinicalNoStoreResponseMixin,
    EncounterBasedAuthorizationBase,
    EMRQuestionnaireResponseMixin,
    EMRModelViewSet,
):
    database_model = MedicationRequest
    pydantic_model = MedicationRequestSpec
    pydantic_read_model = MedicationRequestReadSpec
    pydantic_update_model = MedicationRequestUpdateSpec
    questionnaire_type = "medication_request"
    questionnaire_title = "Medication Request"
    questionnaire_description = "Medication Request"
    questionnaire_subject_type = SubjectType.patient.value
    filterset_class = MedicationRequestFilter
    filter_backends = [
        filters.DjangoFilterBackend,
        rest_framework_filters.OrderingFilter,
    ]
    ordering_fields = ["created_date", "modified_date"]

    def perform_create(self, instance):
        """Serialize all medication creation with consult close on Encounter."""
        with transaction.atomic():
            encounter = Encounter._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            ).get(pk=instance.encounter_id)
            if encounter.status in COMPLETED_CHOICES:
                raise ValidationError(
                    "Cannot create medication requests on a terminal encounter"
                )
            if not AuthorizationController.call(
                "can_update_encounter_clinical_data",
                self.request.user,
                encounter,
            ):
                raise PermissionDenied("You do not have permission to update encounter")
            instance.encounter = encounter
            return super().perform_create(instance)

    def update(self, request, *args, **kwargs):
        reference = self.get_object()
        with transaction.atomic():
            encounter = Encounter._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            ).get(pk=reference.encounter_id)
            if encounter.status in COMPLETED_CHOICES:
                raise ValidationError(
                    "Cannot update medication requests on a terminal encounter"
                )
            medication = get_object_or_404(
                self.get_queryset().select_for_update(of=("self",)),
                pk=reference.pk,
            )
            medication.encounter = encounter
            self.authorize_update({}, medication)
            return Response(self.handle_update(medication, request.data))

    def destroy(self, request, *args, **kwargs):
        reference = self.get_object()
        with transaction.atomic():
            encounter = Encounter._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            ).get(pk=reference.encounter_id)
            if encounter.status in COMPLETED_CHOICES:
                raise ValidationError(
                    "Cannot delete medication requests on a terminal encounter"
                )
            medication = get_object_or_404(
                self.get_queryset().select_for_update(of=("self",)),
                pk=reference.pk,
            )
            medication.encounter = encounter
            self.authorize_destroy(medication)
            self.perform_destroy(medication)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get_queryset(self):
        self.authorize_read_for_medication()
        return (
            super()
            .get_queryset()
            .filter(patient__external_id=self.kwargs["patient_external_id"])
            .select_related("patient", "encounter", "created_by", "updated_by")
        )

    def authorize_create(self, instance):
        super().authorize_create(instance)
        if instance.requester:
            encounter = get_object_or_404(Encounter, external_id=instance.encounter)
            requester = get_object_or_404(User, external_id=instance.requester)
            if not AuthorizationController.call(
                "can_update_encounter_clinical_data", requester, encounter
            ):
                raise PermissionDenied(
                    "Requester does not have permission to update encounter"
                )

    def _validate_idempotent_read_context(self, request_spec, encounter=None):
        patient = self.get_patient_obj()
        encounter = encounter or get_object_or_404(
            Encounter, external_id=request_spec.encounter
        )
        patient_access = AuthorizationController.call(
            "can_view_clinical_data", self.request.user, patient
        )
        encounter_access = AuthorizationController.call(
            "can_view_encounter_clinical_data", self.request.user, encounter
        )
        if (
            not patient_access and not encounter_access
        ) or encounter.patient_id != patient.id:
            raise PermissionDenied("Permission denied for medication request context")
        return patient, encounter

    def _authorize_new_idempotent_create(self, request_spec, encounter):
        if not AuthorizationController.call(
            "can_update_encounter_clinical_data",
            self.request.user,
            encounter,
        ):
            raise PermissionDenied("You do not have permission to update encounter")
        if request_spec.requester:
            requester = get_object_or_404(User, external_id=request_spec.requester)
            if not AuthorizationController.call(
                "can_update_encounter_clinical_data", requester, encounter
            ):
                raise PermissionDenied(
                    "Requester does not have permission to update encounter"
                )

    def _idempotency_response(self, request_spec, patient, payload_hash):
        medication_request = (
            MedicationRequest._base_manager.select_related(  # noqa: SLF001
                "patient",
                "encounter",
                "requester",
                "requested_product",
                "prescription",
                "created_by",
                "updated_by",
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if not medication_request:
            return None
        requester_id = (
            str(medication_request.requester.external_id)
            if medication_request.requester
            else None
        )
        stored_context_matches = all(
            [
                not medication_request.deleted,
                medication_request.patient_id == patient.id,
                medication_request.encounter.external_id == request_spec.encounter,
                requester_id
                == (str(request_spec.requester) if request_spec.requester else None),
                medication_request.created_by_id == self.request.user.id,
            ]
        )
        if (
            not stored_context_matches
            or medication_request.client_request_payload_hash != payload_hash
        ):
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
        return Response(
            {
                "client_request_id": str(request_spec.client_request_id),
                "medication_request": MedicationRequestReadSpec.serialize(
                    medication_request
                ).to_json(),
                "replayed": True,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=IdempotentMedicationRequestCreateSpec,
        responses={
            200: IdempotentMedicationRequestCreateResponseSpec,
            201: IdempotentMedicationRequestCreateResponseSpec,
        },
    )
    @action(detail=False, methods=["POST"], url_path="idempotent-create")
    def idempotent_create(self, request, *args, **kwargs):
        request_spec = self._validate_idempotent_request(request.data)
        patient, _ = self._validate_idempotent_read_context(request_spec)
        payload_hash = self._idempotency_payload_hash(request_spec, patient)

        if response := self._idempotency_response(request_spec, patient, payload_hash):
            return response

        try:
            with transaction.atomic():
                encounter = get_object_or_404(
                    Encounter.objects.select_for_update(),
                    external_id=request_spec.encounter,
                )
                patient, encounter = self._validate_idempotent_read_context(
                    request_spec, encounter
                )
                if response := self._idempotency_response(
                    request_spec, patient, payload_hash
                ):
                    return response
                require_workflow_mutations_enabled(encounter.facility.external_id)
                self._authorize_new_idempotent_create(request_spec, encounter)

                create_spec = request_spec.model_copy(
                    update={"create_prescription": None}
                )
                create_spec._context = {"is_create": True}  # noqa: SLF001
                medication_request = create_spec.de_serialize()
                medication_request.encounter = encounter
                medication_request.patient = patient
                medication_request.client_request_id = request_spec.client_request_id
                medication_request.client_request_payload_hash = payload_hash
                medication_request.created_by = request.user
                medication_request.updated_by = request.user

                # Reserve the idempotency key before any prescription or
                # QuestionnaireResponse side effect is created.
                medication_request.save(force_insert=True)

                if request_spec.create_prescription:
                    medication_request.prescription = resolve_created_prescription(
                        request_spec.create_prescription,
                        medication_request,
                    )

                self.perform_create(medication_request)
        except IntegrityError as exc:
            if not _is_idempotency_constraint_violation(exc):
                raise
            response = self._idempotency_response(request_spec, patient, payload_hash)
            if response:
                return response
            raise

        return Response(
            {
                "client_request_id": str(request_spec.client_request_id),
                "medication_request": MedicationRequestReadSpec.serialize(
                    medication_request
                ).to_json(),
                "replayed": False,
            },
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=IdempotentMedicationRequestCreateSpec,
        responses={200: IdempotentMedicationRequestCreateResponseSpec},
    )
    @action(detail=False, methods=["POST"], url_path="idempotent-reconcile")
    def idempotent_reconcile(self, request, *args, **kwargs):
        request_spec = self._validate_idempotent_request(request.data)
        patient, _ = self._validate_idempotent_read_context(request_spec)
        payload_hash = self._idempotency_payload_hash(request_spec, patient)
        if response := self._idempotency_response(request_spec, patient, payload_hash):
            return response
        return Response(
            {
                "errors": [
                    {
                        "type": "idempotency_not_found",
                        "msg": "No matching idempotent medication request was found",
                    }
                ]
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    def _validate_idempotent_request(self, request_data):
        request_spec = IdempotentMedicationRequestCreateSpec.model_validate(
            request_data,
            context={"is_create": True},
        )
        request_spec._context = {"is_create": True}  # noqa: SLF001
        self.validate_data(request_spec, None)
        return request_spec

    def _idempotency_payload_hash(self, request_spec, patient):
        return canonical_medication_request_hash(
            request_spec,
            patient_id=patient.external_id,
            actor_id=self.request.user.external_id,
        )


def _is_idempotency_constraint_violation(exc):
    cause = getattr(exc, "__cause__", None)
    diagnostic = getattr(cause, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name:
        return constraint_name == MedicationRequest.IDEMPOTENCY_CONSTRAINT_NAME
    return MedicationRequest.IDEMPOTENCY_CONSTRAINT_NAME in str(exc)


InternalQuestionnaireRegistry.register(MedicationRequestViewSet)
