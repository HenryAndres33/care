"""Remove only model state; care_suriname/0002 adopts the existing table."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("users", "0028_draftrecoverykey")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.DeleteModel(name="DraftRecoveryKey")],
        ),
    ]
