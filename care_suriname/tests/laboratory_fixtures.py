from datetime import date
from uuid import uuid4

from model_bakery import baker

from care.emr.models.observation_definition import ObservationDefinition
from care_suriname.resources.laboratory_commands.catalogue import (
    definition_fingerprint,
)
from care_suriname.resources.laboratory_commands.specs import (
    LABORATORY_COMMAND_CONTRACT,
)


def laboratory_context(test):
    user = test.create_super_user()
    patient = test.create_patient(
        name="Synthetic Laboratory Patient",
        date_of_birth=date(1980, 5, 4),
        gender="male",
    )
    facility = test.create_facility(user=user)
    organization = test.create_facility_organization(facility=facility, org_type="root")
    encounter = test.create_encounter(
        patient=patient,
        facility=facility,
        organization=organization,
    )
    definition = make_definition(facility)
    return {
        "user": user,
        "patient": patient,
        "facility": facility,
        "organization": organization,
        "encounter": encounter,
        "definition": definition,
    }


def make_definition(facility, **overrides):
    data = {
        "facility": facility,
        "slug": f"f-{facility.external_id}-glucose",
        "version": 1,
        "title": "Glucose",
        "status": "active",
        "description": "Synthetic test definition",
        "category": "laboratory",
        "code": {
            "system": "http://loinc.org",
            "code": "14749-6",
            "display": "Glucose",
        },
        "permitted_data_type": "quantity",
        "permitted_unit": {
            "system": "http://unitsofmeasure.org",
            "code": "mmol/L",
            "display": "mmol/L",
        },
        "derived_from_uri": "",
        "qualified_ranges": [],
    }
    data.update(overrides)
    return baker.make(ObservationDefinition, **data)


def create_command(context, *, rows=None):
    definition = context["definition"]
    return {
        "contract": LABORATORY_COMMAND_CONTRACT,
        "action": "create_draft",
        "client_request_id": str(uuid4()),
        "expected_version": 0,
        "patient": str(context["patient"].external_id),
        "facility": str(context["facility"].external_id),
        "encounter": str(context["encounter"].external_id),
        "service_request_id": str(uuid4()),
        "report_id": str(uuid4()),
        "source": {"kind": "external_lab", "label": "Synthetic AZP lab"},
        "rows": rows or [result_row(definition)],
    }


def result_row(definition, **overrides):
    row = {
        "row_id": str(uuid4()),
        "collection_group_id": str(uuid4()),
        "definition": {
            "id": str(definition.external_id),
            "slug": definition.slug,
            "version": definition.version,
            "fingerprint": definition_fingerprint(definition),
        },
        "collected_at": {
            "kind": "known",
            "value": "2026-09-19T08:30:00-03:00",
        },
        "confirmed_reference_context": ["fasting_confirmed"],
        "specimen": "serum",
        "value": {
            "kind": "quantity",
            "input": "5,0",
            "unit": definition.permitted_unit,
        },
    }
    row.update(overrides)
    return row


def later_command(create, action, expected_version, **extra):
    shared = {
        key: create[key]
        for key in (
            "contract",
            "patient",
            "facility",
            "encounter",
            "service_request_id",
            "report_id",
        )
    }
    return {
        **shared,
        "action": action,
        "client_request_id": str(uuid4()),
        "expected_version": expected_version,
        **extra,
    }
