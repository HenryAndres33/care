from django.core.exceptions import ValidationError
from django.db import models

from care.emr.models.base import EMRBaseModel

FORM_SUBMISSION_SERIES_HEAD_CONSTRAINT = "formsub_series_head_series_uniq"
FORM_SUBMISSION_SERIES_CURRENT_CONSTRAINT = "formsub_series_head_current_uniq"
CORRECTION_SEQUENCE_CONSTRAINT = "corrcorrection_head_sequence_uniq"
CORRECTION_PREVIOUS_SOURCE_CONSTRAINT = "corrcorrection_previous_source_uniq"
CORRECTION_NEW_SOURCE_CONSTRAINT = "corrcorrection_new_source_uniq"
CORRECTION_HASH_CONSTRAINT = "corrcorrection_hash_uniq"
CORRECTION_OUTBOX_CONSTRAINT = "corrcorrection_outbox_source_uniq"
CORRECTION_CASE_REVIEW_CONSTRAINT = "corrcase_review_uniq"
CORRECTION_CASE_DELIVERY_CONSTRAINT = "corrcase_delivery_uniq"
CORRECTION_EVENT_SEQUENCE_CONSTRAINT = "correvent_case_sequence_uniq"
CORRECTION_EVENT_SOURCE_CONSTRAINT = "correvent_case_source_uniq"
CORRECTION_EVENT_DELIVERY_CONSTRAINT = "correvent_case_delivery_uniq"
REPLACEMENT_ATTEMPT_NUMBER_CONSTRAINT = "corrreplace_case_attempt_uniq"
CORRECTION_COMMAND_CONSTRAINT = "corrcase_cmd_request_id_uniq"
PAPER_ATTESTATION_ATTEMPT_CONSTRAINT = "corrpaper_case_attempt_uniq"
PAPER_ATTESTATION_ARTIFACT_CONSTRAINT = "corrpaper_case_artifact_uniq"


class FormSubmissionSeriesHead(EMRBaseModel):
    SERIES_CONSTRAINT_NAME = FORM_SUBMISSION_SERIES_HEAD_CONSTRAINT
    CURRENT_CONSTRAINT_NAME = FORM_SUBMISSION_SERIES_CURRENT_CONSTRAINT

    series_id = models.UUIDField()
    current_submission = models.ForeignKey(
        "emr.FormSubmission",
        on_delete=models.PROTECT,
        related_name="current_series_heads",
    )
    current_version = models.PositiveIntegerField()
    current_snapshot_hash = models.CharField(max_length=64)
    advanced_at = models.DateTimeField()
    advanced_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="advanced_form_submission_series",
    )
    head_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["series_id"],
                name=FORM_SUBMISSION_SERIES_HEAD_CONSTRAINT,
            ),
            models.UniqueConstraint(
                fields=["current_submission"],
                name=FORM_SUBMISSION_SERIES_CURRENT_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(current_version__gt=0)
                    & models.Q(current_snapshot_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(head_hash__regex=r"^[0-9a-f]{64}$")
                ),
                name="formsub_series_head_complete_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    updated_by=models.F("advanced_by"),
                ),
                name="formsub_series_head_actor_ck",
            ),
        ]


