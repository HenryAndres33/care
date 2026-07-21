from django.core.exceptions import ValidationError
from django.db import models

from care.emr.models.base import EMRBaseModel

MAX_SYNONYMS = 25
MAX_SYNONYM_LENGTH = 200
REGION_SUBTAG_LENGTH = 2


def normalize_clinical_language_tag(value):
    parts = value.strip().replace("_", "-").split("-")
    normalized = [parts[0].lower()]
    normalized.extend(
        part.upper() if len(part) == REGION_SUBTAG_LENGTH else part
        for part in parts[1:]
    )
    return "-".join(normalized)


class ClinicalTermTranslation(EMRBaseModel):
    """Centrally reviewed display terminology without changing source identity."""

    system = models.CharField(max_length=500)
    code = models.CharField(max_length=255)
    language = models.CharField(max_length=35, default="nl-SR")
    concept_kind = models.CharField(max_length=16, default="condition")
    source_display = models.CharField(max_length=500)
    preferred_display = models.CharField(max_length=500)
    synonyms = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, default="draft")
    source_name = models.CharField(max_length=255)
    source_version = models.CharField(max_length=128, default="", blank=True)
    reviewed_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_clinical_term_translations",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["system", "code", "language"],
                name="clinical_term_system_code_lang_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["draft", "in_review", "approved", "inactive"]
                ),
                name="clinical_term_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(concept_kind__in=["condition", "procedure"]),
                name="clinical_term_kind_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["language", "status", "is_active"],
                name="clinical_term_resolver_idx",
            ),
            models.Index(
                fields=["language", "concept_kind", "status", "is_active"],
                name="clinical_term_kind_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if not isinstance(self.synonyms, list):
            raise ValidationError({"synonyms": "Synonyms must be a JSON list"})
        if len(self.synonyms) > MAX_SYNONYMS:
            raise ValidationError({"synonyms": "At most 25 synonyms are allowed"})
        if any(not isinstance(item, str) or not item.strip() for item in self.synonyms):
            raise ValidationError({"synonyms": "Every synonym must be non-empty text"})
        if any(len(item) > MAX_SYNONYM_LENGTH for item in self.synonyms):
            raise ValidationError(
                {"synonyms": "A synonym may contain at most 200 characters"}
            )
        if self.status == "approved" and not (self.reviewed_by_id and self.reviewed_at):
            raise ValidationError(
                {"status": "Approved terminology requires reviewer audit details"}
            )

    def save(self, *args, **kwargs):
        self.system = self.system.strip()
        self.code = self.code.strip()
        self.language = normalize_clinical_language_tag(self.language)
        self.concept_kind = self.concept_kind.strip().casefold()
        self.source_display = self.source_display.strip()
        self.preferred_display = self.preferred_display.strip()
        self.synonyms = list(dict.fromkeys(item.strip() for item in self.synonyms))
        self.source_name = self.source_name.strip()
        self.source_version = self.source_version.strip()
        self.full_clean(exclude=["history", "meta"])
        return super().save(*args, **kwargs)
