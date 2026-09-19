from django.db import models

from care.emr.models.base import EMRBaseModel
from care.emr.models.report.report_upload import (
    ReportUpload,
)
from care.users.models import User

FORM_ARTIFACT_COMMAND_IDEMPOTENCY_CONSTRAINT = "formartifact_cmd_request_id_uniq"


class FormSubmissionArtifactCommand(EMRBaseModel):
    IDEMPOTENCY_CONSTRAINT_NAME = FORM_ARTIFACT_COMMAND_IDEMPOTENCY_CONSTRAINT

    client_request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    patient = models.ForeignKey("emr.Patient", on_delete=models.PROTECT)
    encounter = models.ForeignKey("emr.Encounter", on_delete=models.PROTECT)
    source_submission = models.ForeignKey(
        "emr.FormSubmission",
        on_delete=models.PROTECT,
        related_name="artifact_commands",
    )
    source_version = models.PositiveIntegerField()
    source_snapshot_hash = models.CharField(max_length=64)
    result_artifact = models.ForeignKey(
        ReportUpload,
        on_delete=models.PROTECT,
        related_name="idempotency_commands",
    )

    class Meta:
        db_table = "emr_formsubmissionartifactcommand"
        constraints = [
            models.UniqueConstraint(
                fields=["client_request_id"],
                name=FORM_ARTIFACT_COMMAND_IDEMPOTENCY_CONSTRAINT,
            )
        ]
