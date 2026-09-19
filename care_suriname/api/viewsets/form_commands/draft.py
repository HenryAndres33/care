from django.db import IntegrityError, transaction
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action

from care.emr.models.encounter import Encounter
from care.emr.models.patient import Patient
from care.emr.models.questionnaire import (
    FormSubmission,
    Questionnaire,
)
from care.emr.resources.form_submission.spec import (
    FormSubmissionStatusChoices,
)
from care.utils.shortcuts import get_object_or_404
from care_suriname.models.form_submission_command import (
    FormSubmissionCommand,
)
from care_suriname.resources.form_submission.commands import (
    CreateDraftFormSubmissionSpec,
    FormSubmissionCommandResponseSpec,
    canonical_form_submission_create_hash,
)
from care_suriname.resources.form_submission.note_labs import register_note_labs


class DraftMethods:
    @extend_schema(
        request=CreateDraftFormSubmissionSpec,
        responses={
            200: FormSubmissionCommandResponseSpec,
            201: FormSubmissionCommandResponseSpec,
        },
    )
    @action(detail=False, methods=["POST"], url_path="idempotent-create-draft")
    def idempotent_create_draft(self, request, *args, **kwargs):
        request_spec = CreateDraftFormSubmissionSpec.model_validate(request.data)
        questionnaire, patient, encounter = self._resolve_create_draft_context(
            request_spec
        )
        payload_hash = canonical_form_submission_create_hash(
            request_spec,
            actor_id=request.user.external_id,
        )
        if response := self._create_draft_replay_response(request_spec, payload_hash):
            return response

        try:
            with transaction.atomic():
                if response := self._create_draft_replay_response(
                    request_spec, payload_hash
                ):
                    return response
                questionnaire, patient, encounter = self._lock_create_draft_context(
                    request_spec,
                    questionnaire=questionnaire,
                    patient=patient,
                    encounter=encounter,
                )
                response = self._create_draft_replay_response(
                    request_spec, payload_hash
                )
                if (
                    response is None
                    and FormSubmission._base_manager.filter(  # noqa: SLF001
                        external_id=request_spec.form_instance_id
                    ).exists()
                ):
                    response = self._form_instance_conflict()
                if response:
                    return response

                submission = FormSubmission(
                    external_id=request_spec.form_instance_id,
                    questionnaire=questionnaire,
                    patient=patient,
                    encounter=encounter,
                    status=FormSubmissionStatusChoices.draft.value,
                    response_dump=request_spec.response_dump,
                    created_by=request.user,
                    updated_by=request.user,
                )
                submission.save(force_insert=True)
                register_note_labs(submission, request.user)
                FormSubmissionCommand.objects.create(
                    client_request_id=request_spec.client_request_id,
                    payload_hash=payload_hash,
                    command_type="create_draft",
                    expected_version=1,
                    actor=request.user,
                    patient=patient,
                    encounter=encounter,
                    questionnaire=questionnaire,
                    target_submission=submission,
                    result_submission=submission,
                    created_by=request.user,
                    updated_by=request.user,
                )
        except IntegrityError:
            if response := self._create_draft_replay_response(
                request_spec, payload_hash
            ):
                return response
            if FormSubmission._base_manager.filter(  # noqa: SLF001
                external_id=request_spec.form_instance_id
            ).exists():
                return self._form_instance_conflict()
            raise

        return self._command_response(
            request_spec.client_request_id,
            submission,
            replayed=False,
            response_status=status.HTTP_201_CREATED,
        )

    def _resolve_create_draft_context(self, request_spec):
        questionnaire = get_object_or_404(
            Questionnaire, slug=request_spec.questionnaire
        )
        patient = get_object_or_404(Patient, external_id=request_spec.patient)
        encounter = None
        if request_spec.encounter:
            encounter = get_object_or_404(
                Encounter,
                external_id=request_spec.encounter,
                patient=patient,
            )
        self._authorize_questionnaire_submission(questionnaire)
        if encounter:
            self._authorize_write(encounter=encounter)
        else:
            self._authorize_write(patient=patient)
        return questionnaire, patient, encounter

    def _lock_create_draft_context(
        self,
        request_spec,
        *,
        questionnaire,
        patient,
        encounter,
    ):
        if encounter:
            encounter = (
                Encounter.objects.select_for_update()
                .select_related("patient")
                .get(pk=encounter.pk)
            )
            if (
                encounter.patient_id != patient.id
                or encounter.external_id != request_spec.encounter
            ):
                raise Http404("Form submission context not found")
            self._authorize_write(encounter=encounter)
        else:
            patient = Patient.objects.select_for_update().get(pk=patient.pk)
            self._authorize_write(patient=patient)
        self._authorize_questionnaire_submission(questionnaire)
        from care_suriname.resources.scheduling.operation_plan import (
            validate_planned_form_identity,
        )

        validate_planned_form_identity(
            request_spec.form_instance_id, questionnaire, patient, encounter
        )
        return questionnaire, patient, encounter

    def _create_draft_replay_response(self, request_spec, payload_hash):
        command = (
            FormSubmissionCommand._base_manager.select_related(  # noqa: SLF001
                "actor",
                "patient",
                "encounter",
                "questionnaire",
                "result_submission__questionnaire",
                "result_submission__patient",
                "result_submission__encounter",
                "result_submission__created_by",
                "result_submission__updated_by",
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if not command:
            return None
        submission = command.result_submission
        matches = all(
            [
                not command.deleted,
                not submission.deleted,
                command.payload_hash == payload_hash,
                command.command_type == "create_draft",
                command.expected_version == 1,
                command.actor_id == self.request.user.id,
                command.target_submission_id == submission.id,
                command.patient_id == submission.patient_id,
                command.encounter_id == submission.encounter_id,
                command.questionnaire_id == submission.questionnaire_id,
                submission.external_id == request_spec.form_instance_id,
                submission.status == FormSubmissionStatusChoices.draft.value,
                submission.resource_version == 1,
            ]
        )
        if not matches:
            return self._idempotency_conflict()
        self._authorize_read(submission)
        return self._command_response(
            request_spec.client_request_id,
            submission,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )
