import datetime
from enum import Enum

from pydantic import UUID4, ConfigDict, PositiveInt

from care.emr.models.encounter import Encounter
from care.emr.models.patient import Patient
from care.emr.models.questionnaire import FormSubmission, Questionnaire
from care.emr.resources.base import EMRResource
from care.emr.resources.user.spec import UserSpec
from care.utils.shortcuts import get_object_or_404


class FormSubmissionStatusChoices(str, Enum):
    draft = "draft"
    submitted = "submitted"
    entered_in_error = "entered_in_error"


class BaseFormSubmissionSpec(EMRResource):
    """Base model for form submission"""

    __model__ = FormSubmission

    id: UUID4 | None = None


class FormSubmissionMutableSpec(BaseFormSubmissionSpec):
    status: FormSubmissionStatusChoices
    response_dump: dict


class FormSubmissionUpdateSpec(FormSubmissionMutableSpec):
    """Legacy draft update specification with mandatory optimistic concurrency."""

    model_config = ConfigDict(extra="forbid")

    expected_version: PositiveInt


class FormSubmissionWriteSpec(FormSubmissionMutableSpec):
    """Form submission write specification"""

    questionnaire: str
    patient: UUID4
    encounter: UUID4 | None = None

    def perform_extra_deserialization(self, is_update, obj):
        if not is_update and self.id:
            # A client-owned form-instance UUID makes the initial draft create
            # retry-safe. The database uniqueness of external_id prevents two
            # concurrent creates for the same clinical form instance.
            obj.external_id = self.id
        obj.questionnaire = get_object_or_404(Questionnaire, slug=self.questionnaire)
        obj.patient = get_object_or_404(Patient, external_id=self.patient)
        if self.encounter:
            obj.encounter = get_object_or_404(
                Encounter,
                external_id=self.encounter,
                patient=obj.patient,
            )


class FormSubmissionReadSpec(FormSubmissionMutableSpec):
    """Form submission read specification"""

    status: FormSubmissionStatusChoices
    response_dump: dict
    created_date: datetime.datetime
    modified_date: datetime.datetime | None = None
    questionnaire: str
    patient: UUID4
    encounter: UUID4 | None = None
    series_id: UUID4
    resource_version: PositiveInt
    previous_version: UUID4 | None = None
    amendment_reason: str = ""
    amendment_type: str = ""
    workflow_finalized_at: datetime.datetime | None = None
    workflow_finalized_by: UserSpec | None = None
    finalized_snapshot_hash: str = ""
    entered_in_error_at: datetime.datetime | None = None
    entered_in_error_by: UserSpec | None = None
    entered_in_error_reason: str = ""

    created_by: UserSpec | None = None
    updated_by: UserSpec | None = None

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        mapping["id"] = obj.external_id
        mapping["questionnaire"] = obj.questionnaire.slug
        mapping["patient"] = obj.patient.external_id
        mapping["encounter"] = obj.encounter.external_id if obj.encounter_id else None
        mapping["previous_version"] = (
            obj.previous_version.external_id if obj.previous_version_id else None
        )
        if obj.workflow_finalized_by_id:
            mapping["workflow_finalized_by"] = UserSpec.serialize(
                obj.workflow_finalized_by
            ).to_json()
        if obj.entered_in_error_by_id:
            mapping["entered_in_error_by"] = UserSpec.serialize(
                obj.entered_in_error_by
            ).to_json()
        cls.serialize_audit_users(mapping, obj)
