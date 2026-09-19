import logging
from collections import Counter

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from care.emr.api.viewsets.base import EMRBaseViewSet
from care.emr.api.viewsets.device import disassociate_device_from_encounter
from care.emr.api.viewsets.location import close_related_location_from_encounter
from care.emr.models.device import Device
from care.emr.models.encounter import Encounter, EncounterOrganization
from care.emr.models.location import FacilityLocation
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.organization import FacilityOrganization
from care.emr.models.questionnaire import FormSubmission, QuestionnaireResponse
from care.emr.models.report.report_upload import ReportUpload
from care.emr.models.scheduling.booking import TokenBooking
from care.emr.models.scheduling.token import Token, TokenSubQueue
from care.emr.reports.authorizers.utils import read_report_authorizer
from care.emr.resources.encounter.constants import (
    CLINICALLY_CLOSED_CHOICES,
    StatusChoices,
)
from care.emr.resources.form_submission.commands import (
    finalized_form_submission_snapshot_hash,
)
from care.emr.resources.scheduling.slot.spec import BookingStatusChoices
from care.emr.resources.scheduling.token.spec import TokenStatusOptions
from care.security.authorization.base import AuthorizationController
from care.utils.shortcuts import get_object_or_404
from care_suriname.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care_suriname.correspondence.correction import (
    FormSubmissionSeriesHeadIntegrityError,
    correction_case_integrity_valid,
    lock_current_finalized_form_series,
    review_frozen_integrity_valid,
)
from care_suriname.correspondence.delivery import (
    CorrespondenceDeliveryIntegrityError,
    latest_delivery_event,
    lock_and_verify_delivery_ledger,
)
from care_suriname.correspondence.letter import (
    correspondence_revision_artifact_status,
    correspondence_revision_frozen_integrity_valid,
)
from care_suriname.correspondence.source import compilation_frozen_integrity_valid
from care_suriname.models.consult_closure import (
    ConsultClosure,
    ConsultClosureCommand,
    ConsultClosureRecoveryTask,
)
from care_suriname.models.correspondence import CorrespondenceCompilation
from care_suriname.models.correspondence_correction import (
    CorrespondenceCorrectionCase,
    CorrespondenceCorrectionOutbox,
    CorrespondenceSourceCorrection,
)
from care_suriname.models.correspondence_delivery import CorrespondenceDelivery
from care_suriname.models.correspondence_letter import CorrespondenceLetterRevision
from care_suriname.models.correspondence_review import CorrespondenceReview
from care_suriname.reports.form_submission_artifact import validate_response_dump
from care_suriname.resources.consult_closure import (
    CONSULT_CLOSE_POLICY_ID,
    CONSULT_CLOSE_POLICY_VERSION,
    CONSULT_CLOSE_PREFLIGHT_VERSION,
    EMERGENCY_CLOSE_POLICY_ID,
    UNSCHEDULED_CONSULT_CLOSE_POLICY_ID,
    ConsultCloseCommandCandidateSpec,
    ConsultCloseCommandResponseSpec,
    ConsultCloseCommandSpec,
    ConsultClosePreflightResponseSpec,
    ConsultClosePreflightSpec,
    ConsultClosureRecoveryResolveResponseSpec,
    ConsultClosureRecoveryResolveSpec,
    ConsultClosureStateResponseSpec,
    consult_close_payload_hash,
    consult_close_policy_hash,
    consult_close_preflight_hash,
    consult_closure_command_hash,
    consult_closure_recovery_hash,
    consult_closure_recovery_resolution_hash,
    consult_closure_recovery_resolution_payload_hash,
    consult_closure_snapshot_hash,
)
from care_suriname.resources.form_submission.artifact import has_unresolved_placeholder
from care_suriname.workflow_capabilities import require_workflow_mutations_enabled

logger = logging.getLogger(__name__)

CONFIRMED_MEDICATION_STATUSES = {"active", "completed"}
SHA256_LENGTH = 64


