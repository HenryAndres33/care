"""Adopt the users table without schema SQL; preserve content-type/permission identity."""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class ContentTypeRelabelError(RuntimeError):
    pass


def relabel(apps, schema_editor, old, new):
    alias = schema_editor.connection.alias
    content_types = apps.get_model("contenttypes", "ContentType").objects.using(alias)
    source = list(
        content_types.select_for_update().filter(
            app_label=old, model="draftrecoverykey"
        )
    )
    target = list(
        content_types.select_for_update().filter(
            app_label=new, model="draftrecoverykey"
        )
    )
    if len(source) > 1 or target:
        raise ContentTypeRelabelError(
            "Unexpected or duplicate draft recovery content type"
        )
    if source:
        content_types.filter(pk=source[0].pk).update(app_label=new)
    else:
        # A fresh install has not run post_migrate yet. Missing identity on an
        # installed database must not silently recreate permissions/CT IDs.
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT EXISTS (SELECT 1 FROM users_draftrecoverykey)")
            has_keys = cursor.fetchone()[0]
        if has_keys or content_types.filter(app_label="users", model="user").exists():
            raise ContentTypeRelabelError("Missing draft recovery content type")


def forwards(apps, schema_editor):
    relabel(apps, schema_editor, "users", "care_suriname")


def backwards(apps, schema_editor):
    relabel(apps, schema_editor, "care_suriname", "users")


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0029_move_draft_recovery_to_care_suriname"),
        ("care_suriname", "0001_move_models_to_care_suriname"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="DraftRecoveryKey",
                    fields=[
                        (
                            "id",
                            models.UUIDField(
                                primary_key=True,
                                default=uuid.uuid4,
                                editable=False,
                                serialize=False,
                            ),
                        ),
                        ("wrapping_key_id", models.CharField(max_length=80)),
                        ("nonce", models.BinaryField()),
                        ("ciphertext", models.BinaryField()),
                        ("active", models.BooleanField(default=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        (
                            "owner",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="users.user",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "users_draftrecoverykey",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("owner",),
                                condition=models.Q(active=True),
                                name="one_active_draft_key_per_owner",
                            )
                        ],
                    },
                ),
            ],
        ),
        migrations.RunPython(forwards, backwards),
    ]
