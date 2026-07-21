import json
import logging
from collections import Counter

from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from care.emr.api.viewsets.base import EMRBaseViewSet, EMRRetrieveMixin
from care.emr.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care.emr.correspondence.author import (
    InvalidVerifiedAuthorError,
    verified_author_snapshot,
)
from care.emr.correspondence.correction import (
    FormSubmissionSeriesHeadIntegrityError,
    lock_current_finalized_form_series,
)
from care.emr.correspondence.source import compilation_frozen_integrity_valid
from care.emr.models.correspondence import (
    CorrespondenceCompilation,
    CorrespondenceCompileCommand,
)
from care.emr.models.encounter import Encounter, EncounterOrganization
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.organization import (
    FacilityOrganization,
    FacilityOrganizationUser,
)
from care.emr.models.patient import Patient
from care.emr.models.questionnaire import (
    FormSubmission,
    Questionnaire,
    QuestionnaireResponse,
)
from care.emr.models.report.report_upload import ReportUpload
from care.emr.models.report.template import Template
from care.emr.models.tag_config import TagConfig
from care.emr.reports.authorizers.utils import (
    read_report_authorizer,
    write_report_authorizer,
)
from care.emr.reports.correspondence_compiler import (
    CorrespondenceCompilationError,
    compile_correspondence_html,
    readable_form_html,
    readable_medications_html,
)
from care.emr.reports.form_submission_artifact import validate_response_dump
from care.emr.reports.template_versioning import calculate_template_content_hash
from care.emr.resources.correspondence import (
    CompileCorrespondenceResponseSpec,
    CompileCorrespondenceSpec,
    CorrespondenceCompilationReadSpec,
    canonical_correspondence_command_hash,
    canonical_sha256,
)
from care.emr.resources.form_submission.artifact import has_unresolved_placeholder
from care.emr.resources.form_submission.commands import (
    finalized_form_submission_snapshot_hash,
)
from care.emr.resources.form_submission.spec import FormSubmissionStatusChoices
from care.emr.workflow_capabilities import require_workflow_mutations_enabled
from care.facility.models import Facility
from care.security.authorization.base import AuthorizationController
from care.security.models import RoleModel
from care.users.models import User
from care.utils.shortcuts import get_object_or_404

logger = logging.getLogger(__name__)
CONFIRMED_MEDICATION_STATUSES = {"active", "completed"}
SHA256_LENGTH = 64


