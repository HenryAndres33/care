"""Checks the note's drawings before "Definitief maken" and a correction.

A drawing that cannot be printed exactly as drawn blocks finalizing with a
clear error instead of reaching the paper file wrong (owner, 29 Sep 2026).
The rules equal the frontend decoder (noteDrawingCodec.ts).
"""

import re
from typing import Any

from care_suriname.reports import note_drawings_catalog as catalog

_POINTS = re.compile(r"\d{1,4},\d{1,4}( \d{1,4},\d{1,4})+")
_STAMP_KEYS = {"kind", "mirrored", "note", "rotation", "scale", "size", "x", "y"}
_DRAWING_KEYS = {"number", "stamps", "strokes", "template"}
_OPTIONAL_DRAWING_KEYS = {"section", "text"}


class InvalidNoteDrawingsError(ValueError):
    pass


def note_drawings(response_dump: Any) -> Any:
    """The raw drawings list of a Medisch Dossier dump, or None."""
    content = response_dump.get("content") if isinstance(response_dump, dict) else None
    actions = content.get("clinicalActions") if isinstance(content, dict) else None
    return actions.get("drawings") if isinstance(actions, dict) else None


def validate_note_drawings(response_dump: Any) -> None:
    drawings = note_drawings(response_dump)
    if drawings is None:
        return
    if not isinstance(drawings, list) or len(drawings) > catalog.MAX_DRAWINGS:
        raise InvalidNoteDrawingsError("Tekeningen: ongeldige lijst")
    numbers = []
    for drawing in drawings:
        _check_drawing(drawing)
        numbers.append(drawing["number"])
    if len(set(numbers)) != len(numbers):
        raise InvalidNoteDrawingsError("Tekeningen: dubbel tekeningnummer")


def _check_drawing(drawing: Any) -> None:
    if not isinstance(drawing, dict) or not (
        _DRAWING_KEYS <= set(drawing) <= _DRAWING_KEYS | _OPTIONAL_DRAWING_KEYS
    ):
        raise InvalidNoteDrawingsError("Tekening: onbekende opbouw")
    number = drawing["number"]
    label = f"Tekening {number}"
    if not _is_int(number) or not 1 <= number <= catalog.MAX_NUMBER:
        raise InvalidNoteDrawingsError("Tekening: ongeldig nummer")
    if drawing["template"] not in catalog.TEMPLATES:
        msg = f"{label}: onbekend schema"
        raise InvalidNoteDrawingsError(msg)
    if not _short_text(drawing.get("section", ""), catalog.MAX_SECTION_CHARS):
        msg = f"{label}: ongeldige plaats in de notitie"
        raise InvalidNoteDrawingsError(msg)
    if not _short_text(drawing.get("text", ""), catalog.MAX_TEXT_CHARS):
        msg = f"{label}: toelichting te lang"
        raise InvalidNoteDrawingsError(msg)
    stamps, strokes = drawing["stamps"], drawing["strokes"]
    if not isinstance(stamps, list) or len(stamps) > catalog.MAX_STAMPS:
        msg = f"{label}: te veel stempels"
        raise InvalidNoteDrawingsError(msg)
    if not isinstance(strokes, list) or len(strokes) > catalog.MAX_STROKES:
        msg = f"{label}: te veel lijnen"
        raise InvalidNoteDrawingsError(msg)
    for stamp in stamps:
        _check_stamp(stamp, label)
    for stroke in strokes:
        _check_stroke(stroke, label)


def _check_stamp(stamp: Any, label: str) -> None:
    valid = (
        isinstance(stamp, dict)
        and set(stamp) == _STAMP_KEYS
        and stamp["kind"] in catalog.STAMPS
        and isinstance(stamp["mirrored"], bool)
        and _short_text(stamp["size"], catalog.MAX_SIZE_CHARS)
        and _short_text(stamp["note"], catalog.MAX_NOTE_CHARS)
        and _is_int(stamp["rotation"])
        and 0 <= stamp["rotation"] < 360  # noqa: PLR2004
        and stamp["rotation"] % 15 == 0
        and _is_number(stamp["scale"])
        and catalog.SCALE_MIN <= stamp["scale"] <= catalog.SCALE_MAX
        and _in_canvas(stamp["x"], stamp["y"])
    )
    if not valid:
        msg = f"{label}: ongeldige stempel"
        raise InvalidNoteDrawingsError(msg)


def _check_stroke(stroke: Any, label: str) -> None:
    points = stroke.get("points") if isinstance(stroke, dict) else None
    valid = (
        isinstance(stroke, dict)
        and set(stroke) == {"color", "points"}
        and stroke["color"] in catalog.STROKE_COLORS
        and isinstance(points, str)
        and len(points) <= catalog.MAX_POINTS_CHARS
        and _POINTS.fullmatch(points) is not None
        and all(_in_canvas(*map(int, p.split(","))) for p in points.split(" "))
    )
    if not valid:
        msg = f"{label}: ongeldige lijn"
        raise InvalidNoteDrawingsError(msg)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _short_text(value: Any, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit


def _in_canvas(x: Any, y: Any) -> bool:
    return (
        _is_int(x)
        and _is_int(y)
        and 0 <= x <= catalog.CANVAS_WIDTH
        and 0 <= y <= catalog.CANVAS_HEIGHT
    )
