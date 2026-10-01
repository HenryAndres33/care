from datetime import date
from html import escape

from django.utils import timezone
from django.utils.dateparse import parse_datetime

_ADMISSION_SLOT_TITLES = {
    "admission": "Opnamenotitie",
    "visit": "Visitenotitie",
    "discharge": "Ontslagsamenvatting",
}


def document_title(submission, questionnaire) -> str:
    if questionnaire.slug == OPERATION_QUESTIONNAIRE_SLUG:
        return "Operatieverslag"
    slot = _admission_slot(submission)
    kind = slot.split(":", 1)[0] if slot else None
    return _ADMISSION_SLOT_TITLES.get(kind, "Medisch dossier")


def _admission_slot(submission) -> str | None:
    from care.emr.models.questionnaire import FormSubmission
    from care_suriname.models.admission_documentation import AdmissionDocumentation

    if not submission.encounter_id:
        return None
    series_ids = FormSubmission.objects.filter(series_id=submission.series_id).values(
        "external_id"
    )
    reservation = (
        AdmissionDocumentation.objects.filter(
            admission_id=submission.encounter_id, form_instance_id__in=series_ids
        )
        .only("slot")
        .first()
    )
    return reservation.slot if reservation else None


OPERATION_QUESTIONNAIRE_SLUG = "urology-operaties"
_NOT_RECORDED = "Niet geregistreerd"


def operation_header_rows(response_dump) -> list[tuple[str, str]]:
    """Date, procedure and surgeon of an operation report for the PDF header.

    The operation's own date replaces the consult date: an operation report
    may hang on an older consult, and the paper file needs the day operated.
    """
    content = response_dump.get("content") if isinstance(response_dump, dict) else None
    values = content.get("values") if isinstance(content, dict) else None
    values = values if isinstance(values, dict) else {}

    def text(key: str) -> str:
        value = values.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else ""

    return [
        ("Datum ingreep", _procedure_date(text("operation.procedureDate"))),
        ("Ingreep", text("operation.procedureLabel") or _NOT_RECORDED),
        ("Operateur", text("operation.surgeonDisplay") or _NOT_RECORDED),
    ]


def _procedure_date(raw_value: str) -> str:
    if not raw_value:
        return _NOT_RECORDED
    try:
        return date.fromisoformat(raw_value[:10]).strftime("%d-%m-%Y")
    except ValueError:
        return raw_value


def encounter_date_label(encounter) -> str:
    return "Opnamedatum" if encounter.encounter_class == "imp" else "Consultdatum"


def date_of_birth(patient) -> str:
    if patient.date_of_birth:
        return patient.date_of_birth.strftime("%d-%m-%Y")
    if patient.year_of_birth:
        return str(patient.year_of_birth)
    return "Niet geregistreerd"


def encounter_date(period) -> str:
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


def user_name(user) -> str:
    name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    return name or user.username
