"""Normalize legacy template content before it becomes a frozen letter body."""

from __future__ import annotations

from html import escape

_AZP_LEGACY_PREFIX_MARKERS = (
    "academisch ziekenhuis paramaribo",
    "flustraat 1",
    "correspondentiebrief urologie",
    "patiëntnummer",
)
_SALUTATION_MARKERS = ("geachte collega", "beste collega")
_CLOSING_MARKERS = (
    "met collegiale groet",
    "met vriendelijke groet",
    "hoogachtend",
)
_AZP_LEGACY_FOOTER_MARKERS = (
    "correspondentiebrief · afdeling urologie",
    "vertrouwelijke medische informatie",
)


def normalize_correspondence_template_body(value: str) -> tuple[str, bool]:
    """Remove the server-owned chrome from the known legacy AZP template.

    The match is intentionally strict. Ordinary clinical prose is left untouched
    unless every identifying marker from the old full-document template appears
    before a salutation. The clinical text and chosen closing remain unchanged.
    """

    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    salutation_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.casefold().startswith(_SALUTATION_MARKERS)
        ),
        None,
    )
    if salutation_index is None:
        return value, False

    prefix = "\n".join(lines[:salutation_index]).casefold()
    if not all(marker in prefix for marker in _AZP_LEGACY_PREFIX_MARKERS):
        return value, False

    retained = lines[salutation_index:]
    closing_index = next(
        (
            index
            for index, line in enumerate(retained)
            if line.casefold().startswith(_CLOSING_MARKERS)
        ),
        None,
    )
    if closing_index is not None:
        suffix = "\n".join(retained[closing_index + 1 :]).casefold()
        if all(marker in suffix for marker in _AZP_LEGACY_FOOTER_MARKERS):
            retained = retained[: closing_index + 1]

    return "\n".join(retained), True


def correspondence_text_as_html(value: str) -> str:
    """Create safe, simple HTML for normalized legacy plain text."""

    return "".join(
        f"<p>{escape(line)}</p>" for line in value.splitlines() if line.strip()
    )
