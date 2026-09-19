from django.db import IntegrityError, transaction
from django.http import Http404
from rest_framework import status

from care.emr.models.encounter import Encounter
from care.emr.models.report.report_upload import (
    ReportUpload,
)
from care.emr.reports.authorizers.utils import (
    read_report_authorizer,
    write_report_authorizer,
)
from care_suriname.api.viewsets.form_commands.errors import (
    _ArtifactStaleSourceError,
)
from care_suriname.models.form_submission_artifact_command import (
    FormSubmissionArtifactCommand,
)


class ArtifactReplayMethods:
    def _artifact_command_replay(self, request_spec, source, payload_hash):
        command = (
            FormSubmissionArtifactCommand._base_manager.select_related(  # noqa: SLF001
                "actor",
                "patient",
                "encounter",
                "source_submission__questionnaire",
                "source_submission__patient",
                "source_submission__encounter",
                "result_artifact__generated_by",
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if not command:
            return None
        artifact = command.result_artifact
        matches = all(
            [
                not command.deleted,
                command.payload_hash == payload_hash,
                command.actor_id == self.request.user.id,
                command.source_submission_id == source.id,
                command.patient_id == source.patient_id,
                command.encounter_id == source.encounter_id,
                command.source_version == source.resource_version,
                command.source_snapshot_hash == source.finalized_snapshot_hash,
                self._artifact_is_available(artifact, source),
            ]
        )
        if not matches:
            return self._idempotency_conflict()
        self._authorize_read(source)
        read_report_authorizer(
            self.request.user,
            artifact.report_type,
            artifact.associating_id,
        )
        return self._artifact_command_response(
            request_spec.client_request_id,
            artifact,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )

    def _artifact_command_values(self, request_spec, source, artifact, payload_hash):
        user = self.request.user
        return {
            "client_request_id": request_spec.client_request_id,
            "payload_hash": payload_hash,
            "actor": user,
            "patient": source.patient,
            "encounter": source.encounter,
            "source_submission": source,
            "source_version": source.resource_version,
            "source_snapshot_hash": source.finalized_snapshot_hash,
            "result_artifact": artifact,
            "created_by": user,
            "updated_by": user,
        }

    @staticmethod
    def _existing_source_artifact(source):
        return (
            ReportUpload._base_manager.select_related("generated_by")  # noqa: SLF001
            .filter(
                form_submission=source,
                source_version=source.resource_version,
            )
            .first()
        )

    @staticmethod
    def _artifact_is_available(artifact, source):
        return all(
            [
                not source.deleted,
                not artifact.deleted,
                not artifact.is_archived,
                artifact.upload_completed,
                artifact.patient_id == source.patient_id,
                artifact.encounter_id == source.encounter_id,
                artifact.form_submission_id == source.id,
                artifact.source_version == source.resource_version,
                artifact.source_snapshot_hash == source.finalized_snapshot_hash,
                artifact.report_type == "encounter_report",
            ]
        )

    def _attach_existing_artifact_command(self, request_spec, source, payload_hash):
        try:
            with transaction.atomic():
                if response := self._artifact_command_replay(
                    request_spec, source, payload_hash
                ):
                    return response
                source = self._lock_current_artifact_source(source)
                self._authorize_read(source)
                self._validate_artifact_context(request_spec, source)
                self._validate_finalized_artifact_source(request_spec, source)
                encounter = Encounter.objects.select_for_update().get(
                    pk=source.encounter_id
                )
                if encounter.patient_id != source.patient_id:
                    raise Http404("Form submission context not found")
                source.encounter = encounter
                write_report_authorizer(
                    self.request.user,
                    "encounter_report",
                    str(encounter.external_id),
                )
                artifact = self._existing_source_artifact(source)
                if not artifact or not self._artifact_is_available(artifact, source):
                    return self._artifact_conflict("artifact_source_unavailable")
                FormSubmissionArtifactCommand.objects.create(
                    **self._artifact_command_values(
                        request_spec, source, artifact, payload_hash
                    )
                )
        except IntegrityError:
            if response := self._artifact_command_replay(
                request_spec, source, payload_hash
            ):
                return response
            return self._idempotency_conflict()
        except _ArtifactStaleSourceError:
            return self._artifact_stale_source()
        return self._artifact_command_response(
            request_spec.client_request_id,
            artifact,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )
