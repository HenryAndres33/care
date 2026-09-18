# CARE Suriname, Phase 2 step 1 (docs/development/plug-app.md).
#
# The link between a finalized correspondence revision and its generated PDF
# (a ReportUpload row) moves from the core table to the custom one:
#   ReportUpload.correspondence_revision  ->  CorrespondenceLetterRevision.final_artifact
# so no core model references a plug model. Data is copied, nothing is deleted
# except the now-empty column. Operation order matters for the reverse path:
# the old column and its constraints are re-created only after the data has
# been copied back.

import django.db.models.deletion
from django.db import migrations, models


def forwards(apps, schema_editor):
    ReportUpload = apps.get_model("emr", "ReportUpload")
    Revision = apps.get_model("emr", "CorrespondenceLetterRevision")
    pairs = ReportUpload.objects.filter(
        correspondence_revision__isnull=False
    ).values_list("correspondence_revision_id", "pk")
    for revision_id, artifact_id in pairs:
        Revision.objects.filter(pk=revision_id).update(final_artifact_id=artifact_id)


def backwards(apps, schema_editor):
    ReportUpload = apps.get_model("emr", "ReportUpload")
    Revision = apps.get_model("emr", "CorrespondenceLetterRevision")
    pairs = Revision.objects.filter(final_artifact__isnull=False).values_list(
        "pk", "final_artifact_id"
    )
    for revision_id, artifact_id in pairs:
        ReportUpload.objects.filter(pk=artifact_id).update(
            correspondence_revision_id=revision_id
        )


class Migration(migrations.Migration):
    dependencies = [
        ("emr", "0106_form_submission_lab"),
    ]

    operations = [
        migrations.AddField(
            model_name="correspondenceletterrevision",
            name="final_artifact",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="letter_revision",
                to="emr.reportupload",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="reportupload",
            name="corrartifact_revision_uniq",
        ),
        migrations.RemoveConstraint(
            model_name="reportupload",
            name="formartifact_provenance_ck",
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(
            model_name="reportupload",
            name="correspondence_revision",
        ),
        migrations.AddConstraint(
            model_name="reportupload",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        form_submission__isnull=True,
                        patient__isnull=True,
                        encounter__isnull=True,
                        source_version__isnull=True,
                        source_snapshot_hash="",
                        artifact_sha256="",
                        generated_at__isnull=True,
                        generated_by__isnull=True,
                        template__isnull=False,
                    )
                    | (
                        models.Q(
                            patient__isnull=False,
                            encounter__isnull=False,
                            source_version__isnull=False,
                            generated_at__isnull=False,
                            generated_by__isnull=False,
                            template__isnull=True,
                            upload_completed=True,
                            report_type="encounter_report",
                        )
                        & ~models.Q(source_snapshot_hash="")
                        & ~models.Q(artifact_sha256="")
                    )
                ),
                name="formartifact_provenance_ck",
            ),
        ),
    ]
