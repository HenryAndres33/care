import uuid

from django.conf import settings
from django.db import models


class AdmissionDocumentation(models.Model):
    """Immutable slot reservation; all clinical content remains in FormSubmission."""

    admission = models.ForeignKey("emr.Encounter", on_delete=models.PROTECT)
    slot = models.CharField(max_length=32)
    form_instance_id = models.UUIDField(default=uuid.uuid4, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        db_table = "emr_admissiondocumentation"
        constraints = [
            models.UniqueConstraint(
                fields=["admission", "slot"], name="unique_admission_document_slot"
            )
        ]

    def __str__(self):
        return str(self.form_instance_id)
