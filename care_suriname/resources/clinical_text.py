import datetime
import enum
from typing import Annotated, Literal

from pydantic import (
    UUID4,
    BaseModel,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)


class ClinicalTextKind(str, enum.Enum):
    template = "template"
    list = "list"
    preset = "preset"
    dictionary = "dictionary"


class ClinicalTextStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    inactive = "inactive"
    archived = "archived"


class SmartTextOptionSpec(BaseModel):
    id: Annotated[str, Field(min_length=1, max_length=128)]
    value: Annotated[str, Field(min_length=1, max_length=500)]
    label: Annotated[str, Field(min_length=1, max_length=500)]
    enabled: bool = True


class TemplatePayloadSpec(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str, Field(max_length=4000)] = ""
    shortcut: Annotated[str, Field(min_length=1, max_length=128)]
    body: Annotated[str, Field(min_length=1, max_length=100_000)]
    scopes: Annotated[list[str], Field(min_length=1, max_length=10)]

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, scopes):
        allowed = {"global", "urology", "medisch-dossier", "letters", "operations"}
        if any(scope not in allowed for scope in scopes):
            raise ValueError("Unsupported SmartText scope")
        return list(dict.fromkeys(scopes))


class ListPayloadSpec(BaseModel):
    label: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str, Field(max_length=4000)] = ""
    options: Annotated[list[SmartTextOptionSpec], Field(max_length=200)]


class PresetPayloadSpec(BaseModel):
    label: Annotated[str, Field(min_length=1, max_length=255)]
    options: Annotated[list[SmartTextOptionSpec], Field(max_length=200)]


class DictionaryPayloadSpec(BaseModel):
    trigger: Annotated[str, Field(min_length=1, max_length=128)]
    expansion: Annotated[str, Field(min_length=1, max_length=100_000)]
    scope: Literal["global", "urology", "medisch-dossier", "operations", "letters"]
    entry_kind: Literal["abbreviation", "smartphrase"] = "abbreviation"
    title: Annotated[str, Field(max_length=255)] = ""
    summary: Annotated[str, Field(max_length=4000)] = ""
    category: Annotated[str, Field(min_length=1, max_length=255)] = "Algemeen"
    language: Literal["nl", "en"] = "nl"
    case_sensitive: bool = False
    enabled: bool = True


PAYLOAD_ADAPTERS = {
    ClinicalTextKind.template: TypeAdapter(TemplatePayloadSpec),
    ClinicalTextKind.list: TypeAdapter(ListPayloadSpec),
    ClinicalTextKind.preset: TypeAdapter(PresetPayloadSpec),
    ClinicalTextKind.dictionary: TypeAdapter(DictionaryPayloadSpec),
}


class ClinicalTextResourceWriteSpec(BaseModel):
    facility: UUID4
    kind: ClinicalTextKind
    key: Annotated[str, Field(min_length=1, max_length=128)]
    label: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str, Field(max_length=4000)] = ""
    status: ClinicalTextStatus = ClinicalTextStatus.active
    payload: dict

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value):
        return value.strip().casefold()

    @model_validator(mode="after")
    def validate_payload(self):
        self.payload = (
            PAYLOAD_ADAPTERS[self.kind].validate_python(self.payload).model_dump()
        )
        if self.kind == ClinicalTextKind.template:
            source_key = self.payload["shortcut"]
        elif self.kind == ClinicalTextKind.dictionary:
            source_key = self.payload["trigger"]
        else:
            source_key = self.key
        if source_key.strip().casefold() != self.key:
            raise ValueError("Resource key does not match payload")
        return self


class ClinicalTextResourceUpdateSpec(ClinicalTextResourceWriteSpec):
    expected_version: Annotated[int, Field(ge=1)]


class ClinicalTextAuditUserSpec(BaseModel):
    id: str
    username: str
    first_name: str = ""
    last_name: str = ""


class ClinicalTextResourceReadSpec(ClinicalTextResourceWriteSpec):
    id: str
    version: int
    created_date: datetime.datetime
    modified_date: datetime.datetime
    created_by: ClinicalTextAuditUserSpec | None = None
    updated_by: ClinicalTextAuditUserSpec | None = None


def serialize_clinical_text_resource(resource):
    def user_payload(user):
        if not user:
            return None
        return {
            "id": str(user.external_id),
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }

    return ClinicalTextResourceReadSpec(
        id=str(resource.external_id),
        facility=resource.facility.external_id,
        kind=resource.kind,
        key=resource.key,
        label=resource.label,
        description=resource.description,
        status=resource.status,
        version=resource.version,
        payload=resource.payload,
        created_date=resource.created_date,
        modified_date=resource.modified_date,
        created_by=user_payload(resource.created_by),
        updated_by=user_payload(resource.updated_by),
    ).model_dump(mode="json")
