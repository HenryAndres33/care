from django.db import IntegrityError, transaction
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError

from care.emr.models.encounter import Encounter
from care.emr.models.patient import Patient
from care.emr.models.questionnaire import (
    FormSubmission,
)
from care.emr.resources.encounter.constants import CLINICALLY_CLOSED_CHOICES
from care.emr.resources.form_submission.spec import (
    FormSubmissionStatusChoices,
)
from care.security.authorization.base import AuthorizationController
from care.utils.shortcuts import get_object_or_404
from care_suriname.api.viewsets.form_commands.errors import (
    _CommandConflictError,
    _constraint_name,
)
from care_suriname.correspondence.correction import (
    FormSubmissionSeriesHeadIntegrityError,
    lock_current_finalized_form_series,
)
from care_suriname.models.correspondence_correction import (
    CorrespondenceCorrectionOutbox,
    CorrespondenceSourceCorrection,
    FormSubmissionSeriesHead,
)
from care_suriname.models.form_submission_command import (
    FormSubmissionCommand,
)
from care_suriname.resources.form_submission.commands import (
    canonical_form_submission_command_hash,
)
from care_suriname.workflow_capabilities import require_workflow_mutations_enabled


class ExecutionMethods:
    def _execute_command(  # noqa: PLR0911, PLR0912
        self,
        request,
        *,
        request_spec_type,
        command_type,
        mutation,
        created=False,
    ):
        request_spec = request_spec_type.model_validate(request.data)
        target = get_object_or_404(
            FormSubmission._base_manager.select_related(  # noqa: SLF001
                "questionnaire",
                "patient",
                "encounter",
                "previous_version",
                "created_by",
                "updated_by",
                "workflow_finalized_by",
            ),
            external_id=self.kwargs["external_id"],
        )
        self._authorize_read(target)
        if target.deleted:
            return self._idempotency_conflict()
        payload_hash = canonical_form_submission_command_hash(
            request_spec,
            command_type=command_type,
            target_id=target.external_id,
            actor_id=request.user.external_id,
        )
        if response := self._command_replay_response(
            request_spec, command_type, target, payload_hash
        ):
            return response
        if (
            command_type in {"finalize", "amend", "enter_in_error"}
            and target.encounter_id
        ):
            require_workflow_mutations_enabled(target.encounter.facility.external_id)
        if command_type == "amend" and (
            response := self._response_dump_validation_response(
                request_spec.response_dump
            )
        ):
            return response
        self._validate_command_context(request_spec, target)

        try:
            with transaction.atomic():
                command_target = target
                series_head = None
                uses_series_head = command_type == "amend" or (
                    command_type == "enter_in_error"
                    and command_target.status
                    == FormSubmissionStatusChoices.submitted.value
                )
                if uses_series_head:
                    try:
                        series_head, current = lock_current_finalized_form_series(
                            command_target
                        )
                    except FormSubmissionSeriesHeadIntegrityError:
                        return self._series_integrity_conflict()
                    self._authorize_read(current)
                    if response := self._command_replay_response(
                        request_spec,
                        command_type,
                        command_target,
                        payload_hash,
                    ):
                        return response
                    if current.pk != command_target.pk:
                        return self._version_conflict(series_head.current_version)
                    target = current
                else:
                    target = self._lock_submission(command_target.pk)
                self._authorize_read(target)
                if command_type != "amend" and (
                    response := self._command_replay_response(
                        request_spec, command_type, target, payload_hash
                    )
                ):
                    return response
                self._validate_command_context(request_spec, target)
                if command_type == "finalize" and (
                    response := self._response_dump_validation_response(
                        target.response_dump
                    )
                ):
                    return response
                if command_type == "finalize" and (
                    response := self._urology_operation_validation_response(
                        target.response_dump,
                        questionnaire_slug=target.questionnaire.slug,
                    )
                ):
                    return response
                if command_type == "amend" and (
                    response := self._response_dump_validation_response(
                        target.response_dump,
                        authoritative_source=True,
                    )
                ):
                    return response
                if command_type == "amend" and (
                    response := self._urology_operation_validation_response(
                        request_spec.response_dump,
                        questionnaire_slug=target.questionnaire.slug,
                    )
                ):
                    return response
                self._lock_and_authorize_write_context(target, command_type)
                if target.resource_version != request_spec.expected_version:
                    return self._version_conflict(target.resource_version)

                result = mutation(target, request_spec, series_head=series_head)
                FormSubmissionCommand.objects.create(
                    client_request_id=request_spec.client_request_id,
                    payload_hash=payload_hash,
                    command_type=command_type,
                    expected_version=request_spec.expected_version,
                    actor=request.user,
                    patient=target.patient,
                    encounter=target.encounter,
                    questionnaire=target.questionnaire,
                    target_submission=target,
                    result_submission=result,
                    created_by=request.user,
                    updated_by=request.user,
                )
        except IntegrityError as exc:
            constraint_name = _constraint_name(exc)
            if constraint_name == FormSubmissionCommand.IDEMPOTENCY_CONSTRAINT_NAME:
                response = self._command_replay_response(
                    request_spec, command_type, target, payload_hash
                )
                if response:
                    return response
            if constraint_name == FormSubmission.SERIES_VERSION_CONSTRAINT_NAME:
                latest_version = (
                    FormSubmission.objects.filter(series_id=target.series_id)
                    .order_by("-resource_version")
                    .values_list("resource_version", flat=True)
                    .first()
                )
                return self._version_conflict(latest_version)
            if constraint_name in {
                FormSubmissionSeriesHead.SERIES_CONSTRAINT_NAME,
                FormSubmissionSeriesHead.CURRENT_CONSTRAINT_NAME,
                CorrespondenceSourceCorrection.SEQUENCE_CONSTRAINT_NAME,
                CorrespondenceSourceCorrection.PREVIOUS_SOURCE_CONSTRAINT_NAME,
                CorrespondenceSourceCorrection.NEW_SOURCE_CONSTRAINT_NAME,
                CorrespondenceSourceCorrection.HASH_CONSTRAINT_NAME,
                CorrespondenceCorrectionOutbox.SOURCE_CONSTRAINT_NAME,
            }:
                return self._series_integrity_conflict()
            raise
        except _CommandConflictError as exc:
            return exc.response
        except FormSubmissionSeriesHeadIntegrityError:
            return self._series_integrity_conflict()

        return self._command_response(
            request_spec.client_request_id,
            result,
            replayed=False,
            response_status=(
                status.HTTP_201_CREATED if created else status.HTTP_200_OK
            ),
        )

    def _validate_command_context(self, request_spec, target):
        encounter_id = target.encounter.external_id if target.encounter_id else None
        if not all(
            [
                target.patient.external_id == request_spec.patient,
                encounter_id == request_spec.encounter,
                target.questionnaire.slug == request_spec.questionnaire,
            ]
        ):
            raise Http404("Form submission context not found")

    def _lock_and_authorize_write_context(self, target, command_type):
        self._authorize_questionnaire_submission(target.questionnaire)
        if target.encounter_id:
            encounter = Encounter.objects.select_for_update().get(
                pk=target.encounter_id
            )
            if encounter.patient_id != target.patient_id:
                raise Http404("Form submission context not found")
            if (
                encounter.status in CLINICALLY_CLOSED_CHOICES
                and command_type != "enter_in_error"
            ):
                raise ValidationError(
                    "Clinically closed encounter forms require a reconciliation workflow"
                )
            target.encounter = encounter
            if command_type == "enter_in_error":
                if not AuthorizationController.call(
                    "can_mark_encounter_questionnaire_entered_in_error",
                    self.request.user,
                    encounter,
                ):
                    raise PermissionDenied(
                        "Permission denied for form submission context"
                    )
            else:
                self._authorize_write(encounter=encounter)
        else:
            patient = Patient.objects.select_for_update().get(pk=target.patient_id)
            target.patient = patient
            self._authorize_write(patient=patient)
