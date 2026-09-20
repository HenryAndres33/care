from rest_framework import status
from rest_framework.response import Response

from care.emr.resources.form_submission.spec import (
    FormSubmissionReadSpec,
)
from care_suriname.api.viewsets.form_commands.errors import (
    _CommandConflictError,
)
from care_suriname.models.form_submission_command import FormSubmissionCommand
from care_suriname.reports.form_submission_artifact import (
    MalformedFinalizedSnapshotError,
    validate_response_dump,
)
from care_suriname.resources.form_submission.urology_operation import (
    UROLOGY_OPERATIONS_QUESTIONNAIRE,
    InvalidUrologyOperationResponseError,
    validate_urology_operation_response_dump,
)


class ResponsesMethods:
    @staticmethod
    def _command_response(client_request_id, submission, *, replayed, response_status):
        response = Response(
            {
                "client_request_id": str(client_request_id),
                "replayed": replayed,
                "form_submission": FormSubmissionReadSpec.serialize(
                    submission
                ).to_json(),
            },
            status=response_status,
        )
        response["ETag"] = f'"{submission.external_id}:{submission.resource_version}"'
        return response

    @staticmethod
    def _idempotency_conflict():
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

    @staticmethod
    def _form_instance_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "form_submission_instance_conflict",
                        "msg": (
                            "form_instance_id is already bound to another "
                            "form submission command"
                        ),
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _series_integrity_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "form_submission_series_integrity_failed",
                        "msg": "Finalized form series provenance is unavailable",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _structured_action_linkage_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "structured_action_linkage_invalid",
                        "msg": (
                            "Finalized structured clinical-action linkage is "
                            "missing, invalid, or ambiguous"
                        ),
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    def _response_dump_validation_response(
        self, response_dump, *, authoritative_source=False
    ):
        try:
            validate_response_dump(response_dump)
        except (MalformedFinalizedSnapshotError, RecursionError):
            if authoritative_source:
                return self._series_integrity_conflict()
            return Response(
                {
                    "errors": [
                        {
                            "type": "form_submission_response_invalid",
                            "msg": "Form response is not valid bounded JSON",
                        }
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    @staticmethod
    def _urology_operation_validation_response(response_dump, *, questionnaire_slug):
        if questionnaire_slug != UROLOGY_OPERATIONS_QUESTIONNAIRE:
            return None
        try:
            validate_urology_operation_response_dump(response_dump)
        except InvalidUrologyOperationResponseError as exc:
            return Response(
                {
                    "errors": [
                        {
                            "type": "urology_operation_invalid",
                            "msg": str(exc),
                        }
                    ]
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return None

    def _raise_version_conflict(self, current_version):
        raise _CommandConflictError(self._version_conflict(current_version))

    def _raise_immutable_conflict(self):
        raise _CommandConflictError(self._immutable_conflict())

    def _raise_non_draft_conflict(self):
        raise _CommandConflictError(self._non_draft_conflict())

    def _command_replay_response(
        self, request_spec, command_type, target, payload_hash
    ):
        command = (
            FormSubmissionCommand._base_manager.select_related(  # noqa: SLF001
                "actor",
                "patient",
                "encounter",
                "questionnaire",
                "target_submission",
                "result_submission__questionnaire",
                "result_submission__patient",
                "result_submission__encounter",
                "result_submission__previous_version",
                "result_submission__created_by",
                "result_submission__updated_by",
                "result_submission__workflow_finalized_by",
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if not command:
            return None
        matches = all(
            [
                not command.deleted,
                not command.result_submission.deleted,
                command.payload_hash == payload_hash,
                command.command_type == command_type,
                command.actor_id == self.request.user.id,
                command.target_submission_id == target.id,
                command.patient_id == target.patient_id,
                command.encounter_id == target.encounter_id,
                command.questionnaire_id == target.questionnaire_id,
            ]
        )
        if not matches:
            return self._idempotency_conflict()
        self._authorize_read(command.result_submission)
        return self._command_response(
            request_spec.client_request_id,
            command.result_submission,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )
