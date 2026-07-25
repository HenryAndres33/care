from jsonschema import ValidationError as JSONSchemaValidationError
from jsonschema import validate

from care.emr.extensions.base import ExtensionResource, PlugExtension
from care.emr.registries.extensions.registry import ExtensionRegistry

ADMISSION_NOTE_EXTENSION_NAME = "care_suriname_admission_note"


class EncounterAdmissionNoteExtension(PlugExtension):
    """Native CARE extension for a concise free-text admission note."""

    resource_type = ExtensionResource.encounter
    extension_name = ADMISSION_NOTE_EXTENSION_NAME
    extension_version = "1.0.0"

    # Governance metadata is deliberately colocated with the provisioned schema.
    governance_owner = "CARE Suriname Clinical Governance"
    retention_policy = "Retain and dispose with the owning encounter record."
    migration_path = (
        "Keep the key stable; version schema changes and migrate Encounter.extensions "
        "with a reversible Django data migration before enforcing a new version."
    )

    write_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["text"],
        "properties": {
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4000,
            }
        },
    }
    read_schema = write_schema
    retrieve_schema = write_schema

    def validate(self, data, resource=None):
        try:
            validate(instance=data, schema=self.write_schema)
        except JSONSchemaValidationError as error:
            raise ValueError("Invalid admission note extension") from error
        if not data["text"].strip():
            raise ValueError("Admission note must contain visible text")
        return data


ExtensionRegistry.register(EncounterAdmissionNoteExtension())
