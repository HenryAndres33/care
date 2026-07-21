import json
import re
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from care.emr.models.consult_closure import ConsultClosureRecoveryTask
from care.emr.models.correspondence_correction import CorrespondenceCorrectionOutbox
from care.emr.models.correspondence_delivery import CorrespondenceDeliveryEvent
from care.emr.models.questionnaire import FormSubmission

SAFE_CODE_PATTERN = re.compile(r"^[a-z0-9_:-]{1,64}$")
OUTBOX_OUTSTANDING_STATES = ("pending", "processing", "failed_terminal")
DELIVERY_OUTSTANDING_STATES = (
    "dispatch_pending",
    "dispatching",
    "failed_retryable",
    "failed_terminal",
    "outcome_unknown",
)
DELIVERY_FAILED_OR_UNKNOWN_STATES = (
    "failed_terminal",
    "outcome_unknown",
)


def _safe_code_bucket(value: object) -> str:
    if value in (None, ""):
        return "none"
    candidate = str(value)
    return candidate if SAFE_CODE_PATTERN.fullmatch(candidate) else "invalid_safe_code"


def _age_seconds(timestamp, *, now) -> int | None:
    if timestamp is None:
        return None
    return max(0, int((now - timestamp).total_seconds()))


def _bucket_counts(queryset, field: str) -> dict[str, int]:
    rows = queryset.values(field).annotate(count=models.Count("id")).order_by(field)
    buckets: dict[str, int] = {}
    for row in rows:
        bucket = (
            _safe_code_bucket(row[field]) if field == "safe_code" else str(row[field])
        )
        buckets[bucket] = buckets.get(bucket, 0) + row["count"]
    return buckets


def _table_has_columns(model, table_columns: dict[str, set[str]], *field_names: str):
    table = model._meta.db_table  # noqa: SLF001
    columns = table_columns.get(table)
    if columns is None:
        return False
    required = {model._meta.get_field(field).column for field in field_names}  # noqa: SLF001
    return required.issubset(columns)


def _database_columns() -> dict[str, set[str]]:
    relevant_tables = {
        model._meta.db_table  # noqa: SLF001
        for model in (
            FormSubmission,
            ConsultClosureRecoveryTask,
            CorrespondenceCorrectionOutbox,
            CorrespondenceDeliveryEvent,
        )
    }
    with connection.cursor() as cursor:
        tables = relevant_tables.intersection(
            connection.introspection.table_names(cursor)
        )
        return {
            table: {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor,
                    table,
                )
            }
            for table in tables
        }


def _unavailable() -> dict[str, Any]:
    return {"available": False}


def _actorless_legacy_submissions(table_columns) -> dict[str, Any]:
    if not _table_has_columns(
        FormSubmission,
        table_columns,
        "status",
        "workflow_finalized_by",
    ):
        return _unavailable()
    count = FormSubmission._base_manager.filter(  # noqa: SLF001
        status="submitted",
        workflow_finalized_by__isnull=True,
    ).count()
    return {
        "available": True,
        "count": count,
        "migration_0086_blocked": count > 0,
    }


def _pending_recovery(table_columns, *, now) -> dict[str, Any]:
    if not _table_has_columns(
        ConsultClosureRecoveryTask,
        table_columns,
        "status",
        "safe_code",
        "created_date",
        "deleted",
    ):
        return _unavailable()
    queryset = ConsultClosureRecoveryTask._base_manager.filter(  # noqa: SLF001
        deleted=False,
        status="pending",
    )
    oldest = queryset.aggregate(oldest=models.Min("created_date"))["oldest"]
    return {
        "available": True,
        "count": queryset.count(),
        "oldest_age_seconds": _age_seconds(oldest, now=now),
        "safe_code_buckets": _bucket_counts(queryset, "safe_code"),
    }


