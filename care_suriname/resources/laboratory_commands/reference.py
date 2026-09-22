import json
from dataclasses import asdict
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder

from care_suriname.resources.laboratory_commands.catalogue import LOINC_SYSTEM
from care_suriname.resources.laboratory_reference import (
    InterpretedReference,
    get_reference_metadata,
    interpret_governed_laboratory_reference,
)


def evaluate_reference(observation, definition, context_flags, specimen):
    code = definition.code or {}
    unit = definition.permitted_unit or {}
    loinc = code.get("code", "") if code.get("system") == LOINC_SYSTEM else ""
    metadata = (
        get_reference_metadata(
            loinc,
            unit.get("system", ""),
            unit.get("code"),
        )
        or {}
    )
    if observation.value_type not in {"quantity", "decimal", "integer"}:
        observation.interpretation = {}
        observation.reference_range = []
        return None
    methods = {
        rule.get("method") for rule in metadata.get("rules", []) if rule.get("method")
    }
    method = None
    if methods == {"unspecified"}:
        method = "unspecified"
    elif definition.method:
        method = definition.method.get("code") or definition.method.get("display")
    result = interpret_governed_laboratory_reference(
        loinc=loinc,
        unit_system=unit.get("system", ""),
        unit_code=unit.get("code"),
        value=Decimal(str(observation.value.get("value"))),
        collected_at=observation.effective_datetime,
        birth_date=observation.patient.date_of_birth,
        birth_year=observation.patient.year_of_birth,
        sex=observation.patient.gender or None,
        specimen=specimen,
        method=method,
        context_flags=frozenset(context_flags),
    )
    snapshot = json.loads(json.dumps(asdict(result), cls=DjangoJSONEncoder))
    snapshot["catalogue_fingerprint"] = metadata.get("fingerprint")
    if isinstance(result, InterpretedReference):
        coding = {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
            "code": {"low": "L", "normal": "N", "high": "H"}[result.category],
            "display": result.category.capitalize(),
        }
        observation.interpretation = {
            "code": coding,
            "display": result.category.capitalize(),
            "highlight": result.category != "normal",
        }
        observation.reference_range = [_selected_range(snapshot, unit.get("code"))]
    else:
        observation.interpretation = {}
        observation.reference_range = []
    return snapshot


def _selected_range(snapshot, unit_code):
    rule = snapshot["rule"]
    lower = rule.get("lower")
    upper = rule.get("upper")
    return {
        "min": lower.get("value") if lower else None,
        "min_inclusive": lower.get("inclusive") if lower else None,
        "max": upper.get("value") if upper else None,
        "max_inclusive": upper.get("inclusive") if upper else None,
        "unit": unit_code,
        "interpretation": "normal",
        "value": _range_display(lower, upper),
        "rule_id": rule["id"],
    }


def _range_display(lower, upper):
    parts = []
    if lower:
        parts.append(f"{'>=' if lower.get('inclusive') else '>'}{lower['value']}")
    if upper:
        parts.append(f"{'<=' if upper.get('inclusive') else '<'}{upper['value']}")
    return " to ".join(parts)
