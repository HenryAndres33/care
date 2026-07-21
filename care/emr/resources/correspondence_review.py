from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, PositiveInt

from care.emr.models.correspondence_review import (
    CorrespondenceRecipient,
    CorrespondenceReview,
)
from care.emr.resources.base import EMRResource
from care.emr.resources.correspondence import Sha256, canonical_sha256


class RecipientDiscoverySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient: UUID4
    facility: UUID4


class CorrespondenceRecipientReadSpec(EMRResource):
    __model__ = CorrespondenceRecipient

    id: UUID4
    patient: UUID4
    facility: UUID4
    organization: UUID4 | None
    healthcare_service: UUID4 | None
    recipient_kind: Literal["healthcare_professional"]
    display_name: str
    professional_role: str
    qualification: str
    registration: str
    organization_name: str
    postal_address: dict
    channel_type: str
    channel_identifier: str
    source_type: str
    source_reference: str
    source_provenance: dict
    verified_at: datetime
    verified_by: UUID4
    resource_version: PositiveInt
    content_hash: Sha256

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        mapping["id"] = obj.external_id
        mapping["patient"] = obj.patient.external_id
        mapping["facility"] = obj.facility.external_id
        mapping["organization"] = (
            obj.organization.external_id if obj.organization_id else None
        )
        mapping["healthcare_service"] = (
            obj.healthcare_service.external_id if obj.healthcare_service_id else None
        )
        mapping["verified_by"] = obj.verified_by.external_id


class RecipientDiscoveryResponseSpec(BaseModel):
    results: list[CorrespondenceRecipientReadSpec]


class BindCorrespondenceReviewSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID4
    compilation: UUID4
    compilation_hash: Sha256
    patient: UUID4
    encounter: UUID4
    facility: UUID4
    department: UUID4
    author: UUID4
    recipient: UUID4
    recipient_version: PositiveInt
    recipient_hash: Sha256


class CorrespondenceReviewReadSpec(EMRResource):
    __model__ = CorrespondenceReview

    id: UUID4
    status: str
    compilation: UUID4
    compilation_hash: Sha256
    patient: UUID4
    encounter: UUID4
    facility: UUID4
    department: UUID4
    author: UUID4
    reviewer: UUID4
    recipient: UUID4
    recipient_version: PositiveInt
    recipient_hash: Sha256
    author_snapshot: dict
    recipient_snapshot: dict
    reviewed_at: datetime
    review_hash: Sha256

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        mapping["id"] = obj.external_id
        for field in [
            "compilation",
            "patient",
            "encounter",
            "facility",
            "department",
            "author",
            "reviewer",
            "recipient",
        ]:
            mapping[field] = getattr(obj, field).external_id


class BindCorrespondenceReviewResponseSpec(BaseModel):
    client_request_id: UUID4
    replayed: bool
    review_binding: CorrespondenceReviewReadSpec


def canonical_review_command_hash(
    request_spec: BindCorrespondenceReviewSpec,
    *,
    actor_id: UUID,
) -> str:
    return canonical_sha256(
        {
            "actor": actor_id,
            "command": "bind_correspondence_review",
            "contract": "correspondence-review-command-v1",
            "payload": request_spec.model_dump(
                mode="python", exclude={"client_request_id"}
            ),
        }
    )
