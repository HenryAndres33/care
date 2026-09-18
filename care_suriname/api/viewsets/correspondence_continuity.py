from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from care.emr.api.viewsets.clinical_no_store import ClinicalNoStoreResponseMixin
from care.emr.correspondence.correction import (
    CorrespondenceCorrectionIntegrityError,
    authoritative_form_artifact,
    build_correspondence_change_set,
    correction_case_integrity_valid,
    form_submission_series_head_integrity_valid,
    source_correction_integrity_valid,
)
from care.emr.correspondence.letter import (
    correspondence_revision_actionable,
    correspondence_revision_artifact_status,
    correspondence_revision_frozen_integrity_valid,
)
from care.emr.correspondence.replacement import serialize_replacement_workflow
from care.emr.correspondence.review import (
    reviewed_binding_available,
    reviewed_binding_frozen_integrity_valid,
)
from care.emr.correspondence.source import (
    compilation_frozen_integrity_valid,
    compilation_sources_available,
)
from care.emr.models.correspondence import CorrespondenceCompilation
from care.emr.models.correspondence_correction import (
    CorrespondenceCorrectionCase,
    CorrespondenceCorrectionOutbox,
    CorrespondenceSourceCorrection,
    FormSubmissionSeriesHead,
)
from care.emr.models.correspondence_delivery import CorrespondenceDelivery
from care.emr.models.correspondence_letter import (
    CorrespondenceLetter,
    CorrespondenceLetterRevision,
)
from care.emr.models.correspondence_review import CorrespondenceReview
from care.emr.models.questionnaire import FormSubmission
from care.emr.models.report.report_upload import ReportUpload
from care.emr.reports.authorizers.utils import (
    read_report_authorizer,
    write_report_authorizer,
)
from care.emr.resources.correspondence_continuity import (
    CORRESPONDENCE_CONTINUITY_CONTRACT_VERSION,
    CorrespondenceContinuityQuerySpec,
    CorrespondenceContinuityReadSpec,
    correspondence_continuity_hash,
)
from care.security.authorization.base import AuthorizationController
from care.utils.shortcuts import get_object_or_404

IN_FLIGHT_DELIVERY_STATES = {
    "dispatch_pending",
    "dispatching",
    "outcome_unknown",
}


