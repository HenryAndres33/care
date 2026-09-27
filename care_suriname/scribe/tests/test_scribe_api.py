import json
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from care.security.permissions.encounter import EncounterPermissions
from care.utils.tests.base import CareAPITestBase
from care_suriname.scribe.gemini import GeminiResult, ScribeUpstreamError

ENABLED = {"SCRIBE_ENABLED": "true", "SCRIBE_GCP_PROJECT": "test-project"}
FIELDS = json.dumps(
    [
        {"name": "KLACHT", "type": "plain"},
        {"name": "ZIJDE", "type": "choice", "options": ["links", "rechts"]},
    ]
)
RESULT = GeminiResult(
    payload={
        "transcript": "Arts: waar zit de pijn? Patiënt: links.",
        "answers": [
            {"name": "ZIJDE", "value": "links", "evidence": "links"},
            {"name": "NIET_GEVRAAGD", "value": "x", "evidence": "x"},
        ],
    },
    input_tokens=800,
    audio_tokens=750,
    output_tokens=40,
    thinking_tokens=300,
)


@mock.patch.dict("os.environ", ENABLED)
class ScribeFieldDraftApiTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.patient = self.create_patient()
        self.encounter = self.create_encounter(
            patient=self.patient, facility=self.facility, organization=self.organization
        )
        self.url = reverse("scribe-field-drafts")
        self.client.force_authenticate(user=self.user)

    def allow_notes(self):
        role = self.create_role_with_permissions(
            [EncounterPermissions.can_submit_encounter_questionnaire.name]
        )
        self.attach_role_facility_organization_user(self.organization, self.user, role)

    def post(self, *, audio=b"webm-bytes", content_type="audio/webm;codecs=opus"):
        data = {"encounter": str(self.encounter.external_id), "fields": FIELDS}
        if audio is not None:
            data["audio"] = SimpleUploadedFile("c.webm", audio, content_type)
        return self.client.post(self.url, data, format="multipart")

    def test_disabled_scribe_answers_503_and_reports_off(self):
        self.allow_notes()
        self.assertTrue(self.client.get(self.url).json()["enabled"])
        with mock.patch.dict("os.environ", {"SCRIBE_ENABLED": "false"}):
            response = self.post()
            status_body = self.client.get(self.url).json()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["errors"][0]["type"], "scribe_disabled")
        self.assertEqual(status_body["enabled"], False)

    @mock.patch("care_suriname.scribe.views.generate_field_answers")
    def test_user_without_note_permission_is_refused_before_any_model_call(
        self, generate
    ):
        response = self.post()
        self.assertEqual(response.status_code, 403)
        generate.assert_not_called()

    @mock.patch("care_suriname.scribe.views.generate_field_answers")
    def test_closed_encounter_is_refused(self, generate):
        self.allow_notes()
        self.encounter.status = "completed"
        self.encounter.save()
        self.assertEqual(self.post().status_code, 403)
        generate.assert_not_called()

    @mock.patch("care_suriname.scribe.views.generate_field_answers")
    def test_answers_are_filtered_and_response_is_not_cached(self, generate):
        self.allow_notes()
        generate.return_value = RESULT
        response = self.post()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["contract"], "care-suriname-scribe-v1")
        self.assertEqual(
            body["answers"], [{"name": "ZIJDE", "value": "links", "evidence": "links"}]
        )
        self.assertEqual(body["usage"]["thinking_tokens"], 300)
        self.assertEqual(response["Cache-Control"], "no-store")
        _, audio, mime_type, prompt = generate.call_args.args
        self.assertEqual((audio, mime_type), (b"webm-bytes", "audio/webm"))
        self.assertIn('"name": "KLACHT"', prompt)

    @mock.patch("care_suriname.scribe.views.generate_field_answers")
    def test_invalid_audio_is_refused_without_a_model_call(self, generate):
        self.allow_notes()
        cases = [
            ({"audio": None}, "scribe_audio_missing"),
            ({"content_type": "video/webm"}, "scribe_audio_type_unsupported"),
        ]
        for kwargs, code in cases:
            with self.subTest(code=code):
                response = self.post(**kwargs)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["errors"][0]["type"], code)
        with mock.patch.dict("os.environ", {"SCRIBE_MAX_AUDIO_MB": "0"}):
            response = self.post()
        self.assertEqual(response.json()["errors"][0]["type"], "scribe_audio_too_large")
        generate.assert_not_called()

    @mock.patch("care_suriname.scribe.views.generate_field_answers")
    def test_upstream_failure_is_reported_not_hidden(self, generate):
        self.allow_notes()
        generate.side_effect = ScribeUpstreamError("scribe_upstream_http_429")
        response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["errors"][0]["type"], "scribe_upstream_http_429"
        )
