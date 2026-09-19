"""Called inside the existing serialized FormSubmission command transaction."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from rest_framework.exceptions import PermissionDenied, ValidationError

from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.observation import Observation
from care.emr.models.service_request import ServiceRequest
from care.security.authorization.base import AuthorizationController
from care_suriname.models.form_submission_lab import FormSubmissionLabLink
from care_suriname.resources.form_submission.note_lab_text import (
    DEFAULT_SOURCE,
    REGISTER_FIELD,
    TESTS,
    parse_note_labs,
)

LAB_CATEGORY = {
    "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
    "code": "LAB",
    "display": "Laboratorium",
}


def register_note_labs(submission, actor, *, finalize=False):
    dump = submission.response_dump
    if dump.get("schema") != "care.urology.encounter-owned-form-submission":
        if FormSubmissionLabLink.objects.filter(
            series_id=submission.series_id
        ).exists():
            raise ValidationError("De notitie bevat geregistreerde labkoppelingen.")
        return
    content = dump.get("content", {})
    register = content.get("values", {}).get(REGISTER_FIELD) is True or finalize
    if not register:
        return  # Autosave may preserve unfinished input; never publishes labs.
    links = list(
        FormSubmissionLabLink.objects.select_for_update()
        .filter(series_id=submission.series_id)
        .select_related("report")
    )
    rows = parse_note_labs(content.get("noteText", ""))
    if not rows and not links:
        return
    if dump.get("identity", {}).get("formType") != "medisch-dossier":
        raise ValidationError("Gekoppelde labs vereisen een medische dossiernotitie.")
    encounter = submission.encounter
    if not encounter or encounter.patient_id != submission.patient_id:
        raise ValidationError("De labuitslag vereist hetzelfde patiëntcontact.")
    by_slot = {row.slot: row for row in rows}
    for link in links:
        row = by_slot.get(link.slot)
        if not row or row.fingerprint != link.fingerprint:
            raise ValidationError(
                "Een geregistreerde labuitslag kan niet via notitietekst worden "
                "gewijzigd of verwijderd. Herstel de geregistreerde labregel."
            )
        if (
            link.report.patient_id != submission.patient_id
            or link.report.encounter_id != encounter.id
            or link.report.status != "final"
        ):
            raise ValidationError("De gekoppelde labuitslag moet worden gecontroleerd.")
        observation = Observation.objects.filter(diagnostic_report=link.report).first()
        code, _, unit_code, _ = TESTS[row.slot]
        observed_date = (
            observation.effective_datetime.astimezone(
                ZoneInfo("America/Paramaribo")
            ).date()
            if observation and observation.effective_datetime
            else None
        )
        if (
            not observation
            or observation.status != "final"
            or observation.patient_id != submission.patient_id
            or observation.encounter_id != encounter.id
            or observation.main_code.get("code") != code
            or observation.value.get("value") != row.value
            or observation.value.get("unit", {}).get("code") != unit_code
            or observed_date != row.measured
        ):
            raise ValidationError(
                "De native labuitslag wijkt af; controleer de koppeling."
            )
    existing = {link.slot for link in links}
    for row in rows:
        if row.slot not in existing:
            _create_result(submission, actor, row)


def _create_result(submission, actor, row):
    encounter = submission.encounter
    code, display, unit_code, unit_display = TESTS[row.slot]
    coding = {"system": "http://loinc.org", "code": code, "display": display}
    measured = (
        datetime.combine(row.measured, time.min, ZoneInfo("America/Paramaribo"))
        if row.measured
        else None
    )
    measured_provenance = (
        f"Afnamedatum: {row.measured.isoformat()} (tijd onbekend)."
        if row.measured
        else "Afnamedatum onbekend."
    )
    provenance = (
        f"{row.slot}. "
        f"{row.source if row.source == DEFAULT_SOURCE else f'Externe uitslag; bron: {row.source}'}. "
        f"{measured_provenance} "
        f"Bronnotitie: {submission.external_id}."
    )
    request = ServiceRequest(
        facility=encounter.facility,
        patient=submission.patient,
        encounter=encounter,
        title=row.slot,
        category="laboratory",
        status="completed",
        intent="order",
        priority="routine",
        code=coding,
        note=provenance,
        occurance=measured,
        requester=actor,
        created_by=actor,
        updated_by=actor,
    )
    for permission in ("can_write_service_request", "can_write_diagnostic_report"):
        if not AuthorizationController.call(permission, actor, request):
            raise PermissionDenied(
                "Geen bevoegdheid om deze labuitslag te registreren."
            )
    request.save()
    report = DiagnosticReport.objects.create(
        facility=encounter.facility,
        patient=submission.patient,
        encounter=encounter,
        service_request=request,
        status="final",
        category=LAB_CATEGORY,
        code=coding,
        note=provenance,
        created_by=actor,
        updated_by=actor,
    )
    Observation.objects.create(
        patient=submission.patient,
        encounter=encounter,
        diagnostic_report=report,
        status="final",
        category=LAB_CATEGORY,
        main_code=coding,
        subject_type="encounter",
        subject_id=encounter.external_id,
        effective_datetime=measured,
        data_entered_by=actor,
        value_type="quantity",
        value={
            "value": row.value,
            "unit": {
                "system": "http://unitsofmeasure.org",
                "code": unit_code,
                "display": unit_display,
            },
        },
        note=provenance,
        created_by=actor,
        updated_by=actor,
    )
    FormSubmissionLabLink.objects.create(
        series_id=submission.series_id,
        slot=row.slot,
        submission=submission,
        report=report,
        fingerprint=row.fingerprint,
        created_by=actor,
        updated_by=actor,
    )
