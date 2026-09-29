"""The note's drawings on its PDF (owner, 29 September 2026).

Drawn only from the stored note (``content.clinicalActions.drawings``): the
schematic, the numbered stamps and the freehand lines as one inline SVG, with
a footnote legend under it ("1 Tumor 5 cm, rechter zijwand"), no table. The
figure takes the place of its own note line ("- Afbeelding 1: …", under the
schematic's heading such as "Cystoscopie:"), so the page does not repeat it;
a drawing whose line was removed from the text is printed after the text.
Every image is an inline data URI from the plug's own assets, so WeasyPrint
fetches nothing. Mirrors the frontend DrawingFigure.tsx / NoteDrawingFigures.
"""

import re
from collections.abc import Callable
from html import escape
from typing import Any

from care_suriname.reports import note_drawings_catalog as catalog
from care_suriname.reports import note_drawings_stent as stent
from care_suriname.reports.note_drawings_validation import (
    InvalidNoteDrawingsError,
    note_drawings,
    validate_note_drawings,
)

_BADGE_RADIUS = 24
# "- Afbeelding 1: …"; the first test version wrote "- Tekening 1 (Blaas): …".
_DRAWING_LINE = re.compile(r"^- (?:Afbeelding|Tekening) (\d+)(?:[: (]|$)")
_UNAVAILABLE = (
    '<p class="empty">De afbeeldingen van deze notitie konden niet worden '
    "weergegeven. Raadpleeg de notitie in CARE.</p>"
)


def render_narrative_with_drawings(
    narrative: str,
    response_dump: dict[str, Any],
    render_text: Callable[[str], str],
) -> str:
    drawings = note_drawings(response_dump)
    if not drawings:
        return render_text(narrative)
    try:
        validate_note_drawings(response_dump)
    except InvalidNoteDrawingsError:
        # Finalizing already refuses this; never print a drawing half-right.
        return render_text(narrative) + _UNAVAILABLE
    by_number = {drawing["number"]: drawing for drawing in drawings}
    parts: list[str] = []
    text: list[str] = []

    def flush() -> None:
        chunk = "\n".join(text).strip("\n")
        if chunk.strip():
            parts.append(render_text(chunk))
        text.clear()

    for line in narrative.split("\n"):
        match = _DRAWING_LINE.match(line.strip())
        drawing = by_number.pop(int(match.group(1)), None) if match else None
        if drawing is None:
            text.append(line)
            continue
        flush()
        parts.append(_render_figure(drawing))
    flush()
    parts.extend(_render_figure(drawing) for drawing in by_number.values())
    return "".join(parts)


def _render_figure(drawing: dict[str, Any]) -> str:
    template_file, template_label = catalog.TEMPLATES[drawing["template"]]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="note-drawing-figure" '
        f'viewBox="0 0 {catalog.CANVAS_WIDTH} {catalog.CANVAS_HEIGHT}">'
        f'<image href="{catalog.asset_data_uri(template_file)}" x="0" y="0" '
        f'width="{catalog.CANVAS_WIDTH}" height="{catalog.CANVAS_HEIGHT}"/>'
        + "".join(_render_stroke(stroke) for stroke in drawing["strokes"])
        + "".join(
            _render_stamp(stamp, index + 1, drawing["template"])
            for index, stamp in _stents_first(drawing)
        )
        + "</svg>"
    )
    caption = f"Afbeelding {drawing['number']} \N{EN DASH} {template_label}"
    footnotes = "".join(
        f"<p>{escape(_describe_stamp(stamp, index + 1))}</p>"
        for index, stamp in enumerate(drawing["stamps"])
    )
    return (
        f'<figure class="note-drawing">{svg}<figcaption>'
        f'<p class="note-drawing-caption">{escape(caption)}</p>{footnotes}'
        "</figcaption></figure>"
    )


def _describe_stamp(stamp: dict[str, Any], number: int) -> str:
    """Same wording as the note line (frontend describeNoteDrawingStamp)."""
    base = " ".join(
        part
        for part in (
            f"{number} {catalog.STAMPS[stamp['kind']][1]}",
            stamp["size"].strip(),
        )
        if part
    )
    note = stamp["note"].strip()
    return f"{base}, {note}" if note else base


def _render_stroke(stroke: dict[str, Any]) -> str:
    # Points and colour were validated: digits, commas and spaces only.
    return (
        f'<polyline points="{stroke["points"]}" fill="none" '
        f'stroke="{catalog.STROKE_COLORS[stroke["color"]]}" stroke-width="6" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    )


def _urs_stent_side(stamp: dict[str, Any], template: str) -> str | None:
    """On URS the JJ stent is a line along its side's ureter (29 Sep 2026)."""
    if template != stent.URS_TEMPLATE or stamp["kind"] != "stent":
        return None
    return "rechts" if stamp["x"] < stent.URS_MIDLINE_X else "links"


def _stents_first(drawing: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """URS stents under the other stamps, so stones lie on top."""
    return sorted(
        enumerate(drawing["stamps"]),
        key=lambda item: _urs_stent_side(item[1], drawing["template"]) is None,
    )


def _badge(x: float, y: float, number: int) -> str:
    return (
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{_BADGE_RADIUS}" '
        'fill="#FFFFFF" stroke="#1A1A1A" stroke-width="3"/>'
        f'<text x="{x:.1f}" y="{y + 10:.1f}" font-size="28" '
        'font-weight="700" text-anchor="middle" fill="#1A1A1A" '
        f'font-family="Arial, sans-serif">{number}</text>'
    )


def _render_stamp(stamp: dict[str, Any], number: int, template: str) -> str:
    side = _urs_stent_side(stamp, template)
    if side:
        points = stent.URS_STENT_PATHS[side]
        badge_x = stamp["x"] - 44 if side == "rechts" else stamp["x"] + 44
        return (
            f'<polyline points="{points}" fill="none" '
            f'stroke="{stent.URS_STENT_OUTLINE}" stroke-width="12" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
            f'<polyline points="{points}" fill="none" '
            f'stroke="{stent.URS_STENT_FILL}" stroke-width="7" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
            + "".join(
                f'<polyline points="{band}" fill="none" '
                f'stroke="{stent.URS_STENT_BAND}" stroke-width="12"/>'
                for band in stent.URS_STENT_BANDS[side]
            )
            + _badge(badge_x, stamp["y"], number)
        )
    size = catalog.STAMP_SIZE * stamp["scale"]
    badge = size * 0.42
    mirror = -1 if stamp["mirrored"] else 1
    image = catalog.asset_data_uri(catalog.STAMPS[stamp["kind"]][0])
    return (
        f'<g transform="translate({stamp["x"]} {stamp["y"]})">'
        f'<g transform="rotate({stamp["rotation"]}) scale({mirror} 1)">'
        f'<image href="{image}" x="{-size / 2:.1f}" y="{-size / 2:.1f}" '
        f'width="{size:.1f}" height="{size:.1f}"/></g>'
        + _badge(badge, -badge, number)
        + "</g>"
    )