class ConsultClosureViewSet(ClinicalNoStoreResponseMixin, EMRBaseViewSet):
    """Server-orchestrated, evidence-bound and idempotent consult close."""

    database_model = ConsultClosure

    @extend_schema(
        request=ConsultClosePreflightSpec,
        responses={200: ConsultClosePreflightResponseSpec},
    )
    @action(detail=True, methods=["POST"], url_path="preflight")
    def preflight(self, request, *args, **kwargs):
        request_spec = ConsultClosePreflightSpec.model_validate(request.data)
        reference = self._reference_encounter()
        self._authorize_encounter_read(reference)
        with transaction.atomic():
            result = self._locked_preflight(reference, request_spec)
        public_result = {
            key: value for key, value in result.items() if key != "_context"
        }
        return self._no_store(Response(public_result))

    @extend_schema(
        request=ConsultCloseCommandSpec,
        responses={
            200: ConsultCloseCommandResponseSpec,
            201: ConsultCloseCommandResponseSpec,
        },
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-close")
    def idempotent_close(self, request, *args, **kwargs):  # noqa: PLR0911
        request_spec = ConsultCloseCommandSpec.model_validate(request.data)
        reference = self._reference_encounter()
        self._authorize_encounter_read(reference)
        if request_spec.encounter != reference.external_id:
            raise Http404("Consult close context not found")
        payload_hash = consult_close_payload_hash(
            request_spec,
            actor_id=request.user.external_id,
            encounter_id=reference.external_id,
        )
        if replay := self._command_replay(request_spec, reference, payload_hash):
            return self._no_store(replay)
        require_workflow_mutations_enabled(reference.facility.external_id)

        preflight_spec = ConsultClosePreflightSpec.model_validate(
            {
                "patient": request_spec.patient,
                "facility": request_spec.facility,
                "department": request_spec.department,
                "form_submission": request_spec.form_submission,
                "medication_outcome": request_spec.medication_outcome,
                "correspondence_outcome": request_spec.correspondence_outcome,
                "correspondence_compilation": request_spec.correspondence_compilation,
            }
        )
        try:
            with transaction.atomic():
                result = self._locked_preflight(reference, preflight_spec)
                if not result["ready"]:
                    if replay := self._command_replay(
                        request_spec,
                        reference,
                        payload_hash,
                    ):
                        return replay
                    return self._blocked(result["blocker_codes"])
                candidate = result["command_candidate"]
                requested_candidate = request_spec.model_dump(
                    mode="python",
                    exclude={"client_request_id", "confirmed"},
                )
                if (
                    request_spec.preflight_hash != result["preflight_hash"]
                    or requested_candidate != candidate
                ):
                    return self._blocked(["preflight_stale"])
                context = result["_context"]
                self._authorize_close_context(context)
                closure = self._commit_close(context, request_spec)
                response_data = self._command_response_data(
                    request_spec.client_request_id,
                    closure,
                    replayed=False,
                )
                command = ConsultClosureCommand(
                    client_request_id=request_spec.client_request_id,
                    payload_hash=payload_hash,
                    command_hash="",
                    actor=request.user,
                    encounter=context["encounter"],
                    result_closure=closure,
                    result_snapshot=response_data["closure"],
                    created_by=request.user,
                    updated_by=request.user,
                )
                command.command_hash = consult_closure_command_hash(
                    self._command_hash_material(command)
                )
                command.save(force_insert=True)
        except IntegrityError:
            if replay := self._command_replay(
                request_spec,
                reference,
                payload_hash,
            ):
                return self._no_store(replay)
            return self._blocked(["idempotency_conflict"])
        except (Http404, PermissionDenied):
            raise
        except Exception as exc:
            logger.warning(
                "Consult close failed encounter=%s error=%s",
                reference.external_id,
                type(exc).__name__,
            )
            return self._no_store(
                Response(
                    {"error": "consult_closure_failed"},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            )
        return self._no_store(Response(response_data, status=status.HTTP_201_CREATED))

    @extend_schema(responses={200: ConsultClosureStateResponseSpec})
    def retrieve(self, request, *args, **kwargs):
        reference = self._reference_encounter()
        self._authorize_encounter_read(reference)
        projection = self._integrity_projection(reference)
        if projection.get("unstable"):
            return self._no_store(
                Response(
                    {"error": "consult_closure_projection_unstable"},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            )
        encounter = projection["encounter"]
        self._authorize_encounter_read(encounter)
        closure = projection["closure"] if projection["healthy"] else None
        recovery = projection["recovery"]
        if closure:
            read_report_authorizer(
                request.user,
                closure.form_artifact.report_type,
                closure.form_artifact.associating_id,
            )
        data = {
            "encounter": encounter.external_id,
            "closure": self._closure_read(closure) if closure else None,
            "recovery": self._safe_recovery_read(recovery) if recovery else None,
        }
        return self._no_store(Response(data))

    @extend_schema(
        request=ConsultClosureRecoveryResolveSpec,
        responses={
            200: ConsultClosureRecoveryResolveResponseSpec,
            201: ConsultClosureRecoveryResolveResponseSpec,
        },
    )
    @action(
        detail=True,
        methods=["POST"],
        url_path="idempotent-resolve-recovery",
    )
    def idempotent_resolve_recovery(self, request, *args, **kwargs):  # noqa: PLR0911
        request_spec = ConsultClosureRecoveryResolveSpec.model_validate(request.data)
        reference = self._reference_encounter()
        self._authorize_encounter_read(reference)
        payload_hash = consult_closure_recovery_resolution_payload_hash(
            request_spec,
            actor_id=request.user.external_id,
            encounter_id=reference.external_id,
        )
        for _attempt in range(2):
            with transaction.atomic():
                projection = self._locked_integrity_projection(reference)
                if projection.get("retry_required"):
                    continue
                encounter = projection["encounter"]
                recovery = (
                    ConsultClosureRecoveryTask._base_manager.select_for_update(  # noqa: SLF001
                        of=("self",)
                    )
                    .select_related("encounter", "resolved_by")
                    .filter(
                        resolution_request_id=request_spec.client_request_id,
                        deleted=False,
                    )
                    .first()
                )
                if recovery:
                    if not self._recovery_resolution_replay_valid(
                        recovery,
                        request_spec,
                        encounter,
                        payload_hash,
                    ):
                        return self._blocked(["idempotency_conflict"])
                    if not projection["integrity_valid"]:
                        return self._blocked(["closure_integrity_failed"])
                    self._authorize_recovery_resolution(projection)
                    return self._no_store(
                        Response(
                            self._recovery_resolution_response(
                                request_spec.client_request_id,
                                recovery,
                                replayed=True,
                            )
                        )
                    )

                recovery = (
                    ConsultClosureRecoveryTask._base_manager.select_for_update(  # noqa: SLF001
                        of=("self",)
                    )
                    .select_related("encounter")
                    .filter(
                        external_id=request_spec.recovery,
                        encounter=encounter,
                        status="pending",
                        deleted=False,
                    )
                    .first()
                )
                if (
                    not recovery
                    or recovery.recovery_hash != request_spec.expected_recovery_hash
                    or not self._recovery_integrity_valid(recovery)
                ):
                    return self._blocked(["recovery_stale"])
                if not projection["integrity_valid"]:
                    return self._blocked(["closure_integrity_failed"])
                self._authorize_recovery_resolution(projection)
                require_workflow_mutations_enabled(encounter.facility.external_id)
                recovery.status = "resolved"
                recovery.resolved_at = timezone.now()
                recovery.resolved_by = request.user
                recovery.resolution_request_id = request_spec.client_request_id
                recovery.resolution_payload_hash = payload_hash
                recovery.updated_by = request.user
                recovery.resolution_hash = consult_closure_recovery_resolution_hash(
                    self._recovery_resolution_hash_material(recovery)
                )
                recovery.save(
                    update_fields=[
                        "status",
                        "resolved_at",
                        "resolved_by",
                        "resolution_request_id",
                        "resolution_payload_hash",
                        "resolution_hash",
                        "updated_by",
                        "modified_date",
                    ]
                )
                response_data = self._recovery_resolution_response(
                    request_spec.client_request_id,
                    recovery,
                    replayed=False,
                )
            return self._no_store(
                Response(response_data, status=status.HTTP_201_CREATED)
            )
        return self._blocked(["closure_projection_unstable"])

    def _locked_preflight(self, reference, request_spec):  # noqa: PLR0912, PLR0915
        blockers = set()
        source_reference = get_object_or_404(
            FormSubmission._base_manager.select_related(  # noqa: SLF001
                "patient",
                "encounter",
                "encounter__facility",
                "questionnaire",
            ),
            external_id=request_spec.form_submission,
            encounter__isnull=False,
        )
        department_reference = get_object_or_404(
            FacilityOrganization._base_manager,  # noqa: SLF001
            external_id=request_spec.department,
            facility=source_reference.encounter.facility,
            deleted=False,
        )
        required_forms = self._required_form_slugs(department_reference)
        if len(required_forms) != 1:
            return self._preflight_blocked(["department_invalid"])
        try:
            _head, source = lock_current_finalized_form_series(source_reference)
        except FormSubmissionSeriesHeadIntegrityError:
            return self._preflight_blocked(["form_source_stale"])
        if source.pk != source_reference.pk:
            return self._preflight_blocked(["form_source_stale"])
        if source.questionnaire.slug not in required_forms:
            return self._preflight_blocked(["form_not_finalized"])

        encounter = (
            Encounter._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .select_related("patient", "facility", "appointment")
            .get(pk=reference.pk)
        )
        self._authorize_encounter_read(encounter)
        closures = list(
            ConsultClosure._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(encounter=encounter, deleted=False)
            .order_by("closure_number")
        )
        pending_recoveries = list(
            ConsultClosureRecoveryTask._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(encounter=encounter, status="pending", deleted=False)
            .order_by("pk")
        )
        if pending_recoveries:
            blockers.add("closure_recovery_required")
        if closures and not self._closure_history_integrity_valid(closures):
            blockers.add("closure_integrity_failed")
            self._ensure_pending_recovery(encounter, "closure_integrity_failed")
        if not all(
            [
                encounter.external_id == reference.external_id,
                encounter.patient.external_id == request_spec.patient,
                encounter.facility.external_id == request_spec.facility,
                source.encounter_id == encounter.id,
                source.patient_id == encounter.patient_id,
            ]
        ):
            raise Http404("Consult close context not found")

        department_link = (
            EncounterOrganization._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .select_related("organization")
            .filter(
                encounter=encounter,
                organization__external_id=request_spec.department,
                organization__facility=encounter.facility,
                deleted=False,
            )
            .first()
        )
        if not department_link:
            blockers.add("department_invalid")

        appointment = None
        token = None
        subqueues = []
        if encounter.appointment_id:
            appointment = (
                TokenBooking._base_manager.select_for_update(of=("self",))  # noqa: SLF001
                .select_related("token_slot__resource", "token")
                .get(pk=encounter.appointment_id)
            )
            # Booked consultations without a queue token close on booking
            # evidence alone (CONSULT_CLOSURE_PAPER.md).
            if (
                appointment.patient_id != encounter.patient_id
                or appointment.associated_encounter_id != encounter.id
            ):
                raise Http404("Consult close context not found")
            if appointment.token_id:
                token = (
                    Token._base_manager.select_for_update(of=("self",))  # noqa: SLF001
                    .select_related("queue__resource", "category")
                    .get(pk=appointment.token_id)
                )
                subqueues = list(
                    TokenSubQueue._base_manager.select_for_update(of=("self",))  # noqa: SLF001
                    .filter(current_token=token)
                    .order_by("pk")
                )
                if (
                    token.patient_id != encounter.patient_id
                    or token.booking_id != appointment.id
                    or token.facility_id != encounter.facility_id
                ):
                    raise Http404("Consult close context not found")

        artifact = (
            ReportUpload._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(
                form_submission=source,
                source_version=source.resource_version,
            )
            .first()
        )
        if not self._valid_form_source(source):
            blockers.add("form_not_finalized")
        if not artifact or not self._valid_form_artifact(source, artifact):
            blockers.add("form_artifact_invalid")

        medication_actions = self._medication_actions(
            source,
            encounter,
            request_spec.medication_outcome,
            blockers,
        )
        correspondence = self._correspondence_evidence(
            source,
            encounter,
            request_spec,
            blockers,
        )
        correspondence_objects = correspondence.pop("_objects")

        if encounter.status in CLINICALLY_CLOSED_CHOICES:
            blockers.add("encounter_terminal")
        elif encounter.status != StatusChoices.in_progress.value:
            blockers.add("encounter_state_stale")
        if token and token.status != TokenStatusOptions.IN_PROGRESS.value:
            blockers.add("token_state_stale")
        if appointment and (
            appointment.status != BookingStatusChoices.in_consultation.value
        ):
            blockers.add("booking_state_stale")
        if blockers:
            return self._preflight_blocked(blockers)

        close_context = {
            "appointment": appointment,
            "artifact": artifact,
            "closures": closures,
            "department": department_link.organization,
            "encounter": encounter,
            "source": source,
            "subqueues": subqueues,
            "token": token,
            **correspondence_objects,
        }
        self._authorize_close_context(close_context)

        policy_id = (
            CONSULT_CLOSE_POLICY_ID
            if appointment
            else (
                EMERGENCY_CLOSE_POLICY_ID
                if encounter.encounter_class == "emer"
                else UNSCHEDULED_CONSULT_CLOSE_POLICY_ID
            )
        )
        candidate = {
            "encounter": encounter.external_id,
            "patient": encounter.patient.external_id,
            "facility": encounter.facility.external_id,
            "department": department_link.organization.external_id,
            "token": self._external_uuid(token),
            "appointment": self._external_uuid(appointment),
            "expected_encounter_status": StatusChoices.in_progress.value,
            "expected_encounter_modified_at": encounter.modified_date,
            "expected_token_status": TokenStatusOptions.IN_PROGRESS.value
            if token
            else None,
            "expected_token_modified_at": token.modified_date if token else None,
            "expected_booking_status": BookingStatusChoices.in_consultation.value
            if appointment
            else None,
            "expected_booking_modified_at": appointment.modified_date
            if appointment
            else None,
            "policy_id": policy_id,
            "policy_version": CONSULT_CLOSE_POLICY_VERSION,
            "policy_hash": consult_close_policy_hash(
                required_forms,
                policy_id,
            ),
            "preflight_version": CONSULT_CLOSE_PREFLIGHT_VERSION,
            "form_submission": source.external_id,
            "form_source_version": source.resource_version,
            "form_source_hash": source.finalized_snapshot_hash,
            "form_artifact": artifact.external_id,
            "form_artifact_hash": artifact.artifact_sha256,
            "medication_outcome": request_spec.medication_outcome,
            "medication_actions": medication_actions,
            **correspondence,
        }
        preflight_hash = consult_close_preflight_hash(candidate)
        candidate["preflight_hash"] = preflight_hash
        candidate = ConsultCloseCommandCandidateSpec.model_validate(
            candidate
        ).model_dump(mode="python")
        return {
            "ready": True,
            "blocker_codes": [],
            "command_candidate": candidate,
            "checked_at": timezone.now(),
            "preflight_version": CONSULT_CLOSE_PREFLIGHT_VERSION,
            "preflight_hash": preflight_hash,
            "_context": close_context,
        }

    def _medication_actions(self, source, encounter, outcome, blockers):
        responses = list(
            QuestionnaireResponse._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(
                form_submission=source,
                structured_response_type="medication_request",
            )
            .order_by("pk")
        )
        response_counts = Counter()
        malformed = False
        for response in responses:
            value = response.structured_responses
            medication_data = (
                value.get("medication_request", {}) if isinstance(value, dict) else {}
            )
            medication_id = medication_data.get("id")
            if response.deleted or response.status != "completed" or not medication_id:
                malformed = True
            else:
                response_counts[str(medication_id)] += 1
        medications = list(
            MedicationRequest._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(encounter=encounter, deleted=False)
            .order_by("external_id")
        )
        by_id = {str(item.external_id): item for item in medications}
        if set(by_id) != set(response_counts):
            malformed = True
        actions = []
        for medication_id, medication in by_id.items():
            count = response_counts.get(medication_id, 0)
            if (
                count != 1
                or medication.patient_id != source.patient_id
                or medication.encounter_id != encounter.id
                or medication.deleted
                or medication.status not in CONFIRMED_MEDICATION_STATUSES
                or medication.intent != "order"
                or medication.do_not_perform
                or not medication.client_request_id
                or not self._valid_sha256(medication.client_request_payload_hash)
            ):
                malformed = True
                continue
            actions.append(
                {
                    "id": medication.external_id,
                    "client_request_id": medication.client_request_id,
                }
            )
        if malformed or len(actions) != len(medications):
            blockers.add("medication_incomplete")
        if outcome == "completed" and not actions:
            blockers.add("medication_incomplete")
        if outcome == "not_required" and (responses or actions):
            blockers.add("medication_incomplete")
        return actions

    def _correspondence_evidence(self, source, encounter, spec, blockers):  # noqa: PLR0911, PLR0912, PLR0915
        empty = {
            "correspondence_outcome": spec.correspondence_outcome,
            "correspondence_compilation": None,
            "correspondence_delivery": None,
            "correspondence_delivery_event_sequence": None,
            "correspondence_delivery_event_hash": None,
            "correspondence_case": None,
            "correspondence_case_version": None,
            "correspondence_case_hash": None,
            "_objects": {},
        }
        if spec.correspondence_outcome == "not_required":
            compilations = list(
                CorrespondenceCompilation._base_manager.select_for_update(of=("self",))  # noqa: SLF001
                .filter(form_submission__series_id=source.series_id, deleted=False)
                .order_by("pk")
            )
            corrections = list(
                CorrespondenceSourceCorrection._base_manager.select_for_update(  # noqa: SLF001
                    of=("self",)
                )
                .filter(source_head__series_id=source.series_id, deleted=False)
                .order_by("sequence")
            )
            outboxes = list(
                CorrespondenceCorrectionOutbox._base_manager.select_for_update(  # noqa: SLF001
                    of=("self",)
                )
                .filter(
                    source_correction__source_head__series_id=source.series_id,
                    deleted=False,
                )
                .order_by("pk")
            )
            cases = list(
                CorrespondenceCorrectionCase._base_manager.select_for_update(  # noqa: SLF001
                    of=("self",)
                )
                .filter(source_head__series_id=source.series_id, deleted=False)
                .order_by("pk")
            )
            reviews = list(
                CorrespondenceReview._base_manager.select_for_update(of=("self",))  # noqa: SLF001
                .filter(compilation__in=compilations, deleted=False)
                .order_by("pk")
            )
            revisions = list(
                CorrespondenceLetterRevision._base_manager.select_for_update(  # noqa: SLF001
                    of=("self",)
                )
                .filter(
                    letter__review__compilation__in=compilations,
                    deleted=False,
                )
                .order_by("pk")
            )
            deliveries = list(
                CorrespondenceDelivery._base_manager.select_for_update(of=("self",))  # noqa: SLF001
                .filter(review__compilation__in=compilations, deleted=False)
                .order_by("pk")
            )
            if reviews or revisions or deliveries or corrections or outboxes or cases:
                blockers.add("correspondence_incomplete")
            return empty

        if spec.correspondence_outcome == "correction_resolved":
            cases = list(
                CorrespondenceCorrectionCase._base_manager.select_for_update(  # noqa: SLF001
                    of=("self",)
                )
                .select_related(
                    "current_submission",
                    "original_compilation",
                    "original_review",
                    "original_delivery",
                )
                .filter(
                    source_head__series_id=source.series_id,
                    current_submission=source,
                    deleted=False,
                )
                .order_by("pk")
            )
            if len(cases) != 1:
                blockers.add("correspondence_incomplete")
                return empty
            case = cases[0]
            compilation = (
                CorrespondenceCompilation._base_manager.select_for_update(of=("self",))  # noqa: SLF001
                .select_related("form_submission", "form_artifact")
                .get(pk=case.original_compilation_id)
            )
            try:
                lock_and_verify_delivery_ledger(case.original_delivery)
                original_latest = latest_delivery_event(
                    case.original_delivery,
                    lock=True,
                )
            except CorrespondenceDeliveryIntegrityError:
                blockers.add("correspondence_incomplete")
                return empty
            resolution_valid = (
                case.resolution_mode == "replacement_acknowledged"
                and case.replacement_status == "acknowledged"
                and case.notification_status == "acknowledged"
                and case.paper_reconciliation_status == "acknowledged"
            ) or (
                case.resolution_mode == "original_not_delivered"
                and original_latest.certainty == "not_delivered"
                and case.delivery_certainty == "not_delivered"
                and case.notification_status == "not_required"
                and case.paper_reconciliation_status == "not_required"
            )
            if (
                case.status != "resolved"
                or not resolution_valid
                or not compilation_frozen_integrity_valid(compilation)
                or not review_frozen_integrity_valid(case.original_review)
                or not correction_case_integrity_valid(case)
            ):
                blockers.add("correspondence_incomplete")
                return empty
            empty.update(
                {
                    "correspondence_compilation": compilation.external_id,
                    "correspondence_case": case.external_id,
                    "correspondence_case_version": case.resource_version,
                    "correspondence_case_hash": case.case_hash,
                }
            )
            empty["_objects"].update(
                {"compilation": compilation, "correction_case": case}
            )
            return empty

        compilation_query = (
            CorrespondenceCompilation._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .select_related("form_submission", "form_artifact")
            .filter(deleted=False)
        )
        prepared_revision = None
        if spec.correspondence_outcome == "paper_prepared":
            prepared_revision = (
                CorrespondenceLetterRevision._base_manager.select_for_update(  # noqa: SLF001
                    of=("self",)
                )
                .select_related("letter__review__compilation")
                .filter(
                    letter__review__compilation__form_submission=source,
                    letter__review__compilation__deleted=False,
                    letter__review__deleted=False,
                    letter__deleted=False,
                    status="finalized",
                    deleted=False,
                    final_artifact__deleted=False,
                    final_artifact__is_archived=False,
                    final_artifact__upload_completed=True,
                )
                .order_by("-finalized_at", "-pk")
                .first()
            )
            if not prepared_revision:
                blockers.add("correspondence_incomplete")
                return empty
            compilation = prepared_revision.letter.review.compilation
        else:
            compilation = get_object_or_404(
                compilation_query,
                external_id=spec.correspondence_compilation,
            )
        if (
            compilation.encounter_id != encounter.id
            or compilation.patient_id != encounter.patient_id
            or compilation.facility_id != encounter.facility_id
        ):
            raise Http404("Consult close correspondence context not found")
        empty["correspondence_compilation"] = compilation.external_id
        empty["_objects"]["compilation"] = compilation
        correction_lineage_exists = (
            hasattr(compilation, "replacement_attempt")
            or CorrespondenceCorrectionCase._base_manager.filter(  # noqa: SLF001
                Q(source_head__series_id=source.series_id)
                | Q(current_submission=source)
                | Q(original_compilation=compilation),
                deleted=False,
            ).exists()
        )
        if (
            correction_lineage_exists
            or compilation.form_submission_id != source.id
            or not compilation_frozen_integrity_valid(compilation)
        ):
            blockers.add("correspondence_stale")
            return empty
        review = (
            CorrespondenceReview._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(compilation=compilation, deleted=False)
            .first()
        )
        if spec.correspondence_outcome == "paper_prepared":
            if (
                not review
                or not review_frozen_integrity_valid(review)
                or prepared_revision is None
                or prepared_revision.letter.review_id != review.id
                or not correspondence_revision_frozen_integrity_valid(prepared_revision)
                or correspondence_revision_artifact_status(prepared_revision)
                != "available"
            ):
                blockers.add("correspondence_incomplete")
            return empty
        delivery = None
        if review:
            delivery = (
                CorrespondenceDelivery._base_manager.select_for_update(of=("self",))  # noqa: SLF001
                .filter(review=review, deleted=False)
                .order_by("pk")
                .last()
            )
        if not delivery:
            blockers.add("correspondence_incomplete")
            return empty
        if not review_frozen_integrity_valid(review):
            blockers.add("correspondence_incomplete")
            return empty
        try:
            lock_and_verify_delivery_ledger(delivery)
            event = latest_delivery_event(delivery, lock=True)
        except CorrespondenceDeliveryIntegrityError:
            blockers.add("correspondence_incomplete")
            return empty
        if event.event_type != "acknowledged":
            blockers.add("correspondence_incomplete")
            return empty
        empty.update(
            {
                "correspondence_delivery": delivery.external_id,
                "correspondence_delivery_event_sequence": event.sequence,
                "correspondence_delivery_event_hash": event.event_hash,
            }
        )
        empty["_objects"].update({"delivery": delivery, "event": event})
        return empty

    def _commit_close(self, context, request_spec):
        encounter = context["encounter"]
        appointment = context["appointment"]
        token = context["token"]
        closed_at = timezone.now()

        history = (
            encounter.status_history
            if isinstance(encounter.status_history, dict)
            else {}
        )
        status_history = list(history.get("history", []))
        status_history.append(
            {"status": StatusChoices.completed.value, "moved_at": str(closed_at)}
        )
        encounter.status_history = {**history, "history": status_history}
        period = encounter.period if isinstance(encounter.period, dict) else {}
        encounter.period = {**period, "end": str(closed_at)}
        encounter.status = StatusChoices.completed.value
        encounter.updated_by = self.request.user
        encounter.save(
            update_fields=[
                "status",
                "status_history",
                "period",
                "updated_by",
                "modified_date",
            ]
        )
        close_related_location_from_encounter(encounter)
        disassociate_device_from_encounter(encounter)
        if encounter.current_location_id:
            encounter.current_location = None
            encounter.updated_by = self.request.user
            encounter.save(
                update_fields=["current_location", "updated_by", "modified_date"]
            )

        if appointment:
            appointment.status = BookingStatusChoices.fulfilled.value
            appointment.updated_by = self.request.user
            appointment.save(update_fields=["status", "updated_by", "modified_date"])
        if token:
            token.status = TokenStatusOptions.FULFILLED.value
            token.is_next = False
            token.updated_by = self.request.user
            token.save(
                update_fields=["status", "is_next", "updated_by", "modified_date"]
            )
        for subqueue in context["subqueues"]:
            if subqueue.current_token_id == token.id:
                subqueue.current_token = None
                subqueue.updated_by = self.request.user
                subqueue.save(
                    update_fields=["current_token", "updated_by", "modified_date"]
                )

        previous = context["closures"][-1] if context["closures"] else None
        closure = ConsultClosure(
            encounter=encounter,
            closure_number=(previous.closure_number + 1 if previous else 1),
            previous_closure=previous,
            patient=encounter.patient,
            facility=encounter.facility,
            department=context["department"],
            token=token,
            appointment=appointment,
            policy_id=request_spec.policy_id,
            policy_version=request_spec.policy_version,
            policy_hash=request_spec.policy_hash,
            preflight_version=request_spec.preflight_version,
            preflight_hash=request_spec.preflight_hash,
            form_submission=context["source"],
            form_source_version=request_spec.form_source_version,
            form_source_hash=request_spec.form_source_hash,
            form_artifact=context["artifact"],
            form_artifact_hash=request_spec.form_artifact_hash,
            medication_outcome=request_spec.medication_outcome,
            medication_actions=[
                item.model_dump(mode="json") for item in request_spec.medication_actions
            ],
            correspondence_outcome=request_spec.correspondence_outcome,
            correspondence_compilation=context.get("compilation"),
            correspondence_delivery=context.get("delivery"),
            correspondence_delivery_event_sequence=request_spec.correspondence_delivery_event_sequence,
            correspondence_delivery_event_hash=request_spec.correspondence_delivery_event_hash,
            correspondence_case=context.get("correction_case"),
            correspondence_case_version=request_spec.correspondence_case_version,
            correspondence_case_hash=request_spec.correspondence_case_hash,
            encounter_status=StatusChoices.completed.value,
            token_status=TokenStatusOptions.FULFILLED.value
            if token
            else "not_required",
            booking_status=BookingStatusChoices.fulfilled.value
            if appointment
            else "not_required",
            closed_at=closed_at,
            closed_by=self.request.user,
            closure_hash="",
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        closure.closure_hash = consult_closure_snapshot_hash(
            self._closure_hash_material(closure)
        )
        closure.save(force_insert=True)
        return closure

    def _command_replay(self, request_spec, encounter, payload_hash):
        command = (
            ConsultClosureCommand._base_manager.select_related(  # noqa: SLF001
                "actor",
                "encounter",
                "result_closure__patient",
                "result_closure__facility",
                "result_closure__department",
                "result_closure__token",
                "result_closure__appointment",
                "result_closure__form_submission",
                "result_closure__form_artifact",
                "result_closure__closed_by",
            )
            .filter(client_request_id=request_spec.client_request_id, deleted=False)
            .first()
        )
        if not command:
            return None
        if not all(
            [
                command.actor_id == self.request.user.id,
                command.encounter_id == encounter.id,
                command.payload_hash == payload_hash,
            ]
        ):
            return self._blocked(["idempotency_conflict"])
        projection = None
        for _attempt in range(2):
            with transaction.atomic():
                projection = self._locked_integrity_projection(encounter)
                if projection.get("retry_required"):
                    continue
                command = (
                    ConsultClosureCommand._base_manager.select_for_update(of=("self",))  # noqa: SLF001
                    .select_related("actor", "encounter", "result_closure")
                    .get(pk=command.pk)
                )
                if (
                    not projection["healthy"]
                    or projection["closure"].id != command.result_closure_id
                    or command.command_hash
                    != consult_closure_command_hash(
                        self._command_hash_material(command)
                    )
                    or command.result_snapshot
                    != self._closure_read(command.result_closure)
                ):
                    return self._blocked(["closure_integrity_failed"])
                break
        else:
            return self._blocked(["closure_projection_unstable"])
        self._authorize_encounter_read(projection["encounter"])
        read_report_authorizer(
            self.request.user,
            command.result_closure.form_artifact.report_type,
            command.result_closure.form_artifact.associating_id,
        )
        return Response(
            self._command_response_data(
                request_spec.client_request_id,
                command.result_closure,
                replayed=True,
            ),
            status=status.HTTP_200_OK,
        )

    def _integrity_projection(self, reference):
        for _attempt in range(2):
            with transaction.atomic():
                projection = self._locked_integrity_projection(reference)
            if not projection.get("retry_required"):
                return projection
        return {
            "encounter": reference,
            "closure": None,
            "healthy": False,
            "integrity_valid": False,
            "recovery": None,
            "unstable": True,
        }

    @staticmethod
    def _latest_closure_reference(reference):
        return (
            ConsultClosure._base_manager.select_related("form_submission")  # noqa: SLF001
            .filter(encounter=reference, deleted=False)
            .order_by("-closure_number")
            .first()
        )

    def _locked_integrity_projection(self, reference):
        closure_reference = self._latest_closure_reference(reference)
        current_source = None
        source_integrity = True
        if closure_reference:
            try:
                _head, current_source = lock_current_finalized_form_series(
                    closure_reference.form_submission
                )
            except FormSubmissionSeriesHeadIntegrityError:
                source_integrity = False
        encounter = (
            Encounter._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .select_related("patient", "facility", "appointment")
            .get(pk=reference.pk)
        )
        closures = list(
            ConsultClosure._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .select_related(
                "previous_closure",
                "patient",
                "facility",
                "department",
                "token",
                "appointment",
                "form_submission__questionnaire",
                "form_artifact",
                "closed_by",
                "correspondence_compilation",
                "correspondence_delivery",
                "correspondence_case",
            )
            .filter(encounter=encounter, deleted=False)
            .order_by("closure_number")
        )
        recoveries = list(
            ConsultClosureRecoveryTask._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(encounter=encounter, deleted=False)
            .order_by("created_date")
        )
        locked_latest = closures[-1] if closures else None
        if (closure_reference is None) != (locked_latest is None) or (
            closure_reference is not None
            and locked_latest is not None
            and closure_reference.pk != locked_latest.pk
        ):
            return {
                "encounter": encounter,
                "closure": None,
                "healthy": False,
                "integrity_valid": False,
                "recovery": None,
                "retry_required": True,
            }
        pending = next((item for item in recoveries if item.status == "pending"), None)
        if not closures:
            return {
                "encounter": encounter,
                "closure": None,
                "healthy": pending is None,
                "integrity_valid": pending is None,
                "recovery": pending or (recoveries[-1] if recoveries else None),
            }

        closure = closures[-1]
        appointment = (
            TokenBooking._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .select_related("token_slot__resource")
            .filter(pk=closure.appointment_id)
            .first()
        )
        token = (
            Token._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .select_related("queue__resource")
            .filter(pk=closure.token_id)
            .first()
        )
        subqueue_points_to_token = token is not None and (
            TokenSubQueue._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(current_token=token)
            .exists()
        )
        location_points_to_encounter = (
            FacilityLocation._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(current_encounter=encounter)
            .exists()
        )
        device_points_to_encounter = (
            Device._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(current_encounter=encounter)
            .exists()
        )

        integrity_valid = all(
            [
                source_integrity,
                current_source is not None,
                current_source and current_source.pk == closure.form_submission_id,
                self._closure_history_integrity_valid(closures),
                all(self._recovery_integrity_valid(item) for item in recoveries),
                encounter.status == StatusChoices.completed.value,
                (
                    (
                        (
                            closure.policy_id == EMERGENCY_CLOSE_POLICY_ID
                            and encounter.encounter_class == "emer"
                        )
                        or (
                            closure.policy_id == UNSCHEDULED_CONSULT_CLOSE_POLICY_ID
                            and encounter.encounter_class != "emer"
                        )
                    )
                    and encounter.appointment_id is None
                    and closure.appointment_id is None
                    and closure.token_id is None
                    and closure.token_status == "not_required"
                    and closure.booking_status == "not_required"
                )
                if appointment is None
                else (
                    closure.policy_id == CONSULT_CLOSE_POLICY_ID
                    and encounter.appointment_id == appointment.id
                    and appointment.status == BookingStatusChoices.fulfilled.value
                    and appointment.associated_encounter_id == encounter.id
                    and self._closure_token_healthy(closure, appointment, token)
                ),
                encounter.current_location_id is None,
                not subqueue_points_to_token,
                not location_points_to_encounter,
                not device_points_to_encounter,
            ]
        )
        if integrity_valid:
            try:
                integrity_valid = self._closure_evidence_current(
                    closure,
                    current_source,
                    encounter,
                )
            except Exception:
                integrity_valid = False
        if not integrity_valid:
            pending = pending or self._ensure_pending_recovery(
                encounter,
                "closure_integrity_failed",
            )
        return {
            "encounter": encounter,
            "closure": closure,
            "appointment": appointment,
            "token": token,
            "healthy": integrity_valid and pending is None,
            "integrity_valid": integrity_valid,
            "recovery": pending or (recoveries[-1] if recoveries else None),
        }

    def _closure_evidence_current(self, closure, source, encounter):
        if (
            not self._valid_form_source(source)
            or not self._valid_form_artifact(source, closure.form_artifact)
            or closure.form_source_version != source.resource_version
            or closure.form_source_hash != source.finalized_snapshot_hash
            or closure.form_artifact_hash != closure.form_artifact.artifact_sha256
        ):
            return False
        required_forms = self._required_form_slugs(closure.department)
        if (
            source.questionnaire.slug not in required_forms
            or closure.policy_hash
            != consult_close_policy_hash(required_forms, closure.policy_id)
        ):
            return False
        blockers = set()
        medication_actions = self._medication_actions(
            source,
            encounter,
            closure.medication_outcome,
            blockers,
        )
        normalized_actions = [
            {"id": str(item["id"]), "client_request_id": str(item["client_request_id"])}
            for item in medication_actions
        ]
        if normalized_actions != closure.medication_actions:
            return False
        correspondence_spec = ConsultClosePreflightSpec.model_validate(
            {
                "patient": closure.patient.external_id,
                "facility": closure.facility.external_id,
                "department": closure.department.external_id,
                "form_submission": source.external_id,
                "medication_outcome": closure.medication_outcome,
                "correspondence_outcome": closure.correspondence_outcome,
                "correspondence_compilation": (
                    closure.correspondence_compilation.external_id
                    if closure.correspondence_outcome == "delivery_acknowledged"
                    and closure.correspondence_compilation
                    else None
                ),
            }
        )
        evidence = self._correspondence_evidence(
            source,
            encounter,
            correspondence_spec,
            blockers,
        )
        evidence.pop("_objects")
        expected = {
            "correspondence_outcome": closure.correspondence_outcome,
            "correspondence_compilation": self._external_uuid(
                closure.correspondence_compilation
            ),
            "correspondence_delivery": self._external_uuid(
                closure.correspondence_delivery
            ),
            "correspondence_delivery_event_sequence": closure.correspondence_delivery_event_sequence,
            "correspondence_delivery_event_hash": closure.correspondence_delivery_event_hash,
            "correspondence_case": self._external_uuid(closure.correspondence_case),
            "correspondence_case_version": closure.correspondence_case_version,
            "correspondence_case_hash": closure.correspondence_case_hash,
        }
        return not blockers and evidence == expected

    @staticmethod
    def _closure_token_healthy(closure, appointment, token):
        """Queue tokens are optional for booked consults (CONSULT_CLOSURE_PAPER.md)."""
        if closure.token_id is None:
            return (
                token is None
                and appointment.token_id is None
                and closure.token_status == "not_required"
            )
        return (
            token is not None
            and appointment.token_id == token.id
            and token.status == TokenStatusOptions.FULFILLED.value
            and token.booking_id == appointment.id
            and token.patient_id == closure.patient_id
            and closure.token_status == TokenStatusOptions.FULFILLED.value
        )

    def _closure_history_integrity_valid(self, closures):
        previous = None
        for number, closure in enumerate(closures, start=1):
            if (
                closure.closure_number != number
                or closure.previous_closure_id
                != (previous.id if previous is not None else None)
                or closure.closure_hash
                != consult_closure_snapshot_hash(self._closure_hash_material(closure))
            ):
                return False
            commands = list(
                ConsultClosureCommand._base_manager.select_for_update(of=("self",))  # noqa: SLF001
                .select_related("actor", "encounter", "result_closure")
                .filter(result_closure=closure, deleted=False)
                .order_by("pk")
            )
            if not commands:
                return False
            for command in commands:
                if (
                    command.encounter_id != closure.encounter_id
                    or command.result_snapshot != self._closure_read(closure)
                    or command.command_hash
                    != consult_closure_command_hash(
                        self._command_hash_material(command)
                    )
                ):
                    return False
            previous = closure
        return True

    def _ensure_pending_recovery(self, encounter, safe_code):
        existing = (
            ConsultClosureRecoveryTask._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(encounter=encounter, status="pending", deleted=False)
            .first()
        )
        if existing:
            return existing
        recovery = ConsultClosureRecoveryTask(
            encounter=encounter,
            status="pending",
            safe_code=safe_code,
            resolved_at=None,
            resolved_by=None,
            resolution_request_id=None,
            resolution_payload_hash=None,
            resolution_hash=None,
            recovery_hash="",
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        recovery.recovery_hash = consult_closure_recovery_hash(
            self._recovery_open_hash_material(recovery)
        )
        recovery.save(force_insert=True)
        return recovery

    @staticmethod
    def _required_form_slugs(department):
        configured = settings.CONSULT_CLOSE_REQUIRED_FORMS_BY_DEPARTMENT
        keys = [str(department.external_id), department.name.strip().casefold()]
        values = next((configured[key] for key in keys if key in configured), [])
        return sorted({str(value).strip() for value in values if str(value).strip()})

    def _authorize_close_context(self, context):
        encounter = context["encounter"]
        if not AuthorizationController.call(
            "can_update_encounter_obj",
            self.request.user,
            encounter,
        ):
            raise PermissionDenied("Permission denied for consult close")
        if context["appointment"] and not AuthorizationController.call(
            "can_write_booking",
            context["appointment"].token_slot.resource,
            self.request.user,
        ):
            raise PermissionDenied("Permission denied for consult close")
        if context["token"] and not AuthorizationController.call(
            "can_write_token",
            context["token"].queue.resource,
            self.request.user,
        ):
            raise PermissionDenied("Permission denied for consult close")
        read_report_authorizer(
            self.request.user,
            context["artifact"].report_type,
            context["artifact"].associating_id,
        )

    def _authorize_recovery_resolution(self, projection):
        closure = projection.get("closure")
        if not closure:
            raise PermissionDenied("Permission denied for consult recovery")
        if not AuthorizationController.call(
            "can_restart_encounter_obj",
            self.request.user,
            projection["encounter"],
        ):
            raise PermissionDenied("Permission denied for consult recovery")
        if projection["appointment"] and not AuthorizationController.call(
            "can_write_booking",
            projection["appointment"].token_slot.resource,
            self.request.user,
        ):
            raise PermissionDenied("Permission denied for consult recovery")
        if projection["token"] and not AuthorizationController.call(
            "can_write_token",
            projection["token"].queue.resource,
            self.request.user,
        ):
            raise PermissionDenied("Permission denied for consult recovery")
        read_report_authorizer(
            self.request.user,
            closure.form_artifact.report_type,
            closure.form_artifact.associating_id,
        )

    def _authorize_encounter_read(self, encounter):
        if AuthorizationController.call(
            "can_view_clinical_data",
            self.request.user,
            encounter.patient,
        ) or (
            AuthorizationController.call(
                "can_view_encounter_obj",
                self.request.user,
                encounter,
            )
            and AuthorizationController.call(
                "can_view_encounter_clinical_data",
                self.request.user,
                encounter,
            )
        ):
            return
        raise PermissionDenied("Permission denied for consult close")

    def _reference_encounter(self):
        return get_object_or_404(
            Encounter._base_manager.select_related("patient", "facility"),  # noqa: SLF001
            external_id=self.kwargs["external_id"],
            deleted=False,
        )

    @staticmethod
    def _valid_form_source(source):
        if (
            source.deleted
            or source.status != "submitted"
            or not source.finalized_snapshot_hash
            or finalized_form_submission_snapshot_hash(source)
            != source.finalized_snapshot_hash
            or has_unresolved_placeholder(source.response_dump)
        ):
            return False
        try:
            validate_response_dump(source.response_dump)
        except Exception:
            return False
        return True

    @staticmethod
    def _valid_form_artifact(source, artifact):
        return all(
            [
                not artifact.deleted,
                not artifact.is_archived,
                artifact.upload_completed,
                artifact.form_submission_id == source.id,
                artifact.patient_id == source.patient_id,
                artifact.encounter_id == source.encounter_id,
                artifact.source_version == source.resource_version,
                artifact.source_snapshot_hash == source.finalized_snapshot_hash,
                ConsultClosureViewSet._valid_sha256(artifact.artifact_sha256),
                artifact.report_type == "encounter_report",
            ]
        )

    @staticmethod
    def _valid_sha256(value):
        return (
            isinstance(value, str)
            and len(value) == SHA256_LENGTH
            and all(character in "0123456789abcdef" for character in value)
        )

    @staticmethod
    def _preflight_blocked(blockers):
        codes = sorted(set(blockers))
        return {
            "ready": False,
            "blocker_codes": codes,
            "command_candidate": None,
            "checked_at": timezone.now(),
            "preflight_version": CONSULT_CLOSE_PREFLIGHT_VERSION,
            "preflight_hash": consult_close_preflight_hash(
                {"blocker_codes": codes, "ready": False}
            ),
        }

    @staticmethod
    def _blocked(blockers):
        return ConsultClosureViewSet._no_store(
            Response(
                {
                    "error": "consult_closure_blocked",
                    "blocker_codes": sorted(set(blockers)),
                },
                status=status.HTTP_409_CONFLICT,
            )
        )

    @staticmethod
    def _no_store(response):
        response["Cache-Control"] = "no-store"
        return response

    def _closure_read(self, closure):
        return {
            "id": str(closure.external_id),
            "closure_number": closure.closure_number,
            "previous_closure": self._external_id(closure.previous_closure),
            "encounter": str(closure.encounter.external_id),
            "patient": str(closure.patient.external_id),
            "facility": str(closure.facility.external_id),
            "department": str(closure.department.external_id),
            "token": self._external_id(closure.token),
            "appointment": self._external_id(closure.appointment),
            "status": "completed",
            "encounter_status": closure.encounter_status,
            "token_status": closure.token_status,
            "booking_status": closure.booking_status,
            "policy_id": closure.policy_id,
            "policy_version": closure.policy_version,
            "policy_hash": closure.policy_hash,
            "preflight_version": closure.preflight_version,
            "preflight_hash": closure.preflight_hash,
            "form_submission": str(closure.form_submission.external_id),
            "form_source_version": closure.form_source_version,
            "form_source_hash": closure.form_source_hash,
            "form_artifact": str(closure.form_artifact.external_id),
            "form_artifact_hash": closure.form_artifact_hash,
            "medication_outcome": closure.medication_outcome,
            "medication_actions": closure.medication_actions,
            "correspondence_outcome": closure.correspondence_outcome,
            "correspondence_compilation": self._external_id(
                closure.correspondence_compilation
            ),
            "correspondence_delivery": self._external_id(
                closure.correspondence_delivery
            ),
            "correspondence_delivery_event_sequence": closure.correspondence_delivery_event_sequence,
            "correspondence_delivery_event_hash": closure.correspondence_delivery_event_hash,
            "correspondence_case": self._external_id(closure.correspondence_case),
            "correspondence_case_version": closure.correspondence_case_version,
            "correspondence_case_hash": closure.correspondence_case_hash,
            "closed_at": closure.closed_at.isoformat().replace("+00:00", "Z"),
            "closed_by": str(closure.closed_by.external_id),
            "closure_hash": closure.closure_hash,
            "recovery_status": "not_required",
        }

    def _closure_hash_material(self, closure):
        value = self._closure_read(closure)
        value.pop("closure_hash", None)
        value.update(
            {
                "preflight_version": closure.preflight_version,
                "preflight_hash": closure.preflight_hash,
                "correspondence_delivery_event_sequence": closure.correspondence_delivery_event_sequence,
                "correspondence_delivery_event_hash": closure.correspondence_delivery_event_hash,
                "correspondence_case_version": closure.correspondence_case_version,
                "correspondence_case_hash": closure.correspondence_case_hash,
            }
        )
        return value

    @staticmethod
    def _command_hash_material(command):
        return {
            "actor": command.actor.external_id,
            "client_request_id": command.client_request_id,
            "encounter": command.encounter.external_id,
            "payload_hash": command.payload_hash,
            "result_closure": command.result_closure.external_id,
            "result_snapshot": command.result_snapshot,
        }

    def _command_response_data(self, client_request_id, closure, *, replayed):
        return {
            "client_request_id": str(client_request_id),
            "replayed": replayed,
            "closure": self._closure_read(closure),
        }

    @staticmethod
    def _external_id(value):
        return str(value.external_id) if value else None

    def _safe_recovery_read(self, recovery):
        valid = self._recovery_integrity_valid(recovery)
        return {
            "id": str(recovery.external_id),
            "status": recovery.status if valid else "pending",
            "safe_code": recovery.safe_code if valid else "recovery_integrity_failed",
            "recovery_hash": recovery.recovery_hash if valid else None,
            "created_at": recovery.created_date.isoformat().replace("+00:00", "Z"),
            "resolved_at": (
                recovery.resolved_at.isoformat().replace("+00:00", "Z")
                if valid and recovery.resolved_at
                else None
            ),
        }

    @staticmethod
    def _recovery_open_hash_material(recovery):
        return {
            "encounter": recovery.encounter.external_id,
            "resolved_at": None,
            "safe_code": recovery.safe_code,
            "status": "pending",
        }

    @staticmethod
    def _recovery_resolution_hash_material(recovery):
        return {
            "actor": recovery.resolved_by.external_id,
            "encounter": recovery.encounter.external_id,
            "opened_hash": recovery.recovery_hash,
            "recovery": recovery.external_id,
            "request_id": recovery.resolution_request_id,
            "payload_hash": recovery.resolution_payload_hash,
            "resolved_at": recovery.resolved_at,
            "status": recovery.status,
        }

    def _recovery_integrity_valid(self, recovery):
        opening_valid = recovery.recovery_hash == consult_closure_recovery_hash(
            self._recovery_open_hash_material(recovery)
        )
        if not opening_valid:
            return False
        if recovery.status == "pending":
            return all(
                value is None
                for value in [
                    recovery.resolved_at,
                    recovery.resolved_by_id,
                    recovery.resolution_request_id,
                    recovery.resolution_payload_hash,
                    recovery.resolution_hash,
                ]
            )
        if recovery.status != "resolved" or any(
            value is None
            for value in [
                recovery.resolved_at,
                recovery.resolved_by_id,
                recovery.resolution_request_id,
                recovery.resolution_payload_hash,
                recovery.resolution_hash,
            ]
        ):
            return False
        return recovery.resolution_hash == consult_closure_recovery_resolution_hash(
            self._recovery_resolution_hash_material(recovery)
        )

    def _recovery_resolution_replay_valid(
        self,
        recovery,
        request_spec,
        encounter,
        payload_hash,
    ):
        return all(
            [
                recovery.encounter_id == encounter.id,
                recovery.external_id == request_spec.recovery,
                recovery.recovery_hash == request_spec.expected_recovery_hash,
                recovery.resolved_by_id == self.request.user.id,
                recovery.resolution_payload_hash == payload_hash,
                self._recovery_integrity_valid(recovery),
            ]
        )

    def _recovery_resolution_response(self, request_id, recovery, replayed):
        return {
            "client_request_id": str(request_id),
            "replayed": replayed,
            "recovery": self._safe_recovery_read(recovery),
        }

    @staticmethod
    def _external_uuid(value):
        return value.external_id if value else None