class CorrespondenceContinuityViewSet(ClinicalNoStoreResponseMixin, viewsets.ViewSet):
    @extend_schema(
        parameters=[CorrespondenceContinuityQuerySpec],
        responses={200: CorrespondenceContinuityReadSpec},
    )
    def list(self, request, *args, **kwargs):
        try:
            query = CorrespondenceContinuityQuerySpec.model_validate(
                dict(request.query_params.items())
            )
        except PydanticValidationError:
            return Response(
                {"error": "correspondence_continuity_query_invalid"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reference = get_object_or_404(
            CorrespondenceCompilation._base_manager.select_related(  # noqa: SLF001
                "patient",
                "encounter",
                "form_artifact",
            ),
            external_id=query.compilation,
            patient__external_id=query.patient,
            encounter__external_id=query.encounter,
        )
        self._authorize_read(reference)
        try:
            with transaction.atomic():
                payload = self._build_locked_payload(reference.pk)
        except _ContinuityPendingError:
            response = Response(
                {"error": "correspondence_continuity_pending"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
            response["Retry-After"] = "1"
            response["Cache-Control"] = "no-store"
            return response
        except (
            MultipleObjectsReturned,
            ObjectDoesNotExist,
            _HistoricalIntegrityError,
        ):
            response = Response(
                {"error": "correspondence_continuity_integrity_failed"},
                status=status.HTTP_409_CONFLICT,
            )
            response["Cache-Control"] = "no-store"
            return response
        response = Response(payload)
        response["ETag"] = (
            f'"{payload["continuity_id"]}:{payload["resource_version"]}:'
            f'{payload["continuity_hash"]}"'
        )
        response["Cache-Control"] = "no-store"
        response["Vary"] = "Authorization, Cookie"
        return response

    def _build_locked_payload(self, compilation_pk):
        series_id = (
            CorrespondenceCompilation._base_manager.filter(  # noqa: SLF001
                pk=compilation_pk
            )
            .values_list("form_submission__series_id", flat=True)
            .get()
        )
        head = (
            FormSubmissionSeriesHead._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related("advanced_by")
            .get(series_id=series_id)
        )
        current = (
            FormSubmission._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .select_related(
                "patient",
                "encounter",
                "questionnaire",
                "previous_version",
                "created_by",
                "updated_by",
                "workflow_finalized_by",
            )
            .get(pk=head.current_submission_id)
        )
        head.current_submission = current
        case_reference = self._lock_case_reference(compilation_pk)
        compilation = (
            CorrespondenceCompilation._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related(*self._compilation_related_fields())
            .get(pk=compilation_pk)
        )
        self._authorize_read(compilation)
        if not compilation_frozen_integrity_valid(compilation):
            raise _HistoricalIntegrityError

        history = self._lock_historical_chain(compilation)
        if not history["integrity_valid"]:
            raise _HistoricalIntegrityError

        if not form_submission_series_head_integrity_valid(head, current=current):
            raise _HistoricalIntegrityError
        source_integrity = True
        corrections = []
        if source_integrity and current.pk != compilation.form_submission_id:
            try:
                corrections = self._source_correction_chain(
                    head,
                    frozen=compilation.form_submission,
                    current=current,
                )
            except CorrespondenceCorrectionIntegrityError:
                source_integrity = False

        source_result = (
            "current",
            compilation.form_artifact,
            [],
            None,
            "none",
        )
        if not source_integrity:
            source_result = ("integrity_failed", None, [], None, "none")
        elif current.pk != compilation.form_submission_id:
            source_result = self._resolve_stale_source(
                compilation=compilation,
                current=current,
                history=history,
                corrections=corrections,
                correction_case=(
                    self._case_graph(case_reference.pk) if case_reference else None
                ),
            )

        (
            source_state,
            authoritative_artifact,
            changes,
            correction_case,
            required_action,
        ) = source_result

        policy = self._action_policy(
            compilation=compilation,
            history=history,
            source_state=source_state,
            required_action=required_action,
            authoritative_artifact=authoritative_artifact,
            correction_case=correction_case,
        )
        payload = {
            "action_policy": policy,
            "authoritative_source": (
                self._source_payload(current, authoritative_artifact)
                if authoritative_artifact
                else None
            ),
            "changes": changes,
            "checked_at": timezone.now(),
            "context": {
                "compilation": str(compilation.external_id),
                "department": str(compilation.department.external_id),
                "encounter": str(compilation.encounter.external_id),
                "facility": str(compilation.facility.external_id),
                "patient": str(compilation.patient.external_id),
            },
            "continuity_hash": "0" * 64,
            "continuity_id": str(compilation.external_id),
            "contract_version": CORRESPONDENCE_CONTINUITY_CONTRACT_VERSION,
            "correction_case": (
                self._case_payload(correction_case) if correction_case else None
            ),
            "frozen_source": self._source_payload(
                compilation.form_submission,
                compilation.form_artifact,
            ),
            "historical_chain": self._historical_payload(compilation, history),
            "required_action": required_action,
            "resource_version": self._continuity_version(
                head,
                history,
                correction_case,
            ),
            "source_state": source_state,
        }
        payload["continuity_hash"] = correspondence_continuity_hash(payload)
        return CorrespondenceContinuityReadSpec.model_validate(payload).model_dump(
            mode="json"
        )

    def _resolve_stale_source(
        self,
        *,
        compilation,
        current,
        history,
        corrections,
        correction_case,
    ):
        change_set = build_correspondence_change_set(
            compilation.form_submission,
            current,
        )
        projection_state = self._projection_state(corrections)
        if projection_state == "failed":
            return "integrity_failed", None, [], None, "none"
        if projection_state == "pending":
            raise _ContinuityPendingError
        try:
            artifact = authoritative_form_artifact(current)
        except CorrespondenceCorrectionIntegrityError:
            return "integrity_failed", None, [], None, "none"

        source_state = "stale" if artifact else "unavailable"
        required_action = self._required_action(history["delivery_state"])
        if correction_case and (
            not correction_case_integrity_valid(correction_case)
            or correction_case.latest_source_correction_id != corrections[-1].id
            or correction_case.current_submission_id != current.id
            or correction_case.source_head_hash != corrections[-1].source_head_hash
        ):
            return "integrity_failed", None, [], None, "none"
        if correction_case and (
            correction_case.delivery_state != history["delivery_state"]
            or correction_case.delivery_certainty != history["delivery_certainty"]
            or (
                correction_case.replacement_status == "not_started"
                and correction_case.notification_status
                != self._expected_notification(history)
            )
        ):
            self._schedule_delivery_refresh(history["delivery"])
            raise _ContinuityPendingError
        if (
            required_action
            in {
                "resolve_delivery_outcome",
                "send_correction",
            }
            and not correction_case
        ):
            self._schedule_delivery_refresh(history["delivery"])
            raise _ContinuityPendingError
        return (
            source_state,
            artifact,
            change_set.display_changes,
            correction_case,
            required_action,
        )

    @staticmethod
    def _expected_notification(history):
        if history["delivery_state"] == "acknowledged":
            return "required"
        if history["delivery_certainty"] == "not_delivered":
            return "not_required"
        return "unknown"

    @staticmethod
    def _schedule_delivery_refresh(delivery):
        from care.emr.tasks.correspondence_correction import (
            refresh_correspondence_correction_delivery,
        )

        delivery_external_id = str(delivery.external_id)
        transaction.on_commit(
            lambda: refresh_correspondence_correction_delivery.delay(
                delivery_external_id
            ),
            robust=True,
        )

    def _lock_historical_chain(self, compilation):
        review = (
            CorrespondenceReview._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .select_related(
                "author",
                "recipient",
                "patient",
                "facility",
                "compilation__form_submission__questionnaire",
                "compilation__form_artifact",
            )
            .filter(compilation=compilation)
            .first()
        )
        if review and not reviewed_binding_frozen_integrity_valid(review):
            return {"integrity_valid": False}
        letter = None
        revision = None
        artifact = None
        delivery = None
        latest_event = None
        if review:
            letter = (
                CorrespondenceLetter._base_manager.select_for_update(of=("self",))  # noqa: SLF001
                .filter(review=review)
                .first()
            )
        if letter:
            revision = (
                CorrespondenceLetterRevision._base_manager.select_for_update(  # noqa: SLF001
                    of=("self",)
                )
                .select_related("letter__review", "previous_revision", "finalized_by")
                .filter(letter=letter, status="finalized")
                .first()
            )
        if revision:
            artifact = (
                ReportUpload._base_manager.select_for_update(of=("self",))  # noqa: SLF001
                .filter(letter_revision=revision)
                .first()
            )
            if (
                not correspondence_revision_frozen_integrity_valid(revision)
                or correspondence_revision_artifact_status(
                    revision,
                    artifact=artifact,
                )
                == "integrity_failed"
            ):
                return {"integrity_valid": False}
            delivery = (
                CorrespondenceDelivery._base_manager.select_for_update(  # noqa: SLF001
                    of=("self",)
                )
                .select_related(
                    "artifact",
                    "recipient",
                    "revision__letter__review__compilation__form_artifact",
                    "review",
                )
                .filter(revision=revision)
                .first()
            )
        if delivery:
            from care.emr.correspondence.delivery import (
                CorrespondenceDeliveryIntegrityError,
                latest_delivery_event,
                lock_and_verify_delivery_ledger,
            )

            try:
                lock_and_verify_delivery_ledger(delivery)
            except CorrespondenceDeliveryIntegrityError:
                return {"integrity_valid": False}
            latest_event = latest_delivery_event(delivery, lock=True)
            if not latest_event:
                return {"integrity_valid": False}
        draft_revision = None
        if letter:
            draft_revision = (
                CorrespondenceLetterRevision._base_manager.select_for_update(  # noqa: SLF001
                    of=("self",)
                )
                .select_related(
                    "letter__review__compilation__form_submission__questionnaire",
                    "letter__review__compilation__form_artifact",
                    "letter__review__compilation__template",
                    "letter__review__compilation__encounter_reason",
                    "letter__review__author",
                    "letter__review__recipient",
                    "letter__review__patient",
                    "letter__review__encounter",
                    "letter__review__facility",
                    "letter__review__department",
                    "previous_revision",
                    "finalized_by",
                )
                .filter(letter=letter, status="draft", deleted=False)
                .order_by("-resource_version", "-id")
                .first()
            )
        return {
            "artifact": artifact,
            "delivery": delivery,
            "delivery_certainty": (latest_event.certainty if latest_event else None),
            "delivery_event_occurred_at": (
                latest_event.occurred_at if latest_event else None
            ),
            "delivery_state": (latest_event.event_type if latest_event else None),
            "event_sequence": latest_event.sequence if latest_event else 0,
            "integrity_valid": True,
            "letter": letter,
            "draft_revision": draft_revision,
            "review": review,
            "revision": revision,
        }

    @staticmethod
    def _source_correction_chain(head, *, frozen, current):
        corrections = list(
            CorrespondenceSourceCorrection._base_manager.select_related(  # noqa: SLF001
                "corrected_by",
                "source_head__advanced_by",
                "previous_submission__workflow_finalized_by",
                "new_submission__workflow_finalized_by",
            )
            .filter(
                source_head=head,
                new_version__gt=frozen.resource_version,
                new_version__lte=current.resource_version,
            )
            .order_by("sequence")
        )
        previous = frozen
        for correction in corrections:
            if (
                correction.previous_submission_id != previous.id
                or not source_correction_integrity_valid(correction)
            ):
                raise CorrespondenceCorrectionIntegrityError
            previous = correction.new_submission
        if not corrections or previous.id != current.id:
            raise CorrespondenceCorrectionIntegrityError
        return corrections

    @staticmethod
    def _lock_case_reference(compilation_pk):
        return (
            CorrespondenceCorrectionCase._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .only("pk")
            .filter(original_compilation_id=compilation_pk)
            .first()
        )

    @staticmethod
    def _case_graph(case_pk):
        return CorrespondenceCorrectionCase._base_manager.select_related(  # noqa: SLF001
            "source_head",
            "original_compilation__form_submission__questionnaire",
            "original_compilation__form_artifact",
            "original_review__author",
            "original_review__recipient",
            "original_delivery",
            "frozen_submission__patient",
            "frozen_submission__encounter",
            "frozen_submission__questionnaire",
            "frozen_submission__workflow_finalized_by",
            "current_submission__patient",
            "current_submission__encounter",
            "current_submission__questionnaire",
            "current_submission__workflow_finalized_by",
            "latest_source_correction__corrected_by",
            "latest_source_correction__source_head",
            "latest_source_correction__previous_submission__workflow_finalized_by",
            "latest_source_correction__new_submission__workflow_finalized_by",
            "replacement_compilation",
            "replacement_review",
            "replacement_revision",
            "replacement_artifact",
            "replacement_delivery",
            "resolved_by",
        ).get(pk=case_pk)

    @staticmethod
    def _projection_state(corrections):
        if not corrections:
            return "failed"
        statuses = list(
            CorrespondenceCorrectionOutbox._base_manager.filter(  # noqa: SLF001
                source_correction__in=corrections
            ).values_list("status", flat=True)
        )
        if len(statuses) != len(corrections) or any(
            status == "failed_terminal" for status in statuses
        ):
            return "failed"
        if any(status in {"pending", "processing"} for status in statuses):
            return "pending"
        if all(status == "completed" for status in statuses):
            return "completed"
        return "failed"

    @staticmethod
    def _required_action(delivery_state):
        if delivery_state == "acknowledged":
            return "send_correction"
        if delivery_state in IN_FLIGHT_DELIVERY_STATES:
            return "resolve_delivery_outcome"
        return "regenerate_unsent"

    def _action_policy(
        self,
        *,
        compilation,
        history,
        source_state,
        required_action,
        authoritative_artifact,
        correction_case,
    ):
        current = source_state == "current"
        review = history["review"]
        letter = history["letter"]
        draft_revision = history["draft_revision"]
        revision = history["revision"]
        delivery = history["delivery"]
        expected_author_id = review.author_id if review else compilation.author_id
        live_permission = self._can_mutate(
            compilation,
            expected_author_id=expected_author_id,
        )
        compilation_actionable = bool(
            current and compilation_sources_available(compilation)
        )
        review_actionable = bool(
            compilation_actionable
            and review
            and reviewed_binding_available(review, lock_verifier=False)
        )
        draft_actionable = bool(
            review_actionable
            and draft_revision
            and correspondence_revision_actionable(
                draft_revision,
                expected_status="draft",
            )
        )
        finalized_actionable = bool(
            review_actionable
            and revision
            and correspondence_revision_actionable(
                revision,
                expected_status="finalized",
            )
        )
        blockers = set()
        if source_state == "integrity_failed":
            blockers.add("integrity_failed")
        if source_state == "unavailable":
            blockers.update({"source_unavailable", "replacement_unavailable"})
        if source_state == "stale":
            blockers.add("source_stale")
        if required_action == "resolve_delivery_outcome":
            blockers.add("delivery_outcome_unresolved")
        if required_action == "send_correction":
            blockers.add("correction_required")
        if correction_case and correction_case.status == "resolved":
            blockers.add("historical_only")
        if not live_permission:
            blockers.add("permission_required")
        if source_state != "current" and revision:
            blockers.add("verification_required")
        replacement_attempt_current = bool(
            correction_case
            and correction_case.replacement_attempt_id
            and correction_case.replacement_attempt.source_submission_id
            == correction_case.current_submission_id
        )
        terminal_replacement_restart = bool(
            replacement_attempt_current
            and self._terminal_replacement_restart_ready(correction_case)
        )
        replacement_ready = bool(
            source_state == "stale"
            and authoritative_artifact
            and required_action == "send_correction"
            and correction_case
            and correction_case.status == "open"
            and live_permission
            and (not replacement_attempt_current or terminal_replacement_restart)
        )
        if source_state not in {"current", "stale"}:
            blockers.add("replacement_unavailable")
        can_retry = bool(
            finalized_actionable
            and live_permission
            and delivery
            and history["delivery_state"] == "failed_retryable"
        )
        return {
            "blocker_codes": sorted(blockers),
            "can_draft": bool(
                (
                    review_actionable
                    and live_permission
                    and review
                    and (not letter or draft_actionable)
                )
                or (
                    correction_case
                    and replacement_attempt_current
                    and correction_case.replacement_status == "draft"
                    and live_permission
                )
            ),
            "can_finalize": bool(
                (draft_actionable and live_permission)
                or (
                    correction_case
                    and replacement_attempt_current
                    and correction_case.replacement_status == "draft"
                    and live_permission
                )
            ),
            "can_generate_controlled_print_copy": bool(
                correction_case
                and replacement_attempt_current
                and correction_case.replacement_artifact_id
                and correction_case.replacement_status
                in {"finalized", "delivery_pending", "acknowledged"}
                and live_permission
            ),
            "can_read_historical": True,
            "can_retry_original": can_retry,
            "can_review": bool(
                compilation_actionable and live_permission and not review
            ),
            "can_send_original": bool(
                finalized_actionable and live_permission and not delivery
            ),
            "can_start_replacement": replacement_ready,
        }

    @staticmethod
    def _terminal_replacement_restart_ready(case):
        if any(
            [
                case.replacement_status != "failed",
                case.replacement_delivery_state != "failed_terminal",
                case.replacement_delivery_certainty != "not_delivered",
                not case.replacement_delivery_id,
            ]
        ):
            return False
        from care.emr.correspondence.delivery import (
            CorrespondenceDeliveryIntegrityError,
            latest_delivery_event,
            lock_and_verify_delivery_ledger,
        )

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
        try:
            lock_and_verify_delivery_ledger(delivery)
        except CorrespondenceDeliveryIntegrityError:
            return False
        latest = latest_delivery_event(delivery, lock=True)
        return bool(
            latest
            and latest.event_type == "failed_terminal"
            and latest.certainty == "not_delivered"
            and latest.event_type == case.replacement_delivery_state
            and latest.certainty == case.replacement_delivery_certainty
        )

    def _can_mutate(self, compilation, *, expected_author_id):
        del expected_author_id
        user = self.request.user
        if any(
            [
                user.deleted,
                not user.is_active,
                not user.verified,
                user.is_service_account,
            ]
        ):
            return False
        try:
            write_report_authorizer(
                user,
                compilation.form_artifact.report_type,
                compilation.form_artifact.associating_id,
            )
        except PermissionDenied:
            return False
        return True

    @staticmethod
    def _source_payload(source, artifact):
        return {
            "form_artifact": str(artifact.external_id),
            "form_artifact_hash": artifact.artifact_sha256,
            "form_series": str(source.series_id),
            "form_source_hash": source.finalized_snapshot_hash,
            "form_submission": str(source.external_id),
            "form_version": source.resource_version,
        }

    @staticmethod
    def _historical_payload(compilation, history):
        review = history["review"]
        revision = history["revision"]
        artifact = history["artifact"]
        delivery = history["delivery"]
        return {
            "artifact": str(artifact.external_id) if artifact else None,
            "artifact_hash": artifact.artifact_sha256 if artifact else None,
            "compilation": str(compilation.external_id),
            "compilation_hash": compilation.compiled_hash,
            "delivery": str(delivery.external_id) if delivery else None,
            "delivery_certainty": history["delivery_certainty"],
            "delivery_hash": delivery.delivery_hash if delivery else None,
            "delivery_state": history["delivery_state"],
            "review": str(review.external_id) if review else None,
            "review_hash": review.review_hash if review else None,
            "revision": str(revision.external_id) if revision else None,
            "revision_hash": revision.revision_hash if revision else None,
            "revision_version": revision.resource_version if revision else None,
        }

    @staticmethod
    def _case_payload(case):
        correction = case.latest_source_correction
        return {
            "amendment_author": str(correction.corrected_by.external_id),
            "amendment_occurred_at": correction.corrected_at,
            "amendment_reason": correction.reason,
            "amendment_type": correction.amendment_type,
            "case_hash": case.case_hash,
            "created_at": case.created_date,
            "id": str(case.external_id),
            "notification_status": case.notification_status,
            "paper_reconciliation_status": case.paper_reconciliation_status,
            "replacement": serialize_replacement_workflow(case),
            "resolution_mode": case.resolution_mode,
            "resolved_at": case.resolved_at,
            "resource_version": case.resource_version,
            "status": case.status,
        }

    @staticmethod
    def _continuity_version(head, history, correction_case):
        return max(
            1,
            head.current_version
            + (history["revision"].resource_version if history["revision"] else 0)
            + history["event_sequence"]
            + (correction_case.resource_version if correction_case else 0),
        )

    def _authorize_read(self, compilation):
        patient_access = AuthorizationController.call(
            "can_view_clinical_data", self.request.user, compilation.patient
        ) or AuthorizationController.call(
            "can_view_patient_questionnaire_responses",
            self.request.user,
            compilation.patient,
        )
        encounter_access = AuthorizationController.call(
            "can_view_encounter_clinical_data",
            self.request.user,
            compilation.encounter,
        ) or AuthorizationController.call(
            "can_view_encounter_obj",
            self.request.user,
            compilation.encounter,
        )
        if not (patient_access or encounter_access):
            raise PermissionDenied("Permission denied for correspondence continuity")
        read_report_authorizer(
            self.request.user,
            compilation.form_artifact.report_type,
            compilation.form_artifact.associating_id,
        )

    @staticmethod
    def _compilation_related_fields():
        return [
            "patient",
            "encounter",
            "facility",
            "department",
            "encounter_reason",
            "form_submission__patient",
            "form_submission__encounter",
            "form_submission__questionnaire",
            "form_submission__workflow_finalized_by",
            "form_artifact",
            "template",
            "author",
        ]


class _ContinuityPendingError(ValueError):
    pass


class _HistoricalIntegrityError(ValueError):
    pass
