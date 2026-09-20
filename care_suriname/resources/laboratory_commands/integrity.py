from care.emr.models.observation import Observation
from care.emr.models.observation_definition import ObservationDefinition
from care_suriname.resources.laboratory_commands.catalogue import (
    validate_definition_identity,
)
from care_suriname.resources.laboratory_commands.errors import conflict
from care_suriname.resources.laboratory_commands.locking import lock_observations
from care_suriname.resources.laboratory_commands.observations import (
    result_provenance,
    validate_reference_snapshot,
)


def prelock_report_resources(report, command):
    definition_ids = set(
        Observation._base_manager.filter(  # noqa: SLF001
            diagnostic_report=report
        ).values_list("observation_definition__external_id", flat=True)
    )
    rows = getattr(command, "rows", None) or [
        item.row for item in getattr(command, "replacements", [])
    ]
    definition_ids.update(row.definition.id for row in rows)
    list(
        ObservationDefinition._base_manager.select_for_update(  # noqa: SLF001
            of=("self",)
        )
        .filter(external_id__in=definition_ids)
        .order_by("id")
    )
    lock_observations(report)


def lock_stored_definitions(report):
    definition_ids = set(
        Observation._base_manager.filter(  # noqa: SLF001
            diagnostic_report=report,
            deleted=False,
        ).values_list("observation_definition_id", flat=True)
    )
    return {
        item.external_id: item
        for item in ObservationDefinition._base_manager.select_for_update(  # noqa: SLF001
            of=("self",)
        )
        .filter(id__in=definition_ids)
        .order_by("id")
    }


def validate_stored_definitions(report, observations, definitions):
    for observation in observations:
        identity = result_provenance(observation).get("definition")
        definition = definitions.get(observation.observation_definition.external_id)
        if (
            not identity
            or not definition
            or definition.facility_id != report.facility_id
        ):
            raise conflict("catalogue_changed", "Stored definition is unavailable.")
        validate_definition_identity(definition, identity)
        validate_reference_snapshot(observation, definition)


def validate_corrected_collection_groups(existing, command):
    targets = {item.replaces_observation_id for item in command.replacements}
    signatures = {}
    for observation in existing.values():
        if (
            observation.deleted
            or observation.status == "entered_in_error"
            or observation.external_id in targets
        ):
            continue
        provenance = result_provenance(observation)
        _add_group_signature(
            signatures,
            provenance.get("collection_group_id"),
            provenance.get("collected_at"),
            provenance.get("confirmed_reference_context", []),
        )
    for replacement in command.replacements:
        row = replacement.row
        _add_group_signature(
            signatures,
            str(row.collection_group_id),
            row.collected_at.model_dump(mode="json"),
            row.confirmed_reference_context,
        )


def _add_group_signature(signatures, group_id, collected_at, confirmed):
    signature = (str(collected_at), tuple(confirmed))
    previous = signatures.setdefault(group_id, signature)
    if previous != signature:
        raise conflict(
            "collection_group_conflict",
            "Rows in one collection group have conflicting context.",
        )
