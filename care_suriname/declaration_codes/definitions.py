"""The declaration code lists as native CARE charge item definitions.

One ChargeItemDefinition per code, without price components: CARE records
the code, never a price or an invoice (owner, 30 Sep 2026). The native model
has no code field, so the code travels in a stable slug (`szf-217031`) and in
`derived_from_uri` (the code system plus code), which the frontend also puts
on each ChargeItem's `code`. See DECLARATION_CODES.md.
"""

from typing import NamedTuple

from care.emr.models.charge_item_definition import ChargeItemDefinition
from care_suriname.declaration_codes.catalog import CODE_LISTS, CodeList

CODE_SYSTEM_BASE = "https://care-suriname.sr/declaratiecodes"
PURPOSE = "Declaratiecode (alleen code, geen prijs)"


class DeclarationCodeConflictError(Exception):
    """A definition that is not ours already uses one of our slugs."""


class PlannedDefinition(NamedTuple):
    slug_value: str
    title: str
    description: str
    derived_from_uri: str


def code_system(code_list: CodeList) -> str:
    return f"{CODE_SYSTEM_BASE}/{code_list.key}"


def planned_definitions() -> list[PlannedDefinition]:
    planned = []
    for code_list in CODE_LISTS:
        for code, description in code_list.codes:
            planned.append(
                PlannedDefinition(
                    slug_value=f"{code_list.key}-{code}".lower(),
                    title=f"{code} {description}",
                    description=f"Declaratiecode {code_list.label}",
                    derived_from_uri=f"{code_system(code_list)}/{code}",
                )
            )
    return planned


def _is_ours(definition: ChargeItemDefinition) -> bool:
    return (definition.derived_from_uri or "").startswith(f"{CODE_SYSTEM_BASE}/")


def plan_changes(facility) -> dict[str, list]:
    """What an apply would create, update, keep, retire (no writes)."""
    existing = {
        definition.derived_from_uri: definition
        for definition in ChargeItemDefinition.objects.filter(facility=facility)
        if _is_ours(definition)
    }
    ours = {definition.slug for definition in existing.values()}
    clashes = [
        definition.slug
        for definition in ChargeItemDefinition.objects.filter(
            facility=facility,
            slug__in=[
                ChargeItemDefinition.calculate_slug_from_facility(
                    facility.external_id, item.slug_value
                )
                for item in planned_definitions()
            ],
        )
        if definition.slug not in ours
    ]
    if clashes:
        # A hand-made definition with one of our slugs: never overwrite it.
        raise DeclarationCodeConflictError(", ".join(sorted(clashes)))
    create, update, keep = [], [], []
    for item in planned_definitions():
        current = existing.pop(item.derived_from_uri, None)
        if current is None:
            create.append(item)
        elif (
            current.title != item.title
            or current.description != item.description
            or current.status != "active"
            or current.price_components
        ):
            update.append((current, item))
        else:
            keep.append(current)
    retire = [d for d in existing.values() if d.status != "retired"]
    return {"create": create, "keep": keep, "retire": retire, "update": update}


def apply_changes(facility, actor, changes: dict[str, list]) -> None:
    for item in changes["create"]:
        definition = ChargeItemDefinition(
            facility=facility,
            status="active",
            title=item.title,
            slug=ChargeItemDefinition.calculate_slug_from_facility(
                facility.external_id, item.slug_value
            ),
            derived_from_uri=item.derived_from_uri,
            description=item.description,
            purpose=PURPOSE,
            price_components=[],
            can_edit_charge_item=False,
            created_by=actor,
            updated_by=actor,
        )
        definition.save()
    for current, item in changes["update"]:
        current.title = item.title
        current.description = item.description
        current.status = "active"
        current.price_components = []
        current.updated_by = actor
        current.save()
    for current in changes["retire"]:
        current.status = "retired"
        current.updated_by = actor
        current.save()
