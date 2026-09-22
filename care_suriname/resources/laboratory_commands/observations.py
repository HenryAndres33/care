from care.emr.models.observation import Observation
from care_suriname.resources.form_submission.note_labs import LAB_CATEGORY
from care_suriname.resources.laboratory_commands.errors import conflict
from care_suriname.resources.laboratory_commands.reference import evaluate_reference
from care_suriname.resources.laboratory_commands.specs import (
    parse_laboratory_decimal,
    parse_laboratory_integer,
)

RESULT_META_KEY = "laboratory_result"


def apply_result_row(
    observation: Observation,
    *,
    row,
    definition,
    report,
    source,
    actor,
    status="final",
    parent=None,
    correction_reason=None,
):
    creating = observation.id is None
    observation.external_id = row.row_id
    observation.deleted = False
    observation.patient = report.patient
    observation.encounter = report.encounter
    observation.diagnostic_report = report
    observation.observation_definition = definition
    observation.status = status
    observation.category = LAB_CATEGORY
    observation.main_code = definition.code
    observation.subject_type = "encounter"
    observation.subject_id = report.encounter.external_id
    observation.effective_datetime = (
        row.collected_at.value if row.collected_at.kind == "known" else None
    )
    observation.data_entered_by = actor
    observation.value_type = row.value.kind
    observation.value = stored_value(row.value, definition)
    observation.body_site = definition.body_site or {}
    observation.method = definition.method or {}
    observation.parent = parent
    observation.note = f"Extern laboratorium: {source.label}."
    if correction_reason:
        observation.note += f" Correctie: {correction_reason}"
    reference = evaluate_reference(
        observation,
        definition,
        row.confirmed_reference_context,
        row.specimen,
    )
    observation.meta = _result_meta(
        observation.meta,
        observation=observation,
        row=row,
        source=source,
        reference=reference,
        correction_reason=correction_reason,
    )
    if creating:
        observation.created_by = actor
    observation.updated_by = actor
    observation.save()
    return observation


def stored_value(value, definition) -> dict:
    if value.kind == "quantity":
        return {
            "value": format(parse_laboratory_decimal(value.input), "f"),
            "unit": definition.permitted_unit,
        }
    if value.kind == "decimal":
        return {"value": format(parse_laboratory_decimal(value.input), "f")}
    if value.kind == "integer":
        return {"value": str(parse_laboratory_integer(value.input))}
    return {"value": value.value}


def observation_to_row(observation: Observation) -> dict:
    provenance = result_provenance(observation)
    identity = provenance.get("definition")
    if not identity or str(observation.external_id) != provenance.get("row_id"):
        raise conflict(
            "aggregate_drift",
            "Laboratory row provenance does not match the native observation.",
        )
    value = _read_value(observation, provenance)
    return {
        "row_id": str(observation.external_id),
        "collection_group_id": provenance.get("collection_group_id"),
        "observation_id": str(observation.external_id),
        "status": observation.status,
        "definition": identity,
        "code": observation.main_code,
        "method": observation.method or None,
        "body_site": observation.body_site or None,
        "collected_at": (
            {"kind": "known", "value": observation.effective_datetime}
            if observation.effective_datetime
            else {"kind": "unknown"}
        ),
        "specimen": provenance.get("specimen"),
        "confirmed_reference_context": provenance.get(
            "confirmed_reference_context", []
        ),
        "value": value,
        "interpretation": observation.interpretation or {},
        "reference_range": observation.reference_range or [],
        "reference_provenance": provenance.get("reference_provenance"),
        "parent_observation_id": (
            str(observation.parent) if observation.parent else None
        ),
        "correction_reason": provenance.get("correction_reason"),
    }


def result_provenance(observation: Observation) -> dict:
    meta = observation.meta or {}
    return meta.get("care_suriname", {}).get(RESULT_META_KEY, {})


def validate_reference_snapshot(observation, definition):
    provenance = result_provenance(observation)
    stored_context = provenance.get("reference_context")
    current_context = {
        "collected_at": provenance.get("collected_at"),
        **patient_birth_context(observation.patient),
        "recorded_sex": observation.patient.gender or None,
        "specimen": provenance.get("specimen"),
        "confirmed": provenance.get("confirmed_reference_context", []),
    }
    if stored_context != current_context:
        raise conflict(
            "reference_context_changed",
            "Patient or reference context changed after draft review.",
        )
    stored_interpretation = observation.interpretation
    stored_range = observation.reference_range
    current_reference = evaluate_reference(
        observation,
        definition,
        current_context["confirmed"],
        current_context["specimen"],
    )
    current_interpretation = observation.interpretation
    current_range = observation.reference_range
    observation.interpretation = stored_interpretation
    observation.reference_range = stored_range
    if (
        current_reference != provenance.get("reference_provenance")
        or current_interpretation != stored_interpretation
        or current_range != stored_range
    ):
        raise conflict(
            "reference_context_changed",
            "Reference assessment changed after draft review.",
        )


def _read_value(observation, provenance):
    stored = observation.value.get("value")
    if observation.value_type == "quantity":
        return {
            "kind": "quantity",
            "input": provenance.get("input"),
            "stored": str(stored),
            "unit": observation.value.get("unit"),
        }
    if observation.value_type in {"decimal", "integer"}:
        return {
            "kind": observation.value_type,
            "input": provenance.get("input"),
            "stored": str(stored),
        }
    return {"kind": observation.value_type, "value": stored}


def _result_meta(meta, *, observation, row, source, reference, correction_reason):
    updated = dict(meta or {})
    namespace = dict(updated.get("care_suriname", {}))
    provenance = {
        "row_id": str(row.row_id),
        "collection_group_id": str(row.collection_group_id),
        "definition": row.definition.model_dump(mode="json"),
        "source": source.model_dump(mode="json"),
        "collected_at": row.collected_at.model_dump(mode="json"),
        "confirmed_reference_context": row.confirmed_reference_context,
        "specimen": row.specimen,
        "reference_context": _reference_context_snapshot(row, observation),
        "reference_provenance": reference,
    }
    if hasattr(row.value, "input"):
        provenance["input"] = row.value.input
    if correction_reason:
        provenance["correction_reason"] = correction_reason
    namespace[RESULT_META_KEY] = provenance
    updated["care_suriname"] = namespace
    return updated


def patient_birth_context(patient) -> dict:
    """Birth facts that drive age-at-collection.

    ``birth_year`` is recorded only when the exact date is absent, so rows
    stored before year-of-birth support keep an identical context when a date
    of birth exists, and a later added or changed year of birth is detected.
    """
    context = {
        "birth_date": (
            patient.date_of_birth.isoformat() if patient.date_of_birth else None
        ),
    }
    if patient.date_of_birth is None:
        context["birth_year"] = patient.year_of_birth
    return context


def _reference_context_snapshot(row, observation):
    return {
        "collected_at": row.collected_at.model_dump(mode="json"),
        **patient_birth_context(observation.patient),
        "recorded_sex": observation.patient.gender or None,
        "specimen": row.specimen,
        "confirmed": row.confirmed_reference_context,
    }
