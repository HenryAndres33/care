"""Requested multi-analyte stress data uses the normal note command contract."""

from care.emr.models.observation import Observation
from care.emr.resources.form_submission.note_lab_text import TESTS
from care.emr.tests.test_form_submission_note_labs import NoteLabCommandTests


class MultiTestNoteLabs(NoteLabCommandTests):
    def test_multi_analyte_native_identity_units_and_repeat_saves(self):
        self.allow_labs()
        rows = [
            f"{slot}: {index + 1},5 {spec[3]}; afnamedatum: 2025-01-01"
            for index, (slot, spec) in enumerate(TESTS.items())
        ]
        text = (
            "Gekoppeld laboratorium:\n"
            + "\n".join(rows)
            + "\nEinde gekoppeld laboratorium."
        )
        for _ in range(5):
            response = self.update(self.dump(text))
            self.assertEqual(response.status_code, 200, response.data)
            self.submission.refresh_from_db()
        self.assertEqual(Observation.objects.count(), len(TESTS))
        for index, (slot, spec) in enumerate(TESTS.items()):
            observation = Observation.objects.get(value__value=f"{index + 1}.5")
            self.assertEqual(observation.main_code["code"], spec[0], slot)
            self.assertEqual(observation.value["unit"]["code"], spec[2], slot)
            self.assertEqual(observation.patient_id, self.patient.id)

    def test_incorrect_or_ambiguous_units_roll_back_whole_batch(self):
        self.allow_labs()
        for line in [
            "Hemoglobine: 8 mmol/L",
            "D-dimeer FEU: 0.5 mg/L",
            "D-dimeer DDU: 0.5 mg/L FEU",
            "Creatinine: 1 mg/dL",
            "Glucose: <5 mmol/L",
            "Natrium: -1 mmol/L",
        ]:
            text = (
                "Gekoppeld laboratorium:\nCRP: 10 mg/L; afnamedatum: 2025-01-01\n"
                + line
                + "; afnamedatum: 2025-01-01\nEinde gekoppeld laboratorium."
            )
            response = self.update(self.dump(text))
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(Observation.objects.count(), 0)
