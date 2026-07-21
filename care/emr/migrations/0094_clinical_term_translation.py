# Generated for the CARE-native nl-SR terminology resolver.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

SEED_TERMS = [
    {
        "system": "http://snomed.info/sct",
        "code": "266569009",
        "source_display": "Benign prostatic hyperplasia",
        "preferred_display": "Benigne prostaathyperplasie",
        "synonyms": ["BPH", "goedaardige prostaatvergroting"],
    },
    {
        "system": "http://snomed.info/sct",
        "code": "34436003",
        "source_display": "Hematuria",
        "preferred_display": "Hematurie",
        "synonyms": ["bloed in de urine"],
    },
    {
        "system": "http://snomed.info/sct",
        "code": "95570007",
        "source_display": "Kidney stone",
        "preferred_display": "Niersteen",
        "synonyms": ["nierstenen", "nefrolithiasis"],
    },
    {
        "system": "http://snomed.info/sct",
        "code": "68566005",
        "source_display": "Urinary tract infection",
        "preferred_display": "Urineweginfectie",
        "synonyms": ["UWI"],
    },
    {
        "system": "http://snomed.info/sct",
        "code": "399068003",
        "source_display": "Malignant tumor of prostate",
        "preferred_display": "Prostaatcarcinoom",
        "synonyms": ["prostaatkanker"],
    },
]


def seed_draft_terms(apps, schema_editor):
    translation = apps.get_model("emr", "ClinicalTermTranslation")
    translation.objects.bulk_create(
        [
            translation(
                language="nl-SR",
                status="draft",
                source_name="CARE Suriname urology review seed",
                source_version="2026-07-20",
                is_active=True,
                **item,
            )
            for item in SEED_TERMS
        ],
        ignore_conflicts=True,
    )


def remove_seed_draft_terms(apps, schema_editor):
    translation = apps.get_model("emr", "ClinicalTermTranslation")
    translation.objects.filter(
        language="nl-SR",
        status="draft",
        source_name="CARE Suriname urology review seed",
        code__in=[item["code"] for item in SEED_TERMS],
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("emr", "0093_diagnosis_native_problem_list"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ClinicalTermTranslation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "external_id",
                    models.UUIDField(default=uuid.uuid4, db_index=True, unique=True),
                ),
                (
                    "created_date",
                    models.DateTimeField(
                        auto_now_add=True, blank=True, db_index=True, null=True
                    ),
                ),
                (
                    "modified_date",
                    models.DateTimeField(
                        auto_now=True, blank=True, db_index=True, null=True
                    ),
                ),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("history", models.JSONField(default=dict)),
                ("meta", models.JSONField(default=dict)),
                ("system", models.CharField(max_length=500)),
                ("code", models.CharField(max_length=255)),
                ("language", models.CharField(default="nl-SR", max_length=35)),
                ("source_display", models.CharField(max_length=500)),
                ("preferred_display", models.CharField(max_length=500)),
                ("synonyms", models.JSONField(blank=True, default=list)),
                ("status", models.CharField(default="draft", max_length=16)),
                ("source_name", models.CharField(max_length=255)),
                (
                    "source_version",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_clinical_term_translations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="clinicaltermtranslation",
            constraint=models.UniqueConstraint(
                fields=("system", "code", "language"),
                name="clinical_term_system_code_lang_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="clinicaltermtranslation",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("status__in", ["draft", "in_review", "approved", "inactive"])
                ),
                name="clinical_term_status_ck",
            ),
        ),
        migrations.AddIndex(
            model_name="clinicaltermtranslation",
            index=models.Index(
                fields=["language", "status", "is_active"],
                name="clinical_term_resolver_idx",
            ),
        ),
        migrations.RunPython(seed_draft_terms, remove_seed_draft_terms),
    ]
