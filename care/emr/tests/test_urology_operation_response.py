from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied

from care.emr.api.viewsets.form_submission import FormSubmissionViewSet
from care_suriname.resources.form_submission.urology_operation import (
    InvalidUrologyOperationResponseError,
    validate_urology_operation_response_dump,
)


def _response_dump(*, confirmed=True, sections=""):
    return {
        "content": {
            "noteText": "Operatieverslag",
            "values": {
                "operation.clinicalConfirmation": confirmed,
                "operation.confirmedCompanionSectionKeys": sections,
                "operation.procedureKey": "urs",
                "operation.schema": "care.urology.operation-documentation",
                "operation.schemaVersion": "1",
            },
        }
    }


class UrologyOperationResponseValidationTest(SimpleTestCase):
    def test_rejects_unconfirmed_operation(self):
        with self.assertRaises(InvalidUrologyOperationResponseError):
            validate_urology_operation_response_dump(_response_dump(confirmed=False))

    def test_rejects_untouched_companion_defaults(self):
        with self.assertRaises(InvalidUrologyOperationResponseError):
            validate_urology_operation_response_dump(_response_dump())

    def test_accepts_explicitly_reviewed_companion(self):
        validate_urology_operation_response_dump(
            _response_dump(
                sections=(
                    "basis,introductie,toegang,concrement,afronding,"
                    "complicaties,contact"
                )
            )
        )


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
