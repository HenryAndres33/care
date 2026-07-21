import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("emr", "0083_correspondencerecipient_kind_constraint"),
        ("facility", "0484_remove_facility_discount_codes_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CorrespondenceLetter",
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
                ("review_hash", models.CharField(max_length=64)),
            ],
        ),
        migrations.CreateModel(
            name="CorrespondenceLetterCommand",
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
                (
                    "expected_version",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CorrespondenceLetterRevision",
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
                ("resource_version", models.PositiveIntegerField()),
                ("status", models.CharField(default="draft", max_length=32)),
                ("source_review_hash", models.CharField(max_length=64)),
                ("body", models.TextField()),
                ("body_hash", models.CharField(max_length=64)),
                ("revision_hash", models.CharField(max_length=64)),
                ("finalized_at", models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.RemoveConstraint(
            model_name="reportupload", name="formartifact_provenance_ck"
        ),
        migrations.AddField(
            model_name="correspondenceletter",
            name="author",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL
            ),
        ),
        migrations.AddField(
            model_name="correspondenceletter",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(app_label)s_%(class)s_created_by",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="correspondenceletter",
            name="department",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                to="emr.facilityorganization",
            ),
        ),
        migrations.AddField(
            model_name="correspondenceletter",
            name="encounter",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, to="emr.encounter"
            ),
        ),
        migrations.AddField(
            model_name="correspondenceletter",
            name="facility",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, to="facility.facility"
            ),
        ),
        migrations.AddField(
            model_name="correspondenceletter",
            name="patient",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, to="emr.patient"
            ),
        ),
        migrations.AddField(
            model_name="correspondenceletter",
            name="review",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="letter_series",
                to="emr.correspondencereview",
            ),
        ),
        migrations.AddField(
            model_name="correspondenceletter",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(app_label)s_%(class)s_updated_by",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="correspondencelettercommand",
            name="actor",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL
            ),
        ),
        migrations.AddField(
            model_name="correspondencelettercommand",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(app_label)s_%(class)s_created_by",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="correspondencelettercommand",
            name="encounter",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, to="emr.encounter"
            ),
        ),
        migrations.AddField(
            model_name="correspondencelettercommand",
            name="letter",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="commands",
                to="emr.correspondenceletter",
            ),
        ),
        migrations.AddField(
            model_name="correspondencelettercommand",
            name="patient",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, to="emr.patient"
            ),
        ),
        migrations.AddField(
            model_name="correspondencelettercommand",
            name="result_artifact",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="correspondence_letter_commands",
                to="emr.reportupload",
            ),
        ),
        migrations.AddField(
            model_name="correspondencelettercommand",
            name="review",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                to="emr.correspondencereview",
            ),
        ),
        migrations.AddField(
            model_name="correspondencelettercommand",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(app_label)s_%(class)s_updated_by",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="correspondenceletterrevision",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(app_label)s_%(class)s_created_by",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="correspondenceletterrevision",
            name="finalized_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="finalized_correspondence_letters",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="correspondenceletterrevision",
            name="letter",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="revisions",
                to="emr.correspondenceletter",
            ),
        ),
        migrations.AddField(
            model_name="correspondenceletterrevision",
            name="previous_revision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="next_revisions",
                to="emr.correspondenceletterrevision",
            ),
        ),
        migrations.AddField(
            model_name="correspondenceletterrevision",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(app_label)s_%(class)s_updated_by",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="correspondencelettercommand",
            name="result_revision",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="result_commands",
                to="emr.correspondenceletterrevision",
            ),
        ),
        migrations.AddField(
            model_name="correspondencelettercommand",
            name="target_revision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="targeted_commands",
                to="emr.correspondenceletterrevision",
            ),
        ),
        migrations.AddField(
            model_name="reportupload",
            name="correspondence_revision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="final_artifacts",
                to="emr.correspondenceletterrevision",
            ),
        ),
        migrations.AddConstraint(
            model_name="reportupload",
            constraint=models.UniqueConstraint(
                fields=("correspondence_revision",), name="corrartifact_revision_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="reportupload",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        form_submission__isnull=True,
                        correspondence_revision__isnull=True,
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
                            form_submission__isnull=False,
                            correspondence_revision__isnull=True,
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
                    | (
                        models.Q(
                            form_submission__isnull=True,
                            correspondence_revision__isnull=False,
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
        migrations.AddConstraint(
            model_name="correspondenceletter",
            constraint=models.UniqueConstraint(
                fields=("review",), name="corrletter_review_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondenceletterrevision",
            constraint=models.UniqueConstraint(
                fields=("letter", "resource_version"),
                name="corrletter_revision_ver_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondenceletterrevision",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="finalized"),
                fields=("letter",),
                name="corrletter_final_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondenceletterrevision",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="draft",
                        finalized_at__isnull=True,
                        finalized_by__isnull=True,
                    )
                    | models.Q(
                        status="finalized",
                        finalized_at__isnull=False,
                        finalized_by__isnull=False,
                    )
                ),
                name="corrletter_revision_status_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondenceletterrevision",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(source_review_hash="")
                    & ~models.Q(body_hash="")
                    & ~models.Q(revision_hash="")
                ),
                name="corrletter_revision_hashes_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencelettercommand",
            constraint=models.UniqueConstraint(
                fields=("client_request_id",), name="corrletter_cmd_request_id_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencelettercommand",
            constraint=models.CheckConstraint(
                condition=models.Q(command_type__in=["create", "revise", "finalize"]),
                name="corrletter_cmd_type_ck",
            ),
        ),
    ]
