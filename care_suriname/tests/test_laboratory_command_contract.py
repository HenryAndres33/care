import copy
import unittest
from decimal import Decimal

from pydantic import ValidationError

from care.emr.resources.common.coding import Coding
from care_suriname.resources.laboratory_commands import (
    LABORATORY_COMMAND_CONTRACT,
    LaboratoryCommandResponse,
    parse_laboratory_decimal,
    parse_laboratory_integer,
    validate_laboratory_command,
)

IDS = {
    "client": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "patient": "11111111-1111-4111-8111-111111111111",
    "facility": "22222222-2222-4222-8222-222222222222",
    "encounter": "33333333-3333-4333-8333-333333333333",
    "request": "44444444-4444-4444-8444-444444444444",
    "report": "55555555-5555-4555-8555-555555555555",
    "row": "66666666-6666-4666-8666-666666666666",
    "group": "69696969-6969-4969-8969-696969696969",
    "definition": "77777777-7777-4777-8777-777777777777",
    "observation": "88888888-8888-4888-8888-888888888888",
    "actor": "99999999-9999-4999-8999-999999999999",
}


def create_payload() -> dict:
    return {
        "contract": LABORATORY_COMMAND_CONTRACT,
        "action": "create_draft",
        "client_request_id": IDS["client"],
        "expected_version": 0,
        "patient": IDS["patient"],
        "facility": IDS["facility"],
        "encounter": IDS["encounter"],
        "service_request_id": IDS["request"],
        "report_id": IDS["report"],
        "source": {"kind": "external_lab", "label": "AZP centraal lab"},
        "rows": [
            {
                "row_id": IDS["row"],
                "collection_group_id": IDS["group"],
                "definition": {
                    "id": IDS["definition"],
                    "slug": "f-azp-creatinine",
                    "version": 1,
                    "fingerprint": "a" * 64,
                },
                "collected_at": {
                    "kind": "known",
                    "value": "2026-09-19T08:30:00-03:00",
                },
                "value": {
                    "kind": "quantity",
                    "input": "5,0",
                    "unit": {
                        "system": "http://unitsofmeasure.org",
                        "code": "umol/L",
                        "display": "µmol/L",
                    },
                },
            }
        ],
    }


def response_payload() -> dict:
    command = create_payload()
    row = command["rows"][0]
    return {
        "contract": LABORATORY_COMMAND_CONTRACT,
        "client_request_id": IDS["client"],
        "replayed": False,
        "command_result_version": 1,
        "report": {
            "id": IDS["report"],
            "service_request_id": IDS["request"],
            "patient": IDS["patient"],
            "facility": IDS["facility"],
            "encounter": IDS["encounter"],
            "status": "preliminary",
            "service_request_status": "draft",
            "source": command["source"],
            "audit": {
                "created_by": {"id": IDS["actor"], "display": "Synthetic actor"},
                "created_at": "2026-09-20T08:31:00-03:00",
                "finalized_by": None,
                "finalized_at": None,
                "latest_corrected_by": None,
                "latest_corrected_at": None,
            },
            "rows": [
                {
                    "observation_id": IDS["row"],
                    "row_id": IDS["row"],
                    "collection_group_id": IDS["group"],
                    "status": "final",
                    "definition": row["definition"],
                    "code": {
                        "system": "http://loinc.org",
                        "code": "14682-9",
                        "display": "Creatinine",
                    },
                    "method": None,
                    "body_site": None,
                    "collected_at": row["collected_at"],
                    "specimen": None,
                    "confirmed_reference_context": [],
                    "value": {**row["value"], "stored": "5.0"},
                    "interpretation": {},
                    "reference_range": [],
                    "reference_provenance": None,
                    "parent_observation_id": None,
                    "correction_reason": None,
                }
            ],
        },
    }


