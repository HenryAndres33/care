import datetime

from pydantic import UUID4

from care.emr.models.report.report_upload import ReportUpload
from care.emr.reports import report_types  # noqa: F401 - Trigger registration
from care.emr.resources.base import EMRResource
from care.emr.resources.report.template.spec import TemplateReadSpec
from care.emr.resources.user.spec import UserSpec


class ReportUploadBaseSpec(EMRResource):
    __model__ = ReportUpload

    id: UUID4 | None = None
    name: str


class ReportUploadListSpec(ReportUploadBaseSpec):
    template: dict | None = None
    report_type: str
    associating_id: str
    archived_by: UserSpec | None = None
    archived_datetime: datetime.datetime | None = None
    upload_completed: bool
    is_archived: bool | None = None
    archive_reason: str | None = None
    created_date: datetime.datetime
    extension: str
    uploaded_by: dict | None = None
    mime_type: str
    patient: UUID4 | None = None
    encounter: UUID4 | None = None
    form_submission: UUID4 | None = None
    correspondence_revision: UUID4 | None = None
    source_version: int | None = None
    source_snapshot_hash: str = ""
    artifact_sha256: str = ""
    generated_at: datetime.datetime | None = None
    generated_by: UserSpec | None = None

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        mapping["id"] = obj.external_id
        mapping["extension"] = obj.get_extension()
        mapping["mime_type"] = obj.meta.get("mime_type")
        if obj.template:
            mapping["template"] = TemplateReadSpec.serialize(obj.template).to_json()
        if obj.patient_id:
            mapping["patient"] = obj.patient.external_id
        if obj.encounter_id:
            mapping["encounter"] = obj.encounter.external_id
        if obj.form_submission_id:
            mapping["form_submission"] = obj.form_submission.external_id
        if obj.correspondence_revision_id:
            mapping["correspondence_revision"] = obj.correspondence_revision.external_id
        if obj.generated_by_id:
            mapping["generated_by"] = UserSpec.serialize(obj.generated_by).to_json()
        cls.serialize_audit_users(mapping, obj)


class ReportUploadRetrieveSpec(ReportUploadListSpec):
    signed_url: str | None = None
    read_signed_url: str | None = None
    internal_name: str

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        super().perform_extra_serialization(mapping, obj)
        if getattr(obj, "_just_created", False):
            mapping["signed_url"] = obj.files_manager.signed_url(obj)
        else:
            mapping["read_signed_url"] = obj.files_manager.read_signed_url(obj)
