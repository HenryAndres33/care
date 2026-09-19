from html import escape

from django.utils import timezone
from django.utils.dateparse import parse_datetime

_ADMISSION_SLOT_TITLES = {
    "admission": "Opnamenotitie",
    "visit": "Visitenotitie",
    "discharge": "Ontslagsamenvatting",
}


def document_title(submission, questionnaire) -> str:
    if questionnaire.slug == "urology-operaties":
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
