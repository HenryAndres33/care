import unittest

from care_suriname.resources.laboratory_reference import TEXTBOOK_REFERENCE_CATALOGUE
from care_suriname.resources.laboratory_reference.configuration import (
    CATALOGUE_VERSION,
    DefinitionSnapshot,
    build_configuration_plan,
    build_reference_metadata,
    build_rollback_plan,
)


class LaboratoryReferenceConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.definition = DefinitionSnapshot(
            id="definition-id",
            slug="facility-leukocytes",
            version=1,
            loinc="6690-2",
            unit_system="http://unitsofmeasure.org",
            unit_code="10*9/L",
            qualified_ranges=(),
            metadata={},
        )

    def test_metadata_has_full_provenance_and_stable_fingerprint(self):
        entry = next(
            entry for entry in TEXTBOOK_REFERENCE_CATALOGUE if entry.loinc == "2951-2"
        )
        first = build_reference_metadata(entry)
        self.assertEqual(first, build_reference_metadata(entry))
        self.assertEqual(first["catalogue_version"], CATALOGUE_VERSION)
        self.assertEqual(
            first["sources"][0]["publisher"],
            "Pathology Harmony Group / Association for Laboratory Medicine",
        )
        self.assertEqual(len(first["fingerprint"]), 64)

        psa = next(
            entry for entry in TEXTBOOK_REFERENCE_CATALOGUE if entry.loinc == "2857-1"
        )
        psa_metadata = build_reference_metadata(psa)
        self.assertEqual(psa_metadata["rules"], [])
        self.assertEqual(
            {source["id"] for source in psa_metadata["sources"]},
            {"abim-reference-ranges-2026", "mayo-psa-2026"},
        )
        self.assertEqual(psa_metadata["catalogue_version"], "general-academic-adult-v1")

    def test_dry_run_blocks_native_age_today_semantics_by_default(self):
        plan = build_configuration_plan(
            TEXTBOOK_REFERENCE_CATALOGUE, (self.definition,)
        )
        self.assertEqual(plan.actions[0].action, "blocked")
        self.assertIn("age now", plan.actions[0].reason)
        self.assertEqual(plan.missing_definitions[0]["loinc"], "59260-0")
        self.assertEqual(
            build_rollback_plan(plan)[0]["action"], "retire_created_definition"
        )

    def test_collection_age_support_does_not_drop_other_required_context(self):
        plan = build_configuration_plan(
            TEXTBOOK_REFERENCE_CATALOGUE,
            (self.definition,),
            collection_age_enforced=True,
        )
        rollback = build_rollback_plan(plan)
        self.assertEqual(plan.actions[0].action, "blocked")
        self.assertIn("cannot be represented", plan.actions[0].reason)
        self.assertNotIn("predecessor_id", rollback[0])


if __name__ == "__main__":
    unittest.main()
