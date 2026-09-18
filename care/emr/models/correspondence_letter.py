from django.core.exceptions import ValidationError
from django.db import models

from care.emr.models.base import EMRBaseModel

LETTER_REVIEW_CONSTRAINT = "corrletter_review_uniq"
LETTER_REVISION_VERSION_CONSTRAINT = "corrletter_revision_ver_uniq"
LETTER_FINAL_CONSTRAINT = "corrletter_final_uniq"
LETTER_COMMAND_CONSTRAINT = "corrletter_cmd_request_id_uniq"


class CorrespondenceLetter(EMRBaseModel):
    REVIEW_CONSTRAINT_NAME = LETTER_REVIEW_CONSTRAINT

    review = models.ForeignKey(
        "emr.CorrespondenceReview",
        on_delete=models.PROTECT,
        related_name="letter_series",
    )
    review_hash = models.CharField(max_length=64)
    patient = models.ForeignKey("emr.Patient", on_delete=models.PROTECT)
    encounter = models.ForeignKey("emr.Encounter", on_delete=models.PROTECT)
    facility = models.ForeignKey("facility.Facility", on_delete=models.PROTECT)
    department = models.ForeignKey("emr.FacilityOrganization", on_delete=models.PROTECT)
    author = models.ForeignKey("users.User", on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["review"], name=LETTER_REVIEW_CONSTRAINT)
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Correspondence letter series are immutable")
        return super().save(*args, **kwargs)


class CorrespondenceLetterRevision(EMRBaseModel):
    VERSION_CONSTRAINT_NAME = LETTER_REVISION_VERSION_CONSTRAINT
    FINAL_CONSTRAINT_NAME = LETTER_FINAL_CONSTRAINT

    letter = models.ForeignKey(
        CorrespondenceLetter,
        on_delete=models.PROTECT,
        related_name="revisions",
    )
    previous_revision = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="next_revisions",
    )
    resource_version = models.PositiveIntegerField()
    status = models.CharField(max_length=32, default="draft")
    source_review_hash = models.CharField(max_length=64)
    body = models.TextField()
    body_hash = models.CharField(max_length=64)
    revision_hash = models.CharField(max_length=64)
    finalized_at = models.DateTimeField(null=True, blank=True)
    # The generated PDF for this revision, stored as a CARE ReportUpload. The
    # link lives here (custom → core) so no core table points at a plug model;
    # see docs/development/plug-app.md, Phase 2 step 1.
    final_artifact = models.OneToOneField(
        "emr.ReportUpload",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="letter_revision",
    )
    finalized_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="finalized_correspondence_letters",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["letter", "resource_version"],
                name=LETTER_REVISION_VERSION_CONSTRAINT,
            ),
            models.UniqueConstraint(
                fields=["letter"],
                condition=models.Q(status="finalized"),
                name=LETTER_FINAL_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="draft",
                        finalized_at__isnull=True,
                        finalized_by__isnull=True,
                    )
                    | models.Q(
                        status="finalized",
                        finalized_at__isnull=False,
                        finalized_by__isnull=False,
                    )
                ),
                name="corrletter_revision_status_ck",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(source_review_hash="")
                    & ~models.Q(body_hash="")
                    & ~models.Q(revision_hash="")
                ),
                name="corrletter_revision_hashes_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Correspondence letter revisions are immutable")
        return super().save(*args, **kwargs)


class CorrespondenceLetterCommand(EMRBaseModel):
    IDEMPOTENCY_CONSTRAINT_NAME = LETTER_COMMAND_CONSTRAINT

    client_request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    command_type = models.CharField(max_length=32)
    expected_version = models.PositiveIntegerField(null=True, blank=True)
    actor = models.ForeignKey("users.User", on_delete=models.PROTECT)
    letter = models.ForeignKey(
        CorrespondenceLetter,
        on_delete=models.PROTECT,
        related_name="commands",
    )
    review = models.ForeignKey("emr.CorrespondenceReview", on_delete=models.PROTECT)
    patient = models.ForeignKey("emr.Patient", on_delete=models.PROTECT)
    encounter = models.ForeignKey("emr.Encounter", on_delete=models.PROTECT)
    target_revision = models.ForeignKey(
        CorrespondenceLetterRevision,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="targeted_commands",
    )
    result_revision = models.ForeignKey(
        CorrespondenceLetterRevision,
        on_delete=models.PROTECT,
        related_name="result_commands",
    )
    result_artifact = models.ForeignKey(
        "emr.ReportUpload",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correspondence_letter_commands",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["client_request_id"],
                name=LETTER_COMMAND_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=models.Q(command_type__in=["create", "revise", "finalize"]),
                name="corrletter_cmd_type_ck",
            ),
        ]