class LaboratoryCommandContractTests(unittest.TestCase):
    def assert_invalid(self, payload: dict):
        with self.assertRaises(ValidationError):
            validate_laboratory_command(payload)

    def test_valid_quantity_preserves_input_and_reuses_native_coding(self):
        command = validate_laboratory_command(create_payload())

        self.assertEqual(command.rows[0].value.input, "5,0")
        self.assertEqual(parse_laboratory_decimal("5,0"), Decimal("5.0"))
        self.assertIsInstance(command.rows[0].value.unit, Coding)
        self.assertEqual(command.rows[0].collected_at.value.utcoffset().seconds, 75600)

    def test_unknown_collection_time_is_explicit(self):
        payload = create_payload()
        payload["rows"][0]["collected_at"] = {"kind": "unknown"}
        command = validate_laboratory_command(payload)

        self.assertEqual(command.rows[0].collected_at.kind, "unknown")

    def test_one_collection_moment_accepts_different_row_specimens(self):
        payload = create_payload()
        payload["rows"][0]["specimen"] = "serum"
        second = copy.deepcopy(payload["rows"][0])
        second["row_id"] = "67676767-6767-4767-8767-676767676767"
        second["specimen"] = "whole_blood"
        payload["rows"].append(second)

        command = validate_laboratory_command(payload)

        self.assertEqual(
            [row.specimen for row in command.rows], ["serum", "whole_blood"]
        )

    def test_impossible_naive_and_implicit_dates_are_rejected(self):
        for collected_at in (
            {"kind": "known", "value": "2026-02-30T08:30:00-03:00"},
            {"kind": "known", "value": "2026-09-20T08:30:00"},
            {"kind": "known", "value": "2099-09-20T08:30:00-03:00"},
            None,
        ):
            with self.subTest(collected_at=collected_at):
                payload = create_payload()
                if collected_at is None:
                    del payload["rows"][0]["collected_at"]
                else:
                    payload["rows"][0]["collected_at"] = collected_at
                self.assert_invalid(payload)

    def test_decimal_precision_and_plain_notation_are_bounded(self):
        self.assertEqual(
            parse_laboratory_decimal("12345678901234.123456"),
            Decimal("12345678901234.123456"),
        )
        self.assertEqual(parse_laboratory_decimal("0.000001"), Decimal("0.000001"))
        self.assertEqual(parse_laboratory_decimal(".5"), Decimal("0.5"))
        self.assertEqual(parse_laboratory_decimal("1."), Decimal(1))
        self.assertEqual(
            parse_laboratory_integer("-12345678901234567890"),
            -12345678901234567890,
        )

        for value in (
            "123456789012345.123456",
            "123456789012345",
            "0.0000001",
            "1e3",
            "NaN",
            " 5,0",
            "1,2.3",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_laboratory_decimal(value)
        with self.assertRaises(ValueError):
            parse_laboratory_integer("1.0")

    def test_versions_and_known_datetime_reject_coercion(self):
        for version in (False, 0.0, "0"):
            with self.subTest(version=version):
                payload = create_payload()
                payload["expected_version"] = version
                self.assert_invalid(payload)
        for version in (True, 1.0, "1"):
            with self.subTest(definition_version=version):
                payload = create_payload()
                payload["rows"][0]["definition"]["version"] = version
                self.assert_invalid(payload)
        for value in (0, 1_758_358_600.0, True):
            with self.subTest(value=value):
                payload = create_payload()
                payload["rows"][0]["collected_at"]["value"] = value
                self.assert_invalid(payload)

    def test_v1_accepts_only_supported_native_definition_value_kinds(self):
        supported = {
            "decimal": {"kind": "decimal", "input": "1,25"},
            "integer": {"kind": "integer", "input": "12"},
            "string": {"kind": "string", "value": "negatief"},
            "boolean": {"kind": "boolean", "value": False},
        }
        for kind, value in supported.items():
            with self.subTest(kind=kind):
                payload = create_payload()
                payload["rows"][0]["value"] = value
                self.assertEqual(
                    validate_laboratory_command(payload).rows[0].value.kind, kind
                )

        for unsupported in ("dateTime", "time", "choice", "coded"):
            with self.subTest(kind=unsupported):
                payload = create_payload()
                payload["rows"][0]["value"] = {"kind": unsupported, "value": "x"}
                self.assert_invalid(payload)

    def test_unit_source_rows_and_extra_fields_are_strict(self):
        mutations = []
        payload = create_payload()
        payload["rows"][0]["value"]["unit"]["system"] = ""
        mutations.append(payload)
        payload = create_payload()
        payload["source"]["label"] = "   "
        mutations.append(payload)
        payload = create_payload()
        payload["rows"].append(copy.deepcopy(payload["rows"][0]))
        mutations.append(payload)
        payload = create_payload()
        payload["unexpected"] = True
        mutations.append(payload)
        payload = create_payload()
        payload["rows"][0]["definition"]["reference"] = {}
        mutations.append(payload)
        payload = create_payload()
        payload["expected_version"] = 1
        mutations.append(payload)
        for payload in mutations:
            self.assert_invalid(payload)

    def test_update_finalize_and_correction_versions_and_identities_are_strict(self):
        update = create_payload()
        update.update(action="update_draft", expected_version=1)
        self.assertEqual(validate_laboratory_command(update).action, "update_draft")
        update["expected_version"] = 0
        self.assert_invalid(update)

        finalize = {
            key: create_payload()[key]
            for key in (
                "contract",
                "client_request_id",
                "patient",
                "facility",
                "encounter",
                "service_request_id",
                "report_id",
            )
        }
        finalize.update(action="finalize", expected_version=1)
        self.assertEqual(validate_laboratory_command(finalize).action, "finalize")

        correct = {**finalize, "action": "correct", "reason": "Gecorrigeerd"}
        correct["replacements"] = [
            {
                "replaces_observation_id": IDS["observation"],
                "row": create_payload()["rows"][0],
            }
        ]
        self.assertEqual(validate_laboratory_command(correct).action, "correct")
        same_identity = copy.deepcopy(correct)
        same_identity["replacements"][0]["row"]["row_id"] = IDS["observation"]
        self.assert_invalid(same_identity)
        correct["replacements"].append(copy.deepcopy(correct["replacements"][0]))
        self.assert_invalid(correct)

    def test_response_shape_is_strict_and_validates_canonical_storage(self):
        response = LaboratoryCommandResponse.model_validate(response_payload())
        self.assertEqual(response.report.rows[0].value.stored, "5.0")

        bad = response_payload()
        bad["report"]["rows"][0]["value"]["stored"] = "5,0"
        with self.assertRaises(ValidationError):
            LaboratoryCommandResponse.model_validate(bad)
        bad = response_payload()
        bad["report"]["rows"][0]["extra"] = "not allowed"
        with self.assertRaises(ValidationError):
            LaboratoryCommandResponse.model_validate(bad)
        bad = response_payload()
        bad["report"]["rows"][0]["collected_at"]["value"] = 1_758_358_600
        with self.assertRaises(ValidationError):
            LaboratoryCommandResponse.model_validate(bad)
