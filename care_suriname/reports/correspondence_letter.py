from datetime import UTC
from html import escape

from care.emr.reports.renderer.generators.weasyprint_generator import (
    WeasyPrintGenerator,
    WeasyPrintGeneratorOptions,
)
from care_suriname.correspondence.presentation import correspondence_presentation_reason
from care_suriname.reports.correspondence_body import render_correspondence_body
from care_suriname.reports.correspondence_letter_branding import render_letterhead
from care_suriname.reports.correspondence_letter_metadata import (
    patient_record_identifier,
)
from care_suriname.reports.correspondence_letter_styles import LETTER_CSS
from care_suriname.reports.patient_pdf_header import (
    RUNNING_PATIENT_CSS,
    render_running_patient_header,
)


class CorrespondenceLetterRenderError(ValueError):
    pass


def build_correspondence_letter_html(*, artifact_id, revision, generated_at):
    review = revision.letter.review
    patient = revision.letter.patient
    encounter = revision.letter.encounter
    author = review.author_snapshot
    recipient = review.recipient_snapshot
    if not revision.body or not author or not recipient:
        raise CorrespondenceLetterRenderError(
            "Final correspondence revision is missing frozen content or identity"
        )

    compilation = review.compilation
    facility_name = getattr(compilation.facility, "name", "Zorginstelling")
    department_name = getattr(compilation.department, "name", "")
    recipient_address = "".join(
        f"<div>{escape(line)}</div>" for line in _postal_address_lines(recipient)
    )
    body = render_correspondence_body(revision.body)
    author_details = " · ".join(
        value
        for value in [
            professional_role_label(author.get("professional_role")),
            author.get("qualification"),
            author.get("registration"),
        ]
        if value
    )
    signature = _signature_html(
        body_text=revision.body,
        author=author,
        author_details=author_details,
        department_name=department_name,
    )
    patient_identifier = patient_record_identifier(patient, encounter)
    running_patient = render_running_patient_header(
        name=patient.name,
        date_of_birth=_display_date(patient.date_of_birth or patient.year_of_birth),
        identifier=patient_identifier,
    )
    subject = _encounter_reason(compilation)
    presentation_date = _presentation_date(compilation)
    letterhead_title = _template_option(
        compilation,
        "letterhead_title",
        str(department_name or "Medische correspondentie").upper(),
    )
    letterhead = render_letterhead(
        facility_name=str(facility_name), department_title=letterhead_title
    )
    return f"""<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <title>Medische brief</title>
  <style>{RUNNING_PATIENT_CSS}
{LETTER_CSS}</style>
</head>
<body>
  {running_patient}
  {letterhead}
  <section class="recipient-block">
    <strong>{escape(str(recipient.get("display_name") or ""))}</strong>
    <div>{escape(str(recipient.get("professional_role") or ""))}</div>
    <div>{escape(str(recipient.get("organization_name") or ""))}</div>
    {recipient_address}
  </section>
  <section class="letter-meta">
    <div><span>Briefdatum</span><strong>{_display_date(generated_at)}</strong></div>
    <div><span>Presentatiedatum</span><strong>{_display_date(presentation_date)}</strong></div>
    <div><span>Betreft</span><strong>{escape(patient.name)}</strong></div>
    <div><span>Geboortedatum</span><strong>{_display_date(patient.date_of_birth or patient.year_of_birth)}</strong></div>
    <div><span>Patiëntnummer</span><strong>{escape(patient_identifier)}</strong></div>
  </section>

  <section class="subject"><span>Onderwerp</span>{escape(subject)}</section>
  <main class="letter-body">{body}</main>
  {signature}

  <footer>
    <span>Vertrouwelijk medisch document</span>
    <span>{escape(str(department_name or letterhead_title))}</span>
    <span>Versie {revision.resource_version}</span>
  </footer>
</body>
</html>"""


