from dataclasses import dataclass

from django.http import Http404
from rest_framework.exceptions import PermissionDenied

from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.encounter import Encounter
from care.emr.models.observation import Observation
from care.emr.models.observation_definition import ObservationDefinition
from care.emr.models.patient import Patient
from care.emr.models.service_request import ServiceRequest
from care.emr.resources.encounter.constants import CLINICALLY_CLOSED_CHOICES
from care.security.authorization.base import AuthorizationController
from care_suriname.resources.laboratory_commands.errors import conflict
from care_suriname.workflow_capabilities import require_workflow_mutations_enabled


@dataclass(frozen=True)
class LockedContext:
    patient: Patient
    encounter: Encounter
    service_request: ServiceRequest | None
    report: DiagnosticReport | None


def authorize_patient_reference(user, patient_id):
    patient = Patient.objects.filter(external_id=patient_id).first()
    if patient is None:
        raise Http404("Laboratory context not found")
    if not AuthorizationController.call("can_view_clinical_data", user, patient):
        raise PermissionDenied("Cannot read this patient's clinical data")


def lock_command_context(command, *, require_existing: bool) -> LockedContext:
    patient = (
        Patient.objects.select_for_update(of=("self",))
        .filter(external_id=command.patient)
        .first()
    )
    encounter = (
        Encounter.objects.select_for_update(of=("self",))
        .select_related("facility", "patient")
        .filter(external_id=command.encounter)
        .first()
    )
    if (
        patient is None
        or encounter is None
        or encounter.patient_id != patient.id
        or encounter.facility.external_id != command.facility
    ):
        raise Http404("Laboratory context not found")
    service_request = (
        ServiceRequest._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related("patient", "facility", "encounter", "requester")
        .filter(external_id=command.service_request_id)
        .first()
    )
    report = (
        DiagnosticReport._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .select_related(
            "patient",
            "facility",
            "encounter",
            "service_request",
            "created_by",
        )
        .filter(external_id=command.report_id)
        .first()
    )
    if require_existing and (service_request is None or report is None):
        raise Http404("Laboratory report not found")
    if service_request or report:
        _validate_existing_context(command, service_request, report, encounter)
    return LockedContext(patient, encounter, service_request, report)


def lock_report(report_id) -> DiagnosticReport:
    reference = (
        DiagnosticReport._base_manager.filter(  # noqa: SLF001
            external_id=report_id,
            deleted=False,
        )
        .values("id", "patient_id", "encounter_id", "service_request_id")
        .first()
    )
    if reference is None:
        raise Http404("Laboratory report not found")
    Patient._base_manager.select_for_update(of=("self",)).get(  # noqa: SLF001
        id=reference["patient_id"]
    )
    Encounter._base_manager.select_for_update(of=("self",)).get(  # noqa: SLF001
        id=reference["encounter_id"]
    )
    ServiceRequest._base_manager.select_for_update(of=("self",)).get(  # noqa: SLF001
        id=reference["service_request_id"]
    )
    report = (
        DiagnosticReport._base_manager.select_for_update(  # noqa: SLF001
            of=("self",)
        )
        .select_related(
            "patient",
            "facility",
            "encounter",
            "service_request",
            "service_request__requester",
            "created_by",
        )
        .filter(id=reference["id"], deleted=False)
        .first()
    )
    if report is None or not all(
        (
            report.patient_id == reference["patient_id"],
            report.encounter_id == reference["encounter_id"],
            report.service_request_id == reference["service_request_id"],
        )
    ):
        raise Http404("Laboratory report not found")
    list(
        Observation._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(diagnostic_report=report)
        .order_by("id")
    )
    return report


def lock_definitions(facility, rows) -> dict:
    identities = {row.definition.id: row.definition for row in rows}
    definitions = list(
        ObservationDefinition._base_manager.select_for_update(  # noqa: SLF001
            of=("self",)
        )
        .filter(facility=facility, external_id__in=identities)
        .order_by("id")
    )
    if len(definitions) != len(identities):
        raise conflict(
            "catalogue_changed",
            "A selected laboratory definition is unavailable.",
        )
    return {item.external_id: item for item in definitions}


def lock_observations(report) -> list[Observation]:
    return list(
        Observation._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(diagnostic_report=report)
        .select_related("observation_definition", "patient", "encounter")
        .order_by("id")
    )


def authorize_mutation(user, context: LockedContext, *, creating: bool):
    if not AuthorizationController.call(
        "can_view_clinical_data", user, context.patient
    ):
        raise PermissionDenied("Cannot read this patient's clinical data")
    if context.encounter.status in CLINICALLY_CLOSED_CHOICES:
        raise conflict("state_conflict", "The encounter is clinically closed.")
    require_workflow_mutations_enabled(context.encounter.facility.external_id)
    if creating:
        allowed = AuthorizationController.call(
            "can_write_service_request_in_encounter",
            user,
            context.encounter,
        )
        if not allowed:
            raise PermissionDenied("Cannot create a laboratory service request")
        return
    if not AuthorizationController.call(
        "can_write_service_request", user, context.service_request
    ) or not AuthorizationController.call(
        "can_write_diagnostic_report", user, context.service_request
    ):
        raise PermissionDenied("Cannot modify this laboratory report")


def authorize_read(user, report):
    if (
        not AuthorizationController.call("can_view_clinical_data", user, report.patient)
        or not AuthorizationController.call(
            "can_read_service_request", user, report.service_request
        )
        or not AuthorizationController.call(
            "can_read_diagnostic_report", user, report.service_request
        )
    ):
        raise PermissionDenied("Cannot read this laboratory report")


def _validate_existing_context(command, service_request, report, encounter):
    if service_request is None or report is None:
        raise conflict("row_identity_conflict", "Laboratory aggregate ID collision.")
    if not all(
        (
            service_request.patient_id == encounter.patient_id,
            service_request.facility_id == encounter.facility_id,
            service_request.encounter_id == encounter.id,
            report.service_request_id == service_request.id,
            report.patient_id == encounter.patient_id,
            report.facility_id == encounter.facility_id,
            report.encounter_id == encounter.id,
        )
    ):
        raise Http404("Laboratory report context not found")
