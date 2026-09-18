import hashlib
import logging

from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from care.emr.api.viewsets.base import EMRBaseViewSet
from care.emr.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care.emr.correspondence.correction import (
    CorrespondenceCorrectionIntegrityError,
    correction_case_integrity_valid,
    lock_current_finalized_form_series,
    paper_attestation_integrity_valid,
    replacement_attempt_integrity_valid,
)
from care.emr.correspondence.delivery import (
    CorrespondenceDeliveryIntegrityError,
    CorrespondenceDispatchNotCurrentError,
    append_delivery_event,
    latest_delivery_event,
    lock_and_assert_correspondence_dispatch_current,
    lock_and_verify_delivery_ledger,
    read_and_verify_correspondence_artifact,
)
from care.emr.correspondence.delivery_adapters import (
    CorrespondenceDeliveryAdapterUnavailableError,
    get_correspondence_delivery_adapter,
)
from care.emr.correspondence.recipient import (
    InvalidVerifiedRecipientError,
    recipient_content_hash,
    recipient_snapshot,
    validate_verified_recipient,
)
from care.emr.correspondence.replacement import (
    command_response,
    commit_case_command,
)
from care.emr.correspondence.review import correspondence_review_hash
from care.emr.models.correspondence_correction import (
    CorrespondenceCorrectionCase,
    CorrespondenceCorrectionCommand,
    CorrespondencePaperReconciliationAttestation,
    CorrespondenceReplacementAttempt,
)
from care.emr.models.correspondence_delivery import (
    CorrespondenceDelivery,
    CorrespondenceDeliveryAttempt,
)
from care.emr.models.correspondence_letter import (
    CorrespondenceLetterRevision,
)
from care.emr.models.correspondence_review import (
    CorrespondenceRecipient,
    CorrespondenceReview,
)
from care.emr.models.encounter import Encounter
from care.emr.models.report.report_upload import ReportUpload
from care.emr.reports.authorizers.utils import (
    read_report_authorizer,
    write_report_authorizer,
)
from care.emr.reports.correspondence_letter import (
    CorrespondenceLetterRenderError,
    build_controlled_correction_copy_html,
    render_correspondence_letter_pdf,
)
from care.emr.resources.correspondence import (
    CompileCorrespondenceSpec,
    canonical_sha256,
)
from care.emr.resources.correspondence_delivery import (
    correspondence_delivery_attempt_hash,
    correspondence_delivery_hash,
    correspondence_delivery_provider_key,
)
from care.emr.resources.correspondence_replacement import (
    CorrespondenceCorrectionCommandResponseSpec,
    CorrespondenceCorrectionCommandSpec,
    canonical_correction_command_payload_hash,
    correspondence_correction_command_hash,
    correspondence_paper_attestation_hash,
    correspondence_replacement_attempt_hash,
)
from care.emr.tasks.correspondence_delivery import (
    dispatch_correspondence_delivery_attempt,
)
from care.emr.workflow_capabilities import (
    require_correspondence_delivery_enabled,
    require_workflow_mutations_enabled,
)
from care.security.authorization.base import AuthorizationController
from care.utils.shortcuts import get_object_or_404
from care_suriname.api.viewsets.correspondence import CorrespondenceCompilationViewSet
from care_suriname.api.viewsets.correspondence_letter import CorrespondenceLetterViewSet

logger = logging.getLogger(__name__)


