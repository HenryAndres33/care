from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied

from care.emr.api.viewsets.form_submission import FormSubmissionViewSet
from care_suriname.resources.form_submission.urology_operation import (
    InvalidUrologyOperationResponseError,
    validate_urology_operation_response_dump,
)


def _response_dump(*, confirmed=True, note_text="Operatieverslag"):
    return {
        "content": {
            "noteText": note_text,
            "values": {
                "operation.clinicalConfirmation": confirmed,
                "operation.procedureKey": "turp",
                "operation.schema": "care.urology.operation-documentation",
                "operation.schemaVersion": "1",
            },
        }
    }


class UrologyOperationResponseValidationTest(SimpleTestCase):
    def test_rejects_unconfirmed_operation(self):
        with self.assertRaises(InvalidUrologyOperationResponseError):
            validate_urology_operation_response_dump(_response_dump(confirmed=False))

    def test_rejects_empty_narrative(self):
        with self.assertRaises(InvalidUrologyOperationResponseError):
            validate_urology_operation_response_dump(_response_dump(note_text=" "))

    def test_accepts_finalized_structured_report_without_section_review(self):
        validate_urology_operation_response_dump(_response_dump())


class QuestionnaireSubmissionAuthorizationTest(SimpleTestCase):
    def test_questionnaire_permission_is_required(self):
        viewset = FormSubmissionViewSet()
        viewset.request = type("Request", (), {"user": object()})()
        questionnaire = object()

        with (
            self.patch_authorization(allowed=False),
            self.assertRaises(PermissionDenied),
        ):
            viewset._authorize_questionnaire_submission(questionnaire)  # noqa: SLF001

    def test_encounter_permission_is_still_required(self):
        viewset = FormSubmissionViewSet()
        viewset.request = type("Request", (), {"user": object()})()

        with (
            self.patch_authorization(allowed=False),
            self.assertRaises(PermissionDenied),
        ):
            viewset._authorize_write(encounter=object())  # noqa: SLF001

    @staticmethod
    def patch_authorization(*, allowed):
        return patch(
            "care.emr.api.viewsets.form_submission.AuthorizationController.call",
            return_value=allowed,
        )
