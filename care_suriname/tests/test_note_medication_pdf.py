"""Contract: medication prescribed from inside a note is printed on its PDF.

Owner request 27 September 2026 (frontend forms/note-medication): the paper
file must list what the consult prescribed in CARE.
"""

from django.test import SimpleTestCase

from care_suriname.reports.form_submission_clinical_content import (
    render_clinical_content,
)


def _dump(actions):
    return {
        "content": {
            "noteText": "Acute urineretentie, katheter geplaatst.",
            "clinicalActions": {
                "schema": "care.urology.form-clinical-actions",
                "version": 1,
                "actions": actions,
            },
        }
    }


class NoteMedicationPdfTests(SimpleTestCase):
    def test_confirmed_note_prescriptions_are_listed(self):
        html = render_clinical_content(
            _dump(
                [
                    {
                        "actionSlot": "medication-request:13f344c0",
                        "state": "confirmed",
                        "narrative": "Start Tamsulosin 0.4 mg, 1dd1, Oraal.",
                    }
                ]
            )
        )
        self.assertIn("Medicatie vastgelegd in CARE tijdens dit consult", html)
        self.assertIn("Start Tamsulosin 0.4 mg, 1dd1, Oraal.", html)

    def test_unconfirmed_and_bph_actions_are_not_printed(self):
        html = render_clinical_content(
            _dump(
                [
                    {
                        "actionSlot": "medication-request:13f344c0",
                        "state": "pending",
                        "narrative": "Never shown",
                    },
                    {
                        "actionSlot": "bph.tamsulosin",
                        "state": "confirmed",
                        "narrative": "BPH line",
                    },
                ]
            )
        )
        self.assertNotIn("Medicatie vastgelegd", html)
        self.assertNotIn("BPH line", html)

    def test_narrative_is_escaped(self):
        html = render_clinical_content(
            _dump(
                [
                    {
                        "actionSlot": "medication-request:1",
                        "state": "confirmed",
                        "narrative": "<b>x</b>",
                    }
                ]
            )
        )
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
