import copy
import unittest
from uuid import UUID

from care_suriname.resources.laboratory_commands import (
    canonical_laboratory_command_hash,
    validate_laboratory_command,
)
from care_suriname.tests.test_laboratory_command_contract import IDS, create_payload


class LaboratoryCommandHashingTests(unittest.TestCase):
    def test_hash_is_deterministic_and_scoped_to_raw_input_actor_and_context(self):
        payload = create_payload()
        command = validate_laboratory_command(payload)
        actor = UUID(IDS["actor"])
        expected = canonical_laboratory_command_hash(command, actor_id=actor)

        retry = copy.deepcopy(payload)
        retry["client_request_id"] = "abababab-abab-4bab-8bab-abababababab"
        retry["rows"][0]["value"]["unit"] = dict(
            reversed(list(retry["rows"][0]["value"]["unit"].items()))
        )
        self.assertEqual(
            canonical_laboratory_command_hash(
                validate_laboratory_command(retry), actor_id=actor
            ),
            expected,
        )

        variants = []
        raw_variant = copy.deepcopy(payload)
        raw_variant["rows"][0]["value"]["input"] = "5.0"
        variants.append((raw_variant, actor))
        context_variant = copy.deepcopy(payload)
        context_variant["patient"] = "12121212-1212-4212-8212-121212121212"
        variants.append((context_variant, actor))
        variants.append((payload, UUID("90909090-9090-4090-8090-909090909090")))

        for variant, variant_actor in variants:
            self.assertNotEqual(
                canonical_laboratory_command_hash(
                    validate_laboratory_command(variant), actor_id=variant_actor
                ),
                expected,
            )
