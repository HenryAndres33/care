from django.db import models

from care.emr.models import SlugBaseModel


class Template(SlugBaseModel):
    facility = models.ForeignKey(
        "facility.Facility",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    slug = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=255)
    template_data = models.TextField()
    template_type = models.CharField(max_length=255)
    default_format = models.CharField(max_length=255)
    context = models.CharField(max_length=100, default="encounter_base")
    description = models.TextField(blank=True, default="")
    options = models.JSONField(default=dict)
    resource_version = models.PositiveIntegerField(default=1)
    content_hash = models.CharField(max_length=64, default="", blank=True)

    def save(self, *args, **kwargs):
        from care.emr.reports.template_versioning import (
            calculate_template_content_hash,
        )

        if self.pk:
            persisted = (
                self.__class__._base_manager.filter(pk=self.pk)  # noqa: SLF001
                .only("resource_version")
                .first()
            )
            if persisted:
                self.resource_version = persisted.resource_version + 1
        self.content_hash = calculate_template_content_hash(self)
        if update_fields := kwargs.get("update_fields"):
            kwargs["update_fields"] = set(update_fields) | {
                "resource_version",
                "content_hash",
            }
        return super().save(*args, **kwargs)
