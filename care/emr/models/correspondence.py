from django.core.exceptions import ValidationError
from django.db import models

from care.emr.models.base import EMRBaseModel

CORRESPONDENCE_SOURCE_CONSTRAINT = "corrcompile_source_fingerprint_uniq"
CORRESPONDENCE_COMMAND_CONSTRAINT = "corrcompile_cmd_request_id_uniq"


class CorrespondenceCompilation(EMRBaseModel):
    SOURCE_CONSTRAINT_NAME = CORRESPONDENCE_SOURCE_CONSTRAINT

    source_fingerprint = models.CharField(max_length=64)
    patient = models.ForeignKey("emr.Patient", on_delete=models.PROTECT)
    encounter = models.ForeignKey("emr.Encounter", on_delete=models.PROTECT)
    facility = models.ForeignKey("facility.Facility", on_delete=models.PROTECT)
    department = models.ForeignKey("emr.FacilityOrganization", on_delete=models.PROTECT)
    encounter_reason = models.ForeignKey("emr.TagConfig", on_delete=models.PROTECT)
    form_submission = models.ForeignKey(
        "emr.FormSubmission",
        on_delete=models.PROTECT,
        related_name="correspondence_compilations",
    )
    form_artifact = models.ForeignKey(
        "emr.ReportUpload",
        on_delete=models.PROTECT,
        related_name="correspondence_compilations",
    )
    template = models.ForeignKey(
        "emr.Template",
        on_delete=models.PROTECT,
        related_name="correspondence_compilations",
    )
    author = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="authored_correspondence_compilations",
    )
    form_source_version = models.PositiveIntegerField()
    form_source_hash = models.CharField(max_length=64)
    form_artifact_hash = models.CharField(max_length=64)
    template_version = models.PositiveIntegerField()
    template_hash = models.CharField(max_length=64)
    medication_sources = models.JSONField(default=list)
    source_provenance = models.JSONField(default=dict)
    compiled_text = models.TextField()
    compiled_html = models.TextField()
    compiled_hash = models.CharField(max_length=64)
    compiled_at = models.DateTimeField()
    status = models.CharField(max_length=32, default="compiled")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_fingerprint"],
                name=CORRESPONDENCE_SOURCE_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="compiled")
                    & ~models.Q(form_source_hash="")
                    & ~models.Q(form_artifact_hash="")
                    & ~models.Q(template_hash="")
                    & ~models.Q(compiled_hash="")
                    & ~models.Q(compiled_html="")
                    & ~models.Q(compiled_text="")
                ),
                name="corrcompile_complete_snapshot_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Compiled correspondence snapshots are immutable")
        return super().save(*args, **kwargs)


class CorrespondenceCompileCommand(EMRBaseModel):
    IDEMPOTENCY_CONSTRAINT_NAME = CORRESPONDENCE_COMMAND_CONSTRAINT

    client_request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    actor = models.ForeignKey("users.User", on_delete=models.PROTECT)
    patient = models.ForeignKey("emr.Patient", on_delete=models.PROTECT)
    encounter = models.ForeignKey("emr.Encounter", on_delete=models.PROTECT)
    form_submission = models.ForeignKey("emr.FormSubmission", on_delete=models.PROTECT)
    result_compilation = models.ForeignKey(
        CorrespondenceCompilation,
        on_delete=models.PROTECT,
        related_name="idempotency_commands",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["client_request_id"],
                name=CORRESPONDENCE_COMMAND_CONSTRAINT,
            )
        ]
