import hashlib
import json
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_form_submission_versions(apps, schema_editor):
    FormSubmission = apps.get_model("emr", "FormSubmission")
    submissions = FormSubmission.objects.select_related(
        "questionnaire", "patient", "encounter"
    ).all()
    for submission in submissions.iterator():
        submission.series_id = uuid.uuid4()
        update_fields = ["series_id"]
        if submission.status == "submitted":
            submission.workflow_finalized_at = (
                submission.modified_date or submission.created_date
            )
            submission.workflow_finalized_by_id = (
                submission.updated_by_id or submission.created_by_id
            )
            snapshot = {
                "amendment_reason": "",
                "amendment_type": "",
                "contract": "form-submission-finalized-snapshot-v1",
                "encounter": (
                    submission.encounter.external_id
                    if submission.encounter_id
                    else None
                ),
                "patient": submission.patient.external_id,
                "previous_version": None,
                "questionnaire": submission.questionnaire.slug,
                "resource_version": 1,
                "response_dump": submission.response_dump,
                "series_id": submission.series_id,
            }
            encoded = json.dumps(
                snapshot,
                default=str,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            submission.finalized_snapshot_hash = hashlib.sha256(encoded).hexdigest()
            update_fields.extend(
                [
                    "workflow_finalized_at",
                    "workflow_finalized_by",
                    "finalized_snapshot_hash",
                ]
            )
        submission.save(update_fields=update_fields)


class Migration(migrations.Migration):
    dependencies = [
        ("emr", "0078_medicationrequest_idempotency"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="formsubmission",
            name="amendment_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="formsubmission",
            name="amendment_type",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="formsubmission",
            name="finalized_snapshot_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="formsubmission",
            name="previous_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="next_versions",
                to="emr.formsubmission",
            ),
        ),
        migrations.AddField(
            model_name="formsubmission",
            name="resource_version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="formsubmission",
            name="series_id",
            field=models.UUIDField(db_index=True, default=uuid.uuid4),
        ),
        migrations.AddField(
            model_name="formsubmission",
            name="workflow_finalized_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="formsubmission",
            name="workflow_finalized_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="workflow_finalized_form_submissions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(
            backfill_form_submission_versions,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="formsubmission",
            constraint=models.UniqueConstraint(
                fields=("series_id", "resource_version"),
                name="formsub_series_resource_ver_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="formsubmission",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        previous_version__isnull=True,
                        amendment_reason="",
                        amendment_type="",
                    )
                    | (
                        models.Q(previous_version__isnull=False)
                        & ~models.Q(amendment_reason="")
                        & models.Q(amendment_type__in=["amendment", "addendum"])
                    )
                ),
                name="formsub_amendment_lineage_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="formsubmission",
            constraint=models.CheckConstraint(
                condition=(
                    (
                        models.Q(status="submitted")
                        & models.Q(workflow_finalized_at__isnull=False)
                        & ~models.Q(finalized_snapshot_hash="")
                    )
                    | (
                        ~models.Q(status="submitted")
                        & models.Q(workflow_finalized_at__isnull=True)
                        & models.Q(workflow_finalized_by__isnull=True)
                        & models.Q(finalized_snapshot_hash="")
                    )
                ),
                name="formsub_finalized_snapshot_ck",
            ),
        ),
        migrations.CreateModel(
            name="FormSubmissionCommand",
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
                    models.UUIDField(db_index=True, default=uuid.uuid4, unique=True),
                ),
                (
                    "created_date",
                    models.DateTimeField(auto_now_add=True, db_index=True, null=True),
                ),
                (
                    "modified_date",
                    models.DateTimeField(auto_now=True, db_index=True, null=True),
                ),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("history", models.JSONField(default=dict)),
                ("meta", models.JSONField(default=dict)),
                ("client_request_id", models.UUIDField()),
                ("payload_hash", models.CharField(max_length=64)),
                ("command_type", models.CharField(max_length=32)),
                ("expected_version", models.PositiveIntegerField()),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
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
                    "encounter",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="emr.encounter",
                    ),
                ),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="emr.patient",
                    ),
                ),
                (
                    "questionnaire",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="emr.questionnaire",
                    ),
                ),
                (
                    "result_submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="result_commands",
                        to="emr.formsubmission",
                    ),
                ),
                (
                    "target_submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="targeted_commands",
                        to="emr.formsubmission",
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
            model_name="formsubmissioncommand",
            constraint=models.UniqueConstraint(
                fields=("client_request_id",),
                name="formsub_cmd_client_request_id_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="formsubmissioncommand",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    command_type__in=["update_draft", "finalize", "amend"]
                ),
                name="formsub_cmd_type_ck",
            ),
        ),
    ]
