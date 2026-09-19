from django.db import transaction
from django.utils import timezone
from django_filters import rest_framework as filters
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from care.emr.api.viewsets.base import (
    EMRBaseViewSet,
    EMRCreateMixin,
    EMRListMixin,
    EMRRetrieveMixin,
    EMRUpdateMixin,
)
from care.emr.models.encounter import Encounter
from care.emr.models.patient import Patient
from care.emr.models.questionnaire import (
    FormSubmission,
    Questionnaire,
)
from care.emr.resources.form_submission.spec import (
    FormSubmissionReadSpec,
    FormSubmissionStatusChoices,
    FormSubmissionUpdateSpec,
    FormSubmissionWriteSpec,
)
from care.security.authorization.base import AuthorizationController
from care.utils.filters.dummy_filter import DummyUUIDFilter
from care.utils.filters.multiselect import MultiSelectFilter
from care.utils.shortcuts import get_object_or_404
from care_suriname.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from plugs.viewset_actions import with_contributed_actions


class FormSubmissionFilters(filters.FilterSet):
    encounter = DummyUUIDFilter()
    patient = DummyUUIDFilter()
    status = MultiSelectFilter(field_name="status")
    questionnaire = filters.CharFilter(
        field_name="questionnaire__slug", lookup_expr="iexact"
    )


@with_contributed_actions("form_submission")
class FormSubmissionViewSet(
    ClinicalNoStoreResponseMixin,
    EMRCreateMixin,
    EMRRetrieveMixin,
    EMRUpdateMixin,
    EMRListMixin,
    EMRBaseViewSet,
):
    database_model = FormSubmission
    pydantic_model = FormSubmissionWriteSpec
    pydantic_read_model = FormSubmissionReadSpec
    pydantic_update_model = FormSubmissionUpdateSpec
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = FormSubmissionFilters

    def validate_data(self, instance, model_obj=None):
        if model_obj is None and instance.status != FormSubmissionStatusChoices.draft:
            raise ValidationError(
                "Legacy create can only create a draft; use idempotent-finalize"
            )

    def authorize_create(self, instance):
        questionnaire = get_object_or_404(Questionnaire, slug=instance.questionnaire)
        self._authorize_questionnaire_submission(questionnaire)
        if instance.encounter:
            encounter = get_object_or_404(
                Encounter,
                external_id=instance.encounter,
                patient__external_id=instance.patient,
            )
            self._authorize_write(encounter=encounter)
        else:
            patient = get_object_or_404(Patient, external_id=instance.patient)
            self._authorize_write(patient=patient)
        return super().authorize_create(instance)

    def authorize_update(self, request_obj, model_instance):
        self._authorize_questionnaire_submission(model_instance.questionnaire)
        if model_instance.encounter:
            self._authorize_write(encounter=model_instance.encounter)
        else:
            self._authorize_write(patient=model_instance.patient)
        return super().authorize_update(request_obj, model_instance)

    def authorize_retrieve(self, model_instance):
        self._authorize_read(model_instance)

    def _authorize_read(self, submission):
        self._authorize_read_context(submission.patient, submission.encounter)

    def _authorize_read_context(self, patient, encounter=None):
        patient_access = AuthorizationController.call(
            "can_view_clinical_data", self.request.user, patient
        ) or AuthorizationController.call(
            "can_view_patient_questionnaire_responses",
            self.request.user,
            patient,
        )
        if encounter:
            encounter_access = AuthorizationController.call(
                "can_view_encounter_clinical_data",
                self.request.user,
                encounter,
            ) or AuthorizationController.call(
                "can_submit_encounter_questionnaire_obj",
                self.request.user,
                encounter,
            )
            allowed = patient_access or encounter_access
        else:
            allowed = patient_access or AuthorizationController.call(
                "can_submit_questionnaire_patient_obj",
                self.request.user,
                patient,
            )
        if not allowed:
            raise PermissionDenied("Permission denied for form submission context")

    def _authorize_write(self, patient=None, encounter=None):
        if patient and not AuthorizationController.call(
            "can_submit_questionnaire_patient_obj", self.request.user, patient
        ):
            raise PermissionDenied("Permission denied for form submission context")
        if encounter and not AuthorizationController.call(
            "can_submit_encounter_questionnaire_obj", self.request.user, encounter
        ):
            raise PermissionDenied("Permission denied for form submission context")

    def _authorize_questionnaire_submission(self, questionnaire):
        if not AuthorizationController.call(
            "can_submit_questionnaire_obj", self.request.user, questionnaire
        ):
            raise PermissionDenied("Permission denied for questionnaire submission")

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related(
                "questionnaire",
                "patient",
                "encounter",
                "previous_version",
                "created_by",
                "updated_by",
                "workflow_finalized_by",
            )
        )
        if self.action != "list":
            return queryset
        if "encounter" in self.request.GET:
            encounter = get_object_or_404(
                Encounter, external_id=self.request.GET["encounter"]
            )
            self._authorize_read_context(encounter.patient, encounter)
            return queryset.filter(encounter=encounter)
        if "patient" in self.request.GET:
            patient = get_object_or_404(
                Patient, external_id=self.request.GET["patient"]
            )
            self._authorize_read_context(patient)
            return queryset.filter(patient=patient)
        raise ValidationError("Patient or encounter is required")

    def update(self, request, *args, **kwargs):
        request_spec = FormSubmissionUpdateSpec.model_validate(request.data)
        if request_spec.status == FormSubmissionStatusChoices.submitted:
            raise ValidationError(
                "Legacy update is draft-only; use idempotent-finalize or idempotent-amend"
            )
        submission = self.get_object()
        with transaction.atomic():
            submission = self._lock_submission(submission.pk)
            self.authorize_update(request_spec, submission)
            if submission.status == FormSubmissionStatusChoices.submitted.value:
                return self._immutable_conflict()
            if submission.status != FormSubmissionStatusChoices.draft.value:
                return self._non_draft_conflict()
            if submission.resource_version != request_spec.expected_version:
                return self._version_conflict(submission.resource_version)
            update_fields = ["resource_version", "updated_by", "modified_date"]
            if request_spec.status == FormSubmissionStatusChoices.entered_in_error:
                submission.status = FormSubmissionStatusChoices.entered_in_error.value
                submission.entered_in_error_at = timezone.now()
                submission.entered_in_error_by = request.user
                submission.entered_in_error_reason = "Draft discarded"
                update_fields.extend(
                    [
                        "entered_in_error_at",
                        "entered_in_error_by",
                        "entered_in_error_reason",
                        "status",
                    ]
                )
            else:
                submission.response_dump = request_spec.response_dump
                update_fields.append("response_dump")
            submission.resource_version += 1
            submission.updated_by = request.user
            submission.save(update_fields=update_fields)
        return Response(FormSubmissionReadSpec.serialize(submission).to_json())

    @staticmethod
    def _lock_submission(pk):
        return (
            FormSubmission.objects.select_for_update(of=("self",))
            .select_related(
                "questionnaire",
                "patient",
                "encounter",
                "previous_version",
                "created_by",
                "updated_by",
                "workflow_finalized_by",
            )
            .get(pk=pk)
        )

    @staticmethod
    def _version_conflict(current_version):
        return Response(
            {
                "errors": [
                    {
                        "type": "version_conflict",
                        "msg": "Form submission has changed",
                    }
                ],
                "current_version": current_version,
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _immutable_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "finalized_form_immutable",
                        "msg": "Finalized form submissions are immutable",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _non_draft_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "form_submission_not_draft",
                        "msg": "Only an active draft can be mutated",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )
