from django.db import models

from care.emr.models.base import EMRBaseModel


class ClinicalTextResource(EMRBaseModel):
    """Facility-scoped, versioned configuration for clinical text authoring."""

    facility = models.ForeignKey("facility.Facility", on_delete=models.CASCADE)
    kind = models.CharField(max_length=32)
    key = models.CharField(max_length=128)
    label = models.CharField(max_length=255)
    description = models.TextField(default="", blank=True)
    status = models.CharField(max_length=16, default="active")
    version = models.PositiveIntegerField(default=1)
    payload = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "kind", "key"],
                name="clinical_text_fac_kind_key_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=["template", "list", "preset", "dictionary"]
                ),
                name="clinical_text_kind_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["draft", "active", "inactive", "archived"]
                ),
                name="clinical_text_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name="clinical_text_version_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        self.key = self.key.strip().casefold()
        return super().save(*args, **kwargs)
