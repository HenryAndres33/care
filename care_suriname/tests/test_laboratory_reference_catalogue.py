import unittest

from care_suriname.resources.laboratory_reference import (
    TEXTBOOK_REFERENCE_CATALOGUE,
    TEXTBOOK_REFERENCE_SOURCES,
)


class LaboratoryReferenceCatalogueTests(unittest.TestCase):
    def test_baseline_has_one_exact_entry_per_definition_variant(self):
        keys = [
            (entry.loinc, entry.unit_system, entry.unit_code)
            for entry in TEXTBOOK_REFERENCE_CATALOGUE
        ]
        self.assertEqual(len(keys), 30)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn(("718-7", "http://unitsofmeasure.org", "g/dL"), keys)
        self.assertIn(("59260-0", "http://unitsofmeasure.org", "mmol/L"), keys)
        self.assertNotIn(("718-7", "http://unitsofmeasure.org", "mmol/L"), keys)

    def test_every_rule_has_reproducible_source_and_exact_unit(self):
        for entry in TEXTBOOK_REFERENCE_CATALOGUE:
            self.assertEqual(
                entry.unit_system,
                "http://unitsofmeasure.org" if entry.unit_code is not None else "",
            )
            for source_id in entry.provenance_source_ids:
                self.assertIn(source_id, TEXTBOOK_REFERENCE_SOURCES)
            for rule in entry.rules:
                with self.subTest(rule=rule.id):
                    self.assertEqual(rule.loinc, entry.loinc)
                    self.assertEqual(rule.unit_system, entry.unit_system)
                    self.assertEqual(rule.unit_code, entry.unit_code)
                    self.assertIn(rule.source_id, TEXTBOOK_REFERENCE_SOURCES)
                    source = TEXTBOOK_REFERENCE_SOURCES[rule.source_id]
                    self.assertTrue(source.url.startswith("https://"))
                    self.assertEqual(source.accessed_on, "2026-09-20")
                    self.assertGreaterEqual(rule.applicability.minimum_age_years, 18)

    def test_catalogue_contains_no_critical_threshold(self):
        serialized = repr(TEXTBOOK_REFERENCE_CATALOGUE).lower()
        self.assertNotIn("critical", serialized)
        self.assertNotIn("alarm", serialized)

    def test_only_revised_general_kidney_entries_use_v2(self):
        revised = {
            entry.loinc
            for entry in TEXTBOOK_REFERENCE_CATALOGUE
            if entry.catalogue_version == "general-academic-adult-v2"
        }
        self.assertEqual(
            revised,
            {
                "14682-9",
                "22664-7",
                "2951-2",
                "2823-3",
                "2075-0",
                "1963-8",
                "14879-1",
            },
        )
        for entry in TEXTBOOK_REFERENCE_CATALOGUE:
            if entry.loinc in revised:
                self.assertTrue(
                    all(rule.method == "unspecified" for rule in entry.rules)
                )


if __name__ == "__main__":
    unittest.main()
