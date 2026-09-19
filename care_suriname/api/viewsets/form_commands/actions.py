from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from care_suriname.resources.form_submission.commands import (
    AmendFormSubmissionSpec,
    EnterFormSubmissionInErrorSpec,
    FinalizeFormSubmissionSpec,
    FormSubmissionCommandResponseSpec,
    UpdateDraftFormSubmissionSpec,
)


class ActionsMethods:
    @extend_schema(
        request=UpdateDraftFormSubmissionSpec,
        responses={200: FormSubmissionCommandResponseSpec},
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-update-draft")
    def idempotent_update_draft(self, request, *args, **kwargs):
        return self._execute_command(
            request,
            request_spec_type=UpdateDraftFormSubmissionSpec,
            command_type="update_draft",
            mutation=self._update_draft,
        )

    @extend_schema(
        request=FinalizeFormSubmissionSpec,
        responses={200: FormSubmissionCommandResponseSpec},
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-finalize")
    def idempotent_finalize(self, request, *args, **kwargs):
        return self._execute_command(
            request,
            request_spec_type=FinalizeFormSubmissionSpec,
            command_type="finalize",
            mutation=self._finalize,
        )

    @extend_schema(
        request=AmendFormSubmissionSpec,
        responses={
            200: FormSubmissionCommandResponseSpec,
            201: FormSubmissionCommandResponseSpec,
        },
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-amend")
    def idempotent_amend(self, request, *args, **kwargs):
        return self._execute_command(
            request,
            request_spec_type=AmendFormSubmissionSpec,
            command_type="amend",
            mutation=self._amend,
            created=True,
        )

    @extend_schema(
        request=EnterFormSubmissionInErrorSpec,
        responses={200: FormSubmissionCommandResponseSpec},
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-enter-in-error")
    def idempotent_enter_in_error(self, request, *args, **kwargs):
        return self._execute_command(
            request,
            request_spec_type=EnterFormSubmissionInErrorSpec,
            command_type="enter_in_error",
            mutation=self._enter_in_error,
        )
