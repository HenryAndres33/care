from django.core.exceptions import ValidationError
from django.db import models

from care.emr.models.base import EMRBaseModel


class ConsultClosure(EMRBaseModel):
    encounter = models.ForeignKey(
        "emr.Encounter",
        on_delete=models.PROTECT,
        related_name="consult_closures",
    )
    closure_number = models.PositiveIntegerField()
    previous_closure = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by_closures",
    )
    patient = models.ForeignKey("emr.Patient", on_delete=models.PROTECT)
    facility = models.ForeignKey("facility.Facility", on_delete=models.PROTECT)
    department = models.ForeignKey(
        "emr.FacilityOrganization",
        on_delete=models.PROTECT,
    )
    token = models.ForeignKey(
        "emr.Token", on_delete=models.PROTECT, null=True, blank=True
    )
    appointment = models.ForeignKey(
        "emr.TokenBooking", on_delete=models.PROTECT, null=True, blank=True
    )
    policy_id = models.CharField(max_length=64)
    policy_version = models.PositiveIntegerField()
    policy_hash = models.CharField(max_length=64)
    preflight_version = models.PositiveIntegerField()
    preflight_hash = models.CharField(max_length=64)
    form_submission = models.ForeignKey("emr.FormSubmission", on_delete=models.PROTECT)
    form_source_version = models.PositiveIntegerField()
    form_source_hash = models.CharField(max_length=64)
    form_artifact = models.ForeignKey("emr.ReportUpload", on_delete=models.PROTECT)
    form_artifact_hash = models.CharField(max_length=64)
    medication_outcome = models.CharField(max_length=32)
    medication_actions = models.JSONField(default=list)
    correspondence_outcome = models.CharField(max_length=32)
    correspondence_compilation = models.ForeignKey(
        "emr.CorrespondenceCompilation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    correspondence_delivery = models.ForeignKey(
        "emr.CorrespondenceDelivery",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    correspondence_delivery_event_sequence = models.PositiveIntegerField(
        null=True,
        blank=True,
    )
    correspondence_delivery_event_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
    )
    correspondence_case = models.ForeignKey(
        "emr.CorrespondenceCorrectionCase",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    correspondence_case_version = models.PositiveIntegerField(null=True, blank=True)
    correspondence_case_hash = models.CharField(max_length=64, null=True, blank=True)
    encounter_status = models.CharField(max_length=32)
    token_status = models.CharField(max_length=32)
    booking_status = models.CharField(max_length=32)
    closed_at = models.DateTimeField()
    closed_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="closed_consults",
    )
    closure_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["encounter", "closure_number"],
                name="consultclosure_encounter_number_uniq",
            ),
            models.UniqueConstraint(
                fields=["preflight_hash"],
                name="consultclosure_preflight_hash_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(closure_number__gt=0)
                    & models.Q(policy_version__gt=0)
                    & models.Q(preflight_version__gt=0)
                    & models.Q(form_source_version__gt=0)
                    & models.Q(policy_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(preflight_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(form_source_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(form_artifact_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(closure_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(encounter_status="completed")
                    & (
                        models.Q(
                            policy_id="care.standard.consult-close",
                            token__isnull=False,
                            appointment__isnull=False,
                            token_status="FULFILLED",
                            booking_status="fulfilled",
                        )
                        | models.Q(
                            policy_id="care.standard.consult-close",
                            token__isnull=True,
                            appointment__isnull=False,
                            token_status="not_required",
                            booking_status="fulfilled",
                        )
                        | models.Q(
                            policy_id="care.standard.emergency-close",
                            token__isnull=True,
                            appointment__isnull=True,
                            token_status="not_required",
                            booking_status="not_required",
                        )
                        | models.Q(
                            policy_id="care.standard.unscheduled-consult-close",
                            token__isnull=True,
                            appointment__isnull=True,
                            token_status="not_required",
                            booking_status="not_required",
                        )
                    )
                    & models.Q(created_by=models.F("closed_by"))
                    & models.Q(updated_by=models.F("closed_by"))
                ),
                name="consultclosure_snapshot_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        closure_number=1,
                        previous_closure__isnull=True,
                    )
                    | models.Q(
                        closure_number__gt=1,
                        previous_closure__isnull=False,
                    )
                ),
                name="consultclosure_lineage_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Consult closures are immutable")
        return super().save(*args, **kwargs)


class ConsultClosureCommand(EMRBaseModel):
    client_request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    command_hash = models.CharField(max_length=64)
    actor = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="consult_closure_commands",
    )
    encounter = models.ForeignKey("emr.Encounter", on_delete=models.PROTECT)
    result_closure = models.ForeignKey(
        ConsultClosure,
        on_delete=models.PROTECT,
        related_name="commands",
    )
    result_snapshot = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["client_request_id"],
                name="consultclosure_cmd_request_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(payload_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(command_hash__regex=r"^[0-9a-f]{64}$")
                    & ~models.Q(result_snapshot={})
                    & models.Q(created_by=models.F("actor"))
                    & models.Q(updated_by=models.F("actor"))
                ),
                name="consultclosure_cmd_snapshot_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Consult closure commands are immutable")
        return super().save(*args, **kwargs)


