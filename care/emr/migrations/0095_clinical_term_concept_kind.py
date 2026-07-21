from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("emr", "0094_clinical_term_translation")]

    operations = [
        migrations.AddField(
            model_name="clinicaltermtranslation",
            name="concept_kind",
            field=models.CharField(default="condition", max_length=16),
        ),
        migrations.AddConstraint(
            model_name="clinicaltermtranslation",
            constraint=models.CheckConstraint(
                condition=models.Q(concept_kind__in=["condition", "procedure"]),
                name="clinical_term_kind_ck",
            ),
        ),
        migrations.AddIndex(
            model_name="clinicaltermtranslation",
            index=models.Index(
                fields=["language", "concept_kind", "status", "is_active"],
                name="clinical_term_kind_idx",
            ),
        ),
    ]
