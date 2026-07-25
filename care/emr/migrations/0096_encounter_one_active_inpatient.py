from django.db import migrations, models
from django.db.models import Count

CONSTRAINT_NAME = "encounter_one_active_inpatient_per_patient"
ACTIVE_STATUSES = ("in_progress", "on_hold")


def validate_existing_active_inpatients(apps, schema_editor):
    encounter = apps.get_model("emr", "Encounter")
    conflicts = (
        encounter.objects.filter(
            deleted=False,
            encounter_class="imp",
            status__in=ACTIVE_STATUSES,
        )
        .values("patient_id")
        .annotate(active_count=Count("id"))
        .filter(active_count__gt=1)
        .count()
    )
    if conflicts:
        error = (
            "Cannot enforce active inpatient uniqueness: "
            f"{conflicts} patient record(s) have duplicate active admissions."
        )
        raise RuntimeError(error)


class Migration(migrations.Migration):
    dependencies = [("emr", "0095_clinical_term_concept_kind")]

    operations = [
        migrations.RunPython(
            validate_existing_active_inpatients,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="encounter",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    deleted=False,
                    encounter_class="imp",
                    status__in=ACTIVE_STATUSES,
                ),
                fields=("patient",),
                name=CONSTRAINT_NAME,
            ),
        ),
    ]
