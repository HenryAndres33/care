from datetime import datetime
from decimal import Decimal
from re import fullmatch
from typing import Annotated, Any, Literal

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from care.emr.resources.common.coding import Coding

LABORATORY_COMMAND_CONTRACT = "care-suriname-laboratory-command-v1"
MAX_NUMERIC_DIGITS = 20
MAX_DECIMAL_PLACES = 6

NonblankLabel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
NonblankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]
NumericInput = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
StrictPositiveInt = Annotated[int, Field(strict=True, gt=0)]
StrictZero = Annotated[int, Field(strict=True, ge=0, le=0)]
CARE_DECIMAL_ADAPTER = TypeAdapter(
    Annotated[
        Decimal,
        Field(
            max_digits=MAX_NUMERIC_DIGITS,
            decimal_places=MAX_DECIMAL_PLACES,
        ),
    ]
)


class StrictSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LaboratoryDefinitionIdentity(StrictSpec):
    id: UUID4
    slug: NonblankLabel
    version: StrictPositiveInt
    fingerprint: Sha256


class KnownCollectionTime(StrictSpec):
    kind: Literal["known"]
    value: datetime

    @field_validator("value", mode="before")
    @classmethod
    def reject_numeric_datetime(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("known collection time must be an ISO-8601 datetime")
        return value

    @field_validator("value")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("known collection time must include a timezone")
        if value > datetime.now(value.tzinfo):
            raise ValueError("known collection time cannot be in the future")
        return value


class UnknownCollectionTime(StrictSpec):
    kind: Literal["unknown"]


CollectionTime = Annotated[
    KnownCollectionTime | UnknownCollectionTime,
    Field(discriminator="kind"),
]


class ExternalLaboratorySource(StrictSpec):
    kind: Literal["external_lab"]
    label: NonblankLabel


def parse_laboratory_decimal(value: str) -> Decimal:
    if fullmatch(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)", value) is None:
        raise ValueError("numeric input must be a plain decimal using comma or dot")
    parsed = Decimal(value.replace(",", "."))
    try:
        return CARE_DECIMAL_ADAPTER.validate_python(parsed)
    except ValueError as error:
        raise ValueError(
            "numeric input exceeds CARE's 20-digit/6-decimal precision"
        ) from error


def parse_laboratory_integer(value: str) -> int:
    if fullmatch(r"[+-]?\d+", value) is None:
        raise ValueError("integer input must contain whole decimal digits")
    parsed = Decimal(value)
    if len(parsed.as_tuple().digits) > MAX_NUMERIC_DIGITS:
        message = f"integer input exceeds {MAX_NUMERIC_DIGITS} digits"
        raise ValueError(message)
    return int(parsed)


class NumericValue(StrictSpec):
    input: NumericInput


class QuantityValue(NumericValue):
    kind: Literal["quantity"]
    unit: Coding

    @field_validator("input")
    @classmethod
    def validate_input(cls, value: str) -> str:
        parse_laboratory_decimal(value)
        return value

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: Coding) -> Coding:
        if not value.system or not value.system.strip() or not value.code.strip():
            raise ValueError("quantity unit requires nonblank system and code")
        return value


class DecimalValue(NumericValue):
    kind: Literal["decimal"]

    @field_validator("input")
    @classmethod
    def validate_input(cls, value: str) -> str:
        parse_laboratory_decimal(value)
        return value


class IntegerValue(NumericValue):
    kind: Literal["integer"]

    @field_validator("input")
    @classmethod
    def validate_input(cls, value: str) -> str:
        parse_laboratory_integer(value)
        return value


class StringValue(StrictSpec):
    kind: Literal["string"]
    value: NonblankText


class BooleanValue(StrictSpec):
    kind: Literal["boolean"]
    value: StrictBool


LaboratoryValue = Annotated[
    QuantityValue | DecimalValue | IntegerValue | StringValue | BooleanValue,
    Field(discriminator="kind"),
]

ConfirmedReferenceContext = Literal[
    "fasting_confirmed",
    "nonpregnant_confirmed",
    "no_anticoagulant",
    "no_renal_failure",
    "no_anemia",
    "no_hemoglobinopathy",
    "no_hiv",
    "stable_red_cell_turnover",
]


class LaboratoryResultRow(StrictSpec):
    row_id: UUID4
    collection_group_id: UUID4
    definition: LaboratoryDefinitionIdentity
    collected_at: CollectionTime
    specimen: Literal["blood", "plasma", "serum", "whole_blood"] | None = None
    confirmed_reference_context: list[ConfirmedReferenceContext] = Field(
        default_factory=list
    )
    value: LaboratoryValue

    @field_validator("confirmed_reference_context")
    @classmethod
    def require_distinct_context(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("confirmed reference context values must be unique")
        return sorted(value)


class CommandContext(StrictSpec):
    contract: Literal[LABORATORY_COMMAND_CONTRACT]
    client_request_id: UUID4
    patient: UUID4
    facility: UUID4
    encounter: UUID4
    service_request_id: UUID4
    report_id: UUID4


class DraftSnapshot(CommandContext):
    source: ExternalLaboratorySource
    rows: list[LaboratoryResultRow] = Field(min_length=1)

    @model_validator(mode="after")
    def require_distinct_rows(self):
        row_ids = [row.row_id for row in self.rows]
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("row_id values must be unique")
        _validate_collection_groups(self.rows)
        return self


class CreateDraftCommand(DraftSnapshot):
    action: Literal["create_draft"]
    expected_version: StrictZero


class UpdateDraftCommand(DraftSnapshot):
    action: Literal["update_draft"]
    expected_version: StrictPositiveInt


class FinalizeCommand(CommandContext):
    action: Literal["finalize"]
    expected_version: StrictPositiveInt


class CorrectionReplacement(StrictSpec):
    replaces_observation_id: UUID4
    row: LaboratoryResultRow


class CorrectCommand(CommandContext):
    action: Literal["correct"]
    expected_version: StrictPositiveInt
    reason: NonblankText
    replacements: list[CorrectionReplacement] = Field(min_length=1)

    @model_validator(mode="after")
    def require_distinct_replacements(self):
        targets = [item.replaces_observation_id for item in self.replacements]
        rows = [item.row.row_id for item in self.replacements]
        if (
            len(targets) != len(set(targets))
            or len(rows) != len(set(rows))
            or set(targets) & set(rows)
        ):
            raise ValueError(
                "correction target and row IDs must be unique and distinct"
            )
        _validate_collection_groups([item.row for item in self.replacements])
        return self


def _validate_collection_groups(rows):
    groups = {}
    for row in rows:
        signature = (
            row.collected_at.model_dump_json(),
            tuple(row.confirmed_reference_context),
        )
        previous = groups.setdefault(row.collection_group_id, signature)
        if previous != signature:
            raise ValueError(
                "rows in one collection group must share date and reference context"
            )


LaboratoryCommand = Annotated[
    CreateDraftCommand | UpdateDraftCommand | FinalizeCommand | CorrectCommand,
    Field(discriminator="action"),
]
LABORATORY_COMMAND_ADAPTER = TypeAdapter(LaboratoryCommand)


def validate_laboratory_command(value: Any) -> LaboratoryCommand:
    return LABORATORY_COMMAND_ADAPTER.validate_python(value)
