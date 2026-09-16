import re
from html import escape
from typing import Any

from care.emr.reports.clinical_narrative import render_clinical_narrative_html

_NARRATIVE_KEYS = ("noteText", "narrativePreview", "narrative", "note")
_TECHNICAL_KEYS = {
    "artifact",
    "artifact_id",
    "clinicalactions",
    "customformdefinition",
    "external_id",
    "finalized_snapshot_hash",
    "identity",
    "resource_version",
    "source_snapshot_hash",
    "source_version",
    "version",
}


def render_clinical_content(response_dump: dict[str, Any]) -> str:
    content = response_dump.get("content")
    if isinstance(content, dict):
        narrative = _first_text(content, _NARRATIVE_KEYS)
        if narrative:
            return _narrative_paragraph(narrative)
        values = content.get("values")
        if isinstance(values, dict):
            return _render_clinical_fields(values)

    narrative = _first_text(response_dump, _NARRATIVE_KEYS)
    excluded = set(_NARRATIVE_KEYS)
    fields = {
        key: value
        for key, value in response_dump.items()
        if key not in excluded and not _is_technical_key(key)
    }
    parts = []
    if narrative:
        parts.append(_narrative_paragraph(narrative))
    if fields:
        parts.append(_render_clinical_fields(fields))
    return "".join(parts) or '<p class="empty">Geen gegevens vastgelegd.</p>'


def _narrative_paragraph(narrative: str) -> str:
    return f'<p class="narrative">{render_clinical_narrative_html(narrative)}</p>'


def _render_clinical_fields(values: dict[str, Any]) -> str:
    rows = []
    for key in sorted(values):
        if _is_technical_key(key):
            continue
        value = values[key]
        if value in (None, "", [], {}):
            continue
        rows.append(
            "<tr>"
            f"<th>{escape(_humanize_key(key))}</th>"
            f"<td>{_render_clinical_value(value)}</td>"
            "</tr>"
        )
    if not rows:
        return '<p class="empty">Geen gegevens vastgelegd.</p>'
    return f'<table class="clinical-table"><tbody>{"".join(rows)}</tbody></table>'


def _render_clinical_value(value: Any) -> str:
    if isinstance(value, bool):
        return "Ja" if value else "Nee"
    if isinstance(value, list):
        items = [item for item in value if item not in (None, "", [], {})]
        return (
            "<ul>"
            + "".join(f"<li>{_render_clinical_value(item)}</li>" for item in items)
            + "</ul>"
        )
    if isinstance(value, dict):
        visible = {
            key: child
            for key, child in value.items()
            if not _is_technical_key(key) and child not in (None, "", [], {})
        }
        if not visible:
            return "-"
        return _render_clinical_fields(visible)
    return escape(str(value))


def _first_text(values: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_technical_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalized in _TECHNICAL_KEYS or normalized.endswith("sha256")


def _humanize_key(key: str) -> str:
    label = key.rsplit(".", maxsplit=1)[-1].replace("_", " ").replace("-", " ")
    label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label)
    return label[:1].upper() + label[1:]
