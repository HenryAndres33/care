import json
from datetime import date

from django.test import SimpleTestCase

from care_suriname.scribe.contract import (
    ScribeField,
    ScribeRequestError,
    audio_mime_type,
    filter_answers,
    parse_fields,
)
from care_suriname.scribe.gemini import ScribeUpstreamError, _payload
from care_suriname.scribe.pricing import estimate_cost_usd
from care_suriname.scribe.prompt import build_prompt

FIELDS = [
    ScribeField(name="KLACHT", type="plain"),
    ScribeField(name="ZIJDE", type="choice", options=["links", "rechts"]),
    ScribeField(name="SYMPTOMEN", type="multi", options=["nycturie", "urgency"]),
    ScribeField(name="LIJST", type="list"),
]


class ScribeContractTests(SimpleTestCase):
    def test_parse_fields_rejects_malformed_and_duplicate_input(self):
        for raw in [None, "", "{", "[]", json.dumps([{"name": "A", "type": "x"}])]:
            with self.subTest(raw=raw), self.assertRaises(ScribeRequestError):
                parse_fields(raw)
        duplicate = json.dumps([{"name": "A", "type": "plain"}] * 2)
        with self.assertRaises(ScribeRequestError):
            parse_fields(duplicate)
        [field] = parse_fields(json.dumps([{"name": "A", "type": "plain"}]))
        self.assertEqual(field.name, "A")

    def test_audio_mime_type_strips_codec_and_rejects_non_audio(self):
        self.assertEqual(audio_mime_type("audio/webm;codecs=opus"), "audio/webm")
        for content_type in [None, "", "video/webm", "application/pdf"]:
            with (
                self.subTest(content_type=content_type),
                self.assertRaises(ScribeRequestError),
            ):
                audio_mime_type(content_type)

    def test_only_requested_fields_and_first_answer_survive(self):
        answers = filter_answers(
            FIELDS,
            [
                {"name": "KLACHT", "value": " pijn in de flank ", "evidence": "pijn"},
                {"name": "KLACHT", "value": "tweede antwoord", "evidence": ""},
                {"name": "ONBEKEND", "value": "x", "evidence": ""},
                {"name": "ZIJDE", "value": "", "evidence": ""},
                "not an object",
            ],
        )
        self.assertEqual(
            answers,
            [{"name": "KLACHT", "value": "pijn in de flank", "evidence": "pijn"}],
        )

    def test_choice_and_multi_must_use_offered_options(self):
        answers = filter_answers(
            FIELDS,
            [
                {"name": "ZIJDE", "value": "Links"},
                {"name": "SYMPTOMEN", "value": "urgency, nycturie, urgency"},
                {"name": "LIJST", "value": "iets"},
            ],
        )
        self.assertEqual(
            answers,
            [{"name": "SYMPTOMEN", "value": "urgency, nycturie", "evidence": ""}],
        )
        self.assertEqual(filter_answers(FIELDS, "not a list"), [])

    def test_prompt_lists_field_names_but_no_record_content(self):
        prompt = build_prompt(FIELDS[:2])
        self.assertIn('"name": "ZIJDE"', prompt)
        self.assertIn("Never guess", prompt)

    def test_cost_estimate_follows_introductory_price(self):
        self.assertEqual(
            estimate_cost_usd("gemini-3.8-flash", 1_000_000, 0, date(2026, 12, 31)),
            0.75,
        )
        self.assertEqual(
            estimate_cost_usd("gemini-3.8-flash", 1_000_000, 0, date(2027, 1, 1)),
            1.5,
        )
        self.assertIsNone(estimate_cost_usd("unknown", 1, 1))


class GeminiPayloadTests(SimpleTestCase):
    def body(self, text, finish="STOP", thought=False):
        parts = [{"text": "denken", "thought": True}] if thought else []
        parts.append({"text": text})
        return {"candidates": [{"finishReason": finish, "content": {"parts": parts}}]}

    def test_thought_parts_are_ignored(self):
        payload = _payload(
            self.body('{"transcript": "t", "answers": []}', thought=True)
        )
        self.assertEqual(payload["transcript"], "t")

    def test_incomplete_or_malformed_output_fails_closed(self):
        for body in [
            {},
            self.body('{"transcript": "t"}', finish="MAX_TOKENS"),
            self.body("not json"),
            self.body('{"answers": []}'),
            self.body("[]"),
        ]:
            with self.subTest(body=body), self.assertRaises(ScribeUpstreamError):
                _payload(body)
