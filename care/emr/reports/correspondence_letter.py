from datetime import UTC
from html import escape

from care.emr.reports.renderer.generators.weasyprint_generator import (
    WeasyPrintGenerator,
    WeasyPrintGeneratorOptions,
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
    metadata_rows = [
        ("Artifact reference", artifact_id),
        ("Letter reference", revision.letter.external_id),
        ("Letter revision", revision.external_id),
        ("Letter version", revision.resource_version),
        ("Revision SHA-256", revision.revision_hash),
        ("Review binding", review.external_id),
        ("Review SHA-256", review.review_hash),
        ("Compilation", review.compilation.external_id),
        ("Compilation SHA-256", review.compilation_hash),
        ("Patient", patient.name),
        ("Patient CARE reference", patient.external_id),
        ("Patient identifiers", _patient_identifiers(patient, encounter)),
        ("Date of birth", patient.date_of_birth or patient.year_of_birth),
        ("Encounter CARE reference", encounter.external_id),
        ("Encounter date", _encounter_date(encounter.period)),
        ("Author", author.get("display")),
        ("Author role", author.get("professional_role")),
        ("Author qualification", author.get("qualification") or "—"),
        ("Author registration", author.get("registration") or "—"),
        ("Recipient", recipient.get("display_name")),
        ("Recipient role", recipient.get("professional_role")),
        ("Recipient organization", recipient.get("organization_name")),
        ("Recipient channel", recipient.get("channel", {}).get("type")),
        ("Finalized at", _timestamp(revision.finalized_at)),
        ("Generated at", _timestamp(generated_at)),
        ("Status", revision.status),
    ]
    metadata = "".join(
        f"<tr><th>{escape(str(label))}</th><td>{escape(str(value))}</td></tr>"
        for label, value in metadata_rows
    )
    body = escape(revision.body)
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>'
        "Final Clinical Correspondence</title></head><body>"
        "<h1>Clinical Correspondence</h1>"
        f'<table class="metadata"><tbody>{metadata}</tbody></table>'
        f'<h2>Letter</h2><pre class="letter-body">{body}</pre>'
        "</body></html>"
    )


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
        "<h1>CONTROLLED CORRECTION COPY</h1>"
        "<p>This replacement supersedes the earlier correspondence delivery "
        f"<strong>{escape(str(superseded_delivery_id))}</strong>.</p>"
        "<table><tbody>"
        f"<tr><th>Correction case</th><td>{escape(str(correction_case_id))}</td></tr>"
        "<tr><th>Replacement attempt</th>"
        f"<td>{escape(str(replacement_attempt_number))}</td></tr>"
        f"<tr><th>Clinical source version</th><td>{escape(str(source_version))}</td></tr>"
        f"<tr><th>Replacement revision</th><td>{escape(str(revision.external_id))}</td></tr>"
        f"<tr><th>Replacement revision hash</th><td>{escape(revision.revision_hash)}</td></tr>"
        "</tbody></table></section>"
    )
    return base.replace("<body>", f"<body>{banner}", 1)


def render_correspondence_letter_pdf(html):
    return WeasyPrintGenerator().generate(
        html,
        WeasyPrintGeneratorOptions(page_size="A4", margin="1.5cm"),
    )


def _patient_identifiers(patient, encounter):
    identifiers = list(patient.instance_identifiers or [])
    facility_identifiers = patient.facility_identifiers or {}
    identifiers.extend(
        facility_identifiers.get(str(encounter.facility_id), [])
        or facility_identifiers.get(encounter.facility_id, [])
    )
    readable = [
        f"{item.get('config', 'identifier')}: {item.get('value', '')}"
        for item in identifiers
        if isinstance(item, dict) and item.get("value")
    ]
    return "; ".join(readable) or "No configured identifier"


def _encounter_date(period):
    if not isinstance(period, dict):
        return "—"
    return str(period.get("start") or period.get("end") or "—")


def _timestamp(value):
    if not value:
        return "—"
    if value.tzinfo is None:
        return value.isoformat()
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