def build_controlled_correction_copy_html(
    *,
    artifact_id,
    revision,
    generated_at,
    correction_case_id,
    replacement_attempt_number,
    source_version,
    superseded_delivery_id,
):
    base = build_correspondence_letter_html(
        artifact_id=artifact_id,
        revision=revision,
        generated_at=generated_at,
    )
    banner = (
        '<section class="controlled-correction-copy">'
        "<h1>GECONTROLEERDE CORRECTIE</h1>"
        "<p>Deze vervangende brief vervangt de eerdere verzending "
        f"<strong>{escape(str(superseded_delivery_id))}</strong>.</p>"
        "<table><tbody>"
        f"<tr><th>Correctiedossier</th><td>{escape(str(correction_case_id))}</td></tr>"
        "<tr><th>Vervangingspoging</th>"
        f"<td>{escape(str(replacement_attempt_number))}</td></tr>"
        f"<tr><th>Bronversie</th><td>{escape(str(source_version))}</td></tr>"
        f"<tr><th>Briefversie</th><td>{escape(str(revision.external_id))}</td></tr>"
        "</tbody></table></section>"
    )
    return base.replace("<body>", f"<body>{banner}", 1)


def render_correspondence_letter_pdf(html):
    return WeasyPrintGenerator().generate(
        html,
        WeasyPrintGeneratorOptions(page_size="A4", margin="1.8cm"),
    )


def _postal_address_lines(recipient):
    address = recipient.get("postal_address")
    if not isinstance(address, dict):
        return []
    preferred_keys = (
        "lines",
        "line",
        "address_line",
        "street_address",
        "postal_code",
        "city",
        "state",
        "country",
    )
    values = []
    for key in preferred_keys:
        value = address.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif isinstance(value, list):
            values.extend(
                item.strip() for item in value if isinstance(item, str) and item.strip()
            )
    return list(dict.fromkeys(values))


def _encounter_reason(compilation):
    fallback = str(
        getattr(compilation.encounter_reason, "display", "")
        or "Medische correspondentie"
    )
    provenance = getattr(compilation, "source_provenance", {}) or {}
    form = provenance.get("form") if isinstance(provenance, dict) else None
    if isinstance(form, dict):
        frozen_reason = form.get("presentation_reason")
        if isinstance(frozen_reason, str) and frozen_reason.strip():
            return frozen_reason.strip()
    source = getattr(compilation, "form_submission", None)
    return correspondence_presentation_reason(
        getattr(source, "response_dump", None),
        fallback=fallback,
    )


def _presentation_date(compilation):
    provenance = getattr(compilation, "source_provenance", {}) or {}
    encounter = provenance.get("encounter") if isinstance(provenance, dict) else None
    if isinstance(encounter, dict) and encounter.get("date"):
        return encounter["date"]
    return getattr(compilation.encounter, "start_date", None)


def _template_option(compilation, key, default):
    options = getattr(compilation.template, "options", {}) or {}
    value = options.get(key) if isinstance(options, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


_CLOSING_MARKERS = (
    "met vriendelijke groet",
    "met collegiale groet",
    "collegiale groet",
    "vriendelijke groet",
    "hoogachtend",
    "with kind regards",
    "kind regards",
    "sincerely",
)

# CARE's built-in role names are English; the paper letter is Dutch.
_ROLE_LABELS = {
    "doctor": "Arts",
    "nurse": "Verpleegkundige",
    "staff": "Medewerker",
    "administrator": "Beheerder",
    "facility admin": "Beheerder",
    "volunteer": "Vrijwilliger",
}


def professional_role_label(role):
    text = str(role or "").strip()
    return _ROLE_LABELS.get(text.casefold(), text)


def _signature_html(*, body_text, author, author_details, department_name):
    """Exactly one closing per letter; the author identity always comes from
    the frozen server snapshot, never from the typed body."""
    author_display = str(author.get("display") or "").strip()
    normalized_body = body_text.casefold()
    body_has_closing = any(marker in normalized_body for marker in _CLOSING_MARKERS)
    if (
        author_display
        and author_display.casefold() in normalized_body
        and body_has_closing
    ):
        return ""
    greeting = "" if body_has_closing else "<div>Met vriendelijke groet,</div>"
    department = f"<div>{escape(str(department_name))}</div>" if department_name else ""
    return (
        '<section class="signature">'
        f"{greeting}"
        f"<strong>{escape(author_display)}</strong>"
        f"<div>{escape(author_details)}</div>"
        f"{department}"
        "</section>"
    )


def _display_date(value):
    if not value:
        return "Niet vastgelegd"
    if hasattr(value, "astimezone"):
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.strftime("%d-%m-%Y")
    text = str(value)
    try:
        year, month, day = text[:10].split("-")
        return f"{day}-{month}-{year}"
    except ValueError:
        return text
