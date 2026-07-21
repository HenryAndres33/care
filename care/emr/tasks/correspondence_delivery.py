from datetime import timedelta
from logging import Logger

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from care.emr.correspondence.delivery import (
    CorrespondenceDeliveryIntegrityError,
    CorrespondenceDispatchNotCurrentError,
    append_delivery_event,
    latest_delivery_event,
    lock_and_assert_correspondence_dispatch_current,
    lock_and_verify_delivery_ledger,
    lock_correspondence_dispatch_source_current,
    read_and_verify_correspondence_artifact,
)
from care.emr.correspondence.delivery_adapters import (
    CorrespondenceDeliveryAdapterUnavailableError,
    get_correspondence_delivery_adapter,
    synthetic_delivery_mode,
)
from care.emr.models.correspondence_delivery import (
    CorrespondenceDelivery,
    CorrespondenceDeliveryAttempt,
    CorrespondenceDeliveryEvent,
)
from care.emr.reports.authorizers.utils import write_report_authorizer
from care.emr.workflow_capabilities import correspondence_delivery_enabled

logger: Logger = get_task_logger(__name__)
OUTBOX_BATCH_SIZE = 100
MAX_AUTOMATIC_RECONCILIATION_LOOKUPS = 5


@shared_task(ignore_result=True)
def dispatch_correspondence_delivery_attempt(attempt_external_id: str):
    """Claim one durable pending attempt and dispatch it at most once per claim."""
    attempt_id = _claim_pending_attempt(attempt_external_id)
    if attempt_id is None:
        return
    _dispatch_claimed_attempt(attempt_id)


@shared_task(ignore_result=True)
def scan_correspondence_delivery_outbox():
    """Re-enqueue pending work and reconcile abandoned dispatching claims."""
    pending = list(
        CorrespondenceDeliveryAttempt._base_manager.filter(  # noqa: SLF001
            deleted=False,
            events__event_type="dispatch_pending",
        )
        .exclude(events__event_type="dispatching")
        .order_by("requested_at")
        .values_list("external_id", flat=True)[:OUTBOX_BATCH_SIZE]
    )
    for attempt_id in pending:
        dispatch_correspondence_delivery_attempt.delay(str(attempt_id))

    cutoff = timezone.now() - timedelta(
        seconds=getattr(settings, "CORRESPONDENCE_DISPATCH_STALE_SECONDS", 300)
    )
    abandoned = list(
        CorrespondenceDeliveryAttempt._base_manager.filter(  # noqa: SLF001
            deleted=False,
            events__event_type="dispatching",
            events__occurred_at__lte=cutoff,
        )
        .exclude(
            events__event_type__in=[
                "acknowledged",
                "failed_retryable",
                "failed_terminal",
                "outcome_unknown",
            ]
        )
        .order_by("requested_at")
        .values_list("external_id", flat=True)[:OUTBOX_BATCH_SIZE]
    )
    for attempt_id in abandoned:
        reconcile_correspondence_delivery_attempt.delay(
            str(attempt_id),
            automatic=True,
        )

    latest_events = CorrespondenceDeliveryEvent._base_manager.filter(  # noqa: SLF001
        delivery_id=OuterRef("delivery_id")
    ).order_by("-sequence")
    unresolved = list(
        CorrespondenceDeliveryAttempt._base_manager.filter(  # noqa: SLF001
            deleted=False,
        )
        .annotate(
            latest_event_type=Subquery(latest_events.values("event_type")[:1]),
            latest_attempt_id=Subquery(latest_events.values("attempt_id")[:1]),
        )
        .filter(latest_event_type="outcome_unknown", latest_attempt_id=F("id"))
        .annotate(
            lookup_count=Count(
                "events",
                filter=Q(events__safe_code="reconcile_lookup_started"),
            )
        )
        .filter(lookup_count__lt=MAX_AUTOMATIC_RECONCILIATION_LOOKUPS)
        .order_by("requested_at")
        .values_list("external_id", flat=True)[:OUTBOX_BATCH_SIZE]
    )
    for attempt_id in unresolved:
        reconcile_correspondence_delivery_attempt.delay(
            str(attempt_id),
            automatic=True,
        )


