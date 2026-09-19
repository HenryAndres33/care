"""The existing identity-only, facility-authorized directory contract."""

import datetime
from typing import Literal

from pydantic import UUID4, BaseModel, Field

from care.emr.models.patient import Patient
from care.emr.resources.base import EMRResource
from care.emr.resources.patient.spec import GenderChoices


class PatientDirectoryRequestSpec(BaseModel):
    facility: UUID4
    name: str | None = None
    date_of_birth: datetime.date | None = None
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    ordering: Literal[
        "name",
        "-name",
        "phone_number",
        "-phone_number",
        "date_of_birth",
        "-date_of_birth",
        "external_id",
        "-external_id",
    ] = "name"


class PatientDirectorySpec(EMRResource):
    """Minimal patient identity returned to an authorized facility directory."""

    __model__ = Patient

    id: UUID4
    name: str
    gender: GenderChoices
    phone_number: str
    date_of_birth: datetime.date | None = None
    year_of_birth: int | None = None

    @classmethod
    def perform_extra_serialization(cls, mapping, obj, *args, **kwargs):
        mapping["id"] = obj.external_id
