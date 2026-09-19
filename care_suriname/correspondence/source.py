from care.emr.models.medication_request import MedicationRequest
from care.emr.models.organization import FacilityOrganizationUser
from care.emr.resources.form_submission.spec import FormSubmissionStatusChoices
from care_suriname.correspondence.author import (
    InvalidVerifiedAuthorError,
    verified_author_snapshot,
)
from care_suriname.models.correspondence_correction import FormSubmissionSeriesHead
from care_suriname.reports.template_versioning import calculate_template_content_hash
from care_suriname.resources.correspondence import canonical_sha256
from care_suriname.resources.correspondence_correction import (
    form_submission_series_head_hash,
)
from care_suriname.resources.form_submission.commands import (
    finalized_form_submission_snapshot_hash,
)

SHA256_LENGTH = 64


def compilation_frozen_integrity_valid(compilation) -> bool:
    """Validate only the immutable compilation record, never mutable live state."""
    try:
        provenance = compilation.source_provenance
        form = provenance["form"]
        template = provenance["template"]
        encounter = provenance["encounter"]
        patient = provenance["patient"]
        expected_hash = canonical_sha256(
            {
                "compiled_at": compilation.compiled_at,
                "compiled_html": compilation.compiled_html,
                "compiled_text": compilation.compiled_text,
                "compilation": compilation.external_id,
                "provenance": provenance,
            }
        )
        return all(
            [
                not compilation.deleted,
                compilation.status == "compiled",
                len(compilation.source_fingerprint) == SHA256_LENGTH,
                len(compilation.form_source_hash) == SHA256_LENGTH,
                len(compilation.form_artifact_hash) == SHA256_LENGTH,
                len(compilation.template_hash) == SHA256_LENGTH,
                provenance.get("contract") == "correspondence-compilation-snapshot-v1",
                provenance.get("compilation") == str(compilation.external_id),
                form.get("id") == str(compilation.form_submission.external_id),
                form.get("version") == compilation.form_source_version,
                form.get("hash") == compilation.form_source_hash,
                form.get("artifact_id") == str(compilation.form_artifact.external_id),
                form.get("artifact_hash") == compilation.form_artifact_hash,
                template.get("id") == str(compilation.template.external_id),
                template.get("version") == compilation.template_version,
                template.get("hash") == compilation.template_hash,
                encounter.get("id") == str(compilation.encounter.external_id),
                patient.get("id") == str(compilation.patient.external_id),
                provenance.get("medications") == compilation.medication_sources,
                expected_hash == compilation.compiled_hash,
            ]
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def compilation_sources_available(compilation) -> bool:
    """Validate mutable source actionability for a new downstream action."""
    if not compilation_frozen_integrity_valid(compilation):
        return False
    source = compilation.form_submission
    artifact = compilation.form_artifact
    template = compilation.template
    if any(
        [
            compilation.patient.deleted,
            compilation.encounter.deleted,
            compilation.facility.deleted,
            not compilation.facility.is_active,
            compilation.department.deleted,
            not compilation.department.active,
            compilation.encounter_reason.deleted,
            compilation.encounter_reason.status != "active",
            source.deleted,
            source.questionnaire.deleted,
            artifact.deleted,
            artifact.is_archived,
            not artifact.upload_completed,
            template.deleted,
            template.status != "active",
            source.resource_version != compilation.form_source_version,
            source.finalized_snapshot_hash != compilation.form_source_hash,
            artifact.form_submission_id != source.id,
            artifact.source_version != compilation.form_source_version,
            artifact.source_snapshot_hash != compilation.form_source_hash,
            artifact.artifact_sha256 != compilation.form_artifact_hash,
            template.resource_version != compilation.template_version,
            template.content_hash != compilation.template_hash,
            not _source_is_current(source),
        ]
    ):
        return False
    try:
        if (
            finalized_form_submission_snapshot_hash(source)
            != compilation.form_source_hash
        ):
            return False
        if calculate_template_content_hash(template) != compilation.template_hash:
            return False
    except (AttributeError, TypeError, ValueError):
        return False
    return all(
        [
            _medication_sources_actionable(compilation.medication_sources),
            _author_source_actionable(compilation),
        ]
    )


def _medication_sources_actionable(snapshots) -> bool:
    if not isinstance(snapshots, list):
        return False
    ids = [item.get("id") for item in snapshots if isinstance(item, dict)]
    if len(ids) != len(snapshots) or None in ids or len(ids) != len(set(ids)):
        return False
    medications = {
        str(item.external_id): item
        for item in MedicationRequest.objects.filter(external_id__in=ids)
    }
    if len(medications) != len(ids):
        return False
    return all(
        medication.status in {"active", "completed"}
        and medication.intent == "order"
        and not medication.do_not_perform
        and medication.client_request_payload_hash == snapshot.get("payload_hash")
        for snapshot in snapshots
        if (medication := medications.get(snapshot["id"])) is not None
    )


def _author_source_actionable(compilation) -> bool:
    snapshot = compilation.source_provenance.get("author")
    if not isinstance(snapshot, dict):
        return False
    membership = (
        FacilityOrganizationUser._base_manager.select_related("role")  # noqa: SLF001
        .filter(
            external_id=snapshot.get("membership"),
            user_id=compilation.author_id,
            organization_id=compilation.department_id,
        )
        .first()
    )
    if not membership:
        return False
    try:
        current = verified_author_snapshot(
            user=compilation.author,
            membership=membership,
            role=membership.role,
            facility=compilation.facility,
            department=compilation.department,
        )
    except InvalidVerifiedAuthorError:
        return False
    return current == snapshot


def _source_is_current(source) -> bool:
    head = (
        FormSubmissionSeriesHead._base_manager.select_related(  # noqa: SLF001
            "advanced_by",
            "current_submission__created_by",
            "current_submission__encounter",
            "current_submission__patient",
            "current_submission__previous_version",
            "current_submission__questionnaire",
            "current_submission__updated_by",
            "current_submission__workflow_finalized_by",
        )
        .filter(series_id=source.series_id, deleted=False)
        .first()
    )
    return bool(
        head
        and head.current_submission_id == source.id
        and _source_head_integrity_valid(head)
    )


def _source_head_integrity_valid(head) -> bool:
    current = head.current_submission
    try:
        return all(
            [
                not head.deleted,
                not current.deleted,
                current.status == FormSubmissionStatusChoices.submitted.value,
                current.workflow_finalized_at is not None,
                current.workflow_finalized_by_id is not None,
                finalized_form_submission_snapshot_hash(current)
                == current.finalized_snapshot_hash,
                head.series_id == current.series_id,
                head.current_version == current.resource_version,
                head.current_snapshot_hash == current.finalized_snapshot_hash,
                head.advanced_by_id == current.workflow_finalized_by_id,
                head.updated_by_id == head.advanced_by_id,
                head.advanced_at == current.workflow_finalized_at,
                form_submission_series_head_hash(head) == head.head_hash,
            ]
        )
    except (AttributeError, TypeError, ValueError):
        return False