@shared_task(ignore_result=True)
def reconcile_correspondence_delivery_attempt(
    attempt_external_id: str,
    automatic: bool = False,
):
    """Query the allowlisted provider; never blindly repeat an uncertain send."""
    prepared = _prepare_reconciliation_lookup(
        attempt_external_id,
        automatic=automatic,
    )
    if not prepared:
        return
    try:
        outcome = prepared["adapter"].lookup(
            provider_idempotency_key=prepared["provider_idempotency_key"],
            attempt_number=prepared["attempt_number"],
            delivery_hash=prepared["delivery_hash"],
            artifact_sha256=prepared["artifact_sha256"],
            test_mode=prepared["test_mode"],
        )
    except Exception as exc:
        logger.warning(
            "Correspondence reconciliation lookup failed delivery=%s error=%s",
            prepared["delivery_external_id"],
            type(exc).__name__,
        )
        return
    if outcome.state == "outcome_unknown":
        return
    _finalize_reconciliation_lookup(prepared["attempt_id"], outcome=outcome)


def _prepare_reconciliation_lookup(  # noqa: PLR0911
    attempt_external_id, *, automatic
):
    with transaction.atomic():
        attempt = _locked_attempt(attempt_external_id)
        if not attempt:
            return None
        delivery = _locked_delivery(attempt.delivery_id)
        if not correspondence_delivery_enabled(delivery.facility.external_id):
            return None
        latest = latest_delivery_event(delivery, lock=True)
        if (
            not latest
            or latest.attempt_id != attempt.id
            or latest.event_type
            not in {
                "dispatching",
                "outcome_unknown",
            }
        ):
            return None
        if automatic:
            lookup_count = delivery.events.filter(
                attempt=attempt,
                safe_code="reconcile_lookup_started",
            ).count()
            if lookup_count >= MAX_AUTOMATIC_RECONCILIATION_LOOKUPS:
                return None
        try:
            lock_and_verify_delivery_ledger(delivery)
        except CorrespondenceDeliveryIntegrityError:
            logger.warning(
                "Correspondence reconciliation rejected delivery=%s error=%s",
                delivery.external_id,
                "CorrespondenceDeliveryIntegrityError",
            )
            return None
        try:
            adapter = get_correspondence_delivery_adapter(delivery.recipient)
        except CorrespondenceDeliveryAdapterUnavailableError:
            return None
        append_delivery_event(
            delivery=delivery,
            attempt=attempt,
            event_type="outcome_unknown",
            certainty="unknown",
            safe_code="reconcile_lookup_started",
        )
        return {
            "adapter": adapter,
            "artifact_sha256": delivery.artifact_sha256,
            "attempt_id": attempt.id,
            "attempt_number": attempt.attempt_number,
            "delivery_external_id": str(delivery.external_id),
            "delivery_hash": delivery.delivery_hash,
            "provider_idempotency_key": delivery.provider_idempotency_key,
            "test_mode": synthetic_delivery_mode(delivery.recipient),
        }


def _finalize_reconciliation_lookup(attempt_id, *, outcome):
    with transaction.atomic():
        attempt = (
            CorrespondenceDeliveryAttempt._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related("delivery__recipient")
            .filter(pk=attempt_id)
            .first()
        )
        if not attempt:
            return
        delivery = _locked_delivery(attempt.delivery_id)
        try:
            lock_and_verify_delivery_ledger(delivery)
        except CorrespondenceDeliveryIntegrityError:
            return
        latest = latest_delivery_event(delivery, lock=True)
        if (
            not latest
            or latest.attempt_id != attempt.id
            or latest.event_type != "outcome_unknown"
        ):
            return
        _append_outcome(
            delivery,
            attempt,
            state=outcome.state,
            safe_code=outcome.safe_code,
            provider_ack_reference=outcome.provider_ack_reference,
        )


def _claim_pending_attempt(attempt_external_id: str) -> int | None:  # noqa: PLR0911
    with transaction.atomic():
        revision_id = (
            CorrespondenceDeliveryAttempt._base_manager.filter(  # noqa: SLF001
                external_id=attempt_external_id,
                deleted=False,
            )
            .values_list("delivery__revision_id", flat=True)
            .first()
        )
        if revision_id is None:
            return None
        source = None
        source_error = None
        try:
            source = lock_correspondence_dispatch_source_current(revision_id)
        except CorrespondenceDispatchNotCurrentError as exc:
            source_error = exc
        attempt = _locked_attempt(attempt_external_id)
        if not attempt:
            return None
        delivery = _locked_delivery(attempt.delivery_id)
        if not correspondence_delivery_enabled(delivery.facility.external_id):
            return None
        latest = latest_delivery_event(delivery, lock=True)
        if (
            not latest
            or latest.attempt_id != attempt.id
            or latest.event_type != "dispatch_pending"
        ):
            return None
        try:
            lock_and_verify_delivery_ledger(delivery)
            if source_error:
                raise source_error
            context = lock_and_assert_correspondence_dispatch_current(
                delivery.revision_id,
                locked_source=source,
            )
            _assert_delivery_context(delivery, context)
            _assert_dispatch_authorized(delivery, context)
            get_correspondence_delivery_adapter(context.recipient)
        except CorrespondenceDeliveryIntegrityError:
            logger.warning(
                "Correspondence claim rejected delivery=%s error=%s",
                delivery.external_id,
                "CorrespondenceDeliveryIntegrityError",
            )
            return None
        except (
            CorrespondenceDeliveryAdapterUnavailableError,
            CorrespondenceDispatchNotCurrentError,
            PermissionDenied,
        ) as exc:
            _append_claim_rejection(
                delivery,
                attempt,
                _preflight_terminal_safe_code(exc),
            )
            return None
        append_delivery_event(
            delivery=delivery,
            attempt=attempt,
            event_type="dispatching",
            certainty="attempting",
            safe_code="worker_claimed",
        )
        return attempt.id