class CorrespondenceSourceCorrection(EMRBaseModel):
    SEQUENCE_CONSTRAINT_NAME = CORRECTION_SEQUENCE_CONSTRAINT
    PREVIOUS_SOURCE_CONSTRAINT_NAME = CORRECTION_PREVIOUS_SOURCE_CONSTRAINT
    NEW_SOURCE_CONSTRAINT_NAME = CORRECTION_NEW_SOURCE_CONSTRAINT
    HASH_CONSTRAINT_NAME = CORRECTION_HASH_CONSTRAINT

    source_head = models.ForeignKey(
        FormSubmissionSeriesHead,
        on_delete=models.PROTECT,
        related_name="corrections",
    )
    sequence = models.PositiveIntegerField()
    source_head_hash = models.CharField(max_length=64)
    previous_submission = models.ForeignKey(
        "emr.FormSubmission",
        on_delete=models.PROTECT,
        related_name="source_corrections_from",
    )
    new_submission = models.ForeignKey(
        "emr.FormSubmission",
        on_delete=models.PROTECT,
        related_name="source_corrections_to",
    )
    previous_version = models.PositiveIntegerField()
    new_version = models.PositiveIntegerField()
    previous_snapshot_hash = models.CharField(max_length=64)
    new_snapshot_hash = models.CharField(max_length=64)
    amendment_type = models.CharField(max_length=32)
    reason = models.TextField(max_length=4000)
    corrected_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="correspondence_source_corrections",
    )
    corrected_at = models.DateTimeField()
    correction_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_head", "sequence"],
                name=CORRECTION_SEQUENCE_CONSTRAINT,
            ),
            models.UniqueConstraint(
                fields=["new_submission"],
                name=CORRECTION_NEW_SOURCE_CONSTRAINT,
            ),
            models.UniqueConstraint(
                fields=["previous_submission"],
                name=CORRECTION_PREVIOUS_SOURCE_CONSTRAINT,
            ),
            models.UniqueConstraint(
                fields=["correction_hash"],
                name=CORRECTION_HASH_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(sequence__gt=0)
                    & models.Q(previous_version__gt=0)
                    & models.Q(new_version=models.F("previous_version") + 1)
                    & models.Q(amendment_type__in=["amendment", "addendum"])
                    & ~models.Q(previous_submission=models.F("new_submission"))
                    & models.Q(previous_snapshot_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(new_snapshot_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(source_head_hash__regex=r"^[0-9a-f]{64}$")
                    & ~models.Q(reason="")
                    & models.Q(correction_hash__regex=r"^[0-9a-f]{64}$")
                ),
                name="corrcorrection_lineage_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    created_by=models.F("corrected_by"),
                    updated_by=models.F("corrected_by"),
                ),
                name="corrcorrection_actor_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Correspondence source corrections are immutable")
        return super().save(*args, **kwargs)


class CorrespondenceCorrectionOutbox(EMRBaseModel):
    SOURCE_CONSTRAINT_NAME = CORRECTION_OUTBOX_CONSTRAINT

    source_correction = models.ForeignKey(
        CorrespondenceSourceCorrection,
        on_delete=models.PROTECT,
        related_name="outbox_entries",
    )
    status = models.CharField(max_length=16, default="pending")
    attempt_count = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField()
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    safe_code = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_correction"],
                name=CORRECTION_OUTBOX_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="pending",
                        claimed_at__isnull=True,
                        claim_token__isnull=True,
                        lease_expires_at__isnull=True,
                        completed_at__isnull=True,
                    )
                    | models.Q(
                        status="processing",
                        attempt_count__gt=0,
                        claimed_at__isnull=False,
                        claim_token__isnull=False,
                        lease_expires_at__isnull=False,
                        completed_at__isnull=True,
                    )
                    | models.Q(
                        status="completed",
                        attempt_count__gt=0,
                        claimed_at__isnull=False,
                        claim_token__isnull=False,
                        lease_expires_at__isnull=False,
                        completed_at__isnull=False,
                    )
                    | models.Q(
                        status="failed_terminal",
                        attempt_count__gt=0,
                        claimed_at__isnull=False,
                        claim_token__isnull=False,
                        lease_expires_at__isnull=False,
                        completed_at__isnull=False,
                        safe_code__gt="",
                    )
                ),
                name="corrcorrection_outbox_state_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "available_at", "id"],
                name="corrcorr_outbox_ready_idx",
            ),
            models.Index(
                fields=["status", "claimed_at", "id"],
                name="corrcorr_outbox_reclaim_idx",
            ),
        ]


