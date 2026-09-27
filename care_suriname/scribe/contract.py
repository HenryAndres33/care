"""Request validation and answer filtering for the scribe endpoint.

The model may only answer fields the editor asked about. Choice answers must be
one of the offered options. Anything else is dropped here, so the frontend never
receives a value it did not request.
"""

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

CONTRACT = "care-suriname-scribe-v1"
MAX_FIELDS = 80
MAX_VALUE_CHARS = 2000
MAX_EVIDENCE_CHARS = 300
MAX_OPTION_CHARS = 200
AUDIO_MIME_TYPES = {
    "audio/aac",
    "audio/flac",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
}


class ScribeField(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: Literal["plain", "choice", "multi", "list"]
    options: list[str] = Field(default_factory=list, max_length=60)

    @field_validator("options")
    @classmethod
    def _options(cls, options: list[str]) -> list[str]:
        if any(
            not option.strip() or len(option) > MAX_OPTION_CHARS for option in options
        ):
            raise ValueError("invalid option")
        return options


class ScribeFieldsRequest(BaseModel):
    fields: list[ScribeField] = Field(min_length=1, max_length=MAX_FIELDS)

    @field_validator("fields")
    @classmethod
    def _unique_names(cls, fields: list[ScribeField]) -> list[ScribeField]:
        names = [field.name for field in fields]
        if len(names) != len(set(names)):
            raise ValueError("duplicate field names")
        return fields


class ScribeRequestError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_fields(raw: str | None) -> list[ScribeField]:
    try:
        return ScribeFieldsRequest(fields=json.loads(raw or "")).fields
    except (ValueError, ValidationError, TypeError) as error:
        raise ScribeRequestError("scribe_fields_invalid") from error


def audio_mime_type(content_type: str | None) -> str:
    base = (content_type or "").split(";", 1)[0].strip().lower()
    if base not in AUDIO_MIME_TYPES:
        raise ScribeRequestError("scribe_audio_type_unsupported")
    return base


def _accepted_value(field: ScribeField, value: str) -> str | None:
    if field.type == "plain":
        return value
    if not field.options:
        # A named facility list without inline options cannot be validated here.
        return None
    if field.type in {"choice", "list"}:
        return value if value in field.options else None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if parts and all(part in field.options for part in parts):
        return ", ".join(dict.fromkeys(parts))
    return None


def filter_answers(fields: list[ScribeField], raw_answers: object) -> list[dict]:
    """Keep only well-formed answers to requested fields, first answer per name."""
    by_name = {field.name: field for field in fields}
    accepted: dict[str, dict] = {}
    for item in raw_answers if isinstance(raw_answers, list) else []:
        if not isinstance(item, dict):
            continue
        name, value = item.get("name"), item.get("value")
        if name not in by_name or name in accepted or not isinstance(value, str):
            continue
        value = value.strip()
        if not value or len(value) > MAX_VALUE_CHARS:
            continue
        accepted_value = _accepted_value(by_name[name], value)
        if accepted_value is None:
            continue
        evidence = item.get("evidence")
        accepted[name] = {
            "name": name,
            "value": accepted_value,
            "evidence": evidence.strip()[:MAX_EVIDENCE_CHARS]
            if isinstance(evidence, str)
            else "",
        }
    return list(accepted.values())
