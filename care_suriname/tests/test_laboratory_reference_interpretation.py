import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from care_suriname.resources.laboratory_reference import (
    interpret_governed_laboratory_reference,
)


class LaboratoryReferenceInterpretationTests(unittest.TestCase):
    def test_collection_age_and_hb_units_are_exact(self):
        common = {
            "value": Decimal(13),
            "collected_at": datetime(2026, 9, 20, 12, tzinfo=UTC),
            "birth_date": date(1980, 10, 1),
            "sex": "male",
            "specimen": "blood",
            "method": None,
        }
        result = interpret_governed_laboratory_reference(
            loinc="718-7",
            unit_system="http://unitsofmeasure.org",
            unit_code="g/dL",
            **common,
        )
        wrong_unit = interpret_governed_laboratory_reference(
            loinc="718-7",
            unit_system="http://unitsofmeasure.org",
            unit_code="mmol/L",
            **common,
        )
        wrong_system = interpret_governed_laboratory_reference(
            loinc="718-7",
            unit_system="system-ucum-units",
            unit_code="g/dL",
            **common,
        )
        self.assertEqual(result.status, "interpreted")
        self.assertEqual(result.category, "normal")
        self.assertEqual(wrong_unit.status, "unavailable")
        self.assertEqual(wrong_unit.reason, "reference_not_configured")
        self.assertEqual(wrong_system.reason, "reference_not_configured")

    def test_unknown_collection_and_pregnancy_context_are_unavailable(self):
        common = {
            "loinc": "718-7",
            "unit_system": "http://unitsofmeasure.org",
            "unit_code": "g/dL",
            "value": Decimal(12),
            "birth_date": date(1980, 1, 1),
            "sex": "female",
            "specimen": "blood",
            "method": None,
        }
        unknown = interpret_governed_laboratory_reference(collected_at=None, **common)
        pregnancy_unknown = interpret_governed_laboratory_reference(
            collected_at=datetime(2026, 9, 20, tzinfo=UTC), **common
        )
        self.assertEqual(unknown.reason, "age_at_collection_required")
        self.assertEqual(pregnancy_unknown.reason, "context_required")

    def test_strict_bound_and_method_context_are_not_relaxed(self):
        crp = interpret_governed_laboratory_reference(
            loinc="1988-5",
            unit_system="http://unitsofmeasure.org",
            unit_code="mg/L",
            value=Decimal(3),
            collected_at=datetime(2026, 9, 20, tzinfo=UTC),
            birth_date=date(1980, 1, 1),
            sex="male",
            specimen="serum",
            method=None,
        )
        hba1c = interpret_governed_laboratory_reference(
            loinc="59261-8",
            unit_system="http://unitsofmeasure.org",
            unit_code="mmol/mol",
            value=Decimal(40),
            collected_at=datetime(2026, 9, 20, tzinfo=UTC),
            birth_date=date(1980, 1, 1),
            sex="male",
            specimen="whole_blood",
            method=None,
            context_flags=frozenset(
                {
                    "no_renal_failure",
                    "no_anemia",
                    "no_hemoglobinopathy",
                    "no_hiv",
                    "stable_red_cell_turnover",
                }
            ),
        )
        self.assertEqual(crp.category, "high")
        self.assertEqual(hba1c.reason, "method_required")

    def test_general_renal_defaults_do_not_require_an_invented_method(self):
        common = {
            "unit_system": "http://unitsofmeasure.org",
            "collected_at": datetime(2026, 9, 20, tzinfo=UTC),
            "birth_date": date(1980, 1, 1),
            "sex": "male",
            "specimen": "serum",
            "method": None,
        }
        potassium = interpret_governed_laboratory_reference(
            loinc="2823-3",
            unit_code="mmol/L",
            value=Decimal("5.4"),
            **common,
        )
        creatinine = interpret_governed_laboratory_reference(
            loinc="14682-9",
            unit_code="umol/L",
            value=Decimal(90),
            **common,
        )
        phosphate = interpret_governed_laboratory_reference(
            loinc="14879-1",
            unit_code="mmol/L",
            value=Decimal("1.5"),
            **common,
        )
        self.assertEqual(potassium.status, "interpreted")
        self.assertEqual(potassium.category, "high")
        self.assertEqual(potassium.catalogue_version, "general-academic-adult-v2")
        self.assertEqual(creatinine.status, "interpreted")
        self.assertEqual(creatinine.category, "normal")
        self.assertEqual(creatinine.rule.method, "unspecified")
        self.assertEqual(phosphate.status, "interpreted")
        self.assertEqual(phosphate.category, "normal")
        self.assertEqual(phosphate.rule.source_id, "pathology-harmony-adult-2011")

    def test_governed_composite_specimen_labels_require_explicit_allowed_specimen(self):
        common = {
            "loinc": "1988-5",
            "unit_system": "http://unitsofmeasure.org",
            "unit_code": "mg/L",
            "value": Decimal(2),
            "collected_at": datetime(2026, 9, 20, tzinfo=UTC),
            "birth_date": date(1980, 1, 1),
            "sex": "male",
            "method": None,
        }
        self.assertEqual(
            interpret_governed_laboratory_reference(specimen="serum", **common).status,
            "interpreted",
        )
        self.assertEqual(
            interpret_governed_laboratory_reference(specimen="plasma", **common).status,
            "interpreted",
        )
        self.assertEqual(
            interpret_governed_laboratory_reference(
                specimen="whole_blood", **common
            ).reason,
            "specimen_not_applicable",
        )

    def test_suriname_calendar_age_and_unknown_sex_fail_closed(self):
        birthday_not_reached = interpret_governed_laboratory_reference(
            loinc="718-7",
            unit_system="http://unitsofmeasure.org",
            unit_code="g/dL",
            value=Decimal(13),
            collected_at=datetime(2026, 9, 20, 1, tzinfo=UTC),
            birth_date=date(2008, 9, 20),
            sex="male",
            specimen="blood",
            method=None,
        )
        unknown_sex = interpret_governed_laboratory_reference(
            loinc="6690-2",
            unit_system="http://unitsofmeasure.org",
            unit_code="10*9/L",
            value=Decimal(5),
            collected_at=datetime(2026, 9, 20, tzinfo=UTC),
            birth_date=date(1980, 1, 1),
            sex=None,
            specimen="blood",
            method=None,
        )
        self.assertEqual(
            birthday_not_reached.reason, "pediatric_reference_not_available"
        )
        self.assertEqual(unknown_sex.reason, "sex_required")
        for native_unknown in ("unknown", "other"):
            with self.subTest(sex=native_unknown):
                result = interpret_governed_laboratory_reference(
                    loinc="6690-2",
                    unit_system="http://unitsofmeasure.org",
                    unit_code="10*9/L",
                    value=Decimal(5),
                    collected_at=datetime(2026, 9, 20, tzinfo=UTC),
                    birth_date=date(1980, 1, 1),
                    sex=native_unknown,
                    specimen="blood",
                    method=None,
                )
                self.assertEqual(result.reason, "sex_required")


if __name__ == "__main__":
    unittest.main()

    def test_year_of_birth_only_gives_an_age_interval(self):
        common = {
            "loinc": "2160-0",
            "unit_system": "http://unitsofmeasure.org",
            "unit_code": "umol/L",
            "value": Decimal(200),
            "collected_at": datetime(2026, 9, 20, tzinfo=UTC),
            "birth_date": None,
            "sex": "male",
            "specimen": "serum",
            "method": None,
        }
        adult = interpret_governed_laboratory_reference(birth_year=1986, **common)
        straddling = interpret_governed_laboratory_reference(birth_year=2008, **common)
        child = interpret_governed_laboratory_reference(birth_year=2015, **common)
        unknown = interpret_governed_laboratory_reference(birth_year=None, **common)
        self.assertEqual(adult.status, "interpreted")
        self.assertEqual(adult.category, "high")
        self.assertEqual(straddling.reason, "age_at_collection_required")
        self.assertEqual(child.reason, "pediatric_reference_not_available")
        self.assertEqual(unknown.reason, "age_at_collection_required")
