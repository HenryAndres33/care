"""Workflow stress cases; duplicates across note series are documented, not hidden."""

from uuid import uuid4

from django.urls import reverse

from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.observation import Observation
from care.emr.tests.test_form_submission_note_labs import TEXT, NoteLabCommandTests


class NoteLabStressTests(NoteLabCommandTests):
    def test_twenty_sequential_retries_and_decimal_format_changes(self):
        self.allow_labs()
        for index in range(20):
            text = TEXT if index % 2 else TEXT.replace("8 µg/L", "8,00 µg/L")
            response = self.update(self.dump(text))
            self.assertEqual(response.status_code, 200, response.data)
            self.submission.refresh_from_db()
        self.assertEqual(Observation.objects.count(), 3)
        self.assertEqual(DiagnosticReport.objects.count(), 3)

    def test_malformed_blocks_fail_atomically(self):
        self.allow_labs()
        for text in [
            TEXT + "\n" + TEXT,
            TEXT.replace("PSA actueel: 8", "PSA initieel: 8"),
            TEXT.replace("8 µg/L", "NaN µg/L"),
            TEXT.replace("8 µg/L", "8e3 µg/L"),
            TEXT.replace("2025-02-01", "2025-02-29"),
            TEXT.replace("8 µg/L", "[[*VALUE*]] µg/L"),
        ]:
            with self.subTest(text=text):
                response = self.update(self.dump(text))
                self.assertEqual(response.status_code, 400, response.data)
                self.submission.refresh_from_db()
                self.assertEqual(self.submission.resource_version, 1)
                self.assertEqual(Observation.objects.count(), 0)

    def test_different_form_type_cannot_publish_labs(self):
        self.allow_labs()
        dump = self.dump()
        dump["identity"]["formType"] = "other-form"
        response = self.update(dump)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Observation.objects.count(), 0)

    def test_known_limit_same_lab_copied_to_another_note_creates_new_results(self):
        self.allow_labs()
        self.assertEqual(self.update().status_code, 200)
        payload = self.command(
            response_dump=self.dump(),
            form_instance_id=str(uuid4()),
            note_lab_contract="v1",
        )
        payload.pop("expected_version")
        response = self.client.post(
            reverse("form_submission-idempotent-create-draft"), payload, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Observation.objects.count(), 6)

    def test_known_limit_same_sample_in_initial_and_current_slots_is_not_deduplicated(
        self,
    ):
        self.allow_labs()
        text = TEXT.replace("6 µg/L", "8 µg/L").replace("2025-01-01", "2025-02-01")
        self.assertEqual(self.update(self.dump(text)).status_code, 200)
        self.assertEqual(Observation.objects.filter(value__value="8").count(), 2)
