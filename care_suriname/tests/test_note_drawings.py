"""Contract: drawings made in a note print on its PDF, exactly as stored.

Owner request 29 September 2026 (frontend forms/note-drawings): stamps and
freehand lines on the bladder / urinary-tract schematics, with a numbered
legend, must reach the paper file; a malformed drawing blocks finalizing.
"""

import hashlib

from django.test import SimpleTestCase
from weasyprint import HTML

from care_suriname.reports import note_drawings_catalog as catalog
from care_suriname.reports import note_drawings_stent as stent
from care_suriname.reports.form_submission_clinical_content import (
    render_clinical_content,
)
from care_suriname.reports.note_drawings_validation import (
    InvalidNoteDrawingsError,
    validate_note_drawings,
)


def _stamp(**change):
    return {
        "kind": "tumor",
        "mirrored": False,
        "note": "zijwand rechts",
        "rotation": 0,
        "scale": 1.5,
        "size": "3 x 2 cm",
        "x": 300,
        "y": 400,
    } | change


def _drawing(**change):
    return {
        "number": 1,
        "template": "urology-blaas-v1",
        "stamps": [_stamp(), _stamp(kind="biopsie", note="<b>trigonum</b>", size="")],
        "strokes": [{"color": "rood", "points": "10,10 40,60 90,80"}],
    } | change


def _dump(drawings):
    return {
        "content": {
            "noteText": (
                "Cystoscopie:\n- Afbeelding 1: 1 Tumor 3 x 2 cm, zijwand rechts"
                "\n\nVerder geen klachten"
            ),
            "clinicalActions": {
                "schema": "care.urology.form-clinical-actions",
                "version": 1,
                "actions": [],
                "drawings": drawings,
            },
        }
    }


class NoteDrawingsPdfTests(SimpleTestCase):
    def test_figure_takes_its_lines_place_with_a_footnote_legend(self):
        html = render_clinical_content(_dump([_drawing()]))
        self.assertIn("Cystoscopie:", html)
        self.assertNotIn("- Afbeelding 1:", html)  # the figure replaces it
        self.assertNotIn("Tekeningen", html)
        self.assertIn("Afbeelding 1 \N{EN DASH} Cystoscopie", html)
        self.assertIn("<p>1 Tumor 3 x 2 cm, zijwand rechts</p>", html)
        self.assertNotIn("<table", html)
        self.assertLess(html.index("Cystoscopie:"), html.index("<svg"))
        self.assertLess(html.index("<svg"), html.index("Verder geen klachten"))
        self.assertIn('<polyline points="10,10 40,60 90,80"', html)
        self.assertIn("#C62828", html)
        self.assertEqual(html.count("data:image/png;base64,"), 2)
        self.assertEqual(html.count("data:image/jpeg;base64,"), 1)
        # Typed text is escaped, never markup.
        self.assertIn("&lt;b&gt;trigonum&lt;/b&gt;", html)
        self.assertNotIn("<b>trigonum", html)
        self.assertNotIn('href="http', html)

    def test_drawing_without_its_line_still_prints_after_the_text(self):
        dump = _dump([_drawing(), _drawing(number=2, template="urology-urinewegen-v1")])
        html = render_clinical_content(dump)
        self.assertEqual(html.count("<svg"), 2)
        self.assertIn("Afbeelding 2 \N{EN DASH} URS", html)
        self.assertGreater(
            html.index("Afbeelding 2"), html.index("Verder geen klachten")
        )

    def test_first_version_line_is_replaced_too(self):
        dump = _dump([_drawing()])
        dump["content"]["noteText"] = "Tekeningen:\n- Tekening 1 (Blaas): 1 Tumor"
        html = render_clinical_content(dump)
        self.assertNotIn("- Tekening 1 (Blaas)", html)
        self.assertEqual(html.count("<svg"), 1)

    def test_urs_stent_is_a_line_along_its_ureter_under_the_stones(self):
        drawing = _drawing(
            template="urology-urinewegen-v1",
            stamps=[
                _stamp(kind="steen", x=390, y=700, scale=0.5),
                _stamp(kind="stent", x=360, y=660, scale=0.5, note="rechts", size=""),
            ],
            strokes=[],
        )
        html = render_clinical_content(_dump([drawing]))
        self.assertIn(stent.URS_STENT_PATHS["rechts"], html)
        self.assertNotIn(stent.URS_STENT_PATHS["links"], html)
        self.assertEqual(html.count("data:image/png;base64,"), 1)  # the stone
        self.assertLess(
            html.index(stent.URS_STENT_OUTLINE), html.index("data:image/png")
        )
        self.assertIn("<p>2 Stent, rechts</p>", html)

    def test_urs_stent_line_matches_the_frontend(self):
        joined = "|".join(
            [
                stent.URS_STENT_PATHS["rechts"],
                stent.URS_STENT_PATHS["links"],
                *stent.URS_STENT_BANDS["rechts"],
                *stent.URS_STENT_BANDS["links"],
            ]
        )
        self.assertEqual(
            hashlib.sha256(joined.encode()).hexdigest(),
            "ca4f8f9de14e41d3771c1adfd95af88189dd7c7f1bf3e3ae6fbb72afdd8a152f",
        )

    def test_rendered_page_is_a_pdf(self):
        body = render_clinical_content(_dump([_drawing()]))
        pdf = HTML(string=f"<html><body>{body}</body></html>").write_pdf()
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_note_without_drawings_is_unchanged(self):
        dump = _dump([])
        del dump["content"]["clinicalActions"]["drawings"]
        self.assertNotIn("<svg", render_clinical_content(dump))
        self.assertIn("- Afbeelding 1", render_clinical_content(dump))

    def test_malformed_drawing_is_shown_as_unavailable_not_half_printed(self):
        html = render_clinical_content(_dump([_drawing(template="elders")]))
        self.assertIn("konden niet worden weergegeven", html)
        self.assertNotIn("<svg", html)


