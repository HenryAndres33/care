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
from care.emr.correspondence.delivery import (
    CorrespondenceDeliveryIntegrityError,
    CorrespondenceDispatchNotCurrentError,
    append_delivery_event,
    delivery_frozen_integrity_valid,
    latest_delivery_event,
    lock_and_assert_correspondence_dispatch_current,
    lock_and_verify_delivery_ledger,
    lock_correspondence_dispatch_source_current,
    read_and_verify_correspondence_artifact,
)
from care.emr.correspondence.delivery_adapters import (
    CorrespondenceDeliveryAdapterUnavailableError,
    get_correspondence_delivery_adapter,
)
from care.emr.models.correspondence_delivery import (
    CorrespondenceDelivery,
    CorrespondenceDeliveryAttempt,
)
from care.emr.models.correspondence_letter import CorrespondenceLetterRevision
from care.emr.reports.authorizers.utils import (
    read_report_authorizer,
    write_report_authorizer,
)
from care.emr.resources.correspondence_delivery import (
    CorrespondenceDeliveryCommandResponseSpec,
    CorrespondenceDeliveryListSpec,
    RetryCorrespondenceDeliverySpec,
    SendCorrespondenceDeliverySpec,
    canonical_delivery_command_hash,
    correspondence_delivery_attempt_hash,
    correspondence_delivery_hash,
    correspondence_delivery_provider_key,
)
from care.emr.tasks.correspondence_delivery import (
    dispatch_correspondence_delivery_attempt,
)
from care.emr.workflow_capabilities import require_correspondence_delivery_enabled
from care.security.authorization.base import AuthorizationController
from care.utils.shortcuts import get_object_or_404

logger = logging.getLogger(__name__)


