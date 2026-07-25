from django.utils import timezone
from django.utils.dateparse import parse_datetime

from care.emr.models.encounter import Encounter
from care.emr.models.location import FacilityLocation, FacilityLocationEncounter
from care.emr.resources.encounter.constants import ClassChoices, StatusChoices
from care.emr.resources.location.spec import (
    FacilityLocationOperationalStatusChoices,
    LocationAvailabilityStatusChoices,
    LocationEncounterAvailabilityStatusChoices,
)
from care.utils.shortcuts import get_object_or_404

ACTIVE_DISCHARGE_STATUSES = {
    StatusChoices.in_progress.value,
    StatusChoices.on_hold.value,
}
OPEN_LOCATION_STATUSES = {
    LocationEncounterAvailabilityStatusChoices.active.value,
    LocationEncounterAvailabilityStatusChoices.planned.value,
    LocationEncounterAvailabilityStatusChoices.reserved.value,
}


def lock_discharge_context(external_id):
    encounter = get_object_or_404(
        Encounter._base_manager.select_for_update(of=("self",)).select_related(  # noqa: SLF001
            "patient",
            "facility",
        ),
        external_id=external_id,
        deleted=False,
    )
    location = None
    if encounter.current_location_id:
        location = get_object_or_404(
            FacilityLocation._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
            pk=encounter.current_location_id,
            deleted=False,
        )
    assignments = list(
        FacilityLocationEncounter._base_manager.select_for_update(of=("self",))  # noqa: SLF001
        .filter(
            encounter=encounter,
            deleted=False,
            end_datetime__isnull=True,
            status__in=OPEN_LOCATION_STATUSES,
        )
        .order_by("pk")
    )
    return {
        "assignments": assignments,
        "encounter": encounter,
        "location": location,
    }


def encounter_discharge_blockers(context, request_spec):
    encounter = context["encounter"]
    blockers = set()
    if encounter.encounter_class != ClassChoices.imp.value:
        blockers.add("encounter_not_inpatient")
    if encounter.status not in ACTIVE_DISCHARGE_STATUSES:
        blockers.add("encounter_state_stale")

    period = encounter.period
    admission_start = _admission_start(period)
    if not isinstance(period, dict):
        blockers.add("admission_period_invalid")
    elif period.get("end") is not None:
        blockers.add("admission_period_already_ended")
    if admission_start is None:
        blockers.add("admission_start_missing")
    elif request_spec.discharged_at < admission_start:
        blockers.add("discharged_at_before_admission")
    if request_spec.discharged_at > timezone.now():
        blockers.add("discharged_at_in_future")
    if not _valid_history(encounter.status_history):
        blockers.add("status_history_invalid")
    if not isinstance(encounter.hospitalization, dict):
        blockers.add("hospitalization_invalid")

    blockers.update(_location_blockers(context, request_spec))
    return sorted(blockers)


def apply_encounter_discharge(context, request_spec, actor):
    encounter = context["encounter"]
    location = context["location"]
    released_location = None
    bed_released = False
    if request_spec.release_bed and location:
        assignment = context["assignments"][0]
        assignment.status = LocationEncounterAvailabilityStatusChoices.completed.value
        assignment.end_datetime = request_spec.discharged_at
        assignment.updated_by = actor
        assignment.save(
            update_fields=[
                "status",
                "end_datetime",
                "updated_by",
                "modified_date",
            ]
        )
        released_location = location.external_id
        bed_released = True
        location.current_encounter = None
        location.system_availability_status = (
            LocationAvailabilityStatusChoices.available.value
        )
        location_fields = [
            "current_encounter",
            "system_availability_status",
            "updated_by",
            "modified_date",
        ]
        if (
            location.operational_status
            == FacilityLocationOperationalStatusChoices.O.value
        ):
            location.operational_status = (
                FacilityLocationOperationalStatusChoices.U.value
            )
            location_fields.append("operational_status")
        location.updated_by = actor
        location.save(update_fields=location_fields)
        encounter.current_location = None

    moved_at = request_spec.discharged_at.isoformat()
    status_entry = {
        "status": StatusChoices.discharged.value,
        "moved_at": moved_at,
    }
    encounter.status = StatusChoices.discharged.value
    encounter.status_history = {
        **encounter.status_history,
        "history": [*encounter.status_history["history"], status_entry],
    }
    encounter.period = {**encounter.period, "end": moved_at}
    encounter.hospitalization = {
        **encounter.hospitalization,
        "discharge_disposition": request_spec.discharge_disposition.value,
    }
    encounter.discharge_summary_advice = request_spec.discharge_summary_advice
    encounter.updated_by = actor
    encounter_fields = [
        "status",
        "status_history",
        "period",
        "hospitalization",
        "discharge_summary_advice",
        "updated_by",
        "modified_date",
    ]
    if bed_released:
        encounter_fields.append("current_location")
    encounter.save(update_fields=encounter_fields)
    return {
        "encounter": str(encounter.external_id),
        "status": StatusChoices.discharged.value,
        "status_history_entry": status_entry,
        "discharge_disposition": request_spec.discharge_disposition.value,
        "discharged_at": moved_at,
        "discharge_summary_advice": request_spec.discharge_summary_advice,
        "bed_released": bed_released,
        "released_location": (
            str(released_location) if released_location is not None else None
        ),
    }


def discharge_snapshot_matches_encounter(snapshot, encounter):
    period = encounter.period if isinstance(encounter.period, dict) else {}
    hospitalization = (
        encounter.hospitalization if isinstance(encounter.hospitalization, dict) else {}
    )
    history = (
        encounter.status_history.get("history", [])
        if isinstance(encounter.status_history, dict)
        else []
    )
    return all(
        [
            encounter.status == snapshot.get("status"),
            str(period.get("end")) == snapshot.get("discharged_at"),
            hospitalization.get("discharge_disposition")
            == snapshot.get("discharge_disposition"),
            encounter.discharge_summary_advice
            == snapshot.get("discharge_summary_advice"),
            snapshot.get("status_history_entry") in history,
            not snapshot.get("bed_released") or encounter.current_location_id is None,
        ]
    )


def _location_blockers(context, request_spec):
    encounter = context["encounter"]
    location = context["location"]
    assignments = context["assignments"]
    blockers = set()
    if location is None:
        if assignments:
            blockers.add("bed_assignment_inconsistent")
        return blockers
    if not request_spec.release_bed:
        blockers.add("bed_release_required")
    if location.current_encounter_id != encounter.id:
        blockers.add("bed_assignment_inconsistent")
    matching = [
        item
        for item in assignments
        if item.location_id == encounter.current_location_id
    ]
    if not matching:
        blockers.add("bed_assignment_history_missing")
    if len(assignments) > 1 or len(matching) > 1:
        blockers.add("multiple_active_bed_assignments")
    if matching and matching[0].start_datetime > request_spec.discharged_at:
        blockers.add("discharged_at_before_bed_assignment")
    return blockers


def _admission_start(period):
    if not isinstance(period, dict):
        return None
    value = period.get("start")
    if not isinstance(value, str):
        return None
    parsed = parse_datetime(value)
    if parsed is None or timezone.is_naive(parsed):
        return None
    return parsed


def _valid_history(value):
    return isinstance(value, dict) and isinstance(value.get("history"), list)
