import logging

from django.db import IntegrityError, transaction
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied

from care.emr.models.encounter import Encounter
from care.emr.models.report.report_upload import (
    ReportUpload,
)
from care.emr.reports.authorizers.utils import (
    write_report_authorizer,
)
from care_suriname.api.viewsets.form_commands.errors import (
    _ArtifactStaleSourceError,
    _ArtifactValidationError,
    _constraint_name,
)
from care_suriname.models.form_submission_artifact_command import (
    FormSubmissionArtifactCommand,
)
from care_suriname.reports.form_submission_artifact import (
    MalformedFinalizedSnapshotError,
)
from care_suriname.resources.form_submission.artifact import (
    FormSubmissionArtifactCommandResponseSpec,
    GenerateFormSubmissionArtifactSpec,
    canonical_artifact_command_hash,
)
from care_suriname.workflow_capabilities import require_workflow_mutations_enabled

logger = logging.getLogger("care.emr.api.viewsets.form_submission")


class ArtifactActionMethods:
    @extend_schema(
        request=GenerateFormSubmissionArtifactSpec,
        responses={
            200: FormSubmissionArtifactCommandResponseSpec,
            201: FormSubmissionArtifactCommandResponseSpec,
        },
    )
    @action(
        detail=True,
        methods=["POST"],
        url_path="idempotent-generate-artifact",
    )
    def idempotent_generate_artifact(self, request, *args, **kwargs):  # noqa: PLR0911, PLR0912
        request_spec = GenerateFormSubmissionArtifactSpec.model_validate(request.data)
        source = self._get_artifact_source()
        self._authorize_read(source)
        payload_hash = canonical_artifact_command_hash(
            request_spec,
            source_id=source.external_id,
            actor_id=request.user.external_id,
        )
        if response := self._artifact_command_replay(
            request_spec, source, payload_hash
        ):
            return response
        if source.deleted:
            return self._artifact_conflict("artifact_source_unavailable")
        try:
            self._validate_artifact_context(request_spec, source)
            require_workflow_mutations_enabled(source.encounter.facility.external_id)
        except _ArtifactValidationError as exc:
            return self._artifact_validation_error(str(exc))

        uploaded_artifact = None
        try:
            with transaction.atomic():
                if response := self._artifact_command_replay(
                    request_spec, source, payload_hash
                ):
                    return response
                source = self._lock_current_artifact_source(source)
                self._authorize_read(source)
                self._validate_artifact_context(request_spec, source)
                encounter = Encounter.objects.select_for_update().get(
                    pk=source.encounter_id
                )
                if encounter.patient_id != source.patient_id:
                    raise Http404("Form submission context not found")
                source.encounter = encounter
                self._authorize_read(source)
                write_report_authorizer(
                    request.user,
                    "encounter_report",
                    str(encounter.external_id),
                )
                self._validate_finalized_artifact_source(request_spec, source)

                existing = self._existing_source_artifact(source)
                if existing:
                    if not self._artifact_is_available(existing, source):
                        return self._artifact_conflict("artifact_source_unavailable")
                    FormSubmissionArtifactCommand.objects.create(
                        **self._artifact_command_values(
                            request_spec, source, existing, payload_hash
                        )
                    )
                    return self._artifact_command_response(
                        request_spec.client_request_id,
                        existing,
                        replayed=True,
                        response_status=status.HTTP_200_OK,
                    )

                uploaded_artifact = self._build_and_upload_artifact(source)
                uploaded_artifact.save(force_insert=True, skip_internal_name=True)
                FormSubmissionArtifactCommand.objects.create(
                    **self._artifact_command_values(
                        request_spec,
                        source,
                        uploaded_artifact,
                        payload_hash,
                    )
                )
        except IntegrityError as exc:
            self._delete_uploaded_artifact(uploaded_artifact)
            if (
                _constraint_name(exc)
                == FormSubmissionArtifactCommand.IDEMPOTENCY_CONSTRAINT_NAME
            ):
                if response := self._artifact_command_replay(
                    request_spec, source, payload_hash
                ):
                    return response
                return self._idempotency_conflict()
            if (
                _constraint_name(exc)
                == ReportUpload.FORM_ARTIFACT_SOURCE_CONSTRAINT_NAME
            ):
                return self._attach_existing_artifact_command(
                    request_spec, source, payload_hash
                )
            raise
        except _ArtifactStaleSourceError:
            self._delete_uploaded_artifact(uploaded_artifact)
            return self._artifact_stale_source()
        except (MalformedFinalizedSnapshotError, _ArtifactValidationError) as exc:
            self._delete_uploaded_artifact(uploaded_artifact)
            return self._artifact_validation_error(str(exc))
        except (Http404, PermissionDenied):
            self._delete_uploaded_artifact(uploaded_artifact)
            raise
        except Exception as exc:  # storage/renderer/ledger failure is fail-closed
            self._delete_uploaded_artifact(uploaded_artifact)
            logger.warning(
                "Form artifact generation failed source=%s artifact=%s error=%s",
                source.external_id,
                getattr(uploaded_artifact, "external_id", None),
                type(exc).__name__,
            )
            return self._artifact_generation_failed()

        return self._artifact_command_response(
            request_spec.client_request_id,
            uploaded_artifact,
            replayed=False,
            response_status=status.HTTP_201_CREATED,
        )
