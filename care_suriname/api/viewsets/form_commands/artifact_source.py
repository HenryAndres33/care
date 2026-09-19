import hashlib
import logging

from django.http import Http404
from django.utils import timezone

from care.emr.models.questionnaire import (
    FormSubmission,
)
from care.emr.models.report.report_upload import (
    ReportUpload,
)
from care.emr.resources.form_submission.spec import (
    FormSubmissionStatusChoices,
)
from care.utils.shortcuts import get_object_or_404
from care_suriname.api.viewsets.form_commands.errors import (
    _ArtifactStaleSourceError,
    _ArtifactValidationError,
)
from care_suriname.correspondence.correction import (
    FormSubmissionSeriesHeadIntegrityError,
    lock_current_finalized_form_series,
)
from care_suriname.reports.form_submission_artifact import (
    build_form_submission_artifact_html,
    render_form_submission_artifact_pdf,
    validate_response_dump,
)
from care_suriname.resources.form_submission.artifact import (
    has_unresolved_placeholder,
)
from care_suriname.resources.form_submission.commands import (
    finalized_form_submission_snapshot_hash,
)

logger = logging.getLogger("care.emr.api.viewsets.form_submission")


class ArtifactSourceMethods:
    def _get_artifact_source(self):
        return get_object_or_404(
            FormSubmission._base_manager.select_related(  # noqa: SLF001
                "questionnaire",
                "patient",
                "encounter",
                "encounter__facility",
                "created_by",
                "workflow_finalized_by",
            ),
            external_id=self.kwargs["external_id"],
        )

    def _validate_artifact_context(self, request_spec, source):
        if not source.encounter_id:
            raise _ArtifactValidationError(
                "A finalized encounter-scoped FormSubmission is required"
            )
        if not all(
            [
                source.patient.external_id == request_spec.patient,
                source.encounter.external_id == request_spec.encounter,
                source.questionnaire.slug == request_spec.questionnaire,
            ]
        ):
            raise Http404("Form submission context not found")

    def _validate_finalized_artifact_source(self, request_spec, source):
        if source.deleted:
            raise _ArtifactValidationError("Finalized source is unavailable")
        if source.status != FormSubmissionStatusChoices.submitted.value:
            raise _ArtifactValidationError(
                "Only a workflow-finalized FormSubmission can be rendered"
            )
        if (
            source.resource_version != request_spec.source_version
            or source.finalized_snapshot_hash != request_spec.source_snapshot_hash
        ):
            raise _ArtifactStaleSourceError
        calculated_hash = finalized_form_submission_snapshot_hash(source)
        if (
            not source.finalized_snapshot_hash
            or calculated_hash != source.finalized_snapshot_hash
        ):
            raise _ArtifactValidationError(
                "Finalized source snapshot provenance is malformed"
            )
        validate_response_dump(source.response_dump)
        if has_unresolved_placeholder(source.response_dump):
            raise _ArtifactValidationError(
                "Finalized source contains unresolved placeholder content"
            )

    def _build_and_upload_artifact(self, source):
        generated_at = timezone.now()
        artifact = ReportUpload(
            template=None,
            name="Finalized form",
            internal_name="",
            associating_id=str(source.encounter.external_id),
            upload_completed=True,
            report_type="encounter_report",
            patient=source.patient,
            encounter=source.encounter,
            form_submission=source,
            source_version=source.resource_version,
            source_snapshot_hash=source.finalized_snapshot_hash,
            generated_at=generated_at,
            generated_by=self.request.user,
            created_by=self.request.user,
            updated_by=self.request.user,
            meta={"mime_type": "application/pdf"},
        )
        artifact.internal_name = f"{artifact.external_id}.pdf"
        artifact.name = f"Finalized form {artifact.external_id}"
        html = build_form_submission_artifact_html(
            artifact_id=artifact.external_id,
            submission=source,
            generated_at=generated_at,
        )
        pdf = render_form_submission_artifact_pdf(html)
        if not pdf or not pdf.startswith(b"%PDF"):
            raise _ArtifactValidationError("PDF renderer returned an invalid artifact")
        artifact.artifact_sha256 = hashlib.sha256(pdf).hexdigest()
        try:
            artifact.files_manager.put_object(
                artifact,
                pdf,
                ContentType="application/pdf",
            )
        except Exception:
            self._delete_uploaded_artifact(artifact)
            raise
        return artifact

    @staticmethod
    def _lock_current_artifact_source(source):
        try:
            _head, current = lock_current_finalized_form_series(source)
        except FormSubmissionSeriesHeadIntegrityError as exc:
            raise _ArtifactStaleSourceError from exc
        if current.pk != source.pk:
            raise _ArtifactStaleSourceError
        return current

    @staticmethod
    def _delete_uploaded_artifact(artifact):
        if not artifact:
            return
        try:
            artifact.files_manager.delete_object(artifact, quiet=True)
        except Exception as exc:  # best-effort compensation, never log content
            logger.error(
                "Form artifact storage compensation failed artifact=%s error=%s",
                artifact.external_id,
                type(exc).__name__,
            )