class CorrespondenceDeliveryViewSet(ClinicalNoStoreResponseMixin, EMRBaseViewSet):
    database_model = CorrespondenceDelivery

    def get_queryset(self):
        return super().get_queryset().select_related(*self._related_fields())

    def list(self, request, *args, **kwargs):
        query = CorrespondenceDeliveryListSpec.model_validate(
            dict(request.query_params.items())
        )
        deliveries = self.get_queryset().filter(
            patient__external_id=query.patient,
            encounter__external_id=query.encounter,
        )
        if query.correspondence_revision:
            deliveries = deliveries.filter(
                revision__external_id=query.correspondence_revision
            )
        first = deliveries.first()
        if first:
            self._authorize_read(first)
        else:
            from care.emr.models.encounter import Encounter

            encounter = get_object_or_404(
                Encounter.objects.select_related("patient"),
                external_id=query.encounter,
                patient__external_id=query.patient,
            )
            self._authorize_clinical_read(encounter.patient)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(
            deliveries.order_by("-created_date"), request
        )
        data = []
        for delivery in page:
            self._authorize_read(delivery)
            if not delivery_frozen_integrity_valid(delivery):
                return self._conflict("correspondence_delivery_integrity_failed")
            data.append(self._serialize(delivery))
        return paginator.get_paginated_response(data)

    def retrieve(self, request, *args, **kwargs):
        delivery = self.get_object()
        self._authorize_read(delivery)
        if not delivery_frozen_integrity_valid(delivery):
            return self._conflict("correspondence_delivery_integrity_failed")
        response = Response(self._serialize(delivery))
        latest = latest_delivery_event(delivery)
        response["ETag"] = f'"{latest.sequence}:{latest.event_hash}"'
        return response

    @extend_schema(
        request=SendCorrespondenceDeliverySpec,
        responses={
            200: CorrespondenceDeliveryCommandResponseSpec,
            201: CorrespondenceDeliveryCommandResponseSpec,
        },
    )
    @action(detail=False, methods=["POST"], url_path="idempotent-send")
    def idempotent_send(self, request, *args, **kwargs):  # noqa: PLR0911
        request_spec = SendCorrespondenceDeliverySpec.model_validate(request.data)
        revision = self._get_revision(request_spec.correspondence_revision)
        self._authorize_review_read(revision.letter.review)
        if hasattr(revision.letter.review, "replacement_attempt"):
            return self._conflict("correspondence_replacement_requires_case_command")
        payload_hash = canonical_delivery_command_hash(
            request_spec,
            command_type="send",
            actor_id=request.user.external_id,
            delivery_id=None,
        )
        replay = self._command_replay(
            request_spec.client_request_id,
            payload_hash,
            command_type="send",
        )
        if replay:
            return replay
        require_correspondence_delivery_enabled(
            revision.letter.review.facility.external_id
        )
        try:
            with transaction.atomic():
                context = lock_and_assert_correspondence_dispatch_current(revision.id)
                self._authorize_review_read(context.review)
                self._authorize_new_dispatch(context.review)
                self._validate_send_context(request_spec, context)
                adapter = get_correspondence_delivery_adapter(context.recipient)
                read_and_verify_correspondence_artifact(context.artifact)
                if (
                    CorrespondenceDelivery._base_manager.select_for_update(  # noqa: SLF001
                        of=("self",)
                    )
                    .filter(revision=context.revision)
                    .exists()
                ):
                    replay = self._command_replay(
                        request_spec.client_request_id,
                        payload_hash,
                        command_type="send",
                    )
                    return replay or self._conflict(
                        "correspondence_delivery_already_exists"
                    )
                delivery = self._create_delivery(context, adapter)
                attempt = self._create_attempt(
                    delivery=delivery,
                    client_request_id=request_spec.client_request_id,
                    payload_hash=payload_hash,
                    command_type="send",
                    attempt_number=1,
                    previous_terminal_event=None,
                    adapter=adapter,
                )
                append_delivery_event(
                    delivery=delivery,
                    attempt=attempt,
                    event_type="dispatch_pending",
                    certainty="not_attempted",
                    actor=request.user,
                    safe_code="confirmed_pending",
                )
                self._enqueue_on_commit(attempt)
        except IntegrityError:
            replay = self._command_replay(
                request_spec.client_request_id,
                payload_hash,
                command_type="send",
            )
            return replay or self._conflict("correspondence_delivery_conflict")
        except CorrespondenceDispatchNotCurrentError:
            return self._conflict("correspondence_dispatch_not_current")
        except (
            CorrespondenceDeliveryAdapterUnavailableError,
            CorrespondenceDeliveryIntegrityError,
        ):
            return self._unprocessable("correspondence_dispatch_unavailable")
        except (Http404, PermissionDenied):
            raise
        except Exception as exc:
            logger.warning(
                "Correspondence send command failed revision=%s error=%s",
                revision.external_id,
                type(exc).__name__,
            )
            return self._failed()
        return self._response(
            request_spec.client_request_id,
            delivery,
            replayed=False,
            response_status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=RetryCorrespondenceDeliverySpec,
        responses={
            200: CorrespondenceDeliveryCommandResponseSpec,
            201: CorrespondenceDeliveryCommandResponseSpec,
        },
    )
    @action(detail=True, methods=["POST"], url_path="idempotent-retry")
    def idempotent_retry(self, request, *args, **kwargs):  # noqa: PLR0911
        request_spec = RetryCorrespondenceDeliverySpec.model_validate(request.data)
        delivery = self.get_object()
        self._authorize_read(delivery)
        if delivery.correction_case_reference:
            return self._conflict("correspondence_replacement_requires_case_command")
        payload_hash = canonical_delivery_command_hash(
            request_spec,
            command_type="retry",
            actor_id=request.user.external_id,
            delivery_id=delivery.external_id,
        )
        replay = self._command_replay(
            request_spec.client_request_id,
            payload_hash,
            command_type="retry",
        )
        if replay:
            return replay
        require_correspondence_delivery_enabled(delivery.facility.external_id)
        try:
            with transaction.atomic():
                source = lock_correspondence_dispatch_source_current(
                    delivery.revision_id
                )
                delivery = (
                    CorrespondenceDelivery._base_manager.select_for_update(  # noqa: SLF001
                        of=("self",)
                    )
                    .select_related(*self._related_fields())
                    .get(pk=delivery.pk)
                )
                self._authorize_read(delivery)
                try:
                    lock_and_verify_delivery_ledger(delivery)
                except CorrespondenceDeliveryIntegrityError:
                    return self._conflict("correspondence_delivery_integrity_failed")
                latest = latest_delivery_event(delivery, lock=True)
                if (
                    latest.event_type != "failed_retryable"
                    or latest.sequence != request_spec.expected_event_sequence
                    or latest.event_hash != request_spec.expected_event_hash
                ):
                    return self._conflict("correspondence_delivery_not_retryable")
                context = lock_and_assert_correspondence_dispatch_current(
                    delivery.revision_id,
                    locked_source=source,
                )
                self._authorize_new_dispatch(context.review)
                self._assert_delivery_context(delivery, context)
                adapter = get_correspondence_delivery_adapter(context.recipient)
                read_and_verify_correspondence_artifact(context.artifact)
                attempt = self._create_attempt(
                    delivery=delivery,
                    client_request_id=request_spec.client_request_id,
                    payload_hash=payload_hash,
                    command_type="retry",
                    attempt_number=latest.attempt.attempt_number + 1,
                    previous_terminal_event=latest,
                    adapter=adapter,
                )
                append_delivery_event(
                    delivery=delivery,
                    attempt=attempt,
                    event_type="dispatch_pending",
                    certainty="not_attempted",
                    actor=request.user,
                    safe_code="confirmed_retry_pending",
                )
                self._enqueue_on_commit(attempt)
        except IntegrityError:
            replay = self._command_replay(
                request_spec.client_request_id,
                payload_hash,
                command_type="retry",
            )
            return replay or self._conflict("correspondence_delivery_conflict")
        except CorrespondenceDispatchNotCurrentError:
            return self._conflict("correspondence_dispatch_not_current")
        except (
            CorrespondenceDeliveryAdapterUnavailableError,
            CorrespondenceDeliveryIntegrityError,
        ):
            return self._unprocessable("correspondence_dispatch_unavailable")
        except (Http404, PermissionDenied):
            raise
        except Exception as exc:
            logger.warning(
                "Correspondence retry command failed delivery=%s error=%s",
                delivery.external_id,
                type(exc).__name__,
            )
            return self._failed()
        return self._response(
            request_spec.client_request_id,
            delivery,
            replayed=False,
            response_status=status.HTTP_201_CREATED,
        )

    def _create_delivery(self, context, adapter):
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
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        delivery.provider_idempotency_key = correspondence_delivery_provider_key(
            delivery.external_id
        )
        delivery.delivery_hash = correspondence_delivery_hash(delivery)
        delivery.save(force_insert=True)
        return delivery

    def _create_attempt(
        self,
        *,
        delivery,
        client_request_id,
        payload_hash,
        command_type,
        attempt_number,
        previous_terminal_event,
        adapter,
    ):
        attempt = CorrespondenceDeliveryAttempt(
            delivery=delivery,
            attempt_number=attempt_number,
            client_request_id=client_request_id,
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
    def _enqueue_on_commit(attempt):
        attempt_id = str(attempt.external_id)
        transaction.on_commit(
            lambda: dispatch_correspondence_delivery_attempt.delay(attempt_id)
        )

    def _command_replay(self, client_request_id, payload_hash, *, command_type):
        attempt = (
            CorrespondenceDeliveryAttempt._base_manager.select_related(  # noqa: SLF001
                "requested_by",
                "delivery__revision__letter__review__compilation__form_artifact",
                "delivery__revision__letter__patient",
                "delivery__revision__letter__encounter",
                "delivery__artifact",
                "delivery__review__compilation__form_artifact",
                "delivery__review__patient",
                "delivery__recipient",
                "delivery__patient",
                "delivery__encounter",
                "delivery__facility",
                "delivery__department",
                "delivery__author",
            )
            .filter(client_request_id=client_request_id)
            .first()
        )
        if not attempt:
            return None
        delivery = attempt.delivery
        if not all(
            [
                not attempt.deleted,
                attempt.payload_hash == payload_hash,
                attempt.command_type == command_type,
                attempt.requested_by_id == self.request.user.id,
                delivery_frozen_integrity_valid(delivery),
            ]
        ):
            return self._conflict("correspondence_delivery_idempotency_conflict")
        self._authorize_read(delivery)
        return self._response(
            client_request_id,
            delivery,
            replayed=True,
            response_status=status.HTTP_200_OK,
        )

    def _get_revision(self, external_id):
        return get_object_or_404(
            CorrespondenceLetterRevision._base_manager.select_related(  # noqa: SLF001
                "letter__review__compilation__form_artifact",
                "letter__review__patient",
                "letter__review__encounter",
                "letter__review__facility",
                "letter__review__department",
                "letter__review__author",
                "letter__review__recipient",
            ),
            external_id=external_id,
            deleted=False,
        )

    def _validate_send_context(self, request_spec, context):
        revision = context.revision
        review = context.review
        if not all(
            [
                revision.external_id == request_spec.correspondence_revision,
                revision.resource_version == request_spec.resource_version,
                revision.revision_hash == request_spec.revision_hash,
                context.artifact.external_id == request_spec.artifact,
                context.artifact.artifact_sha256 == request_spec.artifact_sha256,
                review.external_id == request_spec.review_binding,
                review.review_hash == request_spec.review_hash,
                review.patient.external_id == request_spec.patient,
                review.encounter.external_id == request_spec.encounter,
                review.facility.external_id == request_spec.facility,
                review.department.external_id == request_spec.department,
                review.author.external_id == request_spec.author,
                review.recipient.external_id == request_spec.recipient,
                review.recipient_version == request_spec.recipient_version,
                review.recipient_hash == request_spec.recipient_hash,
                self.request.user.external_id == request_spec.author,
            ]
        ):
            raise Http404("Correspondence delivery context not found")

    @staticmethod
    def _assert_delivery_context(delivery, context):
        if not all(
            [
                delivery.revision_id == context.revision.id,
                delivery.artifact_id == context.artifact.id,
                delivery.review_id == context.review.id,
                delivery.recipient_id == context.recipient.id,
                delivery.revision_hash == context.revision.revision_hash,
                delivery.artifact_sha256 == context.artifact.artifact_sha256,
                delivery.review_hash == context.review.review_hash,
                delivery.recipient_hash == context.review.recipient_hash,
            ]
        ):
            raise CorrespondenceDispatchNotCurrentError

    def _authorize_new_dispatch(self, review):
        if (
            self.request.user.id != review.author_id
            or self.request.user.deleted
            or not self.request.user.is_active
            or not self.request.user.verified
            or self.request.user.is_service_account
        ):
            raise PermissionDenied("Verified correspondence author is required")
        write_report_authorizer(
            self.request.user,
            review.compilation.form_artifact.report_type,
            review.compilation.form_artifact.associating_id,
        )

    def _authorize_read(self, delivery):
        self._authorize_review_read(delivery.review)

    def _authorize_review_read(self, review):
        self._authorize_clinical_read(review.patient)
        read_report_authorizer(
            self.request.user,
            review.compilation.form_artifact.report_type,
            review.compilation.form_artifact.associating_id,
        )

    def _authorize_clinical_read(self, patient):
        if not (
            AuthorizationController.call(
                "can_view_clinical_data", self.request.user, patient
            )
            or AuthorizationController.call(
                "can_view_patient_questionnaire_responses",
                self.request.user,
                patient,
            )
        ):
            raise PermissionDenied("Permission denied for correspondence delivery")

    def _serialize(self, delivery):
        attempts = list(
            CorrespondenceDeliveryAttempt._base_manager.filter(  # noqa: SLF001
                delivery=delivery
            )
            .select_related("requested_by", "previous_terminal_event")
            .prefetch_related("events__actor", "events__previous_event")
            .order_by("attempt_number")
        )
        serialized_attempts = []
        for attempt in attempts:
            events = []
            for event in sorted(attempt.events.all(), key=lambda item: item.sequence):
                events.append(
                    {
                        "id": str(event.external_id),
                        "attempt": str(attempt.external_id),
                        "attempt_number": attempt.attempt_number,
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "certainty": event.certainty,
                        "occurred_at": event.occurred_at,
                        "actor_type": event.actor_type,
                        "actor": (
                            str(event.actor.external_id) if event.actor_id else None
                        ),
                        "safe_code": event.safe_code,
                        "provider_ack_reference": (
                            event.provider_ack_reference or None
                        ),
                        "provider_ack_hash": event.provider_ack_hash or None,
                        "provider_ack_at": event.provider_ack_at,
                        "previous_event": (
                            str(event.previous_event.external_id)
                            if event.previous_event_id
                            else None
                        ),
                        "event_hash": event.event_hash,
                    }
                )
            serialized_attempts.append(
                {
                    "id": str(attempt.external_id),
                    "attempt_number": attempt.attempt_number,
                    "command_type": attempt.command_type,
                    "requested_by": str(attempt.requested_by.external_id),
                    "requested_at": attempt.requested_at,
                    "attempt_hash": attempt.attempt_hash,
                    "previous_terminal_event": (
                        str(attempt.previous_terminal_event.external_id)
                        if attempt.previous_terminal_event_id
                        else None
                    ),
                    "events": events,
                }
            )
        latest = latest_delivery_event(delivery)
        return {
            "id": str(delivery.external_id),
            "correspondence_revision": str(delivery.revision.external_id),
            "resource_version": delivery.revision_version,
            "revision_hash": delivery.revision_hash,
            "artifact": str(delivery.artifact.external_id),
            "artifact_sha256": delivery.artifact_sha256,
            "review_binding": str(delivery.review.external_id),
            "review_hash": delivery.review_hash,
            "patient": str(delivery.patient.external_id),
            "encounter": str(delivery.encounter.external_id),
            "facility": str(delivery.facility.external_id),
            "department": str(delivery.department.external_id),
            "author": str(delivery.author.external_id),
            "recipient": str(delivery.recipient.external_id),
            "recipient_version": delivery.recipient_version,
            "recipient_hash": delivery.recipient_hash,
            "channel_type": delivery.channel_type,
            "adapter_name": delivery.adapter_name,
            "adapter_version": delivery.adapter_version,
            "delivery_hash": delivery.delivery_hash,
            "supersedes": (
                str(delivery.supersedes.external_id) if delivery.supersedes_id else None
            ),
            "correction_case_reference": (
                str(delivery.correction_case_reference)
                if delivery.correction_case_reference
                else None
            ),
            "state": latest.event_type,
            "certainty": latest.certainty,
            "latest_event_sequence": latest.sequence,
            "latest_event_hash": latest.event_hash,
            "can_retry": latest.event_type == "failed_retryable",
            "attempts": serialized_attempts,
        }

    def _response(self, client_request_id, delivery, *, replayed, response_status):
        return Response(
            {
                "client_request_id": str(client_request_id),
                "replayed": replayed,
                "delivery": self._serialize(delivery),
            },
            status=response_status,
        )

    @staticmethod
    def _conflict(code):
        return Response({"error": code}, status=status.HTTP_409_CONFLICT)

    @staticmethod
    def _unprocessable(code):
        return Response({"error": code}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    @staticmethod
    def _failed():
        return Response(
            {"error": "correspondence_delivery_failed"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @staticmethod
    def _related_fields():
        return (
            "revision__letter__review__compilation__form_artifact",
            "revision__letter__patient",
            "revision__letter__encounter",
            "artifact",
            "review__compilation__form_artifact",
            "review__patient",
            "review__encounter",
            "review__facility",
            "review__department",
            "review__author",
            "recipient",
            "patient",
            "encounter",
            "facility",
            "department",
            "author",
            "supersedes",
        )
