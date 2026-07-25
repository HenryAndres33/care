from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("emr", "0097_correspondence_recipient_command")]

    operations = [
        migrations.RemoveConstraint(
            model_name="formsubmissioncommand",
            name="formsub_cmd_type_ck",
        ),
        migrations.AddConstraint(
            model_name="formsubmissioncommand",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "command_type__in",
                        [
                            "create_draft",
                            "update_draft",
                            "finalize",
                            "amend",
                            "enter_in_error",
                        ],
                    )
                ),
                name="formsub_cmd_type_ck",
            ),
        ),
    ]
