import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from care_suriname.resources.laboratory_reference.defaults import (
    TEXTBOOK_REFERENCE_CATALOGUE,
    TEXTBOOK_REFERENCE_SOURCES,
)
from care_suriname.resources.laboratory_reference.specs import (
    ReferenceCatalogueEntry,
    TextbookReferenceRule,
    accepted_specimens,
)

CATALOGUE_VERSION = "general-academic-adult-v2"
METADATA_NAMESPACE = "care_suriname"
METADATA_KEY = "laboratory_reference"


@dataclass(frozen=True)
class DefinitionSnapshot:
    id: str
    slug: str
    version: int
    loinc: str
    unit_system: str
    unit_code: str | None
    qualified_ranges: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ConfigurationAction:
    action: Literal["blocked", "create_successor", "noop"]
    definition: DefinitionSnapshot
    desired_ranges: tuple[dict[str, Any], ...]
    desired_metadata: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class ConfigurationPlan:
    catalogue_version: str
    actions: tuple[ConfigurationAction, ...]
    missing_definitions: tuple[dict[str, Any], ...]


def build_reference_metadata(entry: ReferenceCatalogueEntry) -> dict[str, Any]:
    source_ids = sorted(
        {rule.source_id for rule in entry.rules} | set(entry.provenance_source_ids)
    )
    governed = {
        "catalogue_version": entry.catalogue_version,
        "loinc": entry.loinc,
        "unit_system": entry.unit_system,
        "unit_code": entry.unit_code,
        "rules": [_rule_metadata(rule) for rule in entry.rules],
        "sources": [asdict(TEXTBOOK_REFERENCE_SOURCES[id_]) for id_ in source_ids],
        "unavailable_reason": entry.unavailable_reason,
    }
    governed = json.loads(json.dumps(governed))
    canonical = json.dumps(governed, sort_keys=True, separators=(",", ":"))
    return {**governed, "fingerprint": hashlib.sha256(canonical.encode()).hexdigest()}


def get_reference_metadata(
    loinc: str,
    unit_system: str,
    unit_code: str | None,
) -> dict[str, Any] | None:
    entry = next(
        (
            item
            for item in TEXTBOOK_REFERENCE_CATALOGUE
            if item.loinc == loinc
            and item.unit_system == unit_system
            and item.unit_code == unit_code
        ),
        None,
    )
    return build_reference_metadata(entry) if entry is not None else None


def build_configuration_plan(
    catalogue: tuple[ReferenceCatalogueEntry, ...],
    definitions: tuple[DefinitionSnapshot, ...],
    *,
    collection_age_enforced: bool = False,
) -> ConfigurationPlan:
    by_key = {
        (item.loinc, item.unit_system, item.unit_code): item for item in catalogue
    }
    actions = []
    for definition in definitions:
        entry = by_key.get(
            (definition.loinc, definition.unit_system, definition.unit_code)
        )
        if entry is None or not entry.rules:
            continue
        desired_ranges, reason = _compile_native_ranges(
            entry,
            collection_age_enforced=collection_age_enforced,
        )
        desired_metadata = build_reference_metadata(entry)
        current_metadata = (
            definition.metadata.get(METADATA_NAMESPACE, {}).get(METADATA_KEY)
            if isinstance(definition.metadata.get(METADATA_NAMESPACE), dict)
            else None
        )
        if desired_ranges is None:
            action = "blocked"
        elif (
            definition.qualified_ranges == desired_ranges
            and current_metadata == desired_metadata
        ):
            action = "noop"
            reason = "exact governed payload already present"
        else:
            action = "create_successor"
        actions.append(
            ConfigurationAction(
                action=action,
                definition=definition,
                desired_ranges=desired_ranges or (),
                desired_metadata=desired_metadata,
                reason=reason,
            )
        )
    present_keys = {
        (item.loinc, item.unit_system, item.unit_code) for item in definitions
    }
    missing_definitions = tuple(
        {
            "action": "create_definition",
            "loinc": entry.loinc,
            "unit_system": entry.unit_system,
            "unit_code": entry.unit_code,
            "title": "Hemoglobine (mmol/L)",
            "slug_value": "hemoglobine-mmol-l-general-academic-adult-v1",
            "permitted_data_type": "quantity",
            "reference": build_reference_metadata(entry),
            "warning": "Separate definition; never convert a patient result.",
        }
        for entry in catalogue
        if (entry.loinc, entry.unit_system, entry.unit_code)
        == ("59260-0", "http://unitsofmeasure.org", "mmol/L")
        and (entry.loinc, entry.unit_system, entry.unit_code) not in present_keys
    )
    return ConfigurationPlan(
        CATALOGUE_VERSION,
        tuple(actions),
        missing_definitions,
    )


