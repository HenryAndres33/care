from django.db import models

from care.emr.models.base import EMRBaseModel


class FormSubmissionLabLink(EMRBaseModel):
    """Provenance only: clinical results remain native DiagnosticReports."""

    series_id = models.UUIDField(db_index=True)
    slot = models.CharField(max_length=80)
    submission = models.ForeignKey("emr.FormSubmission", on_delete=models.PROTECT)
    report = models.OneToOneField("emr.DiagnosticReport", on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["series_id", "slot"], name="form_lab_series_slot_unique"
            )
        ]
