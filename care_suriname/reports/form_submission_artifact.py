from __future__ import annotations

import json
import math
from datetime import datetime
from html import escape
from typing import Any

from care.emr.reports.renderer.generators.weasyprint_generator import (
    WeasyPrintGenerator,
    WeasyPrintGeneratorOptions,
)
from care_suriname.reports.form_submission_artifact_metadata import (
    date_of_birth,
    encounter_date,
    encounter_date_label,
    user_name,
)
from care_suriname.reports.form_submission_artifact_metadata import (
    document_title as build_document_title,
)
from care_suriname.reports.form_submission_clinical_content import (
    render_clinical_content,
)
from care_suriname.reports.patient_pdf_header import (
    RUNNING_PATIENT_CSS,
    render_running_patient_header,
)

MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_SNAPSHOT_DEPTH = 20
MAX_SNAPSHOT_NODES = 10_000


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
    document_title = build_document_title(submission, questionnaire)
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
        ("Geboortedatum", date_of_birth(patient)),
        (encounter_date_label(encounter), encounter_date(encounter.period)),
        ("Behandelaar", user_name(finalizer)),
    ]
    patient_details = "".join(
        '<div class="patient-field">'
        f'<span class="patient-label">{escape(label)}</span>'
        f'<span class="patient-value">{escape(str(value))}</span>'
        "</div>"
        for label, value in patient_rows
    )
    clinical_content = render_clinical_content(submission.response_dump)
    running_patient = render_running_patient_header(
        name=patient.name, date_of_birth=date_of_birth(patient)
    )

    return f"""<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <title>{escape(document_title)}</title>
  <style>
    {RUNNING_PATIENT_CSS}
    @page {{
      size: A4;
      margin: 24mm 16mm 18mm;
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
      break-after: avoid;
      border-bottom: 1px solid #cbd5e1;
      font-size: 14pt;
      margin: 0 0 12px;
      padding-bottom: 6px;
    }}
    .narrative {{ margin: 0; orphans: 3; white-space: pre-wrap; widows: 3; word-break: normal; }}
    .clinical-heading {{ font-weight: 700; }}
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
  {running_patient}
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