class CorrespondenceCorrectionCase(EMRBaseModel):
    REVIEW_CONSTRAINT_NAME = CORRECTION_CASE_REVIEW_CONSTRAINT
    DELIVERY_CONSTRAINT_NAME = CORRECTION_CASE_DELIVERY_CONSTRAINT

    source_head = models.ForeignKey(
        FormSubmissionSeriesHead,
        on_delete=models.PROTECT,
        related_name="correspondence_correction_cases",
    )
    source_head_hash = models.CharField(max_length=64)
    original_compilation = models.OneToOneField(
        "emr.CorrespondenceCompilation",
        on_delete=models.PROTECT,
        related_name="correction_case",
    )
    original_review = models.OneToOneField(
        "emr.CorrespondenceReview",
        on_delete=models.PROTECT,
        related_name="correction_case",
    )
    original_delivery = models.OneToOneField(
        "emr.CorrespondenceDelivery",
        on_delete=models.PROTECT,
        related_name="correction_case",
    )
    frozen_submission = models.ForeignKey(
        "emr.FormSubmission",
        on_delete=models.PROTECT,
        related_name="frozen_correspondence_correction_cases",
    )
    frozen_version = models.PositiveIntegerField()
    frozen_snapshot_hash = models.CharField(max_length=64)
    current_submission = models.ForeignKey(
        "emr.FormSubmission",
        on_delete=models.PROTECT,
        related_name="current_correspondence_correction_cases",
    )
    current_version = models.PositiveIntegerField()
    current_snapshot_hash = models.CharField(max_length=64)
    latest_source_correction = models.ForeignKey(
        CorrespondenceSourceCorrection,
        on_delete=models.PROTECT,
        related_name="correspondence_correction_cases",
    )
    latest_source_correction_hash = models.CharField(max_length=64)
    change_set_hash = models.CharField(max_length=64)
    delivery_state = models.CharField(max_length=32)
    delivery_certainty = models.CharField(max_length=32)
    notification_status = models.CharField(max_length=32)
    paper_reconciliation_status = models.CharField(max_length=32, default="required")
    replacement_status = models.CharField(max_length=32, default="not_started")
    replacement_delivery_state = models.CharField(
        max_length=32,
        null=True,
        blank=True,
    )
    replacement_delivery_certainty = models.CharField(
        max_length=32,
        null=True,
        blank=True,
    )
    replacement_attempt = models.ForeignKey(
        "emr.CorrespondenceReplacementAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="current_for_cases",
    )
    replacement_compilation = models.ForeignKey(
        "emr.CorrespondenceCompilation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="replacement_correction_cases",
    )
    replacement_review = models.ForeignKey(
        "emr.CorrespondenceReview",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="replacement_correction_cases",
    )
    replacement_revision = models.ForeignKey(
        "emr.CorrespondenceLetterRevision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="replacement_correction_cases",
    )
    replacement_artifact = models.ForeignKey(
        "emr.ReportUpload",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="replacement_correction_cases",
    )
    replacement_delivery = models.ForeignKey(
        "emr.CorrespondenceDelivery",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="replacement_correction_cases",
    )
    status = models.CharField(max_length=16, default="open")
    resource_version = models.PositiveIntegerField(default=1)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_mode = models.CharField(max_length=32, null=True, blank=True)
    resolved_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="resolved_correspondence_correction_cases",
    )
    case_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["original_review"],
                name=CORRECTION_CASE_REVIEW_CONSTRAINT,
            ),
            models.UniqueConstraint(
                fields=["original_delivery"],
                name=CORRECTION_CASE_DELIVERY_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(frozen_version__gt=0)
                    & models.Q(current_version__gt=models.F("frozen_version"))
                    & models.Q(resource_version__gt=0)
                    & models.Q(source_head_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(frozen_snapshot_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(current_snapshot_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(latest_source_correction_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(change_set_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(case_hash__regex=r"^[0-9a-f]{64}$")
                ),
                name="corrcase_lineage_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    delivery_state__in=[
                        "dispatch_pending",
                        "dispatching",
                        "acknowledged",
                        "failed_retryable",
                        "failed_terminal",
                        "outcome_unknown",
                    ],
                    delivery_certainty__in=[
                        "not_attempted",
                        "attempting",
                        "acknowledged",
                        "not_delivered",
                        "unknown",
                    ],
                    notification_status__in=[
                        "not_required",
                        "required",
                        "pending",
                        "acknowledged",
                        "failed",
                        "unknown",
                    ],
                    paper_reconciliation_status__in=[
                        "not_required",
                        "required",
                        "acknowledged",
                    ],
                    replacement_status__in=[
                        "not_started",
                        "compiling",
                        "draft",
                        "finalized",
                        "delivery_pending",
                        "acknowledged",
                        "failed",
                    ],
                ),
                name="corrcase_states_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        replacement_delivery__isnull=True,
                        replacement_delivery_state__isnull=True,
                        replacement_delivery_certainty__isnull=True,
                    )
                    | models.Q(
                        replacement_delivery__isnull=False,
                        replacement_delivery_state__in=[
                            "dispatch_pending",
                            "dispatching",
                            "acknowledged",
                            "failed_retryable",
                            "failed_terminal",
                            "outcome_unknown",
                        ],
                        replacement_delivery_certainty__in=[
                            "not_attempted",
                            "attempting",
                            "acknowledged",
                            "not_delivered",
                            "unknown",
                        ],
                    )
                ),
                name="corrcase_replace_delivery_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="open",
                        resolved_at__isnull=True,
                        resolved_by__isnull=True,
                        resolution_mode__isnull=True,
                    )
                    | (
                        models.Q(
                            status="resolved",
                            resolved_at__isnull=False,
                            resolved_by__isnull=False,
                            resolution_mode="replacement_acknowledged",
                            replacement_status="acknowledged",
                            notification_status="acknowledged",
                        )
                        & ~models.Q(paper_reconciliation_status="required")
                    )
                    | models.Q(
                        status="resolved",
                        resolved_at__isnull=False,
                        resolved_by__isnull=False,
                        resolution_mode="original_not_delivered",
                        delivery_certainty="not_delivered",
                        notification_status="not_required",
                        paper_reconciliation_status="not_required",
                    )
                ),
                name="corrcase_resolution_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        replacement_status="not_started",
                        replacement_attempt__isnull=True,
                        replacement_compilation__isnull=True,
                        replacement_review__isnull=True,
                        replacement_revision__isnull=True,
                        replacement_artifact__isnull=True,
                        replacement_delivery__isnull=True,
                        replacement_delivery_state__isnull=True,
                        replacement_delivery_certainty__isnull=True,
                    )
                    | ~models.Q(replacement_status="not_started")
                ),
                name="corrcase_replacement_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(replacement_review__isnull=True)
                    | models.Q(
                        replacement_attempt__isnull=False,
                        replacement_compilation__isnull=False,
                    )
                )
                & (
                    models.Q(replacement_revision__isnull=True)
                    | models.Q(replacement_review__isnull=False)
                )
                & (
                    models.Q(replacement_artifact__isnull=True)
                    | models.Q(replacement_revision__isnull=False)
                )
                & (
                    models.Q(replacement_delivery__isnull=True)
                    | models.Q(replacement_artifact__isnull=False)
                )
                & (
                    ~models.Q(replacement_status="acknowledged")
                    | models.Q(replacement_delivery__isnull=False)
                ),
                name="corrcase_replace_prefix_ck",
            ),
        ]


