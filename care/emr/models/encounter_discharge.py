from django.core.exceptions import ValidationError
from django.db import models

from care.emr.models.base import EMRBaseModel

ENCOUNTER_DISCHARGE_IDEMPOTENCY_CONSTRAINT = "encounterdischarge_cmd_request_uniq"


class EncounterDischargeCommand(EMRBaseModel):
    IDEMPOTENCY_CONSTRAINT_NAME = ENCOUNTER_DISCHARGE_IDEMPOTENCY_CONSTRAINT

    client_request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    command_hash = models.CharField(max_length=64)
    actor = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="encounter_discharge_commands",
    )
    encounter = models.ForeignKey(
        "emr.Encounter",
        on_delete=models.PROTECT,
        related_name="discharge_commands",
    )
    result_snapshot = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["client_request_id"],
                name=ENCOUNTER_DISCHARGE_IDEMPOTENCY_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(payload_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(command_hash__regex=r"^[0-9a-f]{64}$")
                    & ~models.Q(result_snapshot={})
                    & models.Q(created_by=models.F("actor"))
                    & models.Q(updated_by=models.F("actor"))
                ),
                name="encounterdischarge_cmd_snapshot_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.__class__._base_manager.filter(pk=self.pk).exists():  # noqa: SLF001
            raise ValidationError("Encounter discharge commands are immutable")
        return super().save(*args, **kwargs)
