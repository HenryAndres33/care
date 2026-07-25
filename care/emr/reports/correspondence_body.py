from html import escape

from care.emr.reports.clinical_narrative import (
    normalize_diagnosis_history_layout,
)

CLINICAL_SECTION_LABELS = {
    "reden van komst": "Reden van komst",
    "reden van presentatie": "Reden van komst",
    "reden van verwijzing": "Reden van komst",
    "algemene voorgeschiedenis": "Algemene voorgeschiedenis",
    "urologische voorgeschiedenis": "Urologische voorgeschiedenis",
    "allergie": "Allergie",
    "allergieën": "Allergieën",
    "intoxicaties": "Intoxicaties",
    "thuismedicatie": "Thuismedicatie",
    "anamnese": "Anamnese",
    "lichamelijk onderzoek": "Lichamelijk onderzoek",
    "beeldvorming": "Beeldvorming",
    "lab uitslagen": "Lab uitslagen",
    "laboratoriumuitslagen": "Laboratoriumuitslagen",
    "conclusie & bespreking": "Conclusie & Bespreking",
    "conclusie en bespreking": "Conclusie & Bespreking",
    "beleid": "Beleid",
}
_HISTORY_SECTION_LABELS = {
    "Algemene voorgeschiedenis",
    "Urologische voorgeschiedenis",
}


def render_correspondence_body(body_text):
    body_text = normalize_diagnosis_history_layout(body_text)
    blocks = []
    free_lines = []
    section_label = None
    section_lines = []

    def flush_free():
        if free_lines:
            blocks.extend(_paragraphs(free_lines))
            free_lines.clear()

    def flush_section():
        nonlocal section_label
        if section_label is not None:
            content = (
                _history_list(section_lines)
                if section_label in _HISTORY_SECTION_LABELS
                else "".join(_paragraphs(section_lines))
            )
            blocks.append(
                '<section class="clinical-section">'
                f"<h2>{escape(section_label)}</h2>{content}</section>"
            )
            section_label = None
            section_lines.clear()

    for raw_line in str(body_text or "").replace("\r\n", "\n").split("\n"):
        label, inline_content = _section_start(raw_line)
        if label is not None:
            flush_free()
            flush_section()
            section_label = label
            if inline_content:
                section_lines.append(inline_content)
            continue
        if section_label is None:
            free_lines.append(raw_line)
        else:
            section_lines.append(raw_line)

    flush_free()
    flush_section()
    return "".join(blocks)


def _history_list(lines):
    items = []
    leading_lines = []
    diagnosis = None
    detail_lines = []

    def flush_item():
        nonlocal diagnosis
        if diagnosis is None:
            return
        detail = (
            '<div class="history-detail">'
            + "<br>".join(escape(item) for item in detail_lines)
            + "</div>"
            if detail_lines
            else ""
        )
        items.append(
            '<li><span class="history-diagnosis">'
            f"{escape(diagnosis)}</span>{detail}</li>"
        )
        diagnosis = None
        detail_lines.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            flush_item()
            diagnosis = stripped[2:].strip()
        elif stripped and diagnosis is not None:
            detail_lines.append(stripped)
        elif stripped:
            leading_lines.append(stripped)
    flush_item()

    if not items:
        return "".join(_paragraphs(lines))
    introduction = "".join(_paragraphs(leading_lines))
    return f'{introduction}<ul class="history-list">{"".join(items)}</ul>'


def _section_start(line):
    stripped = line.strip()
    if not stripped:
        return None, ""
    heading, separator, content = stripped.partition(":")
    label = CLINICAL_SECTION_LABELS.get(heading.strip().casefold())
    if label is None:
        return None, ""
    return label, content.strip() if separator else ""


def _paragraphs(lines):
    paragraphs = []
    current = []
    for line in [*lines, ""]:
        stripped = line.strip()
        if stripped:
            current.append(stripped)
            continue
        if current:
            text = "<br>".join(escape(item) for item in current)
            paragraphs.append(f"<p>{text}</p>")
            current = []
    return paragraphs
