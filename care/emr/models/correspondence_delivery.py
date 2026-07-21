from django.core.exceptions import ValidationError
from django.db import models

from care.emr.models.base import EMRBaseModel

DELIVERY_REVISION_CONSTRAINT = "corrdelivery_revision_uniq"
DELIVERY_ATTEMPT_COMMAND_CONSTRAINT = "corrdelivery_attempt_cmd_uniq"
DELIVERY_ATTEMPT_NUMBER_CONSTRAINT = "corrdelivery_attempt_number_uniq"
DELIVERY_RETRY_TRIGGER_CONSTRAINT = "corrdelivery_retry_trigger_uniq"
DELIVERY_EVENT_SEQUENCE_CONSTRAINT = "corrdelivery_event_sequence_uniq"


class CorrespondenceDelivery(EMRBaseModel):
    REVISION_CONSTRAINT_NAME = DELIVERY_REVISION_CONSTRAINT

    revision = models.ForeignKey(
        "emr.CorrespondenceLetterRevision",
        on_delete=models.PROTECT,
        related_name="deliveries",
    )
    artifact = models.ForeignKey(
        "emr.ReportUpload",
        on_delete=models.PROTECT,
        related_name="correspondence_deliveries",
    )
    review = models.ForeignKey(
        "emr.CorrespondenceReview",
        on_delete=models.PROTECT,
        related_name="deliveries",
    )
    recipient = models.ForeignKey(
        "emr.CorrespondenceRecipient",
        on_delete=models.PROTECT,
        related_name="deliveries",
    )
    patient = models.ForeignKey("emr.Patient", on_delete=models.PROTECT)
    encounter = models.ForeignKey("emr.Encounter", on_delete=models.PROTECT)
    facility = models.ForeignKey("facility.Facility", on_delete=models.PROTECT)
    department = models.ForeignKey("emr.FacilityOrganization", on_delete=models.PROTECT)
    author = models.ForeignKey("users.User", on_delete=models.PROTECT)
    revision_version = models.PositiveIntegerField()
    revision_hash = models.CharField(max_length=64)
    artifact_sha256 = models.CharField(max_length=64)
    review_hash = models.CharField(max_length=64)
    recipient_version = models.PositiveIntegerField()
    recipient_hash = models.CharField(max_length=64)
    channel_type = models.CharField(max_length=64)
    adapter_name = models.CharField(max_length=64)
    adapter_version = models.CharField(max_length=32)
    provider_idempotency_key = models.CharField(max_length=64)
    delivery_hash = models.CharField(max_length=64)
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by_deliveries",
    )
    correction_case_reference = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["revision"],
                name=DELIVERY_REVISION_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(revision_hash="")
                    & ~models.Q(artifact_sha256="")
                    & ~models.Q(review_hash="")
                    & ~models.Q(recipient_hash="")
                    & ~models.Q(provider_idempotency_key="")
                    & ~models.Q(delivery_hash="")
                ),
                name="corrdelivery_hashes_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(pk=models.F("supersedes")),
                name="corrdelivery_not_self_supersede_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Correspondence deliveries are immutable")
        return super().save(*args, **kwargs)


class CorrespondenceDeliveryAttempt(EMRBaseModel):
    IDEMPOTENCY_CONSTRAINT_NAME = DELIVERY_ATTEMPT_COMMAND_CONSTRAINT
    ATTEMPT_NUMBER_CONSTRAINT_NAME = DELIVERY_ATTEMPT_NUMBER_CONSTRAINT
    RETRY_TRIGGER_CONSTRAINT_NAME = DELIVERY_RETRY_TRIGGER_CONSTRAINT

    delivery = models.ForeignKey(
        CorrespondenceDelivery,
        on_delete=models.PROTECT,
        related_name="attempts",
    )
    attempt_number = models.PositiveIntegerField()
    client_request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    command_type = models.CharField(max_length=16)
    requested_by = models.ForeignKey("users.User", on_delete=models.PROTECT)
    requested_at = models.DateTimeField()
    adapter_name = models.CharField(max_length=64)
    adapter_version = models.CharField(max_length=32)
    provider_idempotency_key = models.CharField(max_length=64)
    attempt_hash = models.CharField(max_length=64)
    previous_terminal_event = models.ForeignKey(
        "emr.CorrespondenceDeliveryEvent",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="retry_attempts",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["client_request_id"],
                name=DELIVERY_ATTEMPT_COMMAND_CONSTRAINT,
            ),
            models.UniqueConstraint(
                fields=["delivery", "attempt_number"],
                name=DELIVERY_ATTEMPT_NUMBER_CONSTRAINT,
            ),
            models.UniqueConstraint(
                fields=["delivery", "previous_terminal_event"],
                condition=models.Q(previous_terminal_event__isnull=False),
                name=DELIVERY_RETRY_TRIGGER_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=models.Q(command_type__in=["send", "retry"]),
                name="corrdelivery_attempt_cmd_type_ck",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(payload_hash="")
                    & ~models.Q(provider_idempotency_key="")
                    & ~models.Q(attempt_hash="")
                ),
                name="corrdelivery_attempt_hashes_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    created_by=models.F("requested_by"),
                    updated_by=models.F("requested_by"),
                ),
                name="corrdelivery_attempt_actor_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        command_type="send",
                        attempt_number=1,
                        previous_terminal_event__isnull=True,
                    )
                    | models.Q(
                        command_type="retry",
                        attempt_number__gt=1,
                        previous_terminal_event__isnull=False,
                    )
                ),
                name="corrdelivery_attempt_lineage_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Correspondence delivery attempts are immutable")
        return super().save(*args, **kwargs)


