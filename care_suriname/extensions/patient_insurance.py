"""Patient insurance as a native CARE patient extension (see PATIENT_INSURANCE.md)."""

from jsonschema import ValidationError as JSONSchemaValidationError
from jsonschema import validate

from care.emr.extensions.base import ExtensionResource, PlugExtension
from care.emr.registries.extensions.registry import ExtensionRegistry
from care_suriname.extensions.patient_insurance_catalog import INSURER_GROUPS

PATIENT_INSURANCE_EXTENSION_NAME = "care_suriname_insurance"
SCHEMA_VERSION = "1"
POLICY_NUMBER_MAX_LENGTH = 64


def _build_write_schema():
    properties = {
        # A default makes CARE's form send this extension even when untouched,
        # so its required fields are enforced at registration.
        "version": {"const": SCHEMA_VERSION, "default": SCHEMA_VERSION},
        "insurer": {
            "type": "string",
            "title": "Verzekering",
            "enum": [group.name for group in INSURER_GROUPS],
        },
    }
    rules = []
    for group in INSURER_GROUPS:
        if not group.plan_field:
            continue
        properties[group.plan_field] = {
            "type": "string",
            "title": f"Pakket {group.name}",
            "enum": list(group.plans),
            # "" default: CARE's form otherwise fills a newly shown field with {}.
            "default": "",
            "x-ui": {"metadata": {"insurer": group.name}},
        }
        rules.append(
            {
                "if": {
                    "properties": {"insurer": {"const": group.name}},
                    "required": ["insurer"],
                },
                "then": {"required": [group.plan_field, "policy_number"]},
            }
        )
    properties["policy_number"] = {
        "type": "string",
        "title": "Verzekeringsnummer",
        "default": "",
        "minLength": 1,
        "maxLength": POLICY_NUMBER_MAX_LENGTH,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["version", "insurer"],
        "properties": properties,
        "allOf": rules,
    }


def normalize_insurance(data):
    """Trim text and drop blank values; CARE's form sends cleared fields as ''."""
    if not isinstance(data, dict):
        raise ValueError("Invalid insurance extension")
    normalized = {}
    for key, raw in data.items():
        value = raw.strip() if isinstance(raw, str) else raw
        if value in (None, ""):
            continue
        normalized[key] = value
    return normalized


def _group(name):
    return next((group for group in INSURER_GROUPS if group.name == name), None)


def _legacy_summary(extensions):
    # Free text the urology chart stored before this extension (25-30 Sep 2026).
    profile = ((extensions or {}).get("core") or {}).get("urology_patient_profile_v1")
    value = profile.get("insurance_summary") if isinstance(profile, dict) else None
    if not isinstance(value, str):
        return None
    return value.strip() or None


def insurance_display(extensions) -> str | None:
    """One printable line, e.g. 'SURVAM · PZS-basis · nr. 12345'."""
    data = (extensions or {}).get(PATIENT_INSURANCE_EXTENSION_NAME)
    group = _group(data.get("insurer")) if isinstance(data, dict) else None
    if group is None:
        return _legacy_summary(extensions)
    parts = [group.name]
    if group.plan_field and data.get(group.plan_field):
        parts.append(str(data[group.plan_field]))
    if data.get("policy_number"):
        parts.append(f"nr. {data['policy_number']}")
    return " · ".join(parts)


class PatientInsuranceExtension(PlugExtension):
    """Insurer, plan and insurance number of a patient."""

    resource_type = ExtensionResource.patient
    extension_name = PATIENT_INSURANCE_EXTENSION_NAME
    extension_version = "1.0.0"

    governance_owner = "CARE Suriname Clinical Governance"
    retention_policy = "Retain and dispose with the owning patient record."
    migration_path = (
        "Keep the key and plan_field names stable; add plans to the catalogue. "
        "A changed shape gets a new version and a reversible data migration."
    )

    write_schema = _build_write_schema()
    read_schema = write_schema
    retrieve_schema = write_schema

    def validate(self, data, resource=None):
        data = normalize_insurance(data)
        # CARE echoes an empty object for patients registered before this
        # extension existed; that stays "not recorded" instead of blocking
        # every later edit of those patients.
        if not data:
            return data
        try:
            validate(instance=data, schema=self.write_schema)
        except JSONSchemaValidationError as error:
            raise ValueError("Invalid insurance extension") from error
        group = _group(data["insurer"])
        for other in INSURER_GROUPS:
            if other is not group and other.plan_field in data:
                raise ValueError("Insurance plan does not belong to the insurer")
        if not group.plan_field and "policy_number" in data:
            raise ValueError("Eigen rekening has no insurance number")
        return data

    def serialize_extensions(self, data, resource=None):
        return normalize_insurance(data)


ExtensionRegistry.register(PatientInsuranceExtension())
