import csv
import datetime
import enum
import io
from typing import Annotated

from django.db.models import Q
from pydantic import BaseModel, Field, field_validator, model_validator

from care_suriname.models.clinical_term_translation import ClinicalTermTranslation

REGION_SUBTAG_LENGTH = 2
MAX_CSV_ROWS = 500


class ClinicalTermStatus(str, enum.Enum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    inactive = "inactive"


class ClinicalTermKind(str, enum.Enum):
    condition = "condition"
    procedure = "procedure"


BoundedText = Annotated[str, Field(min_length=1, max_length=500)]
Synonym = Annotated[str, Field(min_length=1, max_length=200)]


def normalize_language_tag(value: str) -> str:
    parts = value.strip().replace("_", "-").split("-")
    if not parts or not parts[0]:
        raise ValueError("Language is required")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        normalized.append(part.upper() if len(part) == REGION_SUBTAG_LENGTH else part)
    return "-".join(normalized)


class ClinicalTermTranslationWriteSpec(BaseModel):
    system: BoundedText
    code: Annotated[str, Field(min_length=1, max_length=255)]
    language: Annotated[str, Field(min_length=2, max_length=35)] = "nl-SR"
    concept_kind: ClinicalTermKind = ClinicalTermKind.condition
    source_display: BoundedText
    preferred_display: BoundedText
    synonyms: Annotated[list[Synonym], Field(max_length=25)] = []
    status: ClinicalTermStatus = ClinicalTermStatus.draft
    source_name: Annotated[str, Field(min_length=1, max_length=255)]
    source_version: Annotated[str, Field(max_length=128)] = ""
    is_active: bool = True

    @field_validator(
        "system",
        "code",
        "source_display",
        "preferred_display",
        "source_name",
        "source_version",
    )
    @classmethod
    def strip_text(cls, value):
        return value.strip()

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value):
        return normalize_language_tag(value)

    @field_validator("synonyms")
    @classmethod
    def normalize_synonyms(cls, values):
        normalized = list(dict.fromkeys(item.strip() for item in values))
        if any(not item for item in normalized):
            raise ValueError("Synonyms may not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_review_workflow(self):
        if self.status == ClinicalTermStatus.approved:
            raise ValueError("Use the review workflow to approve terminology")
        return self


class ClinicalTermTranslationUpdateSpec(ClinicalTermTranslationWriteSpec):
    status: ClinicalTermStatus

    @model_validator(mode="after")
    def validate_review_workflow(self):
        return self


class ClinicalTermAuditUserSpec(BaseModel):
    id: str
    username: str


class ClinicalTermTranslationReadSpec(ClinicalTermTranslationUpdateSpec):
    id: str
    created_date: datetime.datetime
    modified_date: datetime.datetime
    created_by: ClinicalTermAuditUserSpec | None = None
    updated_by: ClinicalTermAuditUserSpec | None = None
    reviewed_by: ClinicalTermAuditUserSpec | None = None
    reviewed_at: datetime.datetime | None = None


class ClinicalTermConceptSpec(BaseModel):
    system: BoundedText
    code: Annotated[str, Field(min_length=1, max_length=255)]
    display: BoundedText
    designation: list[dict] = []


class ClinicalTermResolveRequest(BaseModel):
    language: Annotated[str, Field(min_length=2, max_length=35)] = "nl-SR"
    concepts: Annotated[list[ClinicalTermConceptSpec], Field(max_length=100)]

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value):
        return normalize_language_tag(value)


class ClinicalTermCsvImportRequest(BaseModel):
    csv_text: Annotated[str, Field(min_length=1, max_length=1_000_000)]
    dry_run: bool = True


CSV_FIELDS = [
    "system",
    "code",
    "language",
    "concept_kind",
    "source_display",
    "preferred_display",
    "synonyms",
    "status",
    "source_name",
    "source_version",
    "is_active",
]

LEGACY_CSV_FIELDS = [field for field in CSV_FIELDS if field != "concept_kind"]


ALLOWED_STATUS_TRANSITIONS = {
    "draft": {"draft", "in_review", "inactive"},
    "in_review": {"draft", "in_review", "approved", "inactive"},
    "approved": {"approved", "in_review", "inactive"},
    "inactive": {"draft", "inactive"},
}


def validate_status_transition(current: str, target: str):
    if target not in ALLOWED_STATUS_TRANSITIONS[current]:
        message = f"Unsupported status transition from {current} to {target}"
        raise ValueError(message)


def user_payload(user):
    if not user:
        return None
    return {"id": str(user.external_id), "username": user.username}


