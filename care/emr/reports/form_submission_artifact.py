from __future__ import annotations

import json
import math
import re
from datetime import datetime
from html import escape
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from care.emr.reports.clinical_narrative import (
    normalize_diagnosis_history_layout,
)
from care.emr.reports.renderer.generators.weasyprint_generator import (
    WeasyPrintGenerator,
    WeasyPrintGeneratorOptions,
)

MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_SNAPSHOT_DEPTH = 20
MAX_SNAPSHOT_NODES = 10_000

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
_NARRATIVE_KEYS = ("noteText", "narrativePreview", "narrative", "note")


class MalformedFinalizedSnapshotError(ValueError):
    pass


def validate_response_dump(response_dump: Any) -> None:
    if not isinstance(response_dump, dict) or not response_dump:
        raise MalformedFinalizedSnapshotError(
            "Finalized response_dump must be a non-empty JSON object"
        )
    node_count = 0

    def visit(value: Any, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > MAX_SNAPSHOT_NODES or depth > MAX_SNAPSHOT_DEPTH:
            raise MalformedFinalizedSnapshotError(
                "Finalized response_dump exceeds rendering limits"
            )
        if isinstance(value, dict):
            if not all(isinstance(key, str) and key.strip() for key in value):
                raise MalformedFinalizedSnapshotError(
                    "Finalized response_dump contains an invalid field name"
                )
            for key, child in value.items():
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                visit(child, depth + 1)
        elif isinstance(value, float) and not math.isfinite(value):
            raise MalformedFinalizedSnapshotError(
                "Finalized response_dump contains a non-finite number"
            )
        elif not isinstance(value, (str, int, float, bool, type(None))):
            raise MalformedFinalizedSnapshotError(
                "Finalized response_dump contains an unsupported value"
            )

    visit(response_dump, 0)
    try:
        encoded = json.dumps(
            response_dump,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as exc:
        raise MalformedFinalizedSnapshotError(
            "Finalized response_dump is not valid JSON"
        ) from exc
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise MalformedFinalizedSnapshotError("Finalized response_dump is too large")


def build_form_submission_artifact_html(
    *,
    artifact_id,
    submission,
    generated_at: datetime,
) -> str:
    """Build the printable clinical rendition of an immutable form snapshot.

    Artifact IDs, CARE UUIDs, hashes, and renderer timestamps remain stored in
    the database audit trail. They deliberately do not appear on the clinician
    and patient-facing printout.
    """

    validate_response_dump(submission.response_dump)
    patient = submission.patient
    encounter = submission.encounter
    questionnaire = submission.questionnaire
    document_title = (
        "Operatieverslag"
        if questionnaire.slug == "urology-operaties"
        else "Medisch dossier"
    )
    author = submission.created_by
    finalizer = submission.workflow_finalized_by
    if (
        not encounter
        or not author
        or not finalizer
        or not submission.workflow_finalized_at
    ):
        raise MalformedFinalizedSnapshotError(
            "Finalized source is missing required encounter or audit provenance"
        )

    # These values are intentionally accepted for the immutable artifact API,
    # but are audit metadata rather than visible clinical content.
    _ = artifact_id, generated_at
    patient_rows = [
        ("Patient", patient.name),
        ("Geboortedatum", _date_of_birth(patient)),
        ("Consultdatum", _encounter_date(encounter.period)),
        ("Behandelaar", _user_name(finalizer)),
    ]
    patient_details = "".join(
        '<div class="patient-field">'
        f'<span class="patient-label">{escape(label)}</span>'
        f'<span class="patient-value">{escape(str(value))}</span>'
        "</div>"
        for label, value in patient_rows
    )
    clinical_content = _render_clinical_content(submission.response_dump)

    return f"""<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <title>{escape(document_title)}</title>
  <style>
    @page {{
      size: A4;
      margin: 18mm 16mm 18mm;
      @bottom-left {{
        content: "{escape(document_title)}";
        color: #64748b;
        font-family: sans-serif;
        font-size: 8pt;
      }}
      @bottom-right {{
        content: "Pagina " counter(page) " van " counter(pages);
        color: #64748b;
        font-family: sans-serif;
        font-size: 8pt;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: #0f172a;
      font-family: Arial, Helvetica, sans-serif;
      font-size: 10.5pt;
      line-height: 1.55;
    }}
    .header {{
      border-bottom: 3px solid #0891b2;
      margin-bottom: 18px;
      padding-bottom: 12px;
    }}
    .eyebrow {{
      color: #0e7490;
      font-size: 8.5pt;
      font-weight: 700;
      letter-spacing: .08em;
      margin: 0 0 3px;
      text-transform: uppercase;
    }}
    h1 {{ font-size: 23pt; line-height: 1.15; margin: 0; }}
    .form-title {{ color: #475569; font-size: 11pt; margin: 4px 0 0; }}
    .patient-card {{
      background: #f0f9ff;
      border: 1px solid #bae6fd;
      display: grid;
      gap: 10px 20px;
      grid-template-columns: 1fr 1fr;
      margin-bottom: 22px;
      padding: 14px 16px;
    }}
    .patient-field {{ display: flex; flex-direction: column; }}
    .patient-label {{ color: #64748b; font-size: 8pt; font-weight: 700; text-transform: uppercase; }}
    .patient-value {{ color: #0f172a; font-size: 10.5pt; font-weight: 600; }}
    h2 {{
      border-bottom: 1px solid #cbd5e1;
      font-size: 14pt;
      margin: 0 0 12px;
      padding-bottom: 6px;
    }}
    .narrative {{ margin: 0; white-space: pre-wrap; word-break: normal; }}
    .clinical-table {{ border-collapse: collapse; margin-top: 16px; width: 100%; }}
    .clinical-table th, .clinical-table td {{
      border-bottom: 1px solid #e2e8f0;
      padding: 7px 8px;
      text-align: left;
      vertical-align: top;
    }}
    .clinical-table th {{ color: #475569; font-weight: 600; width: 38%; }}
    ul {{ margin: 3px 0; padding-left: 18px; }}
    .empty {{ color: #64748b; font-style: italic; }}
    .status {{
      color: #047857;
      font-size: 8.5pt;
      font-weight: 700;
      margin-top: 24px;
      text-transform: uppercase;
    }}
  </style>
</head>
<body>
  <header class="header">
    <p class="eyebrow">Urologie</p>
    <h1>{escape(document_title)}</h1>
    <p class="form-title">{escape(str(questionnaire.title))}</p>
  </header>
  <section class="patient-card" aria-label="Patientgegevens">{patient_details}</section>
  <main>
    <h2>Verslag</h2>
    {clinical_content}
  </main>
  <p class="status">Definitief vastgelegd</p>
</body>
</html>"""


def render_form_submission_artifact_pdf(html: str) -> bytes:
    return WeasyPrintGenerator().generate(
        html,
        WeasyPrintGeneratorOptions(page_size="A4", margin="0"),
    )


def _render_clinical_content(response_dump: dict[str, Any]) -> str:
    content = response_dump.get("content")
    if isinstance(content, dict):
        narrative = _first_text(content, _NARRATIVE_KEYS)
        if narrative:
            narrative = normalize_diagnosis_history_layout(narrative)
            return f'<p class="narrative">{escape(narrative)}</p>'

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
        narrative = normalize_diagnosis_history_layout(narrative)
        parts.append(f'<p class="narrative">{escape(narrative)}</p>')
    if fields:
        parts.append(_render_clinical_fields(fields))
    return "".join(parts) or '<p class="empty">Geen gegevens vastgelegd.</p>'


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


def _date_of_birth(patient) -> str:
    if patient.date_of_birth:
        return patient.date_of_birth.strftime("%d-%m-%Y")
    if patient.year_of_birth:
        return str(patient.year_of_birth)
    return "Niet geregistreerd"


def _encounter_date(period) -> str:
    if not isinstance(period, dict):
        return "Niet geregistreerd"
    raw_value = period.get("start") or period.get("end")
    if not raw_value:
        return "Niet geregistreerd"
    parsed = parse_datetime(str(raw_value))
    if not parsed:
        return escape(str(raw_value))
    if timezone.is_aware(parsed):
        parsed = timezone.localtime(parsed)
    return parsed.strftime("%d-%m-%Y %H:%M")


def _user_name(user) -> str:
    name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    return name or user.username
