import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("users", "0027_user_cached_role_orgs")]
    operations = [
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
                        on_delete=django.db.models.deletion.PROTECT, to="users.user"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("owner",),
                        condition=models.Q(active=True),
                        name="one_active_draft_key_per_owner",
                    )
                ]
            },
        ),
    ]