def serialize_clinical_term(term):
    return ClinicalTermTranslationReadSpec(
        id=str(term.external_id),
        system=term.system,
        code=term.code,
        language=term.language,
        concept_kind=term.concept_kind,
        source_display=term.source_display,
        preferred_display=term.preferred_display,
        synonyms=term.synonyms,
        status=term.status,
        source_name=term.source_name,
        source_version=term.source_version,
        is_active=term.is_active,
        created_date=term.created_date,
        modified_date=term.modified_date,
        created_by=user_payload(term.created_by),
        updated_by=user_payload(term.updated_by),
        reviewed_by=user_payload(term.reviewed_by),
        reviewed_at=term.reviewed_at,
    ).model_dump(mode="json")


def approved_terms(language="nl-SR", concept_kind=None):
    queryset = ClinicalTermTranslation.objects.filter(
        language=normalize_language_tag(language),
        status=ClinicalTermStatus.approved.value,
        is_active=True,
    )
    if concept_kind:
        queryset = queryset.filter(
            concept_kind=ClinicalTermKind(concept_kind).value,
        )
    return queryset


def _translated_concept(term):
    designations = [
        {"language": term.language, "value": term.preferred_display},
        *({"language": term.language, "value": synonym} for synonym in term.synonyms),
    ]
    return {
        "system": term.system,
        "code": term.code,
        "display": term.preferred_display,
        "designation": designations,
    }


def resolve_concepts(concepts, language="nl-SR"):
    normalized_language = normalize_language_tag(language)
    keys = {(item["system"], item["code"]) for item in concepts}
    translations = {
        (term.system, term.code): term
        for term in approved_terms(normalized_language).filter(
            Q(system__in={key[0] for key in keys})
            & Q(code__in={key[1] for key in keys})
        )
    }
    resolved = []
    for concept in concepts:
        item = dict(concept)
        term = translations.get((item["system"], item["code"]))
        if term:
            translated = _translated_concept(term)
            existing_designations = item.get("designation") or []
            translated["designation"] = [
                *translated["designation"],
                *existing_designations,
            ]
            item = {**item, **translated}
        resolved.append(item)
    return resolved


def search_approved_translation_concepts(
    search,
    language="nl-SR",
    count=25,
    concept_kind=None,
):
    query = search.strip().casefold()
    if not query:
        return []
    matches = []
    for term in approved_terms(language, concept_kind).order_by("preferred_display")[
        :2_000
    ]:
        preferred = term.preferred_display.casefold()
        synonyms = [synonym.casefold() for synonym in term.synonyms]
        source = term.source_display.casefold()
        code = term.code.casefold()
        if query == preferred:
            rank = 0
        elif query in synonyms:
            rank = 1
        elif query == code:
            rank = 2
        elif preferred.startswith(query):
            rank = 3
        elif any(synonym.startswith(query) for synonym in synonyms):
            rank = 4
        elif query in preferred:
            rank = 5
        elif any(query in synonym for synonym in synonyms):
            rank = 6
        elif query in source:
            rank = 7
        elif query in code:
            rank = 8
        else:
            continue
        matches.append((rank, preferred, code, _translated_concept(term)))
    matches.sort(key=lambda item: item[:3])
    return [item[3] for item in matches[:count]]


def parse_csv_import(csv_text):
    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = tuple(reader.fieldnames or [])
    if fieldnames not in {tuple(CSV_FIELDS), tuple(LEGACY_CSV_FIELDS)}:
        message = f"CSV columns must be exactly: {', '.join(CSV_FIELDS)}"
        raise ValueError(message)
    rows = list(reader)
    if len(rows) > MAX_CSV_ROWS:
        raise ValueError("A CSV import may contain at most 500 rows")
    parsed = []
    seen = set()
    for line_number, row in enumerate(rows, start=2):
        active_value = row["is_active"].strip().casefold()
        if active_value not in {"true", "false", "1", "0"}:
            message = f"Line {line_number}: is_active must be true or false"
            raise ValueError(message)
        payload = {
            **row,
            "concept_kind": row.get("concept_kind") or "condition",
            "synonyms": [
                item.strip() for item in row["synonyms"].split("|") if item.strip()
            ],
            "is_active": active_value in {"true", "1"},
        }
        try:
            spec = ClinicalTermTranslationWriteSpec.model_validate(payload)
        except ValueError as exc:
            message = f"Line {line_number}: {exc}"
            raise ValueError(message) from exc
        key = (spec.system, spec.code, spec.language)
        if key in seen:
            message = f"Line {line_number}: duplicate terminology key in CSV"
            raise ValueError(message)
        seen.add(key)
        parsed.append(spec)
    return parsed