def _dispatch_claimed_attempt(attempt_id: int):
    prepared = _prepare_claimed_attempt(attempt_id)
    if not prepared:
        return
    provider_started = False
    try:
        provider_started = True
        outcome = prepared["adapter"].deliver(
            artifact_bytes=prepared["artifact_bytes"],
            provider_idempotency_key=prepared["provider_idempotency_key"],
            attempt_number=prepared["attempt_number"],
            test_mode=prepared["test_mode"],
            delivery_hash=prepared["delivery_hash"],
            artifact_sha256=prepared["artifact_sha256"],
        )
    except Exception as exc:  # receipt outcome cannot safely be inferred here
        logger.warning(
            "Correspondence provider phase failed delivery=%s error=%s",
            prepared["delivery_external_id"],
            type(exc).__name__,
        )
        outcome = None
    _finalize_claimed_attempt(
        attempt_id,
        outcome=outcome,
        provider_started=provider_started,
    )


def _prepare_claimed_attempt(attempt_id: int):  # noqa: PLR0911
    """Phase A: locked ledger/currentness/auth/artifact preflight; no provider call."""
    with transaction.atomic():
        revision_id = (
            CorrespondenceDeliveryAttempt._base_manager.filter(  # noqa: SLF001
                pk=attempt_id,
                deleted=False,
            )
            .values_list("delivery__revision_id", flat=True)
            .first()
        )
        if revision_id is None:
            return None
        source = None
        source_error = None
        try:
            source = lock_correspondence_dispatch_source_current(revision_id)
        except CorrespondenceDispatchNotCurrentError as exc:
            source_error = exc
        attempt = (
            CorrespondenceDeliveryAttempt._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related("delivery__recipient")
            .filter(pk=attempt_id)
            .first()
        )
        if not attempt:
            return None
        delivery = _locked_delivery(attempt.delivery_id)
        latest = latest_delivery_event(delivery, lock=True)
        if (
            not latest
            or latest.attempt_id != attempt.id
            or latest.event_type != "dispatching"
        ):
            return None
        try:
            lock_and_verify_delivery_ledger(delivery)
            if source_error:
                raise source_error
            context = lock_and_assert_correspondence_dispatch_current(
                delivery.revision_id,
                locked_source=source,
            )
            _assert_delivery_context(delivery, context)
            _assert_dispatch_authorized(delivery, context)
            if not correspondence_delivery_enabled(delivery.facility.external_id):
                _append_outcome(
                    delivery,
                    attempt,
                    state="failed_retryable",
                    safe_code="correspondence_delivery_disabled",
                )
                return None
            adapter = get_correspondence_delivery_adapter(context.recipient)
            artifact_bytes = read_and_verify_correspondence_artifact(context.artifact)
            provider_started = adapter.begin(
                provider_idempotency_key=delivery.provider_idempotency_key,
                attempt_number=attempt.attempt_number,
                delivery_hash=delivery.delivery_hash,
                artifact_sha256=delivery.artifact_sha256,
            )
        except CorrespondenceDeliveryIntegrityError as exc:
            logger.warning(
                "Correspondence dispatch rejected delivery=%s error=%s",
                delivery.external_id,
                type(exc).__name__,
            )
            return None
        except (
            CorrespondenceDeliveryAdapterUnavailableError,
            CorrespondenceDispatchNotCurrentError,
            PermissionDenied,
        ) as exc:
            logger.warning(
                "Correspondence dispatch rejected delivery=%s error=%s",
                delivery.external_id,
                type(exc).__name__,
            )
            _append_outcome(
                delivery,
                attempt,
                state="failed_terminal",
                safe_code=_preflight_terminal_safe_code(exc),
            )
            return None
        if not provider_started:
            _append_outcome(
                delivery,
                attempt,
                state="outcome_unknown",
                safe_code="provider_start_integrity_unknown",
            )
            return None
        return {
            "adapter": adapter,
            "artifact_bytes": artifact_bytes,
            "artifact_sha256": delivery.artifact_sha256,
            "attempt_number": attempt.attempt_number,
            "delivery_external_id": str(delivery.external_id),
            "delivery_hash": delivery.delivery_hash,
            "provider_idempotency_key": delivery.provider_idempotency_key,
            "test_mode": synthetic_delivery_mode(context.recipient),
        }


