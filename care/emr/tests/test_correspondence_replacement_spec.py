import inspect
from unittest import TestCase
from uuid import uuid4

from pydantic import ValidationError

from care.emr.resources.correspondence_replacement import (
    CorrespondenceCorrectionCommandSpec,
)

SHA256 = "a" * 64


class TestCorrespondenceCorrectionCommandSpec(TestCase):
    @staticmethod
    def _common(command_type):
        return {
            "client_request_id": uuid4(),
            "command_type": command_type,
            "expected_case_version": 3,
            "expected_case_hash": SHA256,
        }

    def test_replacement_acknowledged_resolution_accepts_exact_attempt_shape(self):
        payload = {
            **self._common("resolve"),
            "attempt": uuid4(),
            "resolution_mode": "replacement_acknowledged",
            "confirmed": True,
        }

        spec = CorrespondenceCorrectionCommandSpec.model_validate(payload)

        self.assertEqual(spec.resolution_mode, "replacement_acknowledged")
        self.assertEqual(spec.attempt, payload["attempt"])
        self.assertTrue(spec.confirmed)

    def test_original_not_delivered_resolution_accepts_shape_without_attempt(self):
        payload = {
            **self._common("resolve"),
            "resolution_mode": "original_not_delivered",
            "confirmed": True,
        }

        spec = CorrespondenceCorrectionCommandSpec.model_validate(payload)

        self.assertEqual(spec.resolution_mode, "original_not_delivered")
        self.assertIsNone(spec.attempt)
        self.assertTrue(spec.confirmed)

    def test_replacement_acknowledged_resolution_rejects_missing_attempt(self):
        payload = {
            **self._common("resolve"),
            "resolution_mode": "replacement_acknowledged",
            "confirmed": True,
        }

        with self.assertRaises(ValidationError):
            CorrespondenceCorrectionCommandSpec.model_validate(payload)

    def test_original_not_delivered_resolution_rejects_replacement_attempt(self):
        payload = {
            **self._common("resolve"),
            "attempt": uuid4(),
            "resolution_mode": "original_not_delivered",
            "confirmed": True,
        }

        with self.assertRaises(ValidationError):
            CorrespondenceCorrectionCommandSpec.model_validate(payload)

    def test_start_replacement_accepts_only_the_exact_start_fields(self):
        medication_action = {
            "id": uuid4(),
            "client_request_id": uuid4(),
        }
        payload = {
            **self._common("start_replacement"),
            "patient": uuid4(),
            "encounter": uuid4(),
            "facility": uuid4(),
            "department": uuid4(),
            "encounter_reason": uuid4(),
            "form_submission": uuid4(),
            "form_source_version": 4,
            "form_source_hash": SHA256,
            "form_artifact": uuid4(),
            "form_artifact_hash": SHA256,
            "medication_actions": [medication_action],
            "template": uuid4(),
            "template_version": 2,
            "template_hash": SHA256,
            "author": uuid4(),
            "recipient": uuid4(),
            "recipient_version": 5,
            "recipient_hash": SHA256,
        }

        spec = CorrespondenceCorrectionCommandSpec.model_validate(payload)

        self.assertEqual(spec.model_fields_set, set(payload))
        self.assertEqual(spec.command_type, "start_replacement")
        self.assertEqual(spec.medication_actions[0].id, medication_action["id"])
        self.assertIsNone(spec.confirmed)
        self.assertIsNone(spec.attempt)

    def test_start_replacement_rejects_a_declared_cross_command_field(self):
        payload = {
            **self._common("start_replacement"),
            "patient": uuid4(),
            "encounter": uuid4(),
            "facility": uuid4(),
            "department": uuid4(),
            "encounter_reason": uuid4(),
            "form_submission": uuid4(),
            "form_source_version": 4,
            "form_source_hash": SHA256,
            "form_artifact": uuid4(),
            "form_artifact_hash": SHA256,
            "medication_actions": [],
            "template": uuid4(),
            "template_version": 2,
            "template_hash": SHA256,
            "author": uuid4(),
            "recipient": uuid4(),
            "recipient_version": 5,
            "recipient_hash": SHA256,
            "confirmed": True,
        }

        with self.assertRaises(ValidationError):
            CorrespondenceCorrectionCommandSpec.model_validate(payload)

    def test_unknown_extra_fields_are_forbidden(self):
        payload = {
            **self._common("resolve"),
            "resolution_mode": "original_not_delivered",
            "confirmed": True,
            "untrusted_extra": "not-accepted",
        }

        with self.assertRaises(ValidationError):
            CorrespondenceCorrectionCommandSpec.model_validate(payload)

    def test_existing_case_is_locked_before_downstream_correspondence_resources(self):
        from care.emr.api.viewsets.correspondence_continuity import (
            CorrespondenceContinuityViewSet,
        )
        from care.emr.correspondence.correction import (
            materialize_claimed_correction_outbox,
            refresh_correction_case_for_delivery,
        )
        from care.emr.correspondence.replacement import (
            refresh_replacement_case_for_delivery,
        )

        continuity = inspect.getsource(
            CorrespondenceContinuityViewSet._build_locked_payload  # noqa: SLF001
        )
        materializer = inspect.getsource(materialize_claimed_correction_outbox)
        original_refresh = inspect.getsource(refresh_correction_case_for_delivery)
        replacement_refresh = inspect.getsource(refresh_replacement_case_for_delivery)

        self.assertLess(
            continuity.index("case_reference = self._lock_case_reference"),
            continuity.index("compilation = ("),
        )
        self.assertLess(
            materializer.index("existing_cases = list("),
            materializer.index("outbox = ("),
        )
        self.assertLess(
            materializer.index("existing_cases = list("),
            materializer.index("compilations = list("),
        )
        self.assertLess(
            original_refresh.index("case_reference = ("),
            original_refresh.index("compilation = ("),
        )
        self.assertLess(
            replacement_refresh.index("case = ("),
            replacement_refresh.index("locked_deliveries ="),
        )
