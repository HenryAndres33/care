import time
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import models

from care.emr.models import EMRBaseModel
from care.emr.utils.file_manager import S3FilesManager
from care.users.models import User
from care.utils.csp.config import BucketType
from care.utils.models.validators import parse_file_extension

FORM_ARTIFACT_SOURCE_CONSTRAINT = "formartifact_source_version_uniq"


class ReportUpload(EMRBaseModel):
    FORM_ARTIFACT_SOURCE_CONSTRAINT_NAME = FORM_ARTIFACT_SOURCE_CONSTRAINT

    template = models.ForeignKey(
        "emr.Template", on_delete=models.PROTECT, null=True, blank=True
    )

    name = models.CharField(max_length=2000)
    internal_name = models.CharField(max_length=2000)
    associating_id = models.CharField(max_length=100, blank=False, null=False)
    upload_completed = models.BooleanField(default=False)
    report_type = models.CharField(max_length=50)

    # Immutable provenance for system-generated finalized FormSubmission artifacts.
    patient = models.ForeignKey(
        "emr.Patient",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="form_submission_artifacts",
    )
    encounter = models.ForeignKey(
        "emr.Encounter",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="form_submission_artifacts",
    )
    form_submission = models.ForeignKey(
        "emr.FormSubmission",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="printable_artifacts",
    )
    source_version = models.PositiveIntegerField(null=True, blank=True)
    source_snapshot_hash = models.CharField(max_length=64, default="", blank=True)
    artifact_sha256 = models.CharField(max_length=64, default="", blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    generated_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="generated_form_submission_artifacts",
    )

    # Archived metadata
    is_archived = models.BooleanField(default=False)
    archive_reason = models.TextField(blank=True)
    archived_datetime = models.DateTimeField(blank=True, null=True)
    archived_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="archived_reports",
    )

    files_manager = S3FilesManager(BucketType.REPORT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["form_submission", "source_version"],
                name=FORM_ARTIFACT_SOURCE_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    # Plain template report: no provenance at all.
                    models.Q(
                        form_submission__isnull=True,
                        patient__isnull=True,
                        encounter__isnull=True,
                        source_version__isnull=True,
                        source_snapshot_hash="",
                        artifact_sha256="",
                        generated_at__isnull=True,
                        generated_by__isnull=True,
                        template__isnull=False,
                    )
                    # Generated clinical artifact (finalized form PDF, or a
                    # correspondence letter PDF linked from
                    # CorrespondenceLetterRevision.final_artifact).
                    | (
                        models.Q(
                            patient__isnull=False,
                            encounter__isnull=False,
                            source_version__isnull=False,
                            generated_at__isnull=False,
                            generated_by__isnull=False,
                            template__isnull=True,
                            upload_completed=True,
                            report_type="encounter_report",
                        )
                        & ~models.Q(source_snapshot_hash="")
                        & ~models.Q(artifact_sha256="")
                    )
                ),
                name="formartifact_provenance_ck",
            ),
        ]

    @property
    def file_type(self):
        """Alias for report_type to maintain compatibility with S3FilesManager"""
        return self.report_type

    def get_extension(self):
        extensions = parse_file_extension(self.internal_name)
        return f".{'.'.join(extensions)}" if extensions else ""

    def save(self, *args, **kwargs):
        """
        Create a random internal name to internally manage the file
        This is used as an intermediate step to avoid leakage of PII in-case of data leak
        """
        skip_internal_name = kwargs.pop("skip_internal_name", False)
        if self.pk:
            persisted = (
                self.__class__._base_manager.filter(pk=self.pk)  # noqa: SLF001
                .only("generated_at", "upload_completed")
                .first()
            )
            if persisted and persisted.generated_at and persisted.upload_completed:
                raise ValidationError("Generated clinical artifacts are immutable")
        if (not self.internal_name or not self.id) and not skip_internal_name:
            internal_name = str(uuid4()) + str(int(time.time()))
            if self.internal_name and (extension := self.get_extension()):
                internal_name = f"{internal_name}{extension}"
            self.internal_name = internal_name
        return super().save(*args, **kwargs)