class CorrespondenceReplacementAttempt(EMRBaseModel):
    ATTEMPT_CONSTRAINT_NAME = REPLACEMENT_ATTEMPT_NUMBER_CONSTRAINT

    case = models.ForeignKey(
        CorrespondenceCorrectionCase,
        on_delete=models.PROTECT,
        related_name="replacement_attempts",
    )
    attempt_number = models.PositiveIntegerField()
    supersedes_attempt = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by_attempts",
    )
    source_head = models.ForeignKey(
        FormSubmissionSeriesHead,
        on_delete=models.PROTECT,
        related_name="replacement_attempts",
    )
    source_head_hash = models.CharField(max_length=64)
    source_correction = models.ForeignKey(
        CorrespondenceSourceCorrection,
        on_delete=models.PROTECT,
        related_name="replacement_attempts",
    )
    source_correction_hash = models.CharField(max_length=64)
    source_submission = models.ForeignKey(
        "emr.FormSubmission",
        on_delete=models.PROTECT,
        related_name="correspondence_replacement_attempts",
    )
    source_version = models.PositiveIntegerField()
    source_snapshot_hash = models.CharField(max_length=64)
    form_artifact = models.ForeignKey(
        "emr.ReportUpload",
        on_delete=models.PROTECT,
        related_name="correspondence_replacement_attempts",
    )
    form_artifact_hash = models.CharField(max_length=64)
    compilation = models.OneToOneField(
        "emr.CorrespondenceCompilation",
        on_delete=models.PROTECT,
        related_name="replacement_attempt",
    )
    review = models.OneToOneField(
        "emr.CorrespondenceReview",
        on_delete=models.PROTECT,
        related_name="replacement_attempt",
    )
    initial_revision = models.OneToOneField(
        "emr.CorrespondenceLetterRevision",
        on_delete=models.PROTECT,
        related_name="replacement_attempt_started",
    )
    started_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="started_correspondence_replacements",
    )
    started_at = models.DateTimeField()
    attempt_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["case", "attempt_number"],
                name=REPLACEMENT_ATTEMPT_NUMBER_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(attempt_number__gt=0)
                    & models.Q(source_version__gt=0)
                    & models.Q(source_head_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(source_correction_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(source_snapshot_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(form_artifact_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(attempt_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(created_by=models.F("started_by"))
                    & models.Q(updated_by=models.F("started_by"))
                ),
                name="corrreplace_snapshot_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        attempt_number=1,
                        supersedes_attempt__isnull=True,
                    )
                    | models.Q(
                        attempt_number__gt=1,
                        supersedes_attempt__isnull=False,
                    )
                ),
                name="corrreplace_lineage_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Correspondence replacement attempts are immutable")
        return super().save(*args, **kwargs)


class CorrespondencePaperReconciliationAttestation(EMRBaseModel):
    case = models.ForeignKey(
        CorrespondenceCorrectionCase,
        on_delete=models.PROTECT,
        related_name="paper_attestations",
    )
    replacement_attempt = models.ForeignKey(
        CorrespondenceReplacementAttempt,
        on_delete=models.PROTECT,
        related_name="paper_attestations",
    )
    controlled_copy_artifact = models.ForeignKey(
        "emr.ReportUpload",
        on_delete=models.PROTECT,
        related_name="paper_reconciliation_attestations",
    )
    artifact_hash = models.CharField(max_length=64)
    attestation_type = models.CharField(max_length=64)
    attested_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="correspondence_paper_attestations",
    )
    attested_at = models.DateTimeField()
    attestation_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["case", "replacement_attempt"],
                name=PAPER_ATTESTATION_ATTEMPT_CONSTRAINT,
            ),
            models.UniqueConstraint(
                fields=["case", "controlled_copy_artifact"],
                name=PAPER_ATTESTATION_ARTIFACT_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        attestation_type=("corrected_copy_filed_prior_copy_reconciled")
                    )
                    & models.Q(artifact_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(attestation_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(created_by=models.F("attested_by"))
                    & models.Q(updated_by=models.F("attested_by"))
                ),
                name="corrpaper_attestation_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Paper reconciliation attestations are immutable")
        return super().save(*args, **kwargs)