class ConsultClosureRecoveryTask(EMRBaseModel):
    encounter = models.ForeignKey(
        "emr.Encounter",
        on_delete=models.PROTECT,
        related_name="consult_closure_recovery_tasks",
    )
    closure = models.ForeignKey(
        ConsultClosure,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recovery_tasks",
    )
    status = models.CharField(max_length=16)
    safe_code = models.CharField(max_length=64)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="resolved_consult_closure_recovery_tasks",
    )
    resolution_request_id = models.UUIDField(null=True, blank=True)
    resolution_payload_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
    )
    resolution_hash = models.CharField(max_length=64, null=True, blank=True)
    recovery_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["encounter"],
                condition=models.Q(status="pending", deleted=False),
                name="consultclosure_recovery_pending_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="pending",
                        resolved_at__isnull=True,
                        resolved_by__isnull=True,
                        resolution_request_id__isnull=True,
                        resolution_payload_hash__isnull=True,
                        resolution_hash__isnull=True,
                    )
                    | models.Q(
                        status="resolved",
                        resolved_at__isnull=False,
                        resolved_by__isnull=False,
                        resolution_request_id__isnull=False,
                        resolution_payload_hash__regex=r"^[0-9a-f]{64}$",
                        resolution_hash__regex=r"^[0-9a-f]{64}$",
                    )
                ),
                name="consultclosure_recovery_state_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(safe_code__regex=r"^[a-z0-9_:-]{1,64}$")
                    & models.Q(recovery_hash__regex=r"^[0-9a-f]{64}$")
                ),
                name="consultclosure_recovery_hash_ck",
            ),
            models.UniqueConstraint(
                fields=["resolution_request_id"],
                condition=models.Q(resolution_request_id__isnull=False),
                name="consultclosure_recovery_resolve_req_uniq",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            persisted = self.__class__._base_manager.filter(pk=self.pk).first()  # noqa: SLF001
            if persisted and not self._is_controlled_resolution(persisted):
                raise ValidationError(
                    "Consult closure recovery tasks allow only controlled resolution"
                )
        return super().save(*args, **kwargs)

    def _is_controlled_resolution(self, persisted):
        immutable_fields = [
            "encounter_id",
            "closure_id",
            "safe_code",
            "recovery_hash",
            "created_by_id",
            "created_date",
            "deleted",
        ]
        return all(
            [
                persisted.status == "pending",
                self.status == "resolved",
                self.resolved_at is not None,
                self.resolved_by_id is not None,
                self.resolution_request_id is not None,
                bool(self.resolution_payload_hash),
                bool(self.resolution_hash),
                self.updated_by_id == self.resolved_by_id,
                all(
                    getattr(self, field) == getattr(persisted, field)
                    for field in immutable_fields
                ),
            ]
        )
