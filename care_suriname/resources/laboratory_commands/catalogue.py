from care.emr.models.observation_definition import ObservationDefinition
from care_suriname.resources.laboratory_commands.errors import (
    conflict,
    invalid_result,
)
from care_suriname.resources.laboratory_commands.hashing import canonical_sha256
from care_suriname.resources.laboratory_reference import get_reference_metadata

CATALOGUE_CONTRACT = "care-suriname-laboratory-catalogue-v1"
REFERENCE_META_PATH = ("care_suriname", "laboratory_reference")
LOINC_SYSTEM = "http://loinc.org"


def definition_fingerprint(definition: ObservationDefinition) -> str:
    return canonical_sha256(
        {
            "body_site": definition.body_site,
            "category": definition.category,
            "code": definition.code,
            "component": definition.component,
            "contract": "laboratory-definition-fingerprint-v1",
            "derived_from_uri": definition.derived_from_uri,
            "deleted": definition.deleted,
            "method": definition.method,
            "permitted_data_type": definition.permitted_data_type,
            "permitted_unit": definition.permitted_unit,
            "qualified_ranges": definition.qualified_ranges,
            "configured_reference": _reference_provenance(definition.meta),
            "reference_overlay": governed_reference(definition),
            "status": definition.status,
            "title": definition.title,
        }
    )


def serialize_definition(definition: ObservationDefinition) -> dict:
    return {
        "id": str(definition.external_id),
        "slug": definition.slug,
        "version": definition.version,
        "version_kind": "database_revision",
        "fingerprint": definition_fingerprint(definition),
        "title": definition.title,
        "status": definition.status,
        "category": definition.category,
        "code": definition.code,
        "permitted_data_type": definition.permitted_data_type,
        "permitted_unit": definition.permitted_unit,
        "body_site": definition.body_site,
        "method": definition.method,
        "qualified_ranges": definition.qualified_ranges or [],
        "reference": governed_reference(definition),
    }


def catalogue_payload(facility) -> dict:
    definitions = ObservationDefinition.objects.filter(
        facility=facility,
        status="active",
        category="laboratory",
    ).order_by("title", "external_id")
    results = [serialize_definition(item) for item in definitions]
    return {
        "contract": CATALOGUE_CONTRACT,
        "facility": str(facility.external_id),
        "count": len(results),
        "results": results,
    }


def validate_definition(definition, identity, value):
    validate_definition_identity(definition, identity)
    if definition.permitted_data_type != value.kind:
        raise invalid_result("Result value kind does not match the definition.")
    if value.kind == "quantity":
        permitted = definition.permitted_unit or {}
        if (
            permitted.get("system") != value.unit.system
            or permitted.get("code") != value.unit.code
        ):
            raise invalid_result("Result unit does not match the definition.")


def validate_definition_identity(definition, identity):
    fingerprint = (
        identity.fingerprint
        if hasattr(identity, "fingerprint")
        else identity["fingerprint"]
    )
    external_id = identity.id if hasattr(identity, "id") else identity["id"]
    slug = identity.slug if hasattr(identity, "slug") else identity["slug"]
    version = identity.version if hasattr(identity, "version") else identity["version"]
    if (
        str(definition.external_id) != str(external_id)
        or definition.slug != slug
        or definition.version != version
        or definition_fingerprint(definition) != fingerprint
        or definition.status != "active"
        or definition.deleted
        or definition.category != "laboratory"
    ):
        raise conflict(
            "catalogue_changed",
            "The laboratory definition no longer matches the selected catalogue.",
        )


def _reference_provenance(meta: dict | None):
    current = meta or {}
    for key in REFERENCE_META_PATH:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def governed_reference(definition):
    code = definition.code or {}
    unit = definition.permitted_unit or {}
    loinc = code.get("code", "") if code.get("system") == LOINC_SYSTEM else ""
    return get_reference_metadata(
        loinc,
        unit.get("system", ""),
        unit.get("code"),
    )
