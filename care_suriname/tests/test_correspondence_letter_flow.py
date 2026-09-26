"""Flow layout checks use synthetic snapshots and the real PDF generator."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase
from weasyprint import HTML

from care_suriname.reports.correspondence_letter import (
    build_controlled_correction_copy_html,
    build_correspondence_letter_html,
    render_correspondence_letter_pdf,
)


def build_flow_revision(body=None, facility_name="Academisch Ziekenhuis Paramaribo"):
    compilation = SimpleNamespace(
        facility=SimpleNamespace(name=facility_name),
        encounter_reason=SimpleNamespace(display="Software simulatie"),
        department=SimpleNamespace(name="Urologie"),
        template=SimpleNamespace(options={"letterhead_title": "Afdeling Urologie"}),
        source_provenance={
            "template": {"name": "Ontslagbrief Urologie"},
            "form": {"presentation_reason": "Software simulatie"},
            "encounter": {"date": "2026-09-26"},
        },
    )
    review = SimpleNamespace(
        compilation=compilation,
        author_snapshot={"display": "Dr. DEMO-SIM Arts", "professional_role": "Doctor"},
        recipient_snapshot={
            "display_name": "Dr. DEMO-SIM Huisarts",
            "postal_address": {"lines": ["SIMULATIEADRES 1", "Paramaribo"]},
        },
    )
    patient = SimpleNamespace(
        name="DEMO-SIM-2026-09-26-FLOW",
        date_of_birth=date(1970, 1, 1),
        year_of_birth=1970,
        instance_identifiers=[],
        facility_identifiers={},
    )
    return SimpleNamespace(
        letter=SimpleNamespace(
            review=review,
            patient=patient,
            encounter=SimpleNamespace(facility_id=uuid4()),
        ),
        body=body
        or (
            "Geachte collega,\n\n"
            "Software simulatie, geen werkelijke zorg.\n\n"
            "Conclusie:\nSynthetische ontslagsamenvatting.\n\n"
            "Beleid:\nSynthetische overdracht voor controle van de briefopmaak."
        ),
        external_id=uuid4(),
        resource_version=2,
    )


class FlowLetterTests(SimpleTestCase):
    def html(self, revision):
        return build_correspondence_letter_html(
            artifact_id=uuid4(),
            revision=revision,
            generated_at=datetime(2026, 9, 26, tzinfo=UTC),
        )

    def render(self, html):
        documents = []

        def write_pdf(instance, **kwargs):
            document = instance.render(**kwargs)
            documents.append(document)
            return document.write_pdf()

        with patch.object(HTML, "write_pdf", write_pdf):
            pdf = render_correspondence_letter_pdf(html)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        return documents[0]

    def page_text(self, page):
        return " ".join(
            box.text
            for box in page._page_box.descendants()  # noqa: SLF001
            if hasattr(box, "text")
        )

    def test_flow_uses_frozen_title_and_preserves_content_and_identity(self):
        revision = build_flow_revision()
        original_body = revision.body
        html = self.html(revision)
        self.assertIn('class="letterhead flow-letterhead"', html)
        self.assertIn('<h1 class="document-title">Ontslagbrief Urologie</h1>', html)
        self.assertEqual(html.count("Academisch Ziekenhuis Paramaribo"), 1)
        for value in (
            "Dr. DEMO-SIM Huisarts",
            "SIMULATIEADRES 1",
            "Synthetische ontslagsamenvatting.",
            "Dr. DEMO-SIM Arts",
            "Niet vastgelegd",
            "counter(pages)",
        ):
            self.assertIn(value, html)
        self.assertEqual(revision.body, original_body)

    def test_title_is_escaped_and_older_snapshots_have_an_honest_fallback(self):
        revision = build_flow_revision()
        template = revision.letter.review.compilation.source_provenance["template"]
        template["name"] = '<script>alert("title")</script>'
        html = self.html(revision)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        template.clear()
        self.assertIn('class="document-title">Medische brief</h1>', self.html(revision))

    def test_other_facilities_do_not_receive_azp_brand_assets(self):
        html = self.html(build_flow_revision(facility_name="DEMO-SIM Andere kliniek"))
        self.assertNotIn('class="letterhead flow-letterhead"', html)
        self.assertNotIn('src="data:image/png', html)
        self.assertNotIn("Flustraat 1", html)
        self.assertIn("DEMO-SIM Andere kliniek", html)

    def test_short_letter_renders_on_one_page_with_all_clinical_sections(self):
        document = self.render(self.html(build_flow_revision()))
        self.assertEqual(len(document.pages), 1)
        text = self.page_text(document.pages[0])
        for expected in (
            "Ontslagbrief Urologie",
            "Synthetische ontslagsamenvatting.",
            "Synthetische overdracht",
            "Dr. DEMO-SIM Arts",
            "Pagina 1 van 1",
        ):
            self.assertIn(expected, text)

    def test_long_section_flows_and_patient_identity_repeats_on_every_page(self):
        paragraphs = [
            f"SIMREGEL-{index:03d} Synthetische tekst voor paginering. " * 3
            for index in range(90)
        ]
        body = "Anamnese:\n" + "\n\n".join(paragraphs) + "\n\nBeleid:\nSIM-EINDE"
        document = self.render(self.html(build_flow_revision(body)))
        self.assertGreater(len(document.pages), 1)
        text = " ".join(self.page_text(page) for page in document.pages)
        for index in range(90):
            self.assertIn(f"SIMREGEL-{index:03d}", text)
        self.assertIn("SIM-EINDE", text)
        for page in document.pages:
            self.assertIn("DEMO-SIM-2026-09-26-FLOW", self.page_text(page))
            for box in page._page_box.descendants():  # noqa: SLF001
                if hasattr(box, "text"):
                    self.assertGreaterEqual(box.position_x, 0)
                    self.assertLessEqual(box.position_x + box.width, page.width + 1)
                    self.assertGreaterEqual(box.position_y, 0)
                    self.assertLessEqual(box.position_y + box.height, page.height + 1)
        self.assertNotIn("Ontslagbrief Urologie", self.page_text(document.pages[1]))

    def test_correction_copy_keeps_the_prominent_replacement_banner(self):
        html = build_controlled_correction_copy_html(
            artifact_id=uuid4(),
            revision=build_flow_revision(),
            generated_at=datetime(2026, 9, 26, tzinfo=UTC),
            correction_case_id="SIM-CORRECTIE",
            replacement_attempt_number=1,
            source_version=1,
            superseded_delivery_id="SIM-OUDE-VERZENDING",
        )
        document = self.render(html)
        self.assertIn("GECONTROLEERDE CORRECTIE", self.page_text(document.pages[0]))
        self.assertIn("SIM-OUDE-VERZENDING", self.page_text(document.pages[0]))
