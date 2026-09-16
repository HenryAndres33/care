# Booked consultations may close without a queue token (CONSULT_CLOSURE_PAPER.md).

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("emr", "0104_unscheduled_consult_closure")]

    operations = [
        migrations.RemoveConstraint(
            model_name="consultclosure",
            name="consultclosure_snapshot_ck",
        ),
        migrations.AddConstraint(
            model_name="consultclosure",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(closure_number__gt=0)
                    & models.Q(policy_version__gt=0)
                    & models.Q(preflight_version__gt=0)
                    & models.Q(form_source_version__gt=0)
                    & models.Q(policy_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(preflight_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(form_source_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(form_artifact_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(closure_hash__regex=r"^[0-9a-f]{64}$")
                    & models.Q(encounter_status="completed")
                    & (
                        models.Q(
                            policy_id="care.standard.consult-close",
                            token__isnull=False,
                            appointment__isnull=False,
                            token_status="FULFILLED",
                            booking_status="fulfilled",
                        )
                        | models.Q(
                            policy_id="care.standard.consult-close",
                            token__isnull=True,
                            appointment__isnull=False,
                            token_status="not_required",
                            booking_status="fulfilled",
                        )
                        | models.Q(
                            policy_id="care.standard.emergency-close",
                            token__isnull=True,
                            appointment__isnull=True,
                            token_status="not_required",
                            booking_status="not_required",
                        )
                        | models.Q(
                            policy_id="care.standard.unscheduled-consult-close",
                            token__isnull=True,
                            appointment__isnull=True,
                            token_status="not_required",
                            booking_status="not_required",
                        )
                    )
                    & models.Q(created_by=models.F("closed_by"))
                    & models.Q(updated_by=models.F("closed_by"))
                ),
                name="consultclosure_snapshot_ck",
            ),
        ),
    ]
