from django.utils import timezone

from care.emr.models.questionnaire import (
    FormSubmission,
)
from care.emr.resources.form_submission.spec import (
    FormSubmissionStatusChoices,
)
from care_suriname.api.viewsets.form_commands.errors import (
    _CommandConflictError,
)
from care_suriname.correspondence.correction import (
    FormSubmissionSeriesHeadIntegrityError,
    advance_finalized_form_series,
    create_finalized_form_series_head,
)
from care_suriname.resources.form_submission.commands import (
    finalized_form_submission_snapshot_hash,
)
from care_suriname.resources.form_submission.note_labs import register_note_labs
from care_suriname.resources.form_submission.structured_actions import (
    InvalidStructuredClinicalActionLink,
    clone_structured_clinical_action_links,
)


class MutationsMethods:
    def _update_draft(self, target, request_spec, *, series_head=None):
        del series_head
        if target.status == FormSubmissionStatusChoices.submitted.value:
            return self._raise_immutable_conflict()
        if target.status != FormSubmissionStatusChoices.draft.value:
            return self._raise_non_draft_conflict()
        target.response_dump = request_spec.response_dump
        register_note_labs(target, self.request.user)
        target.resource_version += 1
        target.updated_by = self.request.user
        target.save(
            update_fields=[
                "response_dump",
                "resource_version",
                "updated_by",
                "modified_date",
            ]
        )
        return target

    def _finalize(self, target, request_spec, *, series_head=None):
        del series_head
        if target.status == FormSubmissionStatusChoices.submitted.value:
            return self._raise_immutable_conflict()
        if target.status != FormSubmissionStatusChoices.draft.value:
            return self._raise_non_draft_conflict()
        register_note_labs(target, self.request.user, finalize=True)
        target.status = FormSubmissionStatusChoices.submitted.value
        target.resource_version += 1
        target.workflow_finalized_at = timezone.now()
        target.workflow_finalized_by = self.request.user
        target.updated_by = self.request.user
        target.finalized_snapshot_hash = finalized_form_submission_snapshot_hash(target)
        target.save(
            update_fields=[
                "status",
                "resource_version",
                "workflow_finalized_at",
                "workflow_finalized_by",
                "updated_by",
                "finalized_snapshot_hash",
                "modified_date",
            ]
        )
        create_finalized_form_series_head(
            submission=target,
            actor=self.request.user,
        )
        return target

    def _amend(self, target, request_spec, *, series_head=None):
        if target.status != FormSubmissionStatusChoices.submitted.value:
            return self._raise_immutable_conflict()
        if series_head is None or series_head.current_submission_id != target.id:
            raise FormSubmissionSeriesHeadIntegrityError
        result = FormSubmission(
            questionnaire=target.questionnaire,
            patient=target.patient,
            encounter=target.encounter,
            status=FormSubmissionStatusChoices.submitted.value,
            response_dump=request_spec.response_dump,
            series_id=target.series_id,
            resource_version=target.resource_version + 1,
            previous_version=target,
            amendment_reason=request_spec.reason.strip(),
            amendment_type=request_spec.amendment_type,
            workflow_finalized_at=timezone.now(),
            workflow_finalized_by=self.request.user,
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        result.finalized_snapshot_hash = finalized_form_submission_snapshot_hash(result)
        result.save(force_insert=True)
        register_note_labs(result, self.request.user, finalize=True)
        try:
            clone_structured_clinical_action_links(
                source=target,
                result=result,
                actor=self.request.user,
            )
        except InvalidStructuredClinicalActionLink as exc:
            raise _CommandConflictError(
                self._structured_action_linkage_conflict()
            ) from exc
        advance_finalized_form_series(
            head=series_head,
            previous=target,
            result=result,
            actor=self.request.user,
        )
        return result

    def _enter_in_error(self, target, request_spec, *, series_head=None):
        del series_head
        if target.status not in {
            FormSubmissionStatusChoices.draft.value,
            FormSubmissionStatusChoices.submitted.value,
        }:
            return self._raise_non_draft_conflict()
        was_draft = target.status == FormSubmissionStatusChoices.draft.value
        target.status = FormSubmissionStatusChoices.entered_in_error.value
        target.entered_in_error_at = timezone.now()
        target.entered_in_error_by = self.request.user
        target.entered_in_error_reason = request_spec.reason.strip()
        target.updated_by = self.request.user
        update_fields = [
            "entered_in_error_at",
            "entered_in_error_by",
            "entered_in_error_reason",
            "status",
            "updated_by",
            "modified_date",
        ]
        if was_draft:
            target.resource_version += 1
            update_fields.append("resource_version")
        target.save(update_fields=update_fields)
        return target