def _finalize_claimed_attempt(attempt_id: int, *, outcome, provider_started: bool):
    """Phase C: append the provider result after the independent provider phase."""
    with transaction.atomic():
        attempt = (
            CorrespondenceDeliveryAttempt._base_manager.select_for_update(  # noqa: SLF001
                of=("self",)
            )
            .select_related("delivery__recipient")
            .filter(pk=attempt_id)
            .first()
        )
        if not attempt:
            return
        delivery = _locked_delivery(attempt.delivery_id)
        try:
            lock_and_verify_delivery_ledger(delivery)
        except CorrespondenceDeliveryIntegrityError as exc:
            logger.warning(
                "Correspondence outcome append rejected delivery=%s error=%s",
                delivery.external_id,
                type(exc).__name__,
            )
            return
        latest = latest_delivery_event(delivery, lock=True)
        if (
            not latest
            or latest.attempt_id != attempt.id
            or latest.event_type not in {"dispatching", "outcome_unknown"}
        ):
            return
        if outcome is None:
            _append_outcome(
                delivery,
                attempt,
                state="outcome_unknown" if provider_started else "failed_retryable",
                safe_code=(
                    "provider_outcome_unknown"
                    if provider_started
                    else "dispatch_internal_retryable"
                ),
            )
            return
        _append_outcome(
            delivery,
            attempt,
            state=outcome.state,
            safe_code=outcome.safe_code,
            provider_ack_reference=outcome.provider_ack_reference,
        )


def _append_outcome(
    delivery,
    attempt,
    *,
    state,
    safe_code,
    provider_ack_reference="",
):
    certainty = {
        "acknowledged": "acknowledged",
        "failed_retryable": "not_delivered",
        "failed_terminal": "not_delivered",
        "outcome_unknown": "unknown",
    }[state]
    append_delivery_event(
        delivery=delivery,
        attempt=attempt,
        event_type=state,
        certainty=certainty,
        safe_code=safe_code,
        provider_ack_reference=provider_ack_reference,
        provider_ack_at=timezone.now() if state == "acknowledged" else None,
    )


def _append_claim_rejection(delivery, attempt, safe_code):
    append_delivery_event(
        delivery=delivery,
        attempt=attempt,
        event_type="dispatching",
        certainty="attempting",
        safe_code="claim_rejected",
    )
    append_delivery_event(
        delivery=delivery,
        attempt=attempt,
        event_type="failed_terminal",
        certainty="not_delivered",
        safe_code=safe_code,
    )


def _preflight_terminal_safe_code(exc):
    if isinstance(exc, CorrespondenceDeliveryAdapterUnavailableError):
        return "adapter_unavailable"
    if isinstance(exc, CorrespondenceDispatchNotCurrentError):
        return "source_not_current"
    return "authorization_revoked"


def _locked_attempt(external_id):
    return (
        CorrespondenceDeliveryAttempt._base_manager.select_for_update(  # noqa: SLF001
            of=("self",)
        )
        .select_related("delivery__recipient")
        .filter(external_id=external_id, deleted=False)
        .first()
    )


def _locked_delivery(delivery_id):
    return (
        CorrespondenceDelivery._base_manager.select_for_update(  # noqa: SLF001
            of=("self",)
        )
        .select_related(
            "recipient",
            "revision__letter",
            "artifact",
            "review__compilation__form_artifact",
            "author",
        )
        .get(pk=delivery_id)
    )


def _assert_delivery_context(delivery, context):
    if not all(
        [
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


def _assert_dispatch_authorized(delivery, context):
    author = delivery.author
    if (
        author.id != context.review.author_id
        or author.deleted
        or not author.is_active
        or not author.verified
        or author.is_service_account
    ):
        raise PermissionDenied("Verified correspondence author is required")
    write_report_authorizer(
        author,
        context.review.compilation.form_artifact.report_type,
        context.review.compilation.form_artifact.associating_id,
    )
