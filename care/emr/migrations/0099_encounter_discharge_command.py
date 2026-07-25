import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("emr", "0098_form_submission_create_draft_command"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EncounterDischargeCommand",
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
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        unique=True,
                    ),
                ),
                (
                    "created_date",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        null=True,
                    ),
                ),
                (
                    "modified_date",
                    models.DateTimeField(
                        auto_now=True,
                        db_index=True,
                        null=True,
                    ),
                ),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("history", models.JSONField(default=dict)),
                ("meta", models.JSONField(default=dict)),
                ("client_request_id", models.UUIDField()),
                ("payload_hash", models.CharField(max_length=64)),
                ("command_hash", models.CharField(max_length=64)),
                ("result_snapshot", models.JSONField(default=dict)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="encounter_discharge_commands",
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
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="discharge_commands",
                        to="emr.encounter",
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
            model_name="encounterdischargecommand",
            constraint=models.UniqueConstraint(
                fields=("client_request_id",),
                name="encounterdischarge_cmd_request_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="encounterdischargecommand",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(payload_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(command_hash__regex=r"^[0-9a-f]{64}$")
                    & ~models.Q(result_snapshot={})
                    & models.Q(created_by=models.F("actor"))
                    & models.Q(updated_by=models.F("actor"))
                ),
                name="encounterdischarge_cmd_snapshot_ck",
            ),
        ),
    ]
