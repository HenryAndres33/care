import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("emr", "0096_encounter_one_active_inpatient")]

    operations = [
        migrations.CreateModel(
            name="CorrespondenceRecipientCommand",
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
                        db_index=True, default=uuid.uuid4, unique=True
                    ),
                ),
                (
                    "created_date",
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, null=True
                    ),
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
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="users.user",
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
                        to="users.user",
                    ),
                ),
                (
                    "facility",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="facility.facility",
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
                    "result_recipient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="idempotency_commands",
                        to="emr.correspondencerecipient",
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
                        to="users.user",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="correspondencerecipientcommand",
            constraint=models.UniqueConstraint(
                fields=("client_request_id",),
                name="corrrecipient_cmd_request_id_uniq",
            ),
        ),
    ]