class CorrespondenceCorrectionCaseViewSet(ClinicalNoStoreResponseMixin, EMRBaseViewSet):
    database_model = CorrespondenceCorrectionCase

    @extend_schema(
        request=CorrespondenceCorrectionCommandSpec,
        responses={
            200: CorrespondenceCorrectionCommandResponseSpec,
            201: CorrespondenceCorrectionCommandResponseSpec,
        },
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-command")
    def idempotent_command(self, request, *args, **kwargs):  # noqa: PLR0911
        request_spec = CorrespondenceCorrectionCommandSpec.model_validate(request.data)
        reference = self._get_reference(self.kwargs["external_id"])
        self._authorize_read(reference)
        payload_hash = canonical_correction_command_payload_hash(
            request_spec,
            actor_id=request.user.external_id,
            case_id=reference.external_id,
        )
        if replay := self._replay(reference, request_spec, payload_hash):
            return replay
        facility_id = reference.original_compilation.facility.external_id
        require_workflow_mutations_enabled(facility_id)
        if request_spec.command_type in {"send_replacement", "retry_replacement"}:
            require_correspondence_delivery_enabled(facility_id)
        uploaded_artifact = None
        try:
            with transaction.atomic():
                head, current = lock_current_finalized_form_series(
                    reference.current_submission
                )
                Encounter._base_manager.select_for_update(  # noqa: SLF001
                    of=("self",)
                ).get(pk=current.encounter_id)
                case = self._lock_case(reference, head, current)
                self._authorize_read(case)
                self._authorize_mutation(case)
                self._assert_case_compare_and_swap(case, request_spec)
                if replay := self._replay(case, request_spec, payload_hash):
                    return replay
                prepared = self._prepare_after_case_lock(
                    case,
                    request_spec,
                    current,
                )
                handler = getattr(self, f"_command_{request_spec.command_type}")
                result = handler(
                    case,
                    request_spec,
                    payload_hash,
                    current=current,
                    prepared=prepared,
                )
                uploaded_artifact = result.get("uploaded_artifact")
                command = commit_case_command(
                    case=case,
                    request_spec=request_spec,
                    payload_hash=payload_hash,
                    actor=request.user,
                    event_type=result["event_type"],
                    safe_code=result["safe_code"],
                    target_attempt=result.get("target_attempt"),
                    result_attempt=result.get("result_attempt"),
                    result_revision=result.get("result_revision"),
                    result_artifact=result.get("result_artifact"),
                    result_delivery=result.get("result_delivery"),
                    result_attestation=result.get("result_attestation"),
                )
        except IntegrityError:
            self._compensate_artifact(uploaded_artifact)
            if replay := self._replay(reference, request_spec, payload_hash):
                return replay
            return self._conflict("correspondence_correction_command_conflict")
        except _CorrectionCommandConflict as exc:
            self._compensate_artifact(uploaded_artifact)
            return self._conflict(exc.code)
        except _CorrectionCommandInvalid as exc:
            self._compensate_artifact(uploaded_artifact)
            return self._unprocessable(exc.code)
        except (Http404, PermissionDenied):
            self._compensate_artifact(uploaded_artifact)
            raise
        except Exception as exc:
            self._compensate_artifact(uploaded_artifact)
            logger.warning(
                "Correspondence correction command failed case=%s command=%s error=%s",
                reference.external_id,
                request_spec.command_type,
                type(exc).__name__,
            )
            return Response(
                {"error": "correspondence_correction_command_failed"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(command_response(command, replayed=False), status=201)

    def _prepare_after_case_lock(self, case, request_spec, current):
        if request_spec.command_type == "start_replacement":
            compile_spec = CompileCorrespondenceSpec.model_validate(
                {
                    key: getattr(request_spec, key)
                    for key in [
                        "client_request_id",
                        "patient",
                        "encounter",
                        "facility",
                        "department",
                        "encounter_reason",
                        "form_submission",
                        "form_source_version",
                        "form_source_hash",
                        "form_artifact",
                        "form_artifact_hash",
                        "medication_actions",
                        "template",
                        "template_version",
                        "template_hash",
                        "author",
                    ]
                }
            )
            compiler = CorrespondenceCompilationViewSet()
            compiler.request = self.request
            compiler._validate_route_context(compile_spec, current)  # noqa: SLF001
            locked_sources = compiler._lock_and_validate_sources(  # noqa: SLF001
                compile_spec,
                current,
            )
            recipient = self._lock_replacement_recipient(case, request_spec)
            return {
                "compile_spec": compile_spec,
                "compiler": compiler,
                "locked_sources": locked_sources,
                "recipient": recipient,
            }
        if request_spec.command_type == "send_replacement":
            revision = self._get_revision(request_spec.target_revision)
            try:
                dispatch = lock_and_assert_correspondence_dispatch_current(revision.id)
                adapter = get_correspondence_delivery_adapter(dispatch.recipient)
                read_and_verify_correspondence_artifact(dispatch.artifact)
            except CorrespondenceDeliveryAdapterUnavailableError as exc:
                raise _CorrectionCommandInvalid(
                    "correspondence_replacement_delivery_unavailable"
                ) from exc
            except CorrespondenceDeliveryIntegrityError as exc:
                raise _CorrectionCommandConflict(
                    "correspondence_replacement_integrity_failed"
                ) from exc
            except CorrespondenceDispatchNotCurrentError as exc:
                raise _CorrectionCommandConflict(
                    "correspondence_replacement_dispatch_stale"
                ) from exc
            return {"dispatch": dispatch, "adapter": adapter}
        return {}

    def _command_start_replacement(
        self,
        case,
        request_spec,
        payload_hash,
        *,
        current,
        prepared,
    ):
        if case.status != "open":
            raise _CorrectionCommandConflict("correspondence_correction_case_resolved")
        original_latest = self._verified_latest_delivery(case.original_delivery)
        if original_latest.event_type != "acknowledged":
            raise _CorrectionCommandConflict(
                "correspondence_original_delivery_not_acknowledged"
            )
        previous_attempt = (
            CorrespondenceReplacementAttempt._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related("source_submission")
            .filter(case=case)
            .order_by("-attempt_number")
            .first()
        )
        if (
            previous_attempt
            and previous_attempt.source_submission_id == current.id
            and not self._same_source_terminal_recovery_allowed(
                case,
                previous_attempt,
            )
        ):
            raise _CorrectionCommandConflict(
                "correspondence_replacement_already_started"
            )
        attempt_number = previous_attempt.attempt_number + 1 if previous_attempt else 1
        fingerprint = canonical_sha256(
            {
                "attempt_number": attempt_number,
                "case": case.external_id,
                "contract": "correspondence-replacement-compilation-v1",
                "payload_hash": payload_hash,
            }
        )
        compiler = prepared["compiler"]
        compilation = compiler._compile(  # noqa: SLF001
            prepared["compile_spec"],
            current,
            prepared["locked_sources"],
            source_fingerprint=fingerprint,
        )
        compilation.save(force_insert=True)
        recipient = prepared["recipient"]
        review = self._build_review(
            compilation,
            recipient,
            prepared["locked_sources"]["author_snapshot"],
            fingerprint=canonical_sha256(
                {
                    "case": case.external_id,
                    "compilation": compilation.external_id,
                    "contract": "correspondence-replacement-review-v1",
                }
            ),
        )
        review.save(force_insert=True)
        letter_builder = CorrespondenceLetterViewSet()
        letter_builder.request = self.request
        letter = letter_builder._create_letter(review)  # noqa: SLF001
        revision = letter_builder._create_revision(  # noqa: SLF001
            letter,
            body=compilation.compiled_text,
            version=1,
            previous=None,
            status_value="draft",
        )
        attempt = CorrespondenceReplacementAttempt(
            case=case,
            attempt_number=attempt_number,
            supersedes_attempt=previous_attempt,
            source_head=case.source_head,
            source_head_hash=case.source_head_hash,
            source_correction=case.latest_source_correction,
            source_correction_hash=case.latest_source_correction_hash,
            source_submission=current,
            source_version=current.resource_version,
            source_snapshot_hash=current.finalized_snapshot_hash,
            form_artifact=compilation.form_artifact,
            form_artifact_hash=compilation.form_artifact_hash,
            compilation=compilation,
            review=review,
            initial_revision=revision,
            started_by=self.request.user,
            started_at=timezone.now(),
            attempt_hash="",
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        attempt.attempt_hash = correspondence_replacement_attempt_hash(attempt)
        attempt.save(force_insert=True)
        case.replacement_attempt = attempt
        case.replacement_compilation = compilation
        case.replacement_review = review
        case.replacement_revision = revision
        case.replacement_artifact = None
        case.replacement_delivery = None
        case.replacement_delivery_state = None
        case.replacement_delivery_certainty = None
        case.replacement_status = "draft"
        case.notification_status = "required"
        case.paper_reconciliation_status = "required"
        return {
            "event_type": "replacement_started",
            "safe_code": "replacement_started",
            "result_attempt": attempt,
            "result_revision": revision,
        }

    def _same_source_terminal_recovery_allowed(self, case, attempt):
        if any(
            [
                case.replacement_attempt_id != attempt.id,
                case.replacement_status != "failed",
                case.replacement_delivery_state != "failed_terminal",
                case.replacement_delivery_certainty != "not_delivered",
                not case.replacement_delivery_id,
            ]
        ):
            return False
        delivery = (
            CorrespondenceDelivery._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related(
                "artifact",
                "recipient",
                "revision__letter__review__compilation__form_artifact",
                "review",
                "supersedes",
            )
            .filter(
                pk=case.replacement_delivery_id,
                correction_case_reference=case.external_id,
                supersedes=case.original_delivery,
                deleted=False,
            )
            .first()
        )
        if not delivery:
            return False
        linked = CorrespondenceCorrectionCommand._base_manager.filter(  # noqa: SLF001
            case=case,
            command_type="send_replacement",
            result_attempt=attempt,
            result_delivery=delivery,
            deleted=False,
        ).exists()
        if not linked:
            return False
        latest = self._verified_latest_delivery(delivery)
        return bool(
            latest.event_type == "failed_terminal"
            and latest.certainty == "not_delivered"
            and latest.event_type == case.replacement_delivery_state
            and latest.certainty == case.replacement_delivery_certainty
        )

    def _command_revise_replacement(
        self,
        case,
        request_spec,
        payload_hash,
        *,
        current,
        prepared,
    ):
        del payload_hash, prepared
        attempt, target = self._target_attempt_and_revision(
            case,
            request_spec,
            current,
            expected_status="draft",
        )
        letter_builder = CorrespondenceLetterViewSet()
        letter_builder.request = self.request
        revision = letter_builder._create_revision(  # noqa: SLF001
            target.letter,
            body=request_spec.body,
            version=target.resource_version + 1,
            previous=target,
            status_value="draft",
        )
        case.replacement_revision = revision
        case.replacement_status = "draft"
        return {
            "event_type": "replacement_revised",
            "safe_code": "replacement_revised",
            "target_attempt": attempt,
            "result_attempt": attempt,
            "result_revision": revision,
        }

    def _command_finalize_replacement(
        self,
        case,
        request_spec,
        payload_hash,
        *,
        current,
        prepared,
    ):
        del payload_hash, prepared
        attempt, target = self._target_attempt_and_revision(
            case,
            request_spec,
            current,
            expected_status="draft",
        )
        letter_builder = CorrespondenceLetterViewSet()
        letter_builder.request = self.request
        revision = letter_builder._create_revision(  # noqa: SLF001
            target.letter,
            body=target.body,
            version=target.resource_version + 1,
            previous=target,
            status_value="finalized",
            commit=False,
        )
        artifact = self._build_controlled_copy(case, attempt, revision)
        artifact.save(force_insert=True, skip_internal_name=True)
        letter_builder._insert_revision_with_artifact(revision, artifact)  # noqa: SLF001
        case.replacement_revision = revision
        case.replacement_artifact = artifact
        case.replacement_status = "finalized"
        return {
            "event_type": "replacement_finalized",
            "safe_code": "replacement_finalized",
            "target_attempt": attempt,
            "result_attempt": attempt,
            "result_revision": revision,
            "result_artifact": artifact,
            "uploaded_artifact": artifact,
        }

    def _command_send_replacement(
        self,
        case,
        request_spec,
        payload_hash,
        *,
        current,
        prepared,
    ):
        attempt, revision = self._target_attempt_and_revision(
            case,
            request_spec,
            current,
            expected_status="finalized",
        )
        dispatch = prepared["dispatch"]
        artifact = dispatch.artifact
        if not all(
            [
                dispatch.revision.id == revision.id,
                artifact.external_id == request_spec.controlled_copy_artifact,
                artifact.artifact_sha256 == request_spec.controlled_copy_artifact_hash,
                case.replacement_artifact_id == artifact.id,
            ]
        ):
            raise _CorrectionCommandConflict(
                "correspondence_replacement_artifact_stale"
            )
        if case.replacement_delivery_id:
            raise _CorrectionCommandConflict(
                "correspondence_replacement_delivery_already_exists"
            )
        delivery = self._create_replacement_delivery(
            case,
            dispatch,
            prepared["adapter"],
        )
        delivery_attempt = self._create_delivery_attempt(
            delivery,
            request_spec.client_request_id,
            payload_hash,
            prepared["adapter"],
            command_type="send",
            attempt_number=1,
            previous_terminal_event=None,
        )
        append_delivery_event(
            delivery=delivery,
            attempt=delivery_attempt,
            event_type="dispatch_pending",
            certainty="not_attempted",
            actor=self.request.user,
            safe_code="correction_pending",
        )
        attempt_id = str(delivery_attempt.external_id)
        transaction.on_commit(
            lambda: dispatch_correspondence_delivery_attempt.delay(attempt_id),
            robust=True,
        )
        case.replacement_delivery = delivery
        case.replacement_delivery_state = "dispatch_pending"
        case.replacement_delivery_certainty = "not_attempted"
        case.replacement_status = "delivery_pending"
        case.notification_status = "pending"
        return {
            "event_type": "replacement_delivery_linked",
            "safe_code": "replacement_delivery_linked",
            "target_attempt": attempt,
            "result_attempt": attempt,
            "result_revision": revision,
            "result_artifact": artifact,
            "result_delivery": delivery,
        }

    def _command_retry_replacement(
        self,
        case,
        request_spec,
        payload_hash,
        *,
        current,
        prepared,
    ):
        del prepared
        attempt = self._target_attempt(case, request_spec.attempt, current)
        delivery = get_object_or_404(
            CorrespondenceDelivery._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            ).select_related(
                "artifact",
                "recipient",
                "revision__letter__review__compilation__form_artifact",
                "review",
                "supersedes",
            ),
            external_id=request_spec.delivery,
            pk=case.replacement_delivery_id,
            correction_case_reference=case.external_id,
        )
        latest = self._verified_latest_delivery(delivery)
        if not all(
            [
                latest.event_type == "failed_retryable",
                latest.sequence == request_spec.delivery_event_sequence,
                latest.event_hash == request_spec.delivery_event_hash,
                delivery.supersedes_id == case.original_delivery_id,
            ]
        ):
            raise _CorrectionCommandConflict(
                "correspondence_replacement_delivery_not_retryable"
            )
        try:
            adapter = get_correspondence_delivery_adapter(delivery.recipient)
            read_and_verify_correspondence_artifact(delivery.artifact)
        except (
            CorrespondenceDeliveryAdapterUnavailableError,
            CorrespondenceDeliveryIntegrityError,
        ) as exc:
            raise _CorrectionCommandInvalid(
                "correspondence_replacement_delivery_unavailable"
            ) from exc
        delivery_attempt = self._create_delivery_attempt(
            delivery,
            request_spec.client_request_id,
            payload_hash,
            adapter,
            command_type="retry",
            attempt_number=latest.attempt.attempt_number + 1,
            previous_terminal_event=latest,
        )
        append_delivery_event(
            delivery=delivery,
            attempt=delivery_attempt,
            event_type="dispatch_pending",
            certainty="not_attempted",
            actor=self.request.user,
            safe_code="correction_retry_pending",
        )
        delivery_attempt_id = str(delivery_attempt.external_id)
        transaction.on_commit(
            lambda: dispatch_correspondence_delivery_attempt.delay(delivery_attempt_id),
            robust=True,
        )
        case.replacement_delivery_state = "dispatch_pending"
        case.replacement_delivery_certainty = "not_attempted"
        case.replacement_status = "delivery_pending"
        case.notification_status = "pending"
        return {
            "event_type": "replacement_delivery_linked",
            "safe_code": "replacement_retry_linked",
            "target_attempt": attempt,
            "result_attempt": attempt,
            "result_revision": case.replacement_revision,
            "result_artifact": case.replacement_artifact,
            "result_delivery": delivery,
        }

    def _command_attest_paper(
        self,
        case,
        request_spec,
        payload_hash,
        *,
        current,
        prepared,
    ):
        del payload_hash, prepared
        attempt = self._target_attempt(case, request_spec.attempt, current)
        artifact = get_object_or_404(
            ReportUpload._base_manager.select_for_update(of=("self",)),  # noqa: SLF001
            external_id=request_spec.controlled_copy_artifact,
            pk=case.replacement_artifact_id,
        )
        if (
            artifact.artifact_sha256 != request_spec.controlled_copy_artifact_hash
            or artifact.meta.get("artifact_kind") != "controlled_correction_copy"
            or artifact.meta.get("correction_case") != str(case.external_id)
            or artifact.meta.get("replacement_attempt") != str(attempt.external_id)
        ):
            raise _CorrectionCommandConflict(
                "correspondence_controlled_copy_integrity_failed"
            )
        if CorrespondencePaperReconciliationAttestation.objects.filter(
            case=case,
            replacement_attempt=attempt,
        ).exists():
            raise _CorrectionCommandConflict("correspondence_paper_already_attested")
        attestation = CorrespondencePaperReconciliationAttestation(
            case=case,
            replacement_attempt=attempt,
            controlled_copy_artifact=artifact,
            artifact_hash=artifact.artifact_sha256,
            attestation_type=request_spec.attestation_type,
            attested_by=self.request.user,
            attested_at=timezone.now(),
            attestation_hash="",
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        attestation.attestation_hash = correspondence_paper_attestation_hash(
            attestation
        )
        attestation.save(force_insert=True)
        if not paper_attestation_integrity_valid(attestation):
            raise CorrespondenceCorrectionIntegrityError
        case.paper_reconciliation_status = "acknowledged"
        return {
            "event_type": "paper_reconciled",
            "safe_code": "paper_copy_reconciled",
            "target_attempt": attempt,
            "result_attempt": attempt,
            "result_artifact": artifact,
            "result_attestation": attestation,
        }

    def _command_resolve(
        self,
        case,
        request_spec,
        payload_hash,
        *,
        current,
        prepared,
    ):
        del payload_hash, prepared
        if request_spec.resolution_mode == "original_not_delivered":
            latest = self._verified_latest_delivery(case.original_delivery)
            if latest.certainty != "not_delivered":
                raise _CorrectionCommandConflict(
                    "correspondence_original_delivery_not_proven_absent"
                )
            case.delivery_state = latest.event_type
            case.delivery_certainty = latest.certainty
            case.notification_status = "not_required"
            case.paper_reconciliation_status = "not_required"
            attempt = None
        else:
            attempt = self._target_attempt(case, request_spec.attempt, current)
            delivery = case.replacement_delivery
            if not delivery:
                raise _CorrectionCommandConflict(
                    "correspondence_replacement_delivery_missing"
                )
            latest = self._verified_latest_delivery(delivery)
            attestation = CorrespondencePaperReconciliationAttestation.objects.filter(
                case=case,
                replacement_attempt=attempt,
            ).first()
            if (
                latest.event_type != "acknowledged"
                or case.replacement_status != "acknowledged"
                or case.notification_status != "acknowledged"
                or case.paper_reconciliation_status != "acknowledged"
                or not attestation
                or not paper_attestation_integrity_valid(attestation)
            ):
                raise _CorrectionCommandConflict(
                    "correspondence_correction_resolution_incomplete"
                )
        case.status = "resolved"
        case.resolution_mode = request_spec.resolution_mode
        case.resolved_at = timezone.now()
        case.resolved_by = self.request.user
        return {
            "event_type": "resolved",
            "safe_code": f"resolved_{request_spec.resolution_mode}",
            "target_attempt": attempt,
            "result_attempt": attempt,
            "result_delivery": (
                case.replacement_delivery if attempt else case.original_delivery
            ),
        }

    def _lock_case(self, reference, head, current):
        case = (
            CorrespondenceCorrectionCase._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related(*self._case_related_fields())
            .get(pk=reference.pk)
        )
        if not correction_case_integrity_valid(case):
            raise _CorrectionCommandConflict(
                "correspondence_correction_case_integrity_failed"
            )
        if case.source_head_id != head.id or case.current_submission_id != current.id:
            raise _CorrectionCommandConflict(
                "correspondence_correction_case_projection_pending"
            )
        return case

    def _assert_case_compare_and_swap(self, case, request_spec):
        if (
            case.resource_version != request_spec.expected_case_version
            or case.case_hash != request_spec.expected_case_hash
        ):
            raise _CorrectionCommandConflict("correspondence_correction_case_stale")
        if case.status == "resolved":
            raise _CorrectionCommandConflict("correspondence_correction_case_resolved")

    def _target_attempt(self, case, attempt_external_id, current):
        attempt = get_object_or_404(
            CorrespondenceReplacementAttempt._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            ).select_related(
                "case",
                "source_head",
                "source_correction",
                "source_submission",
                "form_artifact",
                "compilation",
                "review",
                "initial_revision__letter__review",
                "started_by",
                "supersedes_attempt",
            ),
            external_id=attempt_external_id,
            case=case,
        )
        if (
            case.replacement_attempt_id != attempt.id
            or attempt.source_submission_id != current.id
            or not replacement_attempt_integrity_valid(attempt)
        ):
            raise _CorrectionCommandConflict("correspondence_replacement_attempt_stale")
        return attempt

    def _target_attempt_and_revision(
        self,
        case,
        request_spec,
        current,
        *,
        expected_status,
    ):
        attempt = self._target_attempt(case, request_spec.attempt, current)
        revision = get_object_or_404(
            CorrespondenceLetterRevision._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            ).select_related("letter__review", "previous_revision", "finalized_by"),
            external_id=request_spec.target_revision,
            letter__review=attempt.review,
        )
        latest = (
            CorrespondenceLetterRevision._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .filter(letter=revision.letter)
            .order_by("-resource_version")
            .first()
        )
        if not all(
            [
                case.replacement_revision_id == revision.id,
                latest and latest.id == revision.id,
                revision.resource_version == request_spec.target_revision_version,
                revision.revision_hash == request_spec.target_revision_hash,
                revision.status == expected_status,
            ]
        ):
            raise _CorrectionCommandConflict(
                "correspondence_replacement_revision_stale"
            )
        return attempt, revision

    def _lock_replacement_recipient(self, case, request_spec):
        recipient = get_object_or_404(
            CorrespondenceRecipient._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            ).select_related(
                "patient",
                "facility",
                "organization",
                "healthcare_service",
                "verified_by",
            ),
            external_id=request_spec.recipient,
            pk=case.original_review.recipient_id,
        )
        try:
            validate_verified_recipient(recipient, lock_verifier=True)
        except InvalidVerifiedRecipientError as exc:
            raise _CorrectionCommandInvalid(
                "correspondence_replacement_recipient_invalid"
            ) from exc
        if not all(
            [
                recipient.patient_id == case.original_review.patient_id,
                recipient.facility_id == case.original_review.facility_id,
                recipient.resource_version == request_spec.recipient_version,
                recipient.content_hash == request_spec.recipient_hash,
                recipient_content_hash(recipient) == recipient.content_hash,
            ]
        ):
            raise _CorrectionCommandConflict(
                "correspondence_replacement_recipient_stale"
            )
        return recipient

    def _build_review(self, compilation, recipient, author_snapshot, *, fingerprint):
        review = CorrespondenceReview(
            compilation=compilation,
            compilation_hash=compilation.compiled_hash,
            source_fingerprint=fingerprint,
            patient=compilation.patient,
            encounter=compilation.encounter,
            facility=compilation.facility,
            department=compilation.department,
            author=self.request.user,
            reviewer=self.request.user,
            recipient=recipient,
            recipient_version=recipient.resource_version,
            recipient_hash=recipient.content_hash,
            author_snapshot=author_snapshot,
            recipient_snapshot=recipient_snapshot(recipient),
            reviewed_at=timezone.now(),
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        review.review_hash = correspondence_review_hash(review)
        return review

    def _build_controlled_copy(self, case, attempt, revision):
        generated_at = timezone.now()
        artifact = ReportUpload(
            template=None,
            name="",
            internal_name="",
            associating_id=str(revision.letter.encounter.external_id),
            upload_completed=True,
            report_type="encounter_report",
            patient=revision.letter.patient,
            encounter=revision.letter.encounter,
            source_version=revision.resource_version,
            source_snapshot_hash=revision.revision_hash,
            generated_at=generated_at,
            generated_by=self.request.user,
            created_by=self.request.user,
            updated_by=self.request.user,
            meta={
                "artifact_kind": "controlled_correction_copy",
                "correction_case": str(case.external_id),
                "mime_type": "application/pdf",
                "replacement_attempt": str(attempt.external_id),
                "source_version": attempt.source_version,
                "supersedes_delivery": str(case.original_delivery.external_id),
            },
        )
        artifact.internal_name = f"{artifact.external_id}.pdf"
        artifact.name = f"Controlled correction {artifact.external_id}"
        html = build_controlled_correction_copy_html(
            artifact_id=artifact.external_id,
            revision=revision,
            generated_at=generated_at,
            correction_case_id=case.external_id,
            replacement_attempt_number=attempt.attempt_number,
            source_version=attempt.source_version,
            superseded_delivery_id=case.original_delivery.external_id,
        )
        pdf = render_correspondence_letter_pdf(html)
        if not pdf or not pdf.startswith(b"%PDF"):
            raise CorrespondenceLetterRenderError(
                "PDF renderer returned an invalid controlled correction artifact"
            )
        artifact.artifact_sha256 = hashlib.sha256(pdf).hexdigest()
        artifact.files_manager.put_object(
            artifact,
            pdf,
            ContentType="application/pdf",
        )
        return artifact

    def _create_replacement_delivery(self, case, context, adapter):
        delivery = CorrespondenceDelivery(
            revision=context.revision,
            artifact=context.artifact,
            review=context.review,
            recipient=context.recipient,
            patient=context.review.patient,
            encounter=context.review.encounter,
            facility=context.review.facility,
            department=context.review.department,
            author=context.review.author,
            revision_version=context.revision.resource_version,
            revision_hash=context.revision.revision_hash,
            artifact_sha256=context.artifact.artifact_sha256,
            review_hash=context.review.review_hash,
            recipient_version=context.review.recipient_version,
            recipient_hash=context.review.recipient_hash,
            channel_type=context.recipient.channel_type,
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            provider_idempotency_key="",
            delivery_hash="",
            supersedes=case.original_delivery,
            correction_case_reference=case.external_id,
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        delivery.provider_idempotency_key = correspondence_delivery_provider_key(
            delivery.external_id
        )
        delivery.delivery_hash = correspondence_delivery_hash(delivery)
        delivery.save(force_insert=True)
        return delivery

    def _create_delivery_attempt(
        self,
        delivery,
        request_id,
        payload_hash,
        adapter,
        *,
        command_type,
        attempt_number,
        previous_terminal_event,
    ):
        attempt = CorrespondenceDeliveryAttempt(
            delivery=delivery,
            attempt_number=attempt_number,
            client_request_id=request_id,
            payload_hash=payload_hash,
            command_type=command_type,
            requested_by=self.request.user,
            requested_at=timezone.now(),
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            provider_idempotency_key=delivery.provider_idempotency_key,
            attempt_hash="",
            previous_terminal_event=previous_terminal_event,
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        attempt.attempt_hash = correspondence_delivery_attempt_hash(attempt)
        attempt.save(force_insert=True)
        return attempt

    @staticmethod
    def _verified_latest_delivery(delivery):
        try:
            lock_and_verify_delivery_ledger(delivery)
        except CorrespondenceDeliveryIntegrityError as exc:
            raise _CorrectionCommandConflict(
                "correspondence_delivery_integrity_failed"
            ) from exc
        latest = latest_delivery_event(delivery, lock=True)
        if not latest:
            raise _CorrectionCommandConflict("correspondence_delivery_integrity_failed")
        return latest

    def _replay(self, case, request_spec, payload_hash):
        command = (
            CorrespondenceCorrectionCommand._base_manager.select_related(  # noqa: SLF001
                "actor",
                "case",
                "target_attempt",
                "result_attempt",
                "result_revision",
                "result_artifact",
                "result_delivery",
                "result_attestation",
            )
            .filter(client_request_id=request_spec.client_request_id)
            .first()
        )
        if not command:
            return None
        if not all(
            [
                not command.deleted,
                command.case_id == case.id,
                command.actor_id == self.request.user.id,
                command.command_type == request_spec.command_type,
                command.payload_hash == payload_hash,
                correspondence_correction_command_hash(command) == command.command_hash,
            ]
        ):
            return self._conflict(
                "correspondence_correction_command_idempotency_conflict"
            )
        self._authorize_read(case)
        return Response(command_response(command, replayed=True), status=200)

    def _authorize_read(self, case):
        patient = case.original_compilation.patient
        if not (
            AuthorizationController.call(
                "can_view_clinical_data",
                self.request.user,
                patient,
            )
            or AuthorizationController.call(
                "can_view_patient_questionnaire_responses",
                self.request.user,
                patient,
            )
        ):
            raise PermissionDenied("Permission denied for correspondence correction")
        read_report_authorizer(
            self.request.user,
            case.original_compilation.form_artifact.report_type,
            case.original_compilation.form_artifact.associating_id,
        )

    def _authorize_mutation(self, case):
        user = self.request.user
        if (
            user.deleted
            or not user.is_active
            or not user.verified
            or user.is_service_account
        ):
            raise PermissionDenied("Verified correspondence actor is required")
        write_report_authorizer(
            user,
            case.original_compilation.form_artifact.report_type,
            case.original_compilation.form_artifact.associating_id,
        )

    @staticmethod
    def _get_reference(external_id):
        return get_object_or_404(
            CorrespondenceCorrectionCase._base_manager.select_related(  # noqa: SLF001
                "current_submission",
                "original_compilation__patient",
                "original_compilation__form_artifact",
                "original_review",
                "original_delivery",
                "replacement_delivery",
            ),
            external_id=external_id,
            deleted=False,
        )

    @staticmethod
    def _get_revision(external_id):
        return get_object_or_404(
            CorrespondenceLetterRevision._base_manager.select_related(  # noqa: SLF001
                "letter__review__compilation__form_artifact",
                "letter__review__recipient",
                "letter__patient",
                "letter__encounter",
                "letter__facility",
                "letter__department",
                "letter__author",
            ),
            external_id=external_id,
            deleted=False,
        )

    @staticmethod
    def _case_related_fields():
        return [
            "source_head",
            "frozen_submission__workflow_finalized_by",
            "current_submission__workflow_finalized_by",
            "latest_source_correction__source_head",
            "latest_source_correction__corrected_by",
            "latest_source_correction__previous_submission__workflow_finalized_by",
            "latest_source_correction__new_submission__workflow_finalized_by",
            "original_compilation__patient",
            "original_compilation__form_submission__questionnaire",
            "original_compilation__form_artifact",
            "original_review__author",
            "original_review__recipient",
            "original_delivery",
            "replacement_attempt__source_head",
            "replacement_attempt__source_correction",
            "replacement_attempt__source_submission",
            "replacement_attempt__form_artifact",
            "replacement_attempt__compilation",
            "replacement_attempt__review",
            "replacement_attempt__initial_revision__letter__review",
            "replacement_attempt__started_by",
            "replacement_attempt__supersedes_attempt",
            "replacement_compilation",
            "replacement_review",
            "replacement_revision",
            "replacement_artifact",
            "replacement_delivery",
            "resolved_by",
        ]

    @staticmethod
    def _compensate_artifact(artifact):
        if not artifact:
            return
        try:
            artifact.files_manager.delete_object(artifact, quiet=True)
        except Exception as exc:
            logger.error(
                "Correction artifact compensation failed artifact=%s error=%s",
                artifact.external_id,
                type(exc).__name__,
            )

    @staticmethod
    def _conflict(code):
        return Response({"error": code}, status=status.HTTP_409_CONFLICT)

    @staticmethod
    def _unprocessable(code):
        return Response({"error": code}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)


class _CorrectionCommandConflict(Exception):  # noqa: N818
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _CorrectionCommandInvalid(Exception):  # noqa: N818
    def __init__(self, code):
        self.code = code
        super().__init__(code)