def _correction_outbox(table_columns, *, now) -> dict[str, Any]:
    if not _table_has_columns(
        CorrespondenceCorrectionOutbox,
        table_columns,
        "status",
        "safe_code",
        "created_date",
        "deleted",
    ):
        return _unavailable()
    queryset = CorrespondenceCorrectionOutbox._base_manager.filter(  # noqa: SLF001
        deleted=False,
        status__in=OUTBOX_OUTSTANDING_STATES,
    )
    oldest = queryset.aggregate(oldest=models.Min("created_date"))["oldest"]
    status_buckets = _bucket_counts(queryset, "status")
    return {
        "available": True,
        "outstanding_count": queryset.count(),
        "failed_terminal_count": status_buckets.get("failed_terminal", 0),
        "oldest_outstanding_age_seconds": _age_seconds(oldest, now=now),
        "status_buckets": status_buckets,
        "safe_code_buckets": _bucket_counts(queryset, "safe_code"),
    }


def _delivery_latest_state(table_columns, *, now) -> dict[str, Any]:
    if not _table_has_columns(
        CorrespondenceDeliveryEvent,
        table_columns,
        "delivery",
        "event_type",
        "safe_code",
        "occurred_at",
        "sequence",
        "deleted",
    ):
        return _unavailable()
    latest_for_delivery = (
        CorrespondenceDeliveryEvent._base_manager.filter(  # noqa: SLF001
            deleted=False,
            delivery_id=OuterRef("delivery_id"),
        )
        .order_by("-sequence", "-id")
        .values("id")[:1]
    )
    queryset = CorrespondenceDeliveryEvent._base_manager.filter(  # noqa: SLF001
        deleted=False,
        id=Subquery(latest_for_delivery),
        event_type__in=DELIVERY_OUTSTANDING_STATES,
    )
    oldest = queryset.aggregate(oldest=models.Min("occurred_at"))["oldest"]
    state_buckets = _bucket_counts(queryset, "event_type")
    return {
        "available": True,
        "outstanding_count": queryset.count(),
        "failed_or_unknown_count": sum(
            state_buckets.get(state, 0) for state in DELIVERY_FAILED_OR_UNKNOWN_STATES
        ),
        "oldest_outstanding_age_seconds": _age_seconds(oldest, now=now),
        "state_buckets": state_buckets,
        "safe_code_buckets": _bucket_counts(queryset, "safe_code"),
    }


def build_readiness_report(*, now=None) -> dict[str, Any]:
    now = now or timezone.now()
    table_columns = _database_columns()
    return {
        "contract": "clinical-workflow-readiness-v1",
        "actorless_legacy_form_submissions": _actorless_legacy_submissions(
            table_columns
        ),
        "pending_consult_closure_recovery": _pending_recovery(
            table_columns,
            now=now,
        ),
        "correspondence_correction_outbox": _correction_outbox(
            table_columns,
            now=now,
        ),
        "correspondence_delivery_latest_state": _delivery_latest_state(
            table_columns,
            now=now,
        ),
    }


def _has_blocking_risk(report: dict[str, Any]) -> bool:
    actorless = report["actorless_legacy_form_submissions"]
    recovery = report["pending_consult_closure_recovery"]
    outbox = report["correspondence_correction_outbox"]
    delivery = report["correspondence_delivery_latest_state"]
    return any(
        [
            actorless.get("count", 0) > 0,
            recovery.get("count", 0) > 0,
            outbox.get("failed_terminal_count", 0) > 0,
            delivery.get("failed_or_unknown_count", 0) > 0,
        ]
    )


class Command(BaseCommand):
    help = (
        "Emit a read-only, PHI-minimal JSON readiness report for clinical workflow "
        "migrations and operations. Output never includes names, UUIDs, or clinical text."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fail-on-risk",
            action="store_true",
            help=(
                "Exit non-zero after printing the report when actorless legacy rows, "
                "pending closure recovery, terminal outbox failure, or unknown/terminal "
                "delivery state is present."
            ),
        )

    def handle(self, *args, **options):
        report = build_readiness_report()
        self.stdout.write(json.dumps(report, sort_keys=True, separators=(",", ":")))
        if options["fail_on_risk"] and _has_blocking_risk(report):
            raise CommandError("clinical_workflow_readiness_failed")