class CorrespondenceDeliveryEvent(EMRBaseModel):
    SEQUENCE_CONSTRAINT_NAME = DELIVERY_EVENT_SEQUENCE_CONSTRAINT

    delivery = models.ForeignKey(
        CorrespondenceDelivery,
        on_delete=models.PROTECT,
        related_name="events",
    )
    attempt = models.ForeignKey(
        CorrespondenceDeliveryAttempt,
        on_delete=models.PROTECT,
        related_name="events",
    )
    sequence = models.PositiveIntegerField()
    event_type = models.CharField(max_length=32)
    certainty = models.CharField(max_length=32)
    occurred_at = models.DateTimeField()
    actor_type = models.CharField(max_length=16)
    actor = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correspondence_delivery_events",
    )
    safe_code = models.CharField(max_length=64, blank=True, default="")
    provider_ack_reference = models.CharField(max_length=255, blank=True, default="")
    provider_ack_hash = models.CharField(max_length=64, blank=True, default="")
    provider_ack_at = models.DateTimeField(null=True, blank=True)
    previous_event = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="next_events",
    )
    previous_event_hash = models.CharField(max_length=64, blank=True, default="")
    event_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["delivery", "sequence"],
                name=DELIVERY_EVENT_SEQUENCE_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=models.Q(
                    event_type__in=[
                        "dispatch_pending",
                        "dispatching",
                        "acknowledged",
                        "failed_retryable",
                        "failed_terminal",
                        "outcome_unknown",
                    ]
                ),
                name="corrdelivery_event_type_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    certainty__in=[
                        "not_attempted",
                        "attempting",
                        "acknowledged",
                        "not_delivered",
                        "unknown",
                    ]
                ),
                name="corrdelivery_event_certainty_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(actor_type__in=["user", "system"]),
                name="corrdelivery_event_actor_type_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(actor_type="user", actor__isnull=False)
                    | models.Q(actor_type="system", actor__isnull=True)
                ),
                name="corrdelivery_event_actor_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        event_type="dispatch_pending",
                        certainty="not_attempted",
                        provider_ack_reference="",
                        provider_ack_hash="",
                        provider_ack_at__isnull=True,
                    )
                    | models.Q(
                        event_type="dispatching",
                        certainty="attempting",
                        provider_ack_reference="",
                        provider_ack_hash="",
                        provider_ack_at__isnull=True,
                    )
                    | models.Q(
                        event_type="acknowledged",
                        certainty="acknowledged",
                        provider_ack_reference__gt="",
                        provider_ack_hash__gt="",
                        provider_ack_at__isnull=False,
                    )
                    | models.Q(
                        event_type__in=["failed_retryable", "failed_terminal"],
                        certainty="not_delivered",
                        provider_ack_reference="",
                        provider_ack_hash="",
                        provider_ack_at__isnull=True,
                    )
                    | models.Q(
                        event_type="outcome_unknown",
                        certainty="unknown",
                        provider_ack_reference="",
                        provider_ack_hash="",
                        provider_ack_at__isnull=True,
                    )
                ),
                name="corrdelivery_event_outcome_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        sequence=1,
                        previous_event__isnull=True,
                        previous_event_hash="",
                    )
                    | models.Q(
                        sequence__gt=1,
                        previous_event__isnull=False,
                        previous_event_hash__gt="",
                    )
                ),
                name="corrdelivery_event_chain_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Correspondence delivery events are immutable")
        return super().save(*args, **kwargs)


class CorrespondenceSyntheticProviderReceipt(EMRBaseModel):
    """No-PHI durable state for the local/test-only synthetic provider."""

    provider_idempotency_key = models.CharField(max_length=64)
    attempt_number = models.PositiveIntegerField()
    request_hash = models.CharField(max_length=64)
    outcome_state = models.CharField(max_length=32)
    safe_code = models.CharField(max_length=64)
    provider_ack_reference = models.CharField(max_length=255, blank=True, default="")
    provider_ack_hash = models.CharField(max_length=64, blank=True, default="")
    recorded_at = models.DateTimeField()
    receipt_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider_idempotency_key"],
                name="corrsynthetic_receipt_key_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    outcome_state__in=[
                        "acknowledged",
                        "failed_retryable",
                        "failed_terminal",
                        "outcome_unknown",
                    ]
                ),
                name="corrsynthetic_receipt_outcome_ck",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(provider_idempotency_key="")
                    & ~models.Q(request_hash="")
                    & ~models.Q(safe_code="")
                    & ~models.Q(receipt_hash="")
                ),
                name="corrsynthetic_receipt_hashes_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        outcome_state="acknowledged",
                        provider_ack_reference__gt="",
                        provider_ack_hash__gt="",
                    )
                    | models.Q(
                        outcome_state__in=[
                            "failed_retryable",
                            "failed_terminal",
                            "outcome_unknown",
                        ],
                        provider_ack_reference="",
                        provider_ack_hash="",
                    )
                ),
                name="corrsynthetic_receipt_ack_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Synthetic provider receipts are immutable")
        return super().save(*args, **kwargs)


class CorrespondenceSyntheticProviderInvocation(EMRBaseModel):
    """Durable no-PHI proof that a synthetic provider call actually started."""

    provider_idempotency_key = models.CharField(max_length=64)
    attempt_number = models.PositiveIntegerField()
    request_hash = models.CharField(max_length=64)
    started_at = models.DateTimeField()
    marker_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider_idempotency_key", "attempt_number"],
                name="corrsynthetic_invocation_key_attempt_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(provider_idempotency_key="")
                    & ~models.Q(request_hash="")
                    & ~models.Q(marker_hash="")
                ),
                name="corrsynthetic_invocation_hashes_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Synthetic provider invocations are immutable")
        return super().save(*args, **kwargs)