class NoteDrawingsValidationTests(SimpleTestCase):
    def test_valid_and_absent_drawings_pass(self):
        validate_note_drawings(_dump([_drawing(), _drawing(number=2)]))
        validate_note_drawings({"content": {"noteText": "x"}})
        validate_note_drawings({})

    def test_malformed_drawings_are_refused(self):
        bad = [
            [_drawing(template="elders")],
            [_drawing(extra=1)],
            [_drawing(number=0)],
            [_drawing(), _drawing()],
            [_drawing(stamps=[_stamp(kind="onbekend")])],
            [_drawing(stamps=[_stamp(x=5000)])],
            [_drawing(stamps=[_stamp(rotation=10)])],
            [_drawing(stamps=[_stamp(scale=9)])],
            [_drawing(stamps=[_stamp(size="x" * 41)])],
            [_drawing(stamps=[_stamp(mirrored="ja")])],
            [_drawing(strokes=[{"color": "rood", "points": '1,1" onload="x'}])],
            [_drawing(strokes=[{"color": "paars", "points": "1,1 2,2"}])],
            [_drawing(stamps=[_stamp()] * (catalog.MAX_STAMPS + 1))],
            [_drawing(number=n) for n in range(1, catalog.MAX_DRAWINGS + 2)],
            "geen lijst",
        ]
        for drawings in bad:
            with (
                self.subTest(drawings=str(drawings)[:80]),
                self.assertRaises(InvalidNoteDrawingsError),
            ):
                validate_note_drawings(_dump(drawings))


class NoteDrawingsAssetTests(SimpleTestCase):
    def test_every_catalog_image_exists_and_matches_the_manifest(self):
        """The frontend holds the same SHA256SUMS; a changed image needs a new id."""
        manifest = dict(
            reversed(line.split("  "))
            for line in (catalog.ASSET_DIR / "SHA256SUMS").read_text().splitlines()
        )
        files = [f for f, _ in catalog.TEMPLATES.values()] + [
            f for f, _ in catalog.STAMPS.values()
        ]
        self.assertEqual(sorted(files), sorted(manifest))
        for name in files:
            digest = hashlib.sha256((catalog.ASSET_DIR / name).read_bytes()).hexdigest()
            self.assertEqual(digest, manifest[name], name)
