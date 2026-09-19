import uuid

from django.conf import settings
from django.db import models


class OperationPlan(models.Model):
    """Planning metadata only; clinical content remains in native FormSubmission."""

    booking = models.OneToOneField(
        "emr.TokenBooking", on_delete=models.PROTECT, related_name="operation_plan"
    )
    procedure_key = models.CharField(max_length=40)
    procedure_label = models.CharField(max_length=200)
    revision = models.PositiveIntegerField(default=1)
    revisions = models.JSONField(default=list)
    encounter = models.ForeignKey(
        "emr.Encounter", on_delete=models.PROTECT, null=True, blank=True
    )
    form_instance_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="operation_plans",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="updated_operation_plans",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "emr_operationplan"

    def __str__(self):
        return f"Operation plan for booking {self.booking_id}"
