from datetime import timedelta
from logging import Logger
from uuid import uuid4

from celery import shared_task
from celery.utils.log import get_task_logger
from django.db import transaction
from django.db.models import (
    Case,
    CharField,
    Exists,
    F,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.utils import timezone

from care.emr.correspondence.correction import (
    CorrespondenceCorrectionIntegrityError,
    CorrespondenceCorrectionPendingError,
    materialize_claimed_correction_outbox,
    refresh_correction_case_for_delivery,
)
from care.emr.correspondence.replacement import (
    refresh_replacement_case_for_delivery,
)
from care_suriname.models.correspondence_correction import (
    CorrespondenceCorrectionOutbox,
)
from care_suriname.models.correspondence_delivery import (
    CorrespondenceDelivery,
    CorrespondenceDeliveryEvent,
)

logger: Logger = get_task_logger(__name__)
CORRECTION_OUTBOX_BATCH_SIZE = 100
CORRECTION_OUTBOX_LEASE_SECONDS = 300
CORRECTION_OUTBOX_MAX_ATTEMPTS = 10
CORRECTION_DELIVERY_REPAIR_BATCH_SIZE = 100


@shared_task(ignore_result=True)
def project_correspondence_correction(outbox_external_id: str):
    claim = _claim_correction_outbox(outbox_external_id)
    if not claim:
        return
    outbox_id, claim_token = claim
    try:
        materialize_claimed_correction_outbox(
            outbox_id=outbox_id,
            claim_token=claim_token,
        )
    except CorrespondenceCorrectionPendingError:
        _release_claim(
            outbox_id,
            claim_token,
            safe_code="predecessor_pending",
            delay_seconds=5,
        )
    except CorrespondenceCorrectionIntegrityError:
        _terminal_claim(
            outbox_id,
            claim_token,
            safe_code="projection_integrity_failed",
        )
    except Exception as exc:
        logger.warning(
            "Correspondence correction projection failed outbox=%s error=%s",
            outbox_external_id,
            type(exc).__name__,
        )
        _retry_or_terminal_claim(outbox_id, claim_token)


@shared_task(ignore_result=True)
def scan_correspondence_correction_outbox():
    now = timezone.now()
    candidates = list(
        CorrespondenceCorrectionOutbox._base_manager.filter(  # noqa: SLF001
            deleted=False,
        )
        .filter(
            status="pending",
            available_at__lte=now,
        )
        .order_by("available_at", "id")
        .values_list("external_id", flat=True)[:CORRECTION_OUTBOX_BATCH_SIZE]
    )
    stale = list(
        CorrespondenceCorrectionOutbox._base_manager.filter(  # noqa: SLF001
            deleted=False,
            status="processing",
            lease_expires_at__lte=now,
        )
        .order_by("lease_expires_at", "id")
        .values_list("external_id", flat=True)[:CORRECTION_OUTBOX_BATCH_SIZE]
    )
    seen = set()
    for external_id in [*candidates, *stale]:
        if external_id in seen:
            continue
        seen.add(external_id)
        project_correspondence_correction.delay(str(external_id))


@shared_task(ignore_result=True)
def scan_correspondence_correction_delivery_cases():
    latest_events = CorrespondenceDeliveryEvent._base_manager.filter(  # noqa: SLF001
        delivery_id=OuterRef("pk")
    ).order_by("-sequence")
    projected_sources = CorrespondenceCorrectionOutbox._base_manager.filter(  # noqa: SLF001
        status="completed",
        source_correction__source_head__series_id=OuterRef(
            "review__compilation__form_submission__series_id"
        ),
        source_correction__new_version__gt=OuterRef(
            "review__compilation__form_source_version"
        ),
    )
    candidates = (
        CorrespondenceDelivery._base_manager.filter(deleted=False)  # noqa: SLF001
        .annotate(
            correction_source_projected=Exists(projected_sources),
            latest_certainty=Subquery(latest_events.values("certainty")[:1]),
            latest_state=Subquery(latest_events.values("event_type")[:1]),
            expected_notification=Case(
                When(latest_state="acknowledged", then=Value("required")),
                When(
                    latest_certainty="not_delivered",
                    then=Value("not_required"),
                ),
                default=Value("unknown"),
                output_field=CharField(),
            ),
        )
        .filter(correction_source_projected=True, latest_state__isnull=False)
        .filter(
            Q(correction_case__isnull=False)
            & (
                ~Q(correction_case__delivery_state=F("latest_state"))
                | ~Q(correction_case__delivery_certainty=F("latest_certainty"))
                | Q(correction_case__replacement_status="not_started")
                & ~Q(correction_case__notification_status=F("expected_notification"))
            )
            | Q(
                correction_case__isnull=True,
            )
            & ~Q(latest_certainty="not_delivered")
        )
        .order_by("id")
        .values_list("external_id", flat=True)[:CORRECTION_DELIVERY_REPAIR_BATCH_SIZE]
    )
    for delivery_external_id in candidates:
        refresh_correspondence_correction_delivery.delay(str(delivery_external_id))


@shared_task(ignore_result=True)
def refresh_correspondence_correction_delivery(delivery_external_id: str):
    from care_suriname.models.correspondence_delivery import CorrespondenceDelivery

    delivery_id = (
        CorrespondenceDelivery._base_manager.filter(  # noqa: SLF001
            external_id=delivery_external_id,
            deleted=False,
        )
        .values_list("pk", flat=True)
        .first()
    )
    if not delivery_id:
        return
    try:
        refresh_correction_case_for_delivery(delivery_id)
    except CorrespondenceCorrectionPendingError:
        return
    except CorrespondenceCorrectionIntegrityError:
        logger.warning(
            "Correspondence correction delivery refresh rejected delivery=%s",
            delivery_external_id,
        )


@shared_task(ignore_result=True)
def refresh_correspondence_replacement_delivery(delivery_external_id: str):
    delivery_id = (
        CorrespondenceDelivery._base_manager.filter(  # noqa: SLF001
            external_id=delivery_external_id,
            deleted=False,
            correction_case_reference__isnull=False,
        )
        .values_list("pk", flat=True)
        .first()
    )
    if not delivery_id:
        return
    try:
        refresh_replacement_case_for_delivery(delivery_id)
    except CorrespondenceCorrectionIntegrityError:
        logger.warning(
            "Correspondence replacement delivery refresh rejected delivery=%s",
            delivery_external_id,
        )


@shared_task(ignore_result=True)
def scan_correspondence_replacement_delivery_cases():
    latest_events = CorrespondenceDeliveryEvent._base_manager.filter(  # noqa: SLF001
        delivery_id=OuterRef("pk")
    ).order_by("-sequence")
    candidates = (
        CorrespondenceDelivery._base_manager.filter(  # noqa: SLF001
            deleted=False,
            correction_case_reference__isnull=False,
        )
        .annotate(
            latest_certainty=Subquery(latest_events.values("certainty")[:1]),
            latest_state=Subquery(latest_events.values("event_type")[:1]),
        )
        .filter(latest_state__isnull=False)
        .filter(
            ~Q(
                correction_commands__case__replacement_delivery_state=F("latest_state"),
                correction_commands__case__replacement_delivery_certainty=F(
                    "latest_certainty"
                ),
            )
        )
        .order_by("id")
        .values_list("external_id", flat=True)[:CORRECTION_DELIVERY_REPAIR_BATCH_SIZE]
    )
    for delivery_external_id in candidates:
        refresh_correspondence_replacement_delivery.delay(str(delivery_external_id))


def _claim_correction_outbox(outbox_external_id):
    now = timezone.now()
    with transaction.atomic():
        outbox = (
            CorrespondenceCorrectionOutbox._base_manager.select_for_update(  # noqa: SLF001
                skip_locked=True,
                of=("self",),
            )
            .select_related("source_correction")
            .filter(external_id=outbox_external_id, deleted=False)
            .filter(
                status="pending",
                available_at__lte=now,
            )
            .first()
        )
        if not outbox:
            outbox = (
                CorrespondenceCorrectionOutbox._base_manager.select_for_update(  # noqa: SLF001
                    skip_locked=True,
                    of=("self",),
                )
                .select_related("source_correction")
                .filter(
                    external_id=outbox_external_id,
                    deleted=False,
                    status="processing",
                    lease_expires_at__lte=now,
                )
                .first()
            )
        if not outbox:
            return None
        predecessor_incomplete = CorrespondenceCorrectionOutbox._base_manager.filter(  # noqa: SLF001
            source_correction__source_head_id=outbox.source_correction.source_head_id,
            source_correction__sequence__lt=outbox.source_correction.sequence,
        ).exclude(status="completed")
        if predecessor_incomplete.exists():
            return None
        claim_token = uuid4()
        outbox.status = "processing"
        outbox.attempt_count += 1
        outbox.claimed_at = now
        outbox.claim_token = claim_token
        outbox.lease_expires_at = now + timedelta(
            seconds=CORRECTION_OUTBOX_LEASE_SECONDS
        )
        outbox.completed_at = None
        outbox.safe_code = ""
        outbox.updated_by = None
        outbox.save(
            update_fields=[
                "status",
                "attempt_count",
                "claimed_at",
                "claim_token",
                "lease_expires_at",
                "completed_at",
                "safe_code",
                "updated_by",
                "modified_date",
            ]
        )
        return outbox.pk, claim_token


def _retry_or_terminal_claim(outbox_id, claim_token):
    attempt_count = (
        CorrespondenceCorrectionOutbox._base_manager.filter(  # noqa: SLF001
            pk=outbox_id,
            status="processing",
            claim_token=claim_token,
        )
        .values_list("attempt_count", flat=True)
        .first()
    )
    if attempt_count is None:
        return
    if attempt_count >= CORRECTION_OUTBOX_MAX_ATTEMPTS:
        _terminal_claim(
            outbox_id,
            claim_token,
            safe_code="projection_attempts_exhausted",
        )
        return
    _release_claim(
        outbox_id,
        claim_token,
        safe_code="projection_retryable",
        delay_seconds=min(300, 2**attempt_count),
    )


def _release_claim(
    outbox_id,
    claim_token,
    *,
    safe_code,
    delay_seconds,
):
    with transaction.atomic():
        outbox = (
            CorrespondenceCorrectionOutbox._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(
                pk=outbox_id,
                status="processing",
                claim_token=claim_token,
            )
            .first()
        )
        if not outbox:
            return False
        outbox.status = "pending"
        outbox.available_at = timezone.now() + timedelta(seconds=delay_seconds)
        outbox.claimed_at = None
        outbox.claim_token = None
        outbox.lease_expires_at = None
        outbox.completed_at = None
        outbox.safe_code = safe_code
        outbox.updated_by = None
        outbox.save(
            update_fields=[
                "status",
                "available_at",
                "claimed_at",
                "claim_token",
                "lease_expires_at",
                "completed_at",
                "safe_code",
                "updated_by",
                "modified_date",
            ]
        )
        return True


def _terminal_claim(outbox_id, claim_token, *, safe_code):
    with transaction.atomic():
        outbox = (
            CorrespondenceCorrectionOutbox._base_manager.select_for_update(of=("self",))  # noqa: SLF001
            .filter(
                pk=outbox_id,
                status="processing",
                claim_token=claim_token,
            )
            .first()
        )
        if not outbox:
            return False
        outbox.status = "failed_terminal"
        outbox.completed_at = timezone.now()
        outbox.safe_code = safe_code
        outbox.updated_by = None
        outbox.save(
            update_fields=[
                "status",
                "completed_at",
                "safe_code",
                "updated_by",
                "modified_date",
            ]
        )
        return True