class CorrespondenceCompilationViewSet(
    ClinicalNoStoreResponseMixin,
    EMRRetrieveMixin,
    EMRBaseViewSet,
):
    database_model = CorrespondenceCompilation
    pydantic_read_model = CorrespondenceCompilationReadSpec
    pydantic_retrieve_model = CorrespondenceCompilationReadSpec

    def get_queryset(self):
        return super().get_queryset().select_related(*self._related_fields())

    def authorize_retrieve(self, model_instance):
        self._authorize_clinical_read(model_instance.form_submission)
        read_report_authorizer(
            self.request.user,
            model_instance.form_artifact.report_type,
            model_instance.form_artifact.associating_id,
        )

    def retrieve(self, request, *args, **kwargs):
        compilation = self.get_object()
        self.authorize_retrieve(compilation)
        if not compilation_frozen_integrity_valid(compilation):
            return self._source_conflict("correspondence_source_unavailable")
        return Response(
            CorrespondenceCompilationReadSpec.serialize(
                compilation,
                request.user,
            ).to_json()
        )

    @extend_schema(
        request=CompileCorrespondenceSpec,
        responses={
            200: CompileCorrespondenceResponseSpec,
            201: CompileCorrespondenceResponseSpec,
        },
    )
    @action(detail=False, methods=["POST"], url_path="idempotent-compile")
    def idempotent_compile(  # noqa: PLR0911, PLR0912
        self, request, *args, **kwargs
    ):
        request_spec = CompileCorrespondenceSpec.model_validate(request.data)
        source = self._get_source(request_spec.form_submission)
        self._authorize_clinical_read(source)
        payload_hash = canonical_correspondence_command_hash(
            request_spec,
            actor_id=request.user.external_id,
        )
        if response := self._command_replay(request_spec, source, payload_hash):
            return response
        require_workflow_mutations_enabled(source.encounter.facility.external_id)
        if source.deleted:
            return self._source_conflict("correspondence_source_unavailable")
        try:
            self._validate_route_context(request_spec, source)
        except _InvalidCorrespondenceSourceError as exc:
            return self._source_invalid(str(exc))

        try:
            with transaction.atomic():
                if response := self._command_replay(request_spec, source, payload_hash):
                    return response
                source = self._lock_current_source(source)
                self._authorize_clinical_read(source)
                self._validate_route_context(request_spec, source)
                locked = self._lock_and_validate_sources(request_spec, source)
                existing = (
                    CorrespondenceCompilation._base_manager.select_related(  # noqa: SLF001
                        *self._related_fields()
                    )
                    .filter(source_fingerprint=payload_hash)
                    .first()
                )
                if existing:
                    if not self._compilation_available(existing):
                        return self._source_conflict(
                            "correspondence_source_unavailable"
                        )
                    self._create_command(request_spec, source, existing, payload_hash)
                    return self._command_response(
                        request_spec.client_request_id,
                        existing,
                        replayed=True,
                        response_status=status.HTTP_200_OK,
                    )
                compilation = self._compile(
                    request_spec,
                    source,
                    locked,
                    source_fingerprint=payload_hash,
                )
                compilation.save(force_insert=True)
                self._create_command(request_spec, source, compilation, payload_hash)
        except IntegrityError as exc:
            if (
                _constraint_name(exc)
                == CorrespondenceCompileCommand.IDEMPOTENCY_CONSTRAINT_NAME
            ):
                if response := self._command_replay(request_spec, source, payload_hash):
                    return response
                return self._idempotency_conflict()
            if (
                _constraint_name(exc)
                == CorrespondenceCompilation.SOURCE_CONSTRAINT_NAME
            ):
                return self._attach_existing_command(request_spec, source, payload_hash)
            raise
        except _StaleCorrespondenceSourceError:
            return self._source_conflict("correspondence_source_stale")
        except (
            CorrespondenceCompilationError,
            _InvalidCorrespondenceSourceError,
        ) as exc:
            return self._source_invalid(str(exc))
        except (Http404, PermissionDenied):
            raise
        except Exception as exc:
            logger.warning(
                "Correspondence compilation failed source=%s error=%s",
                source.external_id,
                type(exc).__name__,
            )
            return self._compilation_failed()

        return self._command_response(
            request_spec.client_request_id,
            compilation,
            replayed=False,
            response_status=status.HTTP_201_CREATED,
        )

    def _lock_and_validate_sources(self, request_spec, source):
        encounter = get_object_or_404(
            Encounter.objects.select_for_update(of=("self",)),
            pk=source.encounter_id,
        )
        if encounter.patient_id != source.patient_id:
            raise Http404("Correspondence source context not found")
        patient = get_object_or_404(
            Patient._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
            pk=source.patient_id,
            deleted=False,
        )
        facility = get_object_or_404(
            Facility._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
            pk=encounter.facility_id,
            deleted=False,
            is_active=True,
        )
        questionnaire = get_object_or_404(
            Questionnaire._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
            pk=source.questionnaire_id,
            deleted=False,
        )
        author = get_object_or_404(
            User._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
            pk=self.request.user.pk,
            deleted=False,
            is_active=True,
        )
        if author.external_id != request_spec.author:
            raise Http404("Correspondence source context not found")
        encounter.patient = patient
        encounter.facility = facility
        source.patient = patient
        source.encounter = encounter
        source.questionnaire = questionnaire
        self._authorize_clinical_read(source)
        write_report_authorizer(
            self.request.user,
            "encounter_report",
            str(encounter.external_id),
        )
        self._validate_form_source(request_spec, source)

        artifact = get_object_or_404(
            ReportUpload._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            ).select_related("patient", "encounter", "form_submission"),
            external_id=request_spec.form_artifact,
        )
        self._validate_form_artifact(request_spec, source, artifact)
        read_report_authorizer(
            self.request.user, artifact.report_type, artifact.associating_id
        )

        template = get_object_or_404(
            Template._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            ).select_related("facility"),
            external_id=request_spec.template,
        )
        self._validate_template(request_spec, encounter, template)
        self._authorize_template_read(template)

        department = get_object_or_404(
            FacilityOrganization.objects.select_for_update(of=("self",)),
            external_id=request_spec.department,
            facility=encounter.facility,
            active=True,
        )
        department_links = list(
            EncounterOrganization.objects.select_for_update(of=("self",)).filter(
                encounter=encounter,
                organization=department,
            )[:2]
        )
        if not department_links:
            raise Http404("Correspondence source context not found")
        if len(department_links) != 1:
            raise _InvalidCorrespondenceSourceError(
                "Encounter department linkage is ambiguous"
            )

        memberships = list(
            FacilityOrganizationUser._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            ).filter(
                deleted=False,
                organization=department,
                user=author,
            )[:2]
        )
        if len(memberships) != 1:
            raise _InvalidCorrespondenceSourceError(
                "Authenticated author department membership is missing or ambiguous"
            )
        role = (
            RoleModel._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(
                pk=memberships[0].role_id,
                deleted=False,
                is_archived=False,
            )
            .first()
        )
        if not role:
            raise _InvalidCorrespondenceSourceError(
                "Authenticated author professional role is unavailable"
            )
        try:
            author_snapshot = verified_author_snapshot(
                user=author,
                membership=memberships[0],
                role=role,
                facility=facility,
                department=department,
            )
        except InvalidVerifiedAuthorError as exc:
            raise _InvalidCorrespondenceSourceError(str(exc)) from exc

        reason = get_object_or_404(
            TagConfig.objects.select_for_update(of=("self",)),
            external_id=request_spec.encounter_reason,
            id__in=encounter.tags,
            status="active",
            resource="encounter",
        )
        encounter_date = self._encounter_date(encounter.period)
        medications = self._lock_and_validate_medications(
            request_spec, source, encounter
        )
        return {
            "artifact": artifact,
            "author": author,
            "author_snapshot": author_snapshot,
            "department": department,
            "encounter": encounter,
            "encounter_date": encounter_date,
            "medications": medications,
            "reason": reason,
            "template": template,
        }

    def _validate_form_source(self, request_spec, source):
        if (
            source.deleted
            or source.status != FormSubmissionStatusChoices.submitted.value
        ):
            raise _InvalidCorrespondenceSourceError(
                "A workflow-finalized FormSubmission is required"
            )
        if (
            source.resource_version != request_spec.form_source_version
            or source.finalized_snapshot_hash != request_spec.form_source_hash
        ):
            raise _StaleCorrespondenceSourceError
        if (
            finalized_form_submission_snapshot_hash(source)
            != source.finalized_snapshot_hash
        ):
            raise _InvalidCorrespondenceSourceError(
                "Finalized FormSubmission provenance is malformed"
            )
        validate_response_dump(source.response_dump)
        if has_unresolved_placeholder(source.response_dump):
            raise _InvalidCorrespondenceSourceError(
                "Finalized FormSubmission contains unresolved placeholders"
            )
        if not source.created_by_id or not source.workflow_finalized_by_id:
            raise _InvalidCorrespondenceSourceError(
                "Finalized FormSubmission audit provenance is incomplete"
            )

    @staticmethod
    def _validate_form_artifact(request_spec, source, artifact):
        matches = all(
            [
                not artifact.deleted,
                not artifact.is_archived,
                artifact.upload_completed,
                artifact.form_submission_id == source.id,
                artifact.patient_id == source.patient_id,
                artifact.encounter_id == source.encounter_id,
                artifact.source_version == source.resource_version,
                artifact.source_snapshot_hash == source.finalized_snapshot_hash,
                artifact.artifact_sha256 == request_spec.form_artifact_hash,
                artifact.report_type == "encounter_report",
            ]
        )
        if not matches:
            raise _InvalidCorrespondenceSourceError(
                "Finalized form artifact does not match the requested source"
            )

    @staticmethod
    def _validate_template(request_spec, encounter, template):
        if template.deleted or template.status != "active":
            raise _InvalidCorrespondenceSourceError(
                "An active correspondence template is required"
            )
        if template.template_type != "encounter_report":
            raise _InvalidCorrespondenceSourceError(
                "Template is not valid for encounter correspondence"
            )
        if template.facility_id and template.facility_id != encounter.facility_id:
            raise Http404("Correspondence source context not found")
        if (
            template.resource_version != request_spec.template_version
            or template.content_hash != request_spec.template_hash
        ):
            raise _StaleCorrespondenceSourceError
        if calculate_template_content_hash(template) != template.content_hash:
            raise _InvalidCorrespondenceSourceError(
                "Correspondence template provenance is malformed"
            )

    def _lock_and_validate_medications(self, request_spec, source, encounter):
        requested_ids = [item.id for item in request_spec.medication_actions]
        medications = list(
            MedicationRequest._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related("requested_product", "requester")
            .filter(external_id__in=requested_ids)
        )
        by_external_id = {item.external_id: item for item in medications}
        if len(by_external_id) != len(requested_ids):
            raise _InvalidCorrespondenceSourceError(
                "A requested medication action is missing"
            )

        response_counts = Counter()
        responses = QuestionnaireResponse._base_manager.select_for_update(  # noqa: SLF001
            of=("self",)
        ).filter(
            form_submission=source,
            structured_response_type="medication_request",
        )
        malformed_link = False
        for questionnaire_response in responses:
            if (
                questionnaire_response.deleted
                or questionnaire_response.status != "completed"
            ):
                malformed_link = True
            response = questionnaire_response.structured_responses
            medication_data = (
                response.get("medication_request", {})
                if isinstance(response, dict)
                else {}
            )
            medication_id = medication_data.get("id")
            if medication_id:
                response_counts[str(medication_id)] += 1
            else:
                malformed_link = True

        if malformed_link or set(response_counts) != {
            str(item) for item in requested_ids
        }:
            raise _InvalidCorrespondenceSourceError(
                "Medication action linkage is missing or ambiguous"
            )

        snapshots = []
        for requested in request_spec.medication_actions:
            medication = by_external_id[requested.id]
            if (
                medication.patient_id != source.patient_id
                or medication.encounter_id != encounter.id
            ):
                raise Http404("Correspondence source context not found")
            if (
                medication.deleted
                or medication.status not in CONFIRMED_MEDICATION_STATUSES
                or medication.intent != "order"
                or medication.do_not_perform
                or not medication.client_request_id
                or medication.client_request_id != requested.client_request_id
                or not _valid_sha256(medication.client_request_payload_hash)
            ):
                raise _InvalidCorrespondenceSourceError(
                    "Medication action is not a confirmed immutable source"
                )
            if response_counts[str(medication.external_id)] != 1:
                raise _InvalidCorrespondenceSourceError(
                    "Medication action linkage is missing or ambiguous"
                )
            snapshots.append(self._medication_snapshot(medication))
        return snapshots

    def _compile(self, request_spec, source, locked, *, source_fingerprint):
        compiled_at = timezone.now()
        compilation = CorrespondenceCompilation(
            source_fingerprint=source_fingerprint,
            patient=source.patient,
            encounter=locked["encounter"],
            facility=locked["encounter"].facility,
            department=locked["department"],
            encounter_reason=locked["reason"],
            form_submission=source,
            form_artifact=locked["artifact"],
            template=locked["template"],
            author=locked["author"],
            form_source_version=source.resource_version,
            form_source_hash=source.finalized_snapshot_hash,
            form_artifact_hash=locked["artifact"].artifact_sha256,
            template_version=locked["template"].resource_version,
            template_hash=locked["template"].content_hash,
            medication_sources=locked["medications"],
            compiled_at=compiled_at,
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        provenance = self._source_provenance(compilation, source, locked)
        context = self._template_context(compilation, source, locked, provenance)
        compiled_html, compiled_text = compile_correspondence_html(
            compilation_id=compilation.external_id,
            compiled_at=compiled_at,
            template_data=locked["template"].template_data,
            context=context,
            provenance=provenance,
        )
        compilation.source_provenance = provenance
        compilation.compiled_html = compiled_html
        compilation.compiled_text = compiled_text
        compilation.compiled_hash = canonical_sha256(
            {
                "compiled_at": compiled_at,
                "compiled_html": compiled_html,
                "compiled_text": compiled_text,
                "compilation": compilation.external_id,
                "provenance": provenance,
            }
        )
        return compilation

    def _source_provenance(self, compilation, source, locked):
        return {
            "author": locked["author_snapshot"],
            "compiled_at": compiled_at_iso(compilation.compiled_at),
            "compilation": str(compilation.external_id),
            "contract": "correspondence-compilation-snapshot-v1",
            "department": {
                "id": str(locked["department"].external_id),
                "name": locked["department"].name,
            },
            "encounter": {
                "date": locked["encounter_date"],
                "external_identifier": locked["encounter"].external_identifier,
                "id": str(locked["encounter"].external_id),
                "reason": locked["reason"].display,
                "reason_id": str(locked["reason"].external_id),
            },
            "facility": {
                "id": str(locked["encounter"].facility.external_id),
                "name": locked["encounter"].facility.name,
            },
            "form": {
                "artifact_hash": locked["artifact"].artifact_sha256,
                "artifact_id": str(locked["artifact"].external_id),
                "hash": source.finalized_snapshot_hash,
                "id": str(source.external_id),
                "questionnaire": source.questionnaire.slug,
                "questionnaire_version": source.questionnaire.version,
                "version": source.resource_version,
            },
            "medications": locked["medications"],
            "patient": self._patient_snapshot(source.patient, locked["encounter"]),
            "template": {
                "hash": locked["template"].content_hash,
                "id": str(locked["template"].external_id),
                "name": locked["template"].name,
                "version": locked["template"].resource_version,
            },
        }

    @staticmethod
    def _template_context(compilation, source, locked, provenance):
        return {
            "author": provenance["author"],
            "compilation": {
                "compiled_at": provenance["compiled_at"],
                "id": str(compilation.external_id),
            },
            "encounter": {
                **provenance["encounter"],
                "department": provenance["department"],
                "facility": provenance["facility"],
            },
            "form": {
                **provenance["form"],
                "readable_html": readable_form_html(source.response_dump),
                "responses": source.response_dump,
            },
            "medications": locked["medications"],
            "medications_readable_html": readable_medications_html(
                locked["medications"]
            ),
            "patient": provenance["patient"],
            "template": provenance["template"],
        }

    @staticmethod
    def _patient_snapshot(patient, encounter):
        identifiers = list(patient.instance_identifiers or [])
        facility_identifiers = patient.facility_identifiers or {}
        identifiers.extend(
            facility_identifiers.get(str(encounter.facility_id), [])
            or facility_identifiers.get(encounter.facility_id, [])
        )
        identifiers = [
            item
            for item in identifiers
            if isinstance(item, dict) and str(item.get("value") or "").strip()
        ]
        date_of_birth = patient.date_of_birth or patient.year_of_birth
        if not patient.name.strip() or not date_of_birth or not identifiers:
            raise _InvalidCorrespondenceSourceError(
                "Patient identity requires a name, date of birth, and identifier"
            )
        return {
            "date_of_birth": str(date_of_birth),
            "id": str(patient.external_id),
            "identifiers": identifiers,
            "name": patient.name,
        }

    @staticmethod
    def _medication_snapshot(medication):
        if medication.requested_product_id:
            display = medication.requested_product.name
        elif isinstance(medication.medication, dict):
            display = medication.medication.get("display") or medication.medication.get(
                "code"
            )
        else:
            display = None
        if not display:
            raise _InvalidCorrespondenceSourceError(
                "Medication action has no readable server-owned identity"
            )
        dosage = medication.dosage_instruction
        if not isinstance(dosage, list):
            raise _InvalidCorrespondenceSourceError(
                "Medication action dosage is malformed"
            )
        dosage_text = "; ".join(
            str(item.get("text"))
            for item in dosage
            if isinstance(item, dict) and item.get("text")
        )
        if not dosage_text:
            dosage_text = json.dumps(
                dosage,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        return {
            "authored_on": compiled_at_iso(medication.authored_on),
            "display": str(display),
            "dosage_instruction": dosage,
            "dosage_text": dosage_text,
            "id": str(medication.external_id),
            "intent": medication.intent,
            "payload_hash": medication.client_request_payload_hash,
            "requester": (
                str(medication.requester.external_id)
                if medication.requester_id
                else None
            ),
            "status": medication.status,
        }

    def _command_replay(self, request_spec, source, payload_hash):
        command = (
            CorrespondenceCompileCommand._base_manager.select_related(  # noqa: SLF001
                "actor",
                "patient",
                "encounter",
                "form_submission",
                "result_compilation__form_submission__questionnaire",
                "result_compilation__form_artifact",
                "result_compilation__template",
                *[
                    f"result_compilation__{field}"
                    for field in self._related_fields()
                    if field not in {"form_submission", "form_artifact", "template"}
                ],
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if not command:
            return None
        compilation = command.result_compilation
        matches = all(
            [
                not command.deleted,
                command.payload_hash == payload_hash,
                command.actor_id == self.request.user.id,
                command.patient_id == source.patient_id,
                command.encounter_id == source.encounter_id,
                command.form_submission_id == source.id,
                self._compilation_available(compilation),
            ]
        )
        if not matches:
            return self._idempotency_conflict()
        self.authorize_retrieve(compilation)
        return self._command_response(
            request_spec.client_request_id,
            compilation,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )

    def _compilation_available(self, compilation):
        return compilation_frozen_integrity_valid(compilation)

    def _create_command(self, request_spec, source, compilation, payload_hash):
        CorrespondenceCompileCommand.objects.create(
            client_request_id=request_spec.client_request_id,
            payload_hash=payload_hash,
            actor=self.request.user,
            patient=source.patient,
            encounter=source.encounter,
            form_submission=source,
            result_compilation=compilation,
            created_by=self.request.user,
            updated_by=self.request.user,
        )

    def _attach_existing_command(self, request_spec, source, payload_hash):
        try:
            with transaction.atomic():
                if response := self._command_replay(request_spec, source, payload_hash):
                    return response
                source = self._lock_current_source(source)
                self._authorize_clinical_read(source)
                locked = self._lock_and_validate_sources(request_spec, source)
                compilation = get_object_or_404(
                    CorrespondenceCompilation._base_manager.select_related(  # noqa: SLF001
                        *self._related_fields()
                    ),
                    source_fingerprint=payload_hash,
                )
                if not self._compilation_available(compilation):
                    return self._source_conflict("correspondence_source_unavailable")
                self._create_command(request_spec, source, compilation, payload_hash)
                del locked
        except IntegrityError:
            if response := self._command_replay(request_spec, source, payload_hash):
                return response
            return self._idempotency_conflict()
        except _StaleCorrespondenceSourceError:
            return self._source_conflict("correspondence_source_stale")
        return self._command_response(
            request_spec.client_request_id,
            compilation,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )

    def _validate_route_context(self, request_spec, source):
        if not source.encounter_id:
            raise _InvalidCorrespondenceSourceError(
                "An encounter-scoped finalized FormSubmission is required"
            )
        if not all(
            [
                source.external_id == request_spec.form_submission,
                source.patient.external_id == request_spec.patient,
                source.encounter.external_id == request_spec.encounter,
                source.encounter.facility.external_id == request_spec.facility,
                self.request.user.external_id == request_spec.author,
            ]
        ):
            raise Http404("Correspondence source context not found")

    def _authorize_clinical_read(self, source):
        patient_access = AuthorizationController.call(
            "can_view_clinical_data", self.request.user, source.patient
        ) or AuthorizationController.call(
            "can_view_patient_questionnaire_responses",
            self.request.user,
            source.patient,
        )
        encounter_access = source.encounter_id and (
            AuthorizationController.call(
                "can_view_encounter_clinical_data",
                self.request.user,
                source.encounter,
            )
            or AuthorizationController.call(
                "can_view_encounter_obj",
                self.request.user,
                source.encounter,
            )
        )
        if not (patient_access or encounter_access):
            raise PermissionDenied("Permission denied for correspondence sources")

    def _authorize_template_read(self, template):
        if template.facility and not AuthorizationController.call(
            "can_list_facility_template", self.request.user, template.facility
        ):
            raise PermissionDenied("Permission denied for correspondence template")

    @staticmethod
    def _encounter_date(period):
        if not isinstance(period, dict) or not period.get("start"):
            raise _InvalidCorrespondenceSourceError(
                "Encounter date provenance is missing"
            )
        return str(period["start"])

    @staticmethod
    def _get_source(external_id):
        return get_object_or_404(
            FormSubmission._base_manager.select_related(  # noqa: SLF001
                "patient",
                "encounter__facility",
                "questionnaire",
                "created_by",
                "workflow_finalized_by",
            ),
            external_id=external_id,
        )

    @staticmethod
    def _lock_current_source(source):
        try:
            _head, current = lock_current_finalized_form_series(source)
        except FormSubmissionSeriesHeadIntegrityError as exc:
            raise _StaleCorrespondenceSourceError from exc
        if current.pk != source.pk:
            raise _StaleCorrespondenceSourceError
        return current

    @staticmethod
    def _related_fields():
        return [
            "patient",
            "encounter",
            "facility",
            "department",
            "encounter_reason",
            "form_submission__questionnaire",
            "form_artifact",
            "template",
            "author",
        ]

    @staticmethod
    def _command_response(client_request_id, compilation, *, replayed, response_status):
        response = Response(
            {
                "client_request_id": str(client_request_id),
                "replayed": replayed,
                "compilation": CorrespondenceCompilationReadSpec.serialize(
                    compilation
                ).to_json(),
            },
            status=response_status,
        )
        response["ETag"] = f'"{compilation.external_id}:{compilation.compiled_hash}"'
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
                            "correspondence sources or context"
                        ),
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _source_conflict(error_type):
        return Response(
            {
                "errors": [
                    {
                        "type": error_type,
                        "msg": "Correspondence sources are stale or unavailable",
                    }
                ]
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _source_invalid(message):
        return Response(
            {
                "errors": [
                    {"type": "correspondence_source_invalid", "msg": str(message)}
                ]
            },
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    @staticmethod
    def _compilation_failed():
        return Response(
            {
                "errors": [
                    {
                        "type": "correspondence_compilation_failed",
                        "msg": (
                            "No compilation was committed; retry with the same "
                            "client_request_id"
                        ),
                    }
                ]
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class _InvalidCorrespondenceSourceError(Exception):
    pass


class _StaleCorrespondenceSourceError(Exception):
    pass


def compiled_at_iso(value):
    if value is None:
        return ""
    if timezone.is_naive(value):
        return value.isoformat()
    return value.isoformat().replace("+00:00", "Z")


def _valid_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _constraint_name(exc):
    cause = getattr(exc, "__cause__", None)
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
