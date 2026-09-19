import re
import unicodedata
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    field_validator,
)

from care_suriname.resources.correspondence import Sha256, canonical_sha256

MAX_LETTER_BODY_CHARACTERS = 100_000
DEFAULT_CORRESPONDENCE_LETTER_PAGE_SIZE = 14
MAX_CORRESPONDENCE_LETTER_PAGE_SIZE = 100
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
LetterBody = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_LETTER_BODY_CHARACTERS),
]


class CorrespondenceLetterContextSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID4
    review_binding: UUID4
    review_hash: Sha256
    patient: UUID4
    encounter: UUID4
    facility: UUID4
    department: UUID4
    author: UUID4


class CreateCorrespondenceLetterSpec(CorrespondenceLetterContextSpec):
    body: LetterBody

    @field_validator("body")
    @classmethod
    def validate_body(cls, value):
        return normalize_letter_body(value)


class ReviseCorrespondenceLetterSpec(CreateCorrespondenceLetterSpec):
    expected_version: PositiveInt


class FinalizeCorrespondenceLetterSpec(CorrespondenceLetterContextSpec):
    expected_version: PositiveInt


class CorrespondenceLetterListSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_binding: UUID4
    patient: UUID4
    encounter: UUID4
    limit: int = Field(
        default=DEFAULT_CORRESPONDENCE_LETTER_PAGE_SIZE,
        ge=1,
        le=MAX_CORRESPONDENCE_LETTER_PAGE_SIZE,
    )
    offset: int = Field(default=0, ge=0)


class CorrespondenceLetterArtifactSpec(BaseModel):
    id: UUID4
    sha256: Sha256
    generated_at: datetime
    generated_by: UUID4
    mime_type: Literal["application/pdf"]
    download_url: str | None = None


class CorrespondenceLetterReadSpec(BaseModel):
    id: UUID4
    letter: UUID4
    review_binding: UUID4
    review_hash: Sha256
    patient: UUID4
    encounter: UUID4
    facility: UUID4
    department: UUID4
    author: UUID4
    previous_revision: UUID4 | None
    resource_version: PositiveInt
    status: Literal["draft", "finalized"]
    body: str
    body_hash: Sha256
    revision_hash: Sha256
    finalized_at: datetime | None
    finalized_by: UUID4 | None
    artifact_status: Literal[
        "not_applicable",
        "available",
        "unavailable",
        "integrity_failed",
    ]
    artifact: CorrespondenceLetterArtifactSpec | None


class CorrespondenceLetterCommandResponseSpec(BaseModel):
    client_request_id: UUID4
    replayed: bool
    correspondence: CorrespondenceLetterReadSpec


def canonical_letter_command_hash(
    request_spec: CorrespondenceLetterContextSpec,
    *,
    command_type: str,
    actor_id: UUID,
    target_revision_id: UUID | None,
) -> str:
    return canonical_sha256(
        {
            "actor": actor_id,
            "command": command_type,
            "contract": "correspondence-letter-command-v1",
            "payload": request_spec.model_dump(
                mode="python",
                exclude={"client_request_id"},
            ),
            "target_revision": target_revision_id,
        }
    )


def correspondence_letter_body_hash(body: str) -> str:
    return canonical_sha256(
        {
            "body": body,
            "contract": "correspondence-letter-body-v1",
        }
    )


def correspondence_letter_revision_hash(revision) -> str:
    return canonical_sha256(
        {
            "body_hash": revision.body_hash,
            "contract": "correspondence-letter-revision-v1",
            "finalized_at": revision.finalized_at,
            "finalized_by": (
                revision.finalized_by.external_id if revision.finalized_by_id else None
            ),
            "letter": revision.letter.external_id,
            "previous_revision": (
                revision.previous_revision.external_id
                if revision.previous_revision_id
                else None
            ),
            "resource_version": revision.resource_version,
            "review": revision.letter.review.external_id,
            "review_hash": revision.source_review_hash,
            "revision": revision.external_id,
            "status": revision.status,
        }
    )


def normalize_letter_body(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFC", value.replace("\r\n", "\n").replace("\r", "\n")
    )
    if CONTROL_CHARACTER_PATTERN.search(normalized):
        raise ValueError("Correspondence body contains unsupported control characters")
    normalized = normalized.strip()
    if not normalized:
        raise ValueError("Correspondence body must contain visible text")
    return normalized
