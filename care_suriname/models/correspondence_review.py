from django.core.exceptions import ValidationError
from django.db import models

from care.emr.models.base import EMRBaseModel

RECIPIENT_SOURCE_CONSTRAINT = "corrrecipient_source_scope_uniq"
RECIPIENT_COMMAND_CONSTRAINT = "corrrecipient_cmd_request_id_uniq"
REVIEW_COMPILATION_CONSTRAINT = "corrreview_compilation_uniq"
REVIEW_COMMAND_CONSTRAINT = "corrreview_cmd_request_id_uniq"


class CorrespondenceRecipient(EMRBaseModel):
    SOURCE_CONSTRAINT_NAME = RECIPIENT_SOURCE_CONSTRAINT

    patient = models.ForeignKey(
        "emr.Patient",
        on_delete=models.PROTECT,
        related_name="correspondence_recipients",
    )
    facility = models.ForeignKey(
        "facility.Facility",
        on_delete=models.PROTECT,
        related_name="correspondence_recipients",
    )
    organization = models.ForeignKey(
        "emr.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    healthcare_service = models.ForeignKey(
        "emr.HealthcareService",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    recipient_kind = models.CharField(max_length=64, default="healthcare_professional")
    display_name = models.CharField(max_length=255)
    professional_role = models.CharField(max_length=255)
    qualification = models.CharField(max_length=255, blank=True, default="")
    registration = models.CharField(max_length=255, blank=True, default="")
    organization_name = models.CharField(max_length=255)
    postal_address = models.JSONField(default=dict)
    channel_type = models.CharField(max_length=64)
    channel_identifier = models.CharField(max_length=512)
    source_type = models.CharField(max_length=64)
    source_reference = models.CharField(max_length=255)
    source_provenance = models.JSONField(default=dict)
    active = models.BooleanField(default=True)
    verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="verified_correspondence_recipients",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    resource_version = models.PositiveIntegerField(default=1)
    content_hash = models.CharField(max_length=64, default="", blank=True)

    class Meta:
        db_table = "emr_correspondencerecipient"
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "patient", "source_type", "source_reference"],
                name=RECIPIENT_SOURCE_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(verified=False)
                    | models.Q(verified_by__isnull=False, verified_at__isnull=False)
                ),
                name="corrrecipient_verified_audit_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(recipient_kind="healthcare_professional"),
                name="corrrecipient_kind_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        from care.emr.correspondence.recipient import (
            InvalidRecipientDirectoryPayloadError,
            recipient_content_hash,
            validate_recipient_directory_payloads,
        )

        try:
            validate_recipient_directory_payloads(self)
        except InvalidRecipientDirectoryPayloadError as exc:
            raise ValidationError(str(exc)) from exc

        if self.pk:
            persisted = (
                self.__class__._base_manager.filter(pk=self.pk)  # noqa: SLF001
                .only("resource_version")
                .first()
            )
            if persisted:
                self.resource_version = persisted.resource_version + 1
        self.content_hash = recipient_content_hash(self)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                "resource_version",
                "content_hash",
            }
        return super().save(*args, **kwargs)


class CorrespondenceRecipientCommand(EMRBaseModel):
    IDEMPOTENCY_CONSTRAINT_NAME = RECIPIENT_COMMAND_CONSTRAINT

    client_request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    actor = models.ForeignKey("users.User", on_delete=models.PROTECT)
    patient = models.ForeignKey("emr.Patient", on_delete=models.PROTECT)
    facility = models.ForeignKey("facility.Facility", on_delete=models.PROTECT)
    result_recipient = models.ForeignKey(
        CorrespondenceRecipient,
        on_delete=models.PROTECT,
        related_name="idempotency_commands",
    )

    class Meta:
        db_table = "emr_correspondencerecipientcommand"
        constraints = [
            models.UniqueConstraint(
                fields=["client_request_id"],
                name=RECIPIENT_COMMAND_CONSTRAINT,
            )
        ]


class CorrespondenceReview(EMRBaseModel):
    COMPILATION_CONSTRAINT_NAME = REVIEW_COMPILATION_CONSTRAINT

    compilation = models.ForeignKey(
        "care_suriname.CorrespondenceCompilation",
        on_delete=models.PROTECT,
        related_name="reviews",
    )
    compilation_hash = models.CharField(max_length=64)
    source_fingerprint = models.CharField(max_length=64)
    patient = models.ForeignKey("emr.Patient", on_delete=models.PROTECT)
    encounter = models.ForeignKey("emr.Encounter", on_delete=models.PROTECT)
    facility = models.ForeignKey("facility.Facility", on_delete=models.PROTECT)
    department = models.ForeignKey("emr.FacilityOrganization", on_delete=models.PROTECT)
    author = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="authored_correspondence_reviews",
    )
    reviewer = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="performed_correspondence_reviews",
    )
    recipient = models.ForeignKey(
        CorrespondenceRecipient,
        on_delete=models.PROTECT,
        related_name="reviews",
    )
    recipient_version = models.PositiveIntegerField()
    recipient_hash = models.CharField(max_length=64)
    author_snapshot = models.JSONField(default=dict)
    recipient_snapshot = models.JSONField(default=dict)
    reviewed_at = models.DateTimeField()
    review_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=32, default="reviewed")

    class Meta:
        db_table = "emr_correspondencereview"
        constraints = [
            models.UniqueConstraint(
                fields=["compilation"],
                name=REVIEW_COMPILATION_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="reviewed")
                    & ~models.Q(compilation_hash="")
                    & ~models.Q(source_fingerprint="")
                    & ~models.Q(recipient_hash="")
                    & ~models.Q(review_hash="")
                    & ~models.Q(author_snapshot={})
                    & ~models.Q(recipient_snapshot={})
                ),
                name="corrreview_complete_snapshot_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Reviewed correspondence bindings are immutable")
        return super().save(*args, **kwargs)


class CorrespondenceReviewCommand(EMRBaseModel):
    IDEMPOTENCY_CONSTRAINT_NAME = REVIEW_COMMAND_CONSTRAINT

    client_request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    actor = models.ForeignKey("users.User", on_delete=models.PROTECT)
    compilation = models.ForeignKey(
        "care_suriname.CorrespondenceCompilation", on_delete=models.PROTECT
    )
    patient = models.ForeignKey("emr.Patient", on_delete=models.PROTECT)
    encounter = models.ForeignKey("emr.Encounter", on_delete=models.PROTECT)
    result_review = models.ForeignKey(
        CorrespondenceReview,
        on_delete=models.PROTECT,
        related_name="idempotency_commands",
    )

    class Meta:
        db_table = "emr_correspondencereviewcommand"
        constraints = [
            models.UniqueConstraint(
                fields=["client_request_id"],
                name=REVIEW_COMMAND_CONSTRAINT,
            )
        ]
