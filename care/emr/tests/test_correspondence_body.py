from django.test import SimpleTestCase

from care_suriname.reports.correspondence_body import render_correspondence_body


class TestCorrespondenceBody(SimpleTestCase):
    def test_formats_plain_conclusion_and_plan_headings(self):
        html = render_correspondence_body(
            "Conclusie:\nMacroscopische hematurie.\n\nBeleid:\nTURBT plannen."
        )

        self.assertIn("<h2>Conclusie</h2><p>Macroscopische hematurie.</p>", html)
        self.assertIn("<h2>Beleid</h2><p>TURBT plannen.</p>", html)

    def test_decision_sections_are_semantic_headings_with_compact_paragraphs(self):
        html = render_correspondence_body(
            "Anamnese:\nMacroscopische hematurie\n\n\n"
            "Conclusie & Bespreking:\nMultipele blaastumoren van 2-3 cm\n\n"
            "Beleid:\nTURT"
        )

        self.assertIn("<h2>Conclusie &amp; Bespreking</h2>", html)
        self.assertIn("<h2>Beleid</h2>", html)
        self.assertIn("<p>Multipele blaastumoren van 2-3 cm</p>", html)
        self.assertNotIn("<p></p>", html)