def build_rollback_plan(plan: ConfigurationPlan) -> tuple[dict[str, Any], ...]:
    successor_rollbacks = tuple(
        {
            "action": "retire_successor_and_restore_group_membership",
            "predecessor_id": action.definition.id,
            "predecessor_slug": action.definition.slug,
            "predecessor_version": action.definition.version,
        }
        for action in reversed(plan.actions)
        if action.action == "create_successor"
    )
    creation_rollbacks = tuple(
        {
            "action": "retire_created_definition",
            "loinc": creation["loinc"],
            "unit_system": creation["unit_system"],
            "unit_code": creation["unit_code"],
            "slug_value": creation["slug_value"],
        }
        for creation in reversed(plan.missing_definitions)
    )
    return successor_rollbacks + creation_rollbacks


def _compile_native_ranges(
    entry: ReferenceCatalogueEntry,
    *,
    collection_age_enforced: bool,
) -> tuple[tuple[dict[str, Any], ...] | None, str]:
    if not collection_age_enforced:
        return None, "native CARE evaluates patient age now, not at collection"
    for rule in entry.rules:
        if (
            (rule.lower is not None and not rule.lower.inclusive)
            or (rule.upper is not None and not rule.upper.inclusive)
            or rule.applicability.required_context
            or (
                rule.applicability.nonpregnant_required_for_female
                and rule.applicability.sex in ("any", "female")
            )
            or rule.method != "unspecified"
        ):
            return None, "rule cannot be represented exactly by native CARE ranges"
    return (
        tuple(_native_rule(rule) for rule in entry.rules),
        "create a new definition version; never edit a used definition in place",
    )


def _native_rule(rule: TextbookReferenceRule) -> dict[str, Any]:
    conditions: list[dict[str, Any]] = [
        {
            "metric": "patient_age",
            "operation": "in_range",
            "value": {
                "min": rule.applicability.minimum_age_years,
                "max": rule.applicability.maximum_age_years or 150,
                "value_type": "years",
            },
        }
    ]
    if rule.applicability.sex != "any":
        conditions.append(
            {
                "metric": "patient_gender",
                "operation": "equality",
                "value": rule.applicability.sex,
            }
        )
    numeric_range: dict[str, Any] = {
        "interpretation": {
            "display": "Normal (general academic adult reference)",
        }
    }
    if rule.lower is not None:
        numeric_range["min"] = str(rule.lower.value)
    if rule.upper is not None:
        numeric_range["max"] = str(rule.upper.value)
    return {
        "title": "General academic adult reference; not local-laboratory validated",
        "conditions": conditions,
        "ranges": [numeric_range],
        "default_interpretation": {
            "display": "Outside general academic adult reference",
        },
    }


def _rule_metadata(rule: TextbookReferenceRule) -> dict[str, Any]:
    data = asdict(rule)
    data["accepted_specimens"] = list(accepted_specimens(rule.specimen))
    for name in ("lower", "upper"):
        bound = data[name]
        if bound is not None:
            bound["value"] = str(bound["value"])
    return data
