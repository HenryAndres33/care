import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

import django.db.models.deletion
import django.db.models.expressions
from django.conf import settings
from django.db import migrations, models

MAX_BACKFILL_SERIES_LENGTH = 1000
MAX_COMMAND_LEDGER_DELAY_SECONDS = 300
UUID_V4 = 4


def _normalize(value):  # noqa: PLR0911
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        if value.is_zero():
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _sha256(value):
    encoded = json.dumps(
        _normalize(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _finalized_snapshot_hash(submission):
    return _sha256(
        {
            "amendment_reason": submission.amendment_reason,
            "amendment_type": submission.amendment_type,
            "contract": "form-submission-finalized-snapshot-v1",
            "encounter": (
                submission.encounter.external_id
                if submission.encounter_id
                else None
            ),
            "patient": submission.patient.external_id,
            "previous_version": (
                submission.previous_version.external_id
                if submission.previous_version_id
                else None
            ),
            "questionnaire": submission.questionnaire.slug,
            "resource_version": submission.resource_version,
            "response_dump": submission.response_dump,
            "series_id": submission.series_id,
        }
    )


def _head_hash(submission):
    return _sha256(
        {
            "advanced_at": submission.workflow_finalized_at,
            "advanced_by": submission.workflow_finalized_by.external_id,
            "contract": "form-submission-series-head-v1",
            "current_snapshot_hash": submission.finalized_snapshot_hash,
            "current_submission": submission.external_id,
            "current_version": submission.resource_version,
            "series_id": submission.series_id,
        }
    )


def _correction_hash(*, head, previous, result, sequence, source_head_hash):
    return _sha256(
        {
            "amendment_type": result.amendment_type,
            "contract": "correspondence-source-correction-v1",
            "corrected_at": result.workflow_finalized_at,
            "corrected_by": result.workflow_finalized_by.external_id,
            "new_snapshot_hash": result.finalized_snapshot_hash,
            "new_submission": result.external_id,
            "new_version": result.resource_version,
            "previous_snapshot_hash": previous.finalized_snapshot_hash,
            "previous_submission": previous.external_id,
            "previous_version": previous.resource_version,
            "reason": result.amendment_reason,
            "sequence": sequence,
            "series_id": head.series_id,
            "source_head_hash": source_head_hash,
        }
    )


def _amend_command_hash(*, command, previous, result):
    return _sha256(
        {
            "actor": command.actor.external_id,
            "command": "amend",
            "contract": "form-submission-command-v1",
            "payload": {
                "amendment_type": result.amendment_type,
                "encounter": (
                    result.encounter.external_id if result.encounter_id else None
                ),
                "expected_version": previous.resource_version,
                "patient": result.patient.external_id,
                "questionnaire": result.questionnaire.slug,
                "reason": result.amendment_reason,
                "response_dump": result.response_dump,
            },
            "target": previous.external_id,
        }
    )


def _command_is_valid(command, *, previous, result):
    try:
        if command is None or command.created_date is None:
            return False
        ledger_delay = command.created_date - result.workflow_finalized_at
        return all(
            [
                not command.deleted,
                command.command_type == "amend",
                command.actor_id == result.workflow_finalized_by_id,
                command.created_by_id == command.actor_id,
                command.updated_by_id == command.actor_id,
                command.patient_id == result.patient_id,
                command.encounter_id == result.encounter_id,
                command.questionnaire_id == result.questionnaire_id,
                command.target_submission_id == previous.id,
                command.result_submission_id == result.id,
                command.expected_version == previous.resource_version,
                command.client_request_id.version == UUID_V4,
                ledger_delay.total_seconds() >= 0,
                ledger_delay.total_seconds() <= MAX_COMMAND_LEDGER_DELAY_SECONDS,
                command.payload_hash
                == _amend_command_hash(
                    command=command,
                    previous=previous,
                    result=result,
                ),
            ]
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _series_is_valid(submissions, commands_by_result):
    if not submissions or any(submission.deleted for submission in submissions):
        return False
    root = submissions[0]
    if (
        root.previous_version_id is not None
        or root.amendment_reason
        or root.amendment_type
    ):
        return False
    context = (root.patient_id, root.encounter_id, root.questionnaire_id)
    previous = None
    for submission in submissions:
        if (
            submission.status != "submitted"
            or submission.workflow_finalized_at is None
            or submission.workflow_finalized_by_id is None
            or not submission.finalized_snapshot_hash
            or _finalized_snapshot_hash(submission)
            != submission.finalized_snapshot_hash
            or (
                submission.patient_id,
                submission.encounter_id,
                submission.questionnaire_id,
            )
            != context
        ):
            return False
        if previous is None:
            previous = submission
            continue
        if (
            submission.previous_version_id != previous.id
            or submission.resource_version != previous.resource_version + 1
            or submission.amendment_type not in {"amendment", "addendum"}
            or not submission.amendment_reason
            or not _command_is_valid(
                commands_by_result.get(submission.id),
                previous=previous,
                result=submission,
            )
        ):
            return False
        previous = submission
    return True


def _submitted_series(form_submission_model, alias):
    queryset = (
        form_submission_model._base_manager.using(alias)  # noqa: SLF001
        .select_for_update(of=("self",))
        .filter(status="submitted")
        .select_related(
            "questionnaire",
            "patient",
            "encounter",
            "previous_version",
            "workflow_finalized_by",
        )
        .order_by("series_id", "resource_version", "id")
    )
    series_id = None
    submissions = []
    overflow = False
    for submission in queryset.iterator(chunk_size=500):
        if series_id is not None and submission.series_id != series_id:
            yield submissions, overflow
            submissions = []
            overflow = False
        series_id = submission.series_id
        if len(submissions) < MAX_BACKFILL_SERIES_LENGTH + 1:
            submissions.append(submission)
        else:
            overflow = True
    if submissions:
        yield submissions, overflow


def _commands_by_result(form_submission_command_model, alias, submissions):
    result_ids = [submission.id for submission in submissions[1:]]
    commands = list(
        form_submission_command_model._base_manager.using(alias)  # noqa: SLF001
        .select_for_update(of=("self",))
        .filter(command_type="amend", result_submission_id__in=result_ids)
        .select_related(
            "actor",
            "patient",
            "encounter",
            "questionnaire",
            "target_submission",
            "result_submission",
        )
        .order_by("result_submission_id", "id")
    )
    grouped = {}
    for command in commands:
        if command.result_submission_id in grouped:
            grouped[command.result_submission_id] = None
        else:
            grouped[command.result_submission_id] = command
    return grouped


def backfill_correspondence_source_corrections(apps, schema_editor):
    FormSubmission = apps.get_model("emr", "FormSubmission")
    FormSubmissionCommand = apps.get_model("emr", "FormSubmissionCommand")
    FormSubmissionSeriesHead = apps.get_model(
        "emr", "FormSubmissionSeriesHead"
    )
    CorrespondenceSourceCorrection = apps.get_model(
        "emr", "CorrespondenceSourceCorrection"
    )
    CorrespondenceCorrectionOutbox = apps.get_model(
        "emr", "CorrespondenceCorrectionOutbox"
    )
    alias = schema_editor.connection.alias
    malformed_count = 0
    for submissions, overflow in _submitted_series(FormSubmission, alias):
        commands = _commands_by_result(
            FormSubmissionCommand,
            alias,
            submissions,
        )
        if overflow or not _series_is_valid(submissions, commands):
            malformed_count += 1
    if malformed_count:
        message = (
            "correspondence correction backfill rejected malformed finalized "
            f"series; malformed_series_count={malformed_count}"
        )
        raise RuntimeError(message)

    for submissions, overflow in _submitted_series(FormSubmission, alias):
        if overflow:
            raise RuntimeError("validated correction series unexpectedly overflowed")
        commands = _commands_by_result(
            FormSubmissionCommand,
            alias,
            submissions,
        )
        current = submissions[-1]
        root = submissions[0]
        head = FormSubmissionSeriesHead.objects.using(alias).create(
            series_id=current.series_id,
            current_submission_id=current.id,
            current_version=current.resource_version,
            current_snapshot_hash=current.finalized_snapshot_hash,
            advanced_at=current.workflow_finalized_at,
            advanced_by_id=current.workflow_finalized_by_id,
            head_hash=_head_hash(current),
            created_by_id=root.workflow_finalized_by_id,
            updated_by_id=current.workflow_finalized_by_id,
        )
        previous = root
        for sequence, result in enumerate(submissions[1:], start=1):
            command = commands[result.id]
            historical_head_hash = _head_hash(result)
            correction = CorrespondenceSourceCorrection.objects.using(alias).create(
                source_head_id=head.id,
                sequence=sequence,
                source_head_hash=historical_head_hash,
                previous_submission_id=previous.id,
                new_submission_id=result.id,
                previous_version=previous.resource_version,
                new_version=result.resource_version,
                previous_snapshot_hash=previous.finalized_snapshot_hash,
                new_snapshot_hash=result.finalized_snapshot_hash,
                amendment_type=result.amendment_type,
                reason=result.amendment_reason,
                corrected_by_id=command.actor_id,
                corrected_at=result.workflow_finalized_at,
                correction_hash=_correction_hash(
                    head=head,
                    previous=previous,
                    result=result,
                    sequence=sequence,
                    source_head_hash=historical_head_hash,
                ),
                created_by_id=command.actor_id,
                updated_by_id=command.actor_id,
            )
            CorrespondenceCorrectionOutbox.objects.using(alias).create(
                source_correction_id=correction.id,
                status="pending",
                attempt_count=0,
                available_at=result.workflow_finalized_at,
                created_by_id=command.actor_id,
                updated_by_id=command.actor_id,
            )
            previous = result


class Migration(migrations.Migration):
    dependencies = [
        ("emr", "0085_correspondence_delivery_ledger"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FormSubmissionSeriesHead",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "external_id",
                    models.UUIDField(
                        db_index=True, default=uuid.uuid4, unique=True
                    ),
                ),
                (
                    "created_date",
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, null=True
                    ),
                ),
                (
                    "modified_date",
                    models.DateTimeField(auto_now=True, db_index=True, null=True),
                ),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("history", models.JSONField(default=dict)),
                ("meta", models.JSONField(default=dict)),
                ("series_id", models.UUIDField()),
                ("current_version", models.PositiveIntegerField()),
                ("current_snapshot_hash", models.CharField(max_length=64)),
                ("advanced_at", models.DateTimeField()),
                ("head_hash", models.CharField(max_length=64)),
                (
                    "advanced_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="advanced_form_submission_series",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "current_submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="current_series_heads",
                        to="emr.formsubmission",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CorrespondenceSourceCorrection",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "external_id",
                    models.UUIDField(
                        db_index=True, default=uuid.uuid4, unique=True
                    ),
                ),
                (
                    "created_date",
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, null=True
                    ),
                ),
                (
                    "modified_date",
                    models.DateTimeField(auto_now=True, db_index=True, null=True),
                ),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("history", models.JSONField(default=dict)),
                ("meta", models.JSONField(default=dict)),
                ("sequence", models.PositiveIntegerField()),
                ("source_head_hash", models.CharField(max_length=64)),
                ("previous_version", models.PositiveIntegerField()),
                ("new_version", models.PositiveIntegerField()),
                ("previous_snapshot_hash", models.CharField(max_length=64)),
                ("new_snapshot_hash", models.CharField(max_length=64)),
                ("amendment_type", models.CharField(max_length=32)),
                ("reason", models.TextField(max_length=4000)),
                ("corrected_at", models.DateTimeField()),
                ("correction_hash", models.CharField(max_length=64)),
                (
                    "corrected_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="correspondence_source_corrections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "new_submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="source_corrections_to",
                        to="emr.formsubmission",
                    ),
                ),
                (
                    "previous_submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="source_corrections_from",
                        to="emr.formsubmission",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "source_head",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="corrections",
                        to="emr.formsubmissionserieshead",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CorrespondenceCorrectionOutbox",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "external_id",
                    models.UUIDField(
                        db_index=True, default=uuid.uuid4, unique=True
                    ),
                ),
                (
                    "created_date",
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, null=True
                    ),
                ),
                (
                    "modified_date",
                    models.DateTimeField(auto_now=True, db_index=True, null=True),
                ),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("history", models.JSONField(default=dict)),
                ("meta", models.JSONField(default=dict)),
                ("status", models.CharField(default="pending", max_length=16)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("available_at", models.DateTimeField()),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "safe_code",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "source_correction",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="outbox_entries",
                        to="emr.correspondencesourcecorrection",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["status", "available_at", "id"],
                        name="corrcorr_outbox_ready_idx",
                    ),
                    models.Index(
                        fields=["status", "claimed_at", "id"],
                        name="corrcorr_outbox_reclaim_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("source_correction",),
                        name="corrcorrection_outbox_source_uniq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("claimed_at__isnull", True),
                                ("completed_at__isnull", True),
                                ("status", "pending"),
                            ),
                            models.Q(
                                ("attempt_count__gt", 0),
                                ("claimed_at__isnull", False),
                                ("completed_at__isnull", True),
                                ("status", "processing"),
                            ),
                            models.Q(
                                ("attempt_count__gt", 0),
                                ("claimed_at__isnull", False),
                                ("completed_at__isnull", False),
                                ("status", "completed"),
                            ),
                            models.Q(
                                ("attempt_count__gt", 0),
                                ("claimed_at__isnull", False),
                                ("completed_at__isnull", False),
                                ("status", "failed_terminal"),
                            ),
                            _connector="OR",
                        ),
                        name="corrcorrection_outbox_state_ck",
                    ),
                ],
            },
        ),
        migrations.RunPython(
            backfill_correspondence_source_corrections,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="formsubmissionserieshead",
            constraint=models.UniqueConstraint(
                fields=("series_id",), name="formsub_series_head_series_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="formsubmissionserieshead",
            constraint=models.UniqueConstraint(
                fields=("current_submission",),
                name="formsub_series_head_current_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="formsubmissionserieshead",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("current_version__gt", 0),
                    ("current_snapshot_hash__regex", "^[0-9a-f]{64}$"),
                    ("head_hash__regex", "^[0-9a-f]{64}$"),
                ),
                name="formsub_series_head_complete_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="formsubmissionserieshead",
            constraint=models.CheckConstraint(
                condition=models.Q(("updated_by", models.F("advanced_by"))),
                name="formsub_series_head_actor_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencesourcecorrection",
            constraint=models.UniqueConstraint(
                fields=("source_head", "sequence"),
                name="corrcorrection_head_sequence_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencesourcecorrection",
            constraint=models.UniqueConstraint(
                fields=("new_submission",),
                name="corrcorrection_new_source_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencesourcecorrection",
            constraint=models.UniqueConstraint(
                fields=("previous_submission",),
                name="corrcorrection_previous_source_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencesourcecorrection",
            constraint=models.UniqueConstraint(
                fields=("correction_hash",),
                name="corrcorrection_hash_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencesourcecorrection",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("sequence__gt", 0),
                    ("previous_version__gt", 0),
                    (
                        "new_version",
                        django.db.models.expressions.CombinedExpression(
                            models.F("previous_version"), "+", models.Value(1)
                        ),
                    ),
                    ("amendment_type__in", ["amendment", "addendum"]),
                    models.Q(
                        ("previous_submission", models.F("new_submission")),
                        _negated=True,
                    ),
                    ("previous_snapshot_hash__regex", "^[0-9a-f]{64}$"),
                    ("new_snapshot_hash__regex", "^[0-9a-f]{64}$"),
                    ("source_head_hash__regex", "^[0-9a-f]{64}$"),
                    models.Q(("reason", ""), _negated=True),
                    ("correction_hash__regex", "^[0-9a-f]{64}$"),
                ),
                name="corrcorrection_lineage_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencesourcecorrection",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("created_by", models.F("corrected_by")),
                    ("updated_by", models.F("corrected_by")),
                ),
                name="corrcorrection_actor_ck",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="formsubmission",
            name="formsub_finalized_snapshot_ck",
        ),
        migrations.AddConstraint(
            model_name="formsubmission",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("status", "submitted"),
                        ("workflow_finalized_at__isnull", False),
                        ("workflow_finalized_by__isnull", False),
                        (
                            "finalized_snapshot_hash__regex",
                            "^[0-9a-f]{64}$",
                        ),
                    ),
                    models.Q(
                        models.Q(("status", "submitted"), _negated=True),
                        ("workflow_finalized_at__isnull", True),
                        ("workflow_finalized_by__isnull", True),
                        ("finalized_snapshot_hash", ""),
                    ),
                    _connector="OR",
                ),
                name="formsub_finalized_snapshot_ck",
            ),
        ),
    ]