class CorrespondenceCorrectionCommand(EMRBaseModel):
    IDEMPOTENCY_CONSTRAINT_NAME = CORRECTION_COMMAND_CONSTRAINT

    client_request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    command_hash = models.CharField(max_length=64)
    command_type = models.CharField(max_length=32)
    expected_case_version = models.PositiveIntegerField()
    expected_case_hash = models.CharField(max_length=64)
    actor = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="correspondence_correction_commands",
    )
    case = models.ForeignKey(
        CorrespondenceCorrectionCase,
        on_delete=models.PROTECT,
        related_name="commands",
    )
    target_attempt = models.ForeignKey(
        CorrespondenceReplacementAttempt,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="targeted_commands",
    )
    result_attempt = models.ForeignKey(
        CorrespondenceReplacementAttempt,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="result_commands",
    )
    result_revision = models.ForeignKey(
        "emr.CorrespondenceLetterRevision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correction_commands",
    )
    result_artifact = models.ForeignKey(
        "emr.ReportUpload",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correction_commands",
    )
    result_delivery = models.ForeignKey(
        "emr.CorrespondenceDelivery",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correction_commands",
    )
    result_attestation = models.ForeignKey(
        CorrespondencePaperReconciliationAttestation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correction_commands",
    )
    resulting_case_version = models.PositiveIntegerField()
    resulting_case_hash = models.CharField(max_length=64)
    result_snapshot = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["client_request_id"],
                name=CORRECTION_COMMAND_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        command_type__in=[
                            "start_replacement",
                            "revise_replacement",
                            "finalize_replacement",
                            "send_replacement",
                            "retry_replacement",
                            "attest_paper",
                            "resolve",
                        ]
                    )
                    & models.Q(expected_case_version__gt=0)
                    & models.Q(resulting_case_version__gt=0)
                    & models.Q(payload_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(command_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(expected_case_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(resulting_case_hash__regex=r"^[0-9a-f]{64}$")
                    & ~models.Q(result_snapshot={})
                    & models.Q(created_by=models.F("actor"))
                    & models.Q(updated_by=models.F("actor"))
                ),
                name="corrcase_cmd_snapshot_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Correspondence correction commands are immutable")
        return super().save(*args, **kwargs)


class CorrespondenceCorrectionEvent(EMRBaseModel):
    SEQUENCE_CONSTRAINT_NAME = CORRECTION_EVENT_SEQUENCE_CONSTRAINT

    case = models.ForeignKey(
        CorrespondenceCorrectionCase,
        on_delete=models.PROTECT,
        related_name="events",
    )
    sequence = models.PositiveIntegerField()
    event_type = models.CharField(max_length=32)
    source_correction = models.ForeignKey(
        CorrespondenceSourceCorrection,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="case_events",
    )
    delivery_event = models.ForeignKey(
        "emr.CorrespondenceDeliveryEvent",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correction_case_events",
    )
    replacement_attempt = models.ForeignKey(
        CorrespondenceReplacementAttempt,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="case_events",
    )
    command = models.OneToOneField(
        CorrespondenceCorrectionCommand,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="case_event",
    )
    occurred_at = models.DateTimeField()
    actor_type = models.CharField(max_length=16, default="system")
    actor = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correspondence_correction_events",
    )
    safe_code = models.CharField(max_length=64)
    previous_event = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="next_events",
    )
    previous_event_hash = models.CharField(max_length=64, blank=True, default="")
    previous_case_hash = models.CharField(max_length=64, blank=True, default="")
    resulting_case_hash = models.CharField(max_length=64)
    event_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["case", "sequence"],
                name=CORRECTION_EVENT_SEQUENCE_CONSTRAINT,
            ),
            models.UniqueConstraint(
                fields=["case", "source_correction"],
                condition=models.Q(source_correction__isnull=False),
                name=CORRECTION_EVENT_SOURCE_CONSTRAINT,
            ),
            models.UniqueConstraint(
                fields=["case", "delivery_event"],
                condition=models.Q(delivery_event__isnull=False),
                name=CORRECTION_EVENT_DELIVERY_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(sequence__gt=0)
                    & models.Q(
                        event_type__in=[
                            "opened",
                            "source_advanced",
                            "delivery_classified",
                            "replacement_started",
                            "replacement_revised",
                            "replacement_finalized",
                            "replacement_delivery_linked",
                            "replacement_delivery_classified",
                            "paper_reconciled",
                            "resolved",
                        ]
                    )
                    & models.Q(actor_type__in=["user", "system"])
                    & ~models.Q(safe_code="")
                    & models.Q(resulting_case_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(event_hash__regex=r"^[0-9a-f]{64}$")
                ),
                name="correvent_state_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(actor_type="user", actor__isnull=False)
                    | models.Q(actor_type="system", actor__isnull=True)
                ),
                name="correvent_actor_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        sequence=1,
                        previous_event__isnull=True,
                        previous_event_hash="",
                        previous_case_hash="",
                    )
                    | models.Q(
                        sequence__gt=1,
                        previous_event__isnull=False,
                        previous_event_hash__regex=r"^[0-9a-f]{64}$",
                        previous_case_hash__regex=r"^[0-9a-f]{64}$",
                    )
                ),
                name="correvent_chain_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        event_type__in=["opened", "source_advanced"],
                        source_correction__isnull=False,
                        delivery_event__isnull=True,
                        replacement_attempt__isnull=True,
                        command__isnull=True,
                    )
                    | models.Q(
                        event_type="delivery_classified",
                        source_correction__isnull=True,
                        delivery_event__isnull=False,
                        replacement_attempt__isnull=True,
                        command__isnull=True,
                    )
                    | models.Q(
                        event_type="replacement_delivery_classified",
                        source_correction__isnull=True,
                        delivery_event__isnull=False,
                        replacement_attempt__isnull=False,
                        command__isnull=True,
                    )
                    | models.Q(
                        event_type__in=[
                            "replacement_started",
                            "replacement_revised",
                            "replacement_finalized",
                            "replacement_delivery_linked",
                            "paper_reconciled",
                            "resolved",
                        ],
                        source_correction__isnull=True,
                        delivery_event__isnull=True,
                        command__isnull=False,
                    )
                ),
                name="correvent_source_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Correspondence correction events are immutable")
        return super().save(*args, **kwargs)
