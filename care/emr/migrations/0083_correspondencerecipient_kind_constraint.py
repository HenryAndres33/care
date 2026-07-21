from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("emr", "0082_correspondencerecipient_correspondencereview_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="correspondencerecipient",
            constraint=models.CheckConstraint(
                condition=models.Q(recipient_kind="healthcare_professional"),
                name="corrrecipient_kind_ck",
            ),
        ),
    ]
