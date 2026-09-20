from datetime import datetime
from re import fullmatch
from typing import Annotated, Any, Literal

from pydantic import UUID4, Field, StrictBool, field_validator, model_validator

from care.emr.resources.common.coding import Coding
from care_suriname.resources.laboratory_commands.specs import (
    LABORATORY_COMMAND_CONTRACT,
    BooleanValue,
    CollectionTime,
    ConfirmedReferenceContext,
    DecimalValue,
    ExternalLaboratorySource,
    IntegerValue,
    LaboratoryDefinitionIdentity,
    NonblankLabel,
    NumericValue,
    QuantityValue,
    StrictPositiveInt,
    StrictSpec,
    StringValue,
    parse_laboratory_decimal,
    parse_laboratory_integer,
)


class StoredNumericValue(NumericValue):
    stored: str

    @field_validator("stored")
    @classmethod
    def validate_stored(cls, value: str) -> str:
        if fullmatch(r"[+-]?\d+(?:\.\d+)?", value) is None:
            raise ValueError("stored numeric value must use canonical dot notation")
        parse_laboratory_decimal(value)
        return value


class StoredQuantityValue(StoredNumericValue, QuantityValue):
    pass


class StoredDecimalValue(StoredNumericValue, DecimalValue):
    pass


class StoredIntegerValue(IntegerValue):
    stored: str

    @field_validator("stored")
    @classmethod
    def validate_stored(cls, value: str) -> str:
        parse_laboratory_integer(value)
        return value


StoredLaboratoryValue = Annotated[
    StoredQuantityValue
    | StoredDecimalValue
    | StoredIntegerValue
    | StringValue
    | BooleanValue,
    Field(discriminator="kind"),
]


class LaboratoryResultRead(StrictSpec):
    row_id: UUID4
    collection_group_id: UUID4
    observation_id: UUID4
    status: Literal["final", "amended", "entered_in_error"]
    definition: LaboratoryDefinitionIdentity
    code: Coding
    method: Coding | None
    body_site: Coding | None
    collected_at: CollectionTime
    specimen: Literal["blood", "plasma", "serum", "whole_blood"] | None
    confirmed_reference_context: list[ConfirmedReferenceContext]
    value: StoredLaboratoryValue
    interpretation: dict[str, Any]
    reference_range: list[dict[str, Any]]
    reference_provenance: dict[str, Any] | None
    parent_observation_id: UUID4 | None
    correction_reason: str | None

    @model_validator(mode="after")
    def require_stable_row_identity(self):
        if self.row_id != self.observation_id:
            raise ValueError("row_id must equal the native observation external ID")
        return self


class LaboratoryReportAudit(StrictSpec):
    class Actor(StrictSpec):
        id: UUID4
        display: NonblankLabel

    created_by: Actor
    created_at: datetime
    finalized_by: Actor | None
    finalized_at: datetime | None
    latest_corrected_by: Actor | None
    latest_corrected_at: datetime | None


class LaboratoryReportRead(StrictSpec):
    id: UUID4
    service_request_id: UUID4
    patient: UUID4
    facility: UUID4
    encounter: UUID4
    status: Literal["preliminary", "final"]
    service_request_status: Literal["draft", "completed"]
    source: ExternalLaboratorySource
    audit: LaboratoryReportAudit
    rows: list[LaboratoryResultRead]


class LaboratoryCommandResponse(StrictSpec):
    contract: Literal[LABORATORY_COMMAND_CONTRACT]
    client_request_id: UUID4
    replayed: StrictBool
    command_result_version: StrictPositiveInt
    report: LaboratoryReportRead


class LaboratoryReportResponse(StrictSpec):
    contract: Literal[LABORATORY_COMMAND_CONTRACT]
    command_result_version: StrictPositiveInt
    report: LaboratoryReportRead
