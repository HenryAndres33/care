import hashlib
import logging

from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from care.emr.api.viewsets.base import (
    EMRBaseViewSet,
    EMRCreateMixin,
    EMRListMixin,
    EMRRetrieveMixin,
    EMRUpdateMixin,
)
from care.emr.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care.emr.correspondence.correction import (
    FormSubmissionSeriesHeadIntegrityError,
    advance_finalized_form_series,
    create_finalized_form_series_head,
    lock_current_finalized_form_series,
)
from care.emr.models.correspondence_correction import (
    CorrespondenceCorrectionOutbox,
    CorrespondenceSourceCorrection,
    FormSubmissionSeriesHead,
)
from care.emr.models.encounter import Encounter
from care.emr.models.patient import Patient
from care.emr.models.questionnaire import FormSubmission, FormSubmissionCommand
from care.emr.models.report.report_upload import (
    FormSubmissionArtifactCommand,
    ReportUpload,
)
from care.emr.reports.authorizers.utils import (
    read_report_authorizer,
    write_report_authorizer,
)
from care.emr.reports.form_submission_artifact import (
    MalformedFinalizedSnapshotError,
    build_form_submission_artifact_html,
    render_form_submission_artifact_pdf,
    validate_response_dump,
)
from care.emr.resources.encounter.constants import COMPLETED_CHOICES
from care.emr.resources.form_submission.artifact import (
    FormSubmissionArtifactCommandResponseSpec,
    GenerateFormSubmissionArtifactSpec,
    canonical_artifact_command_hash,
    has_unresolved_placeholder,
)
from care.emr.resources.form_submission.commands import (
    AmendFormSubmissionSpec,
    EnterFormSubmissionInErrorSpec,
    FinalizeFormSubmissionSpec,
    FormSubmissionCommandResponseSpec,
    UpdateDraftFormSubmissionSpec,
    canonical_form_submission_command_hash,
    finalized_form_submission_snapshot_hash,
)
from care.emr.resources.form_submission.spec import (
    FormSubmissionReadSpec,
    FormSubmissionStatusChoices,
    FormSubmissionUpdateSpec,
    FormSubmissionWriteSpec,
)
from care.emr.resources.form_submission.structured_actions import (
    InvalidStructuredClinicalActionLink,
    clone_structured_clinical_action_links,
)
from care.emr.workflow_capabilities import require_workflow_mutations_enabled
from care.security.authorization.base import AuthorizationController
from care.utils.filters.dummy_filter import DummyUUIDFilter
from care.utils.filters.multiselect import MultiSelectFilter
from care.utils.shortcuts import get_object_or_404

logger = logging.getLogger(__name__)


class FormSubmissionFilters(filters.FilterSet):
    encounter = DummyUUIDFilter()
    patient = DummyUUIDFilter()
    status = MultiSelectFilter(field_name="status")
    questionnaire = filters.CharFilter(
        field_name="questionnaire__slug", lookup_expr="iexact"
    )


