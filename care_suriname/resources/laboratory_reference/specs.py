from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

SexBasis = Literal["any", "female", "male"]
Specimen = Literal["blood", "plasma", "serum", "serum_or_plasma", "whole_blood"]
InterpretationCategory = Literal["high", "low", "normal"]
UnavailableReason = Literal[
    "age_at_collection_required",
    "ambiguous_reference",
    "context_not_applicable",
    "context_required",
    "method_required",
    "pediatric_reference_not_available",
    "reference_not_configured",
    "sex_required",
    "specimen_not_applicable",
    "specimen_required",
]


@dataclass(frozen=True)
class ReferenceSource:
    id: str
    publisher: str
    title: str
    url: str
    accessed_on: str
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class ReferenceBound:
    value: Decimal
    inclusive: bool


@dataclass(frozen=True)
class ReferenceApplicability:
    minimum_age_years: int = 18
    maximum_age_years: int | None = None
    sex: SexBasis = "any"
    required_context: tuple[str, ...] = ()
    nonpregnant_required_for_female: bool = True


@dataclass(frozen=True)
class TextbookReferenceRule:
    id: str
    loinc: str
    title: str
    unit_system: str
    unit_code: str | None
    specimen: Specimen
    method: str
    applicability: ReferenceApplicability
    lower: ReferenceBound | None
    upper: ReferenceBound | None
    source_id: str
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReferenceCatalogueEntry:
    loinc: str
    title: str
    unit_system: str
    unit_code: str | None
    rules: tuple[TextbookReferenceRule, ...]
    unavailable_reason: str | None = None
    provenance_source_ids: tuple[str, ...] = ()
    catalogue_version: str = "general-academic-adult-v1"


@dataclass(frozen=True)
class ReferenceContext:
    age_at_collection_years: int | None
    sex: str | None
    specimen: str | None
    method: str | None
    context_flags: frozenset[str] = frozenset()


@dataclass(frozen=True)
class InterpretedReference:
    status: Literal["interpreted"]
    category: InterpretationCategory
    rule: TextbookReferenceRule
    source: ReferenceSource
    catalogue_version: str


@dataclass(frozen=True)
class UnavailableReference:
    status: Literal["unavailable"]
    reason: UnavailableReason
    catalogue_version: str
    loinc: str
    unit_system: str
    unit_code: str | None
    detail: str | None = None


ReferenceResult = InterpretedReference | UnavailableReference


def accepted_specimens(specimen: Specimen) -> tuple[str, ...]:
    """Return the explicit recorded specimen labels accepted by a source label."""
    if specimen == "serum_or_plasma":
        return ("serum", "plasma")
    if specimen == "blood":
        return ("blood", "whole_blood")
    return (specimen,)
