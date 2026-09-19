from django.db import models

from care.emr.models.base import EMRBaseModel
from care.emr.models.questionnaire import (
    FORM_SUBMISSION_COMMAND_IDEMPOTENCY_CONSTRAINT,
    FormSubmission,
    Questionnaire,
)


class FormSubmissionCommand(EMRBaseModel):
    IDEMPOTENCY_CONSTRAINT_NAME = FORM_SUBMISSION_COMMAND_IDEMPOTENCY_CONSTRAINT

    client_request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    command_type = models.CharField(max_length=32)
    expected_version = models.PositiveIntegerField()
    actor = models.ForeignKey("users.User", on_delete=models.PROTECT)
    patient = models.ForeignKey("emr.Patient", on_delete=models.PROTECT)
    encounter = models.ForeignKey(
        "emr.Encounter", on_delete=models.PROTECT, null=True, blank=True
    )
    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.PROTECT)
    target_submission = models.ForeignKey(
        FormSubmission,
        on_delete=models.PROTECT,
        related_name="targeted_commands",
    )
    result_submission = models.ForeignKey(
        FormSubmission,
        on_delete=models.PROTECT,
        related_name="result_commands",
    )

    class Meta:
        db_table = "emr_formsubmissioncommand"
        constraints = [
            models.UniqueConstraint(
                fields=["client_request_id"],
                name=FORM_SUBMISSION_COMMAND_IDEMPOTENCY_CONSTRAINT,
            ),
            models.CheckConstraint(
                condition=models.Q(
                    command_type__in=[
                        "create_draft",
                        "update_draft",
                        "finalize",
                        "amend",
                        "enter_in_error",
                    ]
                ),
                name="formsub_cmd_type_ck",
            ),
        ]