class FormSubmissionViewSet(
    ClinicalNoStoreResponseMixin,
    EMRCreateMixin,
    EMRRetrieveMixin,
    EMRUpdateMixin,
    EMRListMixin,
    EMRBaseViewSet,
):
    database_model = FormSubmission
    pydantic_model = FormSubmissionWriteSpec
    pydantic_read_model = FormSubmissionReadSpec
    pydantic_update_model = FormSubmissionUpdateSpec
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = FormSubmissionFilters

    def validate_data(self, instance, model_obj=None):
        if model_obj is None and instance.status != FormSubmissionStatusChoices.draft:
            raise ValidationError(
                "Legacy create can only create a draft; use idempotent-finalize"
            )

    def authorize_create(self, instance):
        # TODO : Check if the user is part of questionnaire organization
        if instance.encounter:
            encounter = get_object_or_404(
                Encounter,
                external_id=instance.encounter,
                patient__external_id=instance.patient,
            )
            self._authorize_write(encounter=encounter)
        else:
            patient = get_object_or_404(Patient, external_id=instance.patient)
            self._authorize_write(patient=patient)
        return super().authorize_create(instance)

    def authorize_update(self, request_obj, model_instance):
        if model_instance.encounter:
            self._authorize_write(encounter=model_instance.encounter)
        else:
            self._authorize_write(patient=model_instance.patient)
        return super().authorize_update(request_obj, model_instance)

    def authorize_retrieve(self, model_instance):
        self._authorize_read(model_instance)

    def _authorize_read(self, submission):
        self._authorize_read_context(submission.patient, submission.encounter)

    def _authorize_read_context(self, patient, encounter=None):
        patient_access = AuthorizationController.call(
            "can_view_clinical_data", self.request.user, patient
        ) or AuthorizationController.call(
            "can_view_patient_questionnaire_responses",
            self.request.user,
            patient,
        )
        if encounter:
            encounter_access = AuthorizationController.call(
                "can_view_encounter_clinical_data",
                self.request.user,
                encounter,
            ) or AuthorizationController.call(
                "can_submit_encounter_questionnaire_obj",
                self.request.user,
                encounter,
            )
            allowed = patient_access or encounter_access
        else:
            allowed = patient_access or AuthorizationController.call(
                "can_submit_questionnaire_patient_obj",
                self.request.user,
                patient,
            )
        if not allowed:
            raise PermissionDenied("Permission denied for form submission context")

    def _authorize_write(self, patient=None, encounter=None):
        if patient and not AuthorizationController.call(
            "can_submit_questionnaire_patient_obj", self.request.user, patient
        ):
            raise PermissionDenied("Permission denied for form submission context")
        if encounter and not AuthorizationController.call(
            "can_submit_encounter_questionnaire_obj", self.request.user, encounter
        ):
            raise PermissionDenied("Permission denied for form submission context")

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related(
                "questionnaire",
                "patient",
                "encounter",
                "previous_version",
                "created_by",
                "updated_by",
                "workflow_finalized_by",
            )
        )
        if self.action != "list":
            return queryset
        if "encounter" in self.request.GET:
            encounter = get_object_or_404(
                Encounter, external_id=self.request.GET["encounter"]
            )
            self._authorize_read_context(encounter.patient, encounter)
            return queryset.filter(encounter=encounter)
        if "patient" in self.request.GET:
            patient = get_object_or_404(
                Patient, external_id=self.request.GET["patient"]
            )
            self._authorize_read_context(patient)
            return queryset.filter(patient=patient)
        raise ValidationError("Patient or encounter is required")

    def update(self, request, *args, **kwargs):
        request_spec = FormSubmissionUpdateSpec.model_validate(request.data)
        if request_spec.status == FormSubmissionStatusChoices.submitted:
            raise ValidationError(
                "Legacy update is draft-only; use idempotent-finalize or idempotent-amend"
            )
        submission = self.get_object()
        with transaction.atomic():
            submission = self._lock_submission(submission.pk)
            self.authorize_update(request_spec, submission)
            if submission.status == FormSubmissionStatusChoices.submitted.value:
                return self._immutable_conflict()
            if submission.status != FormSubmissionStatusChoices.draft.value:
                return self._non_draft_conflict()
            if submission.resource_version != request_spec.expected_version:
                return self._version_conflict(submission.resource_version)
            update_fields = ["resource_version", "updated_by", "modified_date"]
            if request_spec.status == FormSubmissionStatusChoices.entered_in_error:
                submission.status = FormSubmissionStatusChoices.entered_in_error.value
                submission.entered_in_error_at = timezone.now()
                submission.entered_in_error_by = request.user
                submission.entered_in_error_reason = "Draft discarded"
                update_fields.extend(
                    [
                        "entered_in_error_at",
                        "entered_in_error_by",
                        "entered_in_error_reason",
                        "status",
                    ]
                )
            else:
                submission.response_dump = request_spec.response_dump
                update_fields.append("response_dump")
            submission.resource_version += 1
            submission.updated_by = request.user
            submission.save(update_fields=update_fields)
        return Response(FormSubmissionReadSpec.serialize(submission).to_json())

    @extend_schema(
        request=UpdateDraftFormSubmissionSpec,
        responses={200: FormSubmissionCommandResponseSpec},
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-update-draft")
    def idempotent_update_draft(self, request, *args, **kwargs):
        return self._execute_command(
            request,
            request_spec_type=UpdateDraftFormSubmissionSpec,
            command_type="update_draft",
            mutation=self._update_draft,
        )

    @extend_schema(
        request=FinalizeFormSubmissionSpec,
        responses={200: FormSubmissionCommandResponseSpec},
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-finalize")
    def idempotent_finalize(self, request, *args, **kwargs):
        return self._execute_command(
            request,
            request_spec_type=FinalizeFormSubmissionSpec,
            command_type="finalize",
            mutation=self._finalize,
        )

    @extend_schema(
        request=AmendFormSubmissionSpec,
        responses={
            200: FormSubmissionCommandResponseSpec,
            201: FormSubmissionCommandResponseSpec,
        },
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-amend")
    def idempotent_amend(self, request, *args, **kwargs):
        return self._execute_command(
            request,
            request_spec_type=AmendFormSubmissionSpec,
            command_type="amend",
            mutation=self._amend,
            created=True,
        )

    @extend_schema(
        request=EnterFormSubmissionInErrorSpec,
        responses={200: FormSubmissionCommandResponseSpec},
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-enter-in-error")
    def idempotent_enter_in_error(self, request, *args, **kwargs):
        return self._execute_command(
            request,
            request_spec_type=EnterFormSubmissionInErrorSpec,
            command_type="enter_in_error",
            mutation=self._enter_in_error,
        )

    @extend_schema(
        request=GenerateFormSubmissionArtifactSpec,
        responses={
            200: FormSubmissionArtifactCommandResponseSpec,
            201: FormSubmissionArtifactCommandResponseSpec,
        },
    )
    @action(
        detail=True,
        methods=["POST"],
        url_path="idempotent-generate-artifact",
    )
    def idempotent_generate_artifact(  # noqa: PLR0911, PLR0912
        self, request, *args, **kwargs
    ):
        request_spec = GenerateFormSubmissionArtifactSpec.model_validate(request.data)
        source = self._get_artifact_source()
        self._authorize_read(source)
        payload_hash = canonical_artifact_command_hash(
            request_spec,
            source_id=source.external_id,
            actor_id=request.user.external_id,
        )
        if response := self._artifact_command_replay(
            request_spec, source, payload_hash
        ):
            return response
        if source.deleted:
            return self._artifact_conflict("artifact_source_unavailable")
        try:
            self._validate_artifact_context(request_spec, source)
            require_workflow_mutations_enabled(source.encounter.facility.external_id)
        except _ArtifactValidationError as exc:
            return self._artifact_validation_error(str(exc))

        uploaded_artifact = None
        try:
            with transaction.atomic():
                if response := self._artifact_command_replay(
                    request_spec, source, payload_hash
                ):
                    return response
                source = self._lock_current_artifact_source(source)
                self._authorize_read(source)
                self._validate_artifact_context(request_spec, source)
                encounter = Encounter.objects.select_for_update().get(
                    pk=source.encounter_id
                )
                if encounter.patient_id != source.patient_id:
                    raise Http404("Form submission context not found")
                source.encounter = encounter
                self._authorize_read(source)
                write_report_authorizer(
                    request.user,
                    "encounter_report",
                    str(encounter.external_id),
                )
                self._validate_finalized_artifact_source(request_spec, source)

                existing = self._existing_source_artifact(source)
                if existing:
                    if not self._artifact_is_available(existing, source):
                        return self._artifact_conflict("artifact_source_unavailable")
                    FormSubmissionArtifactCommand.objects.create(
                        **self._artifact_command_values(
                            request_spec, source, existing, payload_hash
                        )
                    )
                    return self._artifact_command_response(
                        request_spec.client_request_id,
                        existing,
                        replayed=True,
                        response_status=status.HTTP_200_OK,
                    )

                uploaded_artifact = self._build_and_upload_artifact(source)
                uploaded_artifact.save(force_insert=True, skip_internal_name=True)
                FormSubmissionArtifactCommand.objects.create(
                    **self._artifact_command_values(
                        request_spec,
                        source,
                        uploaded_artifact,
                        payload_hash,
                    )
                )
        except IntegrityError as exc:
            self._delete_uploaded_artifact(uploaded_artifact)
            if (
                _constraint_name(exc)
                == FormSubmissionArtifactCommand.IDEMPOTENCY_CONSTRAINT_NAME
            ):
                if response := self._artifact_command_replay(
                    request_spec, source, payload_hash
                ):
                    return response
                return self._idempotency_conflict()
            if (
                _constraint_name(exc)
                == ReportUpload.FORM_ARTIFACT_SOURCE_CONSTRAINT_NAME
            ):
                return self._attach_existing_artifact_command(
                    request_spec, source, payload_hash
                )
            raise
        except _ArtifactStaleSourceError:
            self._delete_uploaded_artifact(uploaded_artifact)
            return self._artifact_stale_source()
        except (MalformedFinalizedSnapshotError, _ArtifactValidationError) as exc:
            self._delete_uploaded_artifact(uploaded_artifact)
            return self._artifact_validation_error(str(exc))
        except (Http404, PermissionDenied):
            self._delete_uploaded_artifact(uploaded_artifact)
            raise
        except Exception as exc:  # storage/renderer/ledger failure is fail-closed
            self._delete_uploaded_artifact(uploaded_artifact)
            logger.warning(
                "Form artifact generation failed source=%s artifact=%s error=%s",
                source.external_id,
                getattr(uploaded_artifact, "external_id", None),
                type(exc).__name__,
            )
            return self._artifact_generation_failed()

        return self._artifact_command_response(
            request_spec.client_request_id,
            uploaded_artifact,
            replayed=False,
            response_status=status.HTTP_201_CREATED,
        )

    def _execute_command(  # noqa: PLR0911, PLR0912
        self,
        request,
        *,
        request_spec_type,
        command_type,
        mutation,
        created=False,
    ):
        request_spec = request_spec_type.model_validate(request.data)
        target = get_object_or_404(
            FormSubmission._base_manager.select_related(  # noqa: SLF001
                "questionnaire",
                "patient",
                "encounter",
                "previous_version",
                "created_by",
                "updated_by",
                "workflow_finalized_by",
            ),
            external_id=self.kwargs["external_id"],
        )
        self._authorize_read(target)
        if target.deleted:
            return self._idempotency_conflict()
        payload_hash = canonical_form_submission_command_hash(
            request_spec,
            command_type=command_type,
            target_id=target.external_id,
            actor_id=request.user.external_id,
        )
        if response := self._command_replay_response(
            request_spec, command_type, target, payload_hash
        ):
            return response
        if (
            command_type in {"finalize", "amend", "enter_in_error"}
            and target.encounter_id
        ):
            require_workflow_mutations_enabled(target.encounter.facility.external_id)
        if command_type == "amend" and (
            response := self._response_dump_validation_response(
                request_spec.response_dump
            )
        ):
            return response
        self._validate_command_context(request_spec, target)

        try:
            with transaction.atomic():
                command_target = target
                series_head = None
                uses_series_head = command_type == "amend" or (
                    command_type == "enter_in_error"
                    and command_target.status
                    == FormSubmissionStatusChoices.submitted.value
                )
                if uses_series_head:
                    try:
                        series_head, current = lock_current_finalized_form_series(
                            command_target
                        )
                    except FormSubmissionSeriesHeadIntegrityError:
                        return self._series_integrity_conflict()
                    self._authorize_read(current)
                    if response := self._command_replay_response(
                        request_spec,
                        command_type,
                        command_target,
                        payload_hash,
                    ):
                        return response
                    if current.pk != command_target.pk:
                        return self._version_conflict(series_head.current_version)
                    target = current
                else:
                    target = self._lock_submission(command_target.pk)
                self._authorize_read(target)
                if command_type != "amend" and (
                    response := self._command_replay_response(
                        request_spec, command_type, target, payload_hash
                    )
                ):
                    return response
                self._validate_command_context(request_spec, target)
                if command_type == "finalize" and (
                    response := self._response_dump_validation_response(
                        target.response_dump
                    )
                ):
                    return response
                if command_type == "amend" and (
                    response := self._response_dump_validation_response(
                        target.response_dump,
                        authoritative_source=True,
                    )
                ):
                    return response
                self._lock_and_authorize_write_context(target, command_type)
                if target.resource_version != request_spec.expected_version:
                    return self._version_conflict(target.resource_version)

                result = mutation(target, request_spec, series_head=series_head)
                FormSubmissionCommand.objects.create(
                    client_request_id=request_spec.client_request_id,
                    payload_hash=payload_hash,
                    command_type=command_type,
                    expected_version=request_spec.expected_version,
                    actor=request.user,
                    patient=target.patient,
                    encounter=target.encounter,
                    questionnaire=target.questionnaire,
                    target_submission=target,
                    result_submission=result,
                    created_by=request.user,
                    updated_by=request.user,
                )
        except IntegrityError as exc:
            constraint_name = _constraint_name(exc)
            if constraint_name == FormSubmissionCommand.IDEMPOTENCY_CONSTRAINT_NAME:
                response = self._command_replay_response(
                    request_spec, command_type, target, payload_hash
                )
                if response:
                    return response
            if constraint_name == FormSubmission.SERIES_VERSION_CONSTRAINT_NAME:
                latest_version = (
                    FormSubmission.objects.filter(series_id=target.series_id)
                    .order_by("-resource_version")
                    .values_list("resource_version", flat=True)
                    .first()
                )
                return self._version_conflict(latest_version)
            if constraint_name in {
                FormSubmissionSeriesHead.SERIES_CONSTRAINT_NAME,
                FormSubmissionSeriesHead.CURRENT_CONSTRAINT_NAME,
                CorrespondenceSourceCorrection.SEQUENCE_CONSTRAINT_NAME,
                CorrespondenceSourceCorrection.PREVIOUS_SOURCE_CONSTRAINT_NAME,
                CorrespondenceSourceCorrection.NEW_SOURCE_CONSTRAINT_NAME,
                CorrespondenceSourceCorrection.HASH_CONSTRAINT_NAME,
                CorrespondenceCorrectionOutbox.SOURCE_CONSTRAINT_NAME,
            }:
                return self._series_integrity_conflict()
            raise
        except _CommandConflictError as exc:
            return exc.response
        except FormSubmissionSeriesHeadIntegrityError:
            return self._series_integrity_conflict()

        return self._command_response(
            request_spec.client_request_id,
            result,
            replayed=False,
            response_status=(
                status.HTTP_201_CREATED if created else status.HTTP_200_OK
            ),
        )

    def _update_draft(self, target, request_spec, *, series_head=None):
        del series_head
        if target.status == FormSubmissionStatusChoices.submitted.value:
            return self._raise_immutable_conflict()
        if target.status != FormSubmissionStatusChoices.draft.value:
            return self._raise_non_draft_conflict()
        target.response_dump = request_spec.response_dump
        target.resource_version += 1
        target.updated_by = self.request.user
        target.save(
            update_fields=[
                "response_dump",
                "resource_version",
                "updated_by",
                "modified_date",
            ]
        )
        return target

    def _get_artifact_source(self):
        return get_object_or_404(
            FormSubmission._base_manager.select_related(  # noqa: SLF001
                "questionnaire",
                "patient",
                "encounter",
                "encounter__facility",
                "created_by",
                "workflow_finalized_by",
            ),
            external_id=self.kwargs["external_id"],
        )

    def _validate_artifact_context(self, request_spec, source):
        if not source.encounter_id:
            raise _ArtifactValidationError(
                "A finalized encounter-scoped FormSubmission is required"
            )
        if not all(
            [
                source.patient.external_id == request_spec.patient,
                source.encounter.external_id == request_spec.encounter,
                source.questionnaire.slug == request_spec.questionnaire,
            ]
        ):
            raise Http404("Form submission context not found")

    def _validate_finalized_artifact_source(self, request_spec, source):
        if source.deleted:
            raise _ArtifactValidationError("Finalized source is unavailable")
        if source.status != FormSubmissionStatusChoices.submitted.value:
            raise _ArtifactValidationError(
                "Only a workflow-finalized FormSubmission can be rendered"
            )
        if (
            source.resource_version != request_spec.source_version
            or source.finalized_snapshot_hash != request_spec.source_snapshot_hash
        ):
            raise _ArtifactStaleSourceError
        calculated_hash = finalized_form_submission_snapshot_hash(source)
        if (
            not source.finalized_snapshot_hash
            or calculated_hash != source.finalized_snapshot_hash
        ):
            raise _ArtifactValidationError(
                "Finalized source snapshot provenance is malformed"
            )
        validate_response_dump(source.response_dump)
        if has_unresolved_placeholder(source.response_dump):
            raise _ArtifactValidationError(
                "Finalized source contains unresolved placeholder content"
            )

    def _build_and_upload_artifact(self, source):
        generated_at = timezone.now()
        artifact = ReportUpload(
            template=None,
            name="Finalized form",
            internal_name="",
            associating_id=str(source.encounter.external_id),
            upload_completed=True,
            report_type="encounter_report",
            patient=source.patient,
            encounter=source.encounter,
            form_submission=source,
            source_version=source.resource_version,
            source_snapshot_hash=source.finalized_snapshot_hash,
            generated_at=generated_at,
            generated_by=self.request.user,
            created_by=self.request.user,
            updated_by=self.request.user,
            meta={"mime_type": "application/pdf"},
        )
        artifact.internal_name = f"{artifact.external_id}.pdf"
        artifact.name = f"Finalized form {artifact.external_id}"
        html = build_form_submission_artifact_html(
            artifact_id=artifact.external_id,
            submission=source,
            generated_at=generated_at,
        )
        pdf = render_form_submission_artifact_pdf(html)
        if not pdf or not pdf.startswith(b"%PDF"):
            raise _ArtifactValidationError("PDF renderer returned an invalid artifact")
        artifact.artifact_sha256 = hashlib.sha256(pdf).hexdigest()
        try:
            artifact.files_manager.put_object(
                artifact,
                pdf,
                ContentType="application/pdf",
            )
        except Exception:
            self._delete_uploaded_artifact(artifact)
            raise
        return artifact

    def _artifact_command_replay(self, request_spec, source, payload_hash):
        command = (
            FormSubmissionArtifactCommand._base_manager.select_related(  # noqa: SLF001
                "actor",
                "patient",
                "encounter",
                "source_submission__questionnaire",
                "source_submission__patient",
                "source_submission__encounter",
                "result_artifact__generated_by",
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if not command:
            return None
        artifact = command.result_artifact
        matches = all(
            [
                not command.deleted,
                command.payload_hash == payload_hash,
                command.actor_id == self.request.user.id,
                command.source_submission_id == source.id,
                command.patient_id == source.patient_id,
                command.encounter_id == source.encounter_id,
                command.source_version == source.resource_version,
                command.source_snapshot_hash == source.finalized_snapshot_hash,
                self._artifact_is_available(artifact, source),
            ]
        )
        if not matches:
            return self._idempotency_conflict()
        self._authorize_read(source)
        read_report_authorizer(
            self.request.user,
            artifact.report_type,
            artifact.associating_id,
        )
        return self._artifact_command_response(
            request_spec.client_request_id,
            artifact,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )

    def _artifact_command_values(self, request_spec, source, artifact, payload_hash):
        user = self.request.user
        return {
            "client_request_id": request_spec.client_request_id,
            "payload_hash": payload_hash,
            "actor": user,
            "patient": source.patient,
            "encounter": source.encounter,
            "source_submission": source,
            "source_version": source.resource_version,
            "source_snapshot_hash": source.finalized_snapshot_hash,
            "result_artifact": artifact,
            "created_by": user,
            "updated_by": user,
        }

    @staticmethod
    def _existing_source_artifact(source):
        return (
            ReportUpload._base_manager.select_related(  # noqa: SLF001
                "generated_by"
            )
            .filter(
                form_submission=source,
                source_version=source.resource_version,
            )
            .first()
        )

    @staticmethod
    def _artifact_is_available(artifact, source):
        return all(
            [
                not source.deleted,
                not artifact.deleted,
                not artifact.is_archived,
                artifact.upload_completed,
                artifact.patient_id == source.patient_id,
                artifact.encounter_id == source.encounter_id,
                artifact.form_submission_id == source.id,
                artifact.source_version == source.resource_version,
                artifact.source_snapshot_hash == source.finalized_snapshot_hash,
                artifact.report_type == "encounter_report",
            ]
        )

    def _attach_existing_artifact_command(self, request_spec, source, payload_hash):
        try:
            with transaction.atomic():
                if response := self._artifact_command_replay(
                    request_spec, source, payload_hash
                ):
                    return response
                source = self._lock_current_artifact_source(source)
                self._authorize_read(source)
                self._validate_artifact_context(request_spec, source)
                self._validate_finalized_artifact_source(request_spec, source)
                encounter = Encounter.objects.select_for_update().get(
                    pk=source.encounter_id
                )
                if encounter.patient_id != source.patient_id:
                    raise Http404("Form submission context not found")
                source.encounter = encounter
                write_report_authorizer(
                    self.request.user,
                    "encounter_report",
                    str(encounter.external_id),
                )
                artifact = self._existing_source_artifact(source)
                if not artifact or not self._artifact_is_available(artifact, source):
                    return self._artifact_conflict("artifact_source_unavailable")
                FormSubmissionArtifactCommand.objects.create(
                    **self._artifact_command_values(
                        request_spec, source, artifact, payload_hash
                    )
                )
        except IntegrityError:
            if response := self._artifact_command_replay(
                request_spec, source, payload_hash
            ):
                return response
            return self._idempotency_conflict()
        except _ArtifactStaleSourceError:
            return self._artifact_stale_source()
        return self._artifact_command_response(
            request_spec.client_request_id,
            artifact,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )

    @staticmethod
    def _lock_current_artifact_source(source):
        try:
            _head, current = lock_current_finalized_form_series(source)
        except FormSubmissionSeriesHeadIntegrityError as exc:
            raise _ArtifactStaleSourceError from exc
        if current.pk != source.pk:
            raise _ArtifactStaleSourceError
        return current

    @staticmethod
    def _delete_uploaded_artifact(artifact):
        if not artifact:
            return
        try:
            artifact.files_manager.delete_object(artifact, quiet=True)
        except Exception as exc:  # best-effort compensation, never log content
            logger.error(
                "Form artifact storage compensation failed artifact=%s error=%s",
                artifact.external_id,
                type(exc).__name__,
            )

    @staticmethod
    def _artifact_command_response(
        client_request_id, artifact, *, replayed, response_status
    ):
        try:
            download_url = artifact.files_manager.read_signed_url(artifact)
        except Exception as exc:
            logger.warning(
                "Form artifact download URL unavailable artifact=%s error=%s",
                artifact.external_id,
                type(exc).__name__,
            )
            return Response(
                {
                    "client_request_id": str(client_request_id),
                    "replayed": replayed,
                    "errors": [
                        {
                            "type": "artifact_download_unavailable",
                            "msg": (
                                "Artifact is stored but its download URL is temporarily "
                                "unavailable; retry the exact command"
                            ),
                        }
                    ],
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        response = Response(
            {
                "client_request_id": str(client_request_id),
                "replayed": replayed,
                "artifact": {
                    "id": str(artifact.external_id),
                    "patient": str(artifact.patient.external_id),
                    "encounter": str(artifact.encounter.external_id),
                    "form_submission": str(artifact.form_submission.external_id),
                    "source_version": artifact.source_version,
                    "source_snapshot_hash": artifact.source_snapshot_hash,
                    "artifact_sha256": artifact.artifact_sha256,
                    "generated_at": artifact.generated_at,
                    "generated_by": str(artifact.generated_by.external_id),
                    "status": "completed",
                    "mime_type": "application/pdf",
                    "download_url": download_url,
                },
            },
            status=response_status,
        )
        response["ETag"] = f'"{artifact.external_id}:{artifact.artifact_sha256}"'
        return response

    @staticmethod
    def _artifact_validation_error(message):
        return Response(
            {"errors": [{"type": "artifact_source_invalid", "msg": str(message)}]},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    @staticmethod
    def _artifact_stale_source():
        return Response(
            {
                "errors": [
                    {
                        "type": "artifact_source_stale",
                        "msg": "Finalized FormSubmission version or hash does not match",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _artifact_conflict(error_type):
        return Response(
            {
                "errors": [
                    {
                        "type": error_type,
                        "msg": "Stored form artifact is unavailable",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _artifact_generation_failed():
        return Response(
            {
                "errors": [
                    {
                        "type": "artifact_generation_failed",
                        "msg": "No artifact was committed; retry with the same client_request_id",
                    }
                ]
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    def _finalize(self, target, request_spec, *, series_head=None):
        del series_head
        if target.status == FormSubmissionStatusChoices.submitted.value:
            return self._raise_immutable_conflict()
        if target.status != FormSubmissionStatusChoices.draft.value:
            return self._raise_non_draft_conflict()
        target.status = FormSubmissionStatusChoices.submitted.value
        target.resource_version += 1
        target.workflow_finalized_at = timezone.now()
        target.workflow_finalized_by = self.request.user
        target.updated_by = self.request.user
        target.finalized_snapshot_hash = finalized_form_submission_snapshot_hash(target)
        target.save(
            update_fields=[
                "status",
                "resource_version",
                "workflow_finalized_at",
                "workflow_finalized_by",
                "updated_by",
                "finalized_snapshot_hash",
                "modified_date",
            ]
        )
        create_finalized_form_series_head(
            submission=target,
            actor=self.request.user,
        )
        return target

    def _amend(self, target, request_spec, *, series_head=None):
        if target.status != FormSubmissionStatusChoices.submitted.value:
            return self._raise_immutable_conflict()
        if series_head is None or series_head.current_submission_id != target.id:
            raise FormSubmissionSeriesHeadIntegrityError
        result = FormSubmission(
            questionnaire=target.questionnaire,
            patient=target.patient,
            encounter=target.encounter,
            status=FormSubmissionStatusChoices.submitted.value,
            response_dump=request_spec.response_dump,
            series_id=target.series_id,
            resource_version=target.resource_version + 1,
            previous_version=target,
            amendment_reason=request_spec.reason.strip(),
            amendment_type=request_spec.amendment_type,
            workflow_finalized_at=timezone.now(),
            workflow_finalized_by=self.request.user,
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        result.finalized_snapshot_hash = finalized_form_submission_snapshot_hash(result)
        result.save(force_insert=True)
        try:
            clone_structured_clinical_action_links(
                source=target,
                result=result,
                actor=self.request.user,
            )
        except InvalidStructuredClinicalActionLink as exc:
            raise _CommandConflictError(
                self._structured_action_linkage_conflict()
            ) from exc
        advance_finalized_form_series(
            head=series_head,
            previous=target,
            result=result,
            actor=self.request.user,
        )
        return result

    def _enter_in_error(self, target, request_spec, *, series_head=None):
        del series_head
        if target.status not in {
            FormSubmissionStatusChoices.draft.value,
            FormSubmissionStatusChoices.submitted.value,
        }:
            return self._raise_non_draft_conflict()
        was_draft = target.status == FormSubmissionStatusChoices.draft.value
        target.status = FormSubmissionStatusChoices.entered_in_error.value
        target.entered_in_error_at = timezone.now()
        target.entered_in_error_by = self.request.user
        target.entered_in_error_reason = request_spec.reason.strip()
        target.updated_by = self.request.user
        update_fields = [
            "entered_in_error_at",
            "entered_in_error_by",
            "entered_in_error_reason",
            "status",
            "updated_by",
            "modified_date",
        ]
        if was_draft:
            target.resource_version += 1
            update_fields.append("resource_version")
        target.save(update_fields=update_fields)
        return target

    def _command_replay_response(
        self, request_spec, command_type, target, payload_hash
    ):
        command = (
            FormSubmissionCommand._base_manager.select_related(  # noqa: SLF001
                "actor",
                "patient",
                "encounter",
                "questionnaire",
                "target_submission",
                "result_submission__questionnaire",
                "result_submission__patient",
                "result_submission__encounter",
                "result_submission__previous_version",
                "result_submission__created_by",
                "result_submission__updated_by",
                "result_submission__workflow_finalized_by",
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if not command:
            return None
        matches = all(
            [
                not command.deleted,
                not command.result_submission.deleted,
                command.payload_hash == payload_hash,
                command.command_type == command_type,
                command.actor_id == self.request.user.id,
                command.target_submission_id == target.id,
                command.patient_id == target.patient_id,
                command.encounter_id == target.encounter_id,
                command.questionnaire_id == target.questionnaire_id,
            ]
        )
        if not matches:
            return self._idempotency_conflict()
        self._authorize_read(command.result_submission)
        return self._command_response(
            request_spec.client_request_id,
            command.result_submission,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )

    def _validate_command_context(self, request_spec, target):
        encounter_id = target.encounter.external_id if target.encounter_id else None
        if not all(
            [
                target.patient.external_id == request_spec.patient,
                encounter_id == request_spec.encounter,
                target.questionnaire.slug == request_spec.questionnaire,
            ]
        ):
            raise Http404("Form submission context not found")

    def _lock_and_authorize_write_context(self, target, command_type):
        if target.encounter_id:
            encounter = Encounter.objects.select_for_update().get(
                pk=target.encounter_id
            )
            if encounter.patient_id != target.patient_id:
                raise Http404("Form submission context not found")
            if (
                encounter.status in COMPLETED_CHOICES
                and command_type != "enter_in_error"
            ):
                raise ValidationError(
                    "Completed encounter forms require a future reconciliation workflow"
                )
            target.encounter = encounter
            if command_type == "enter_in_error":
                if not AuthorizationController.call(
                    "can_mark_encounter_questionnaire_entered_in_error",
                    self.request.user,
                    encounter,
                ):
                    raise PermissionDenied(
                        "Permission denied for form submission context"
                    )
            else:
                self._authorize_write(encounter=encounter)
        else:
            patient = Patient.objects.select_for_update().get(pk=target.patient_id)
            target.patient = patient
            self._authorize_write(patient=patient)

    @staticmethod
    def _lock_submission(pk):
        return (
            FormSubmission.objects.select_for_update(of=("self",))
            .select_related(
                "questionnaire",
                "patient",
                "encounter",
                "previous_version",
                "created_by",
                "updated_by",
                "workflow_finalized_by",
            )
            .get(pk=pk)
        )

    @staticmethod
    def _command_response(client_request_id, submission, *, replayed, response_status):
        response = Response(
            {
                "client_request_id": str(client_request_id),
                "replayed": replayed,
                "form_submission": FormSubmissionReadSpec.serialize(
                    submission
                ).to_json(),
            },
            status=response_status,
        )
        response["ETag"] = f'"{submission.external_id}:{submission.resource_version}"'
        return response

    @staticmethod
    def _idempotency_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "idempotency_conflict",
                        "msg": (
                            "client_request_id was already used with different "
                            "request data or context"
                        ),
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _version_conflict(current_version):
        return Response(
            {
                "errors": [
                    {
                        "type": "version_conflict",
                        "msg": "Form submission has changed",
                    }
                ],
                "current_version": current_version,
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _immutable_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "finalized_form_immutable",
                        "msg": "Finalized form submissions are immutable",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _non_draft_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "form_submission_not_draft",
                        "msg": "Only an active draft can be mutated",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _series_integrity_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "form_submission_series_integrity_failed",
                        "msg": "Finalized form series provenance is unavailable",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _structured_action_linkage_conflict():
        return Response(
            {
                "errors": [
                    {
                        "type": "structured_action_linkage_invalid",
                        "msg": (
                            "Finalized structured clinical-action linkage is "
                            "missing, invalid, or ambiguous"
                        ),
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _response_dump_validation_response(
        response_dump, *, authoritative_source=False
    ):
        try:
            validate_response_dump(response_dump)
        except (MalformedFinalizedSnapshotError, RecursionError):
            if authoritative_source:
                return FormSubmissionViewSet._series_integrity_conflict()
            return Response(
                {
                    "errors": [
                        {
                            "type": "form_submission_response_invalid",
                            "msg": "Form response is not valid bounded JSON",
                        }
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    @staticmethod
    def _raise_version_conflict(current_version):
        raise _CommandConflictError(
            FormSubmissionViewSet._version_conflict(current_version)
        )

    @staticmethod
    def _raise_immutable_conflict():
        raise _CommandConflictError(FormSubmissionViewSet._immutable_conflict())

    @staticmethod
    def _raise_non_draft_conflict():
        raise _CommandConflictError(FormSubmissionViewSet._non_draft_conflict())


class _CommandConflictError(Exception):
    def __init__(self, response):
        super().__init__()
        self.response = response


class _ArtifactValidationError(Exception):
    pass


class _ArtifactStaleSourceError(Exception):
    pass


def _constraint_name(exc):
    cause = getattr(exc, "__cause__", None)
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
