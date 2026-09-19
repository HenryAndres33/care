import logging

from rest_framework import status
from rest_framework.response import Response

logger = logging.getLogger("care.emr.api.viewsets.form_submission")


class ArtifactResponsesMethods:
    @staticmethod
    def _artifact_command_response(
        client_request_id, artifact, *, replayed, response_status
    ):
        try:
            download_url = artifact.files_manager.read_signed_url(artifact)
        except Exception as exc:
            logger.warning(
                "Form artifact download URL unavailable artifact=%s error=%s",
                artifact.external_id,
                type(exc).__name__,
            )
            return Response(
                {
                    "client_request_id": str(client_request_id),
                    "replayed": replayed,
                    "errors": [
                        {
                            "type": "artifact_download_unavailable",
                            "msg": (
                                "Artifact is stored but its download URL is temporarily "
                                "unavailable; retry the exact command"
                            ),
                        }
                    ],
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        response = Response(
            {
                "client_request_id": str(client_request_id),
                "replayed": replayed,
                "artifact": {
                    "id": str(artifact.external_id),
                    "patient": str(artifact.patient.external_id),
                    "encounter": str(artifact.encounter.external_id),
                    "form_submission": str(artifact.form_submission.external_id),
                    "source_version": artifact.source_version,
                    "source_snapshot_hash": artifact.source_snapshot_hash,
                    "artifact_sha256": artifact.artifact_sha256,
                    "generated_at": artifact.generated_at,
                    "generated_by": str(artifact.generated_by.external_id),
                    "status": "completed",
                    "mime_type": "application/pdf",
                    "download_url": download_url,
                },
            },
            status=response_status,
        )
        response["ETag"] = f'"{artifact.external_id}:{artifact.artifact_sha256}"'
        return response

    @staticmethod
    def _artifact_validation_error(message):
        return Response(
            {"errors": [{"type": "artifact_source_invalid", "msg": str(message)}]},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    @staticmethod
    def _artifact_stale_source():
        return Response(
            {
                "errors": [
                    {
                        "type": "artifact_source_stale",
                        "msg": "Finalized FormSubmission version or hash does not match",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _artifact_conflict(error_type):
        return Response(
            {
                "errors": [
                    {
                        "type": error_type,
                        "msg": "Stored form artifact is unavailable",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _artifact_generation_failed():
        return Response(
            {
                "errors": [
                    {
                        "type": "artifact_generation_failed",
                        "msg": "No artifact was committed; retry with the same client_request_id",
                    }
                ]
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
