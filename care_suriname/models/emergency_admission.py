from django.conf import settings
from django.db import models


class EmergencyAdmission(models.Model):
    """Audited handoff only; clinical content stays on its original encounter."""

    emergency = models.OneToOneField(
        "emr.Encounter", on_delete=models.PROTECT, related_name="admission_handoff"
    )
    admission = models.ForeignKey(
        "emr.Encounter", on_delete=models.PROTECT, related_name="emergency_handoffs"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "emr_emergencyadmission"

    def __str__(self):
        return f"{self.emergency_id} -> {self.admission_id}"
