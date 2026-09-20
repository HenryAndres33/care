from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from care_suriname.resources.laboratory_reference.configuration import (
    CATALOGUE_VERSION,
)
from care_suriname.resources.laboratory_reference.defaults import (
    TEXTBOOK_REFERENCE_CATALOGUE,
    TEXTBOOK_REFERENCE_SOURCES,
)
from care_suriname.resources.laboratory_reference.specs import (
    InterpretedReference,
    ReferenceBound,
    ReferenceCatalogueEntry,
    ReferenceContext,
    ReferenceResult,
    TextbookReferenceRule,
    UnavailableReference,
    accepted_specimens,
)

ADULT_MINIMUM_AGE = 18
SURINAME_TIME_ZONE = ZoneInfo("America/Paramaribo")


def interpret_textbook_reference(
    entry: ReferenceCatalogueEntry,
    value: Decimal,
    context: ReferenceContext,
) -> ReferenceResult:
    if not entry.rules:
        return UnavailableReference(
            status="unavailable",
            reason="reference_not_configured",
            catalogue_version=entry.catalogue_version,
            loinc=entry.loinc,
            unit_system=entry.unit_system,
            unit_code=entry.unit_code,
            detail=entry.unavailable_reason,
        )
    missing_reason = _missing_context_reason(entry, context)
    if missing_reason is not None:
        return _unavailable(entry, missing_reason)
    applicable = [rule for rule in entry.rules if _applies(rule, context)]
    if len(applicable) != 1:
        reason = "ambiguous_reference" if applicable else "context_not_applicable"
        return _unavailable(entry, reason)
    rule = applicable[0]
    if rule.lower is not None and _is_below(value, rule.lower):
        category = "low"
    elif rule.upper is not None and _is_above(value, rule.upper):
        category = "high"
    else:
        category = "normal"
    return InterpretedReference(
        status="interpreted",
        category=category,
        rule=rule,
        source=TEXTBOOK_REFERENCE_SOURCES[rule.source_id],
        catalogue_version=entry.catalogue_version,
    )


def interpret_governed_laboratory_reference(
    *,
    loinc: str,
    unit_system: str,
    unit_code: str | None,
    value: Decimal,
    collected_at: datetime | None,
    birth_date: date | None,
    sex: str | None,
    specimen: str | None,
    method: str | None,
    context_flags: frozenset[str] = frozenset(),
) -> ReferenceResult:
    entry = next(
        (
            item
            for item in TEXTBOOK_REFERENCE_CATALOGUE
            if item.loinc == loinc
            and item.unit_system == unit_system
            and item.unit_code == unit_code
        ),
        ReferenceCatalogueEntry(
            loinc,
            loinc,
            unit_system,
            unit_code,
            (),
            catalogue_version=CATALOGUE_VERSION,
        ),
    )
    age = _age_at_collection(collected_at, birth_date)
    return interpret_textbook_reference(
        entry,
        value,
        ReferenceContext(
            age_at_collection_years=age,
            sex=sex,
            specimen=specimen,
            method=method,
            context_flags=context_flags,
        ),
    )


def _age_at_collection(
    collected_at: datetime | None,
    birth_date: date | None,
) -> int | None:
    if (
        collected_at is None
        or collected_at.tzinfo is None
        or collected_at.utcoffset() is None
        or birth_date is None
    ):
        return None
    collected_date = collected_at.astimezone(SURINAME_TIME_ZONE).date()
    return (
        collected_date.year
        - birth_date.year
        - (
            (collected_date.month, collected_date.day)
            < (birth_date.month, birth_date.day)
        )
    )


def _unavailable(entry: ReferenceCatalogueEntry, reason):
    return UnavailableReference(
        status="unavailable",
        reason=reason,
        catalogue_version=entry.catalogue_version,
        loinc=entry.loinc,
        unit_system=entry.unit_system,
        unit_code=entry.unit_code,
        detail=entry.unavailable_reason,
    )


def _missing_context_reason(
    entry: ReferenceCatalogueEntry,
    context: ReferenceContext,
):
    if context.age_at_collection_years is None:
        reason = "age_at_collection_required"
    elif context.age_at_collection_years < ADULT_MINIMUM_AGE:
        reason = "pediatric_reference_not_available"
    elif any(
        rule.applicability.sex != "any"
        or rule.applicability.nonpregnant_required_for_female
        for rule in entry.rules
    ) and context.sex not in {"female", "male"}:
        reason = "sex_required"
    elif (
        context.sex == "female"
        and any(
            rule.applicability.nonpregnant_required_for_female for rule in entry.rules
        )
        and "nonpregnant_confirmed" not in context.context_flags
    ):
        reason = "context_required"
    elif context.specimen is None:
        reason = "specimen_required"
    elif not any(
        context.specimen in accepted_specimens(rule.specimen) for rule in entry.rules
    ):
        reason = "specimen_not_applicable"
    elif (
        any(rule.method != "unspecified" for rule in entry.rules) and not context.method
    ):
        reason = "method_required"
    else:
        required = {
            flag for rule in entry.rules for flag in rule.applicability.required_context
        }
        reason = (
            "context_required"
            if required and not required <= context.context_flags
            else None
        )
    return reason


def _applies(rule: TextbookReferenceRule, context: ReferenceContext) -> bool:
    age = context.age_at_collection_years
    maximum = rule.applicability.maximum_age_years
    return (
        age is not None
        and age >= rule.applicability.minimum_age_years
        and (maximum is None or age <= maximum)
        and rule.applicability.sex in ("any", context.sex)
        and not (
            context.sex == "female"
            and rule.applicability.nonpregnant_required_for_female
            and "nonpregnant_confirmed" not in context.context_flags
        )
        and context.specimen in accepted_specimens(rule.specimen)
        and rule.method in ("unspecified", context.method)
        and set(rule.applicability.required_context) <= context.context_flags
    )


def _is_below(value: Decimal, bound: ReferenceBound) -> bool:
    return value < bound.value if bound.inclusive else value <= bound.value


def _is_above(value: Decimal, bound: ReferenceBound) -> bool:
    return value > bound.value if bound.inclusive else value >= bound.value
