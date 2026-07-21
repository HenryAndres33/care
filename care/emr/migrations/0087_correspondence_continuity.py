import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

LEGACY_SAFE_CODE = "legacy_failure_reason_unrecorded"
LEGACY_SAFE_CODE_META_KEY = "correspondence_0087_safe_code_backfill"
OUTBOX_TOKEN_NAMESPACE = uuid.UUID("d5b74ada-c3ae-4c23-850e-c969d21fab65")


def backfill_correction_outbox_leases(apps, schema_editor):
    outbox_model = apps.get_model("emr", "CorrespondenceCorrectionOutbox")
    alias = schema_editor.connection.alias
    rows = outbox_model._base_manager.using(alias).exclude(status="pending")  # noqa: SLF001
    for outbox in rows.iterator(chunk_size=500):
        if outbox.status not in {"processing", "completed", "failed_terminal"}:
            raise RuntimeError("unknown legacy correspondence correction outbox state")
        if outbox.claimed_at is None:
            raise RuntimeError("legacy correspondence correction claim is incomplete")
        if outbox.status != "processing" and outbox.completed_at is None:
            raise RuntimeError(
                "legacy correspondence correction terminal is incomplete"
            )
        outbox.claim_token = uuid.uuid5(
            OUTBOX_TOKEN_NAMESPACE,
            str(outbox.external_id),
        )
        # A legacy in-flight worker cannot possess the new fencing token. Marking
        # its lease expired makes the first 0087 scanner reclaim it safely.
        outbox.lease_expires_at = (
            outbox.claimed_at if outbox.status == "processing" else outbox.completed_at
        )
        update_fields = ["claim_token", "lease_expires_at"]
        if outbox.status == "failed_terminal" and not outbox.safe_code:
            outbox.safe_code = LEGACY_SAFE_CODE
            outbox.meta = {
                **outbox.meta,
                LEGACY_SAFE_CODE_META_KEY: True,
            }
            update_fields.extend(["safe_code", "meta"])
        outbox.save(using=alias, update_fields=update_fields)


def reverse_correction_outbox_leases(apps, schema_editor):
    outbox_model = apps.get_model("emr", "CorrespondenceCorrectionOutbox")
    alias = schema_editor.connection.alias
    rows = outbox_model._base_manager.using(alias).exclude(status="pending")  # noqa: SLF001
    for outbox in rows.iterator(chunk_size=500):
        update_fields = ["claim_token", "lease_expires_at"]
        outbox.claim_token = None
        outbox.lease_expires_at = None
        if outbox.meta.get(LEGACY_SAFE_CODE_META_KEY) is True:
            outbox.safe_code = ""
            outbox.meta = {
                key: value
                for key, value in outbox.meta.items()
                if key != LEGACY_SAFE_CODE_META_KEY
            }
            update_fields.extend(["safe_code", "meta"])
        outbox.save(using=alias, update_fields=update_fields)


class Migration(migrations.Migration):
    dependencies = [
        ("emr", "0086_correspondence_source_correction"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CorrespondenceCorrectionCase",
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
                    models.UUIDField(db_index=True, default=uuid.uuid4, unique=True),
                ),
                (
                    "created_date",
                    models.DateTimeField(auto_now_add=True, db_index=True, null=True),
                ),
                (
                    "modified_date",
                    models.DateTimeField(auto_now=True, db_index=True, null=True),
                ),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("history", models.JSONField(default=dict)),
                ("meta", models.JSONField(default=dict)),
                ("source_head_hash", models.CharField(max_length=64)),
                ("frozen_version", models.PositiveIntegerField()),
                ("frozen_snapshot_hash", models.CharField(max_length=64)),
                ("current_version", models.PositiveIntegerField()),
                ("current_snapshot_hash", models.CharField(max_length=64)),
                ("latest_source_correction_hash", models.CharField(max_length=64)),
                ("change_set_hash", models.CharField(max_length=64)),
                ("delivery_state", models.CharField(max_length=32)),
                ("delivery_certainty", models.CharField(max_length=32)),
                ("notification_status", models.CharField(max_length=32)),
                (
                    "paper_reconciliation_status",
                    models.CharField(default="required", max_length=32),
                ),
                (
                    "replacement_status",
                    models.CharField(default="not_started", max_length=32),
                ),
                ("status", models.CharField(default="open", max_length=16)),
                ("resource_version", models.PositiveIntegerField(default=1)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("case_hash", models.CharField(max_length=64)),
            ],
        ),
        migrations.CreateModel(
            name="CorrespondenceCorrectionEvent",
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
                    models.UUIDField(db_index=True, default=uuid.uuid4, unique=True),
                ),
                (
                    "created_date",
                    models.DateTimeField(auto_now_add=True, db_index=True, null=True),
                ),
                (
                    "modified_date",
                    models.DateTimeField(auto_now=True, db_index=True, null=True),
                ),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("history", models.JSONField(default=dict)),
                ("meta", models.JSONField(default=dict)),
                ("sequence", models.PositiveIntegerField()),
                ("event_type", models.CharField(max_length=32)),
                ("occurred_at", models.DateTimeField()),
                ("actor_type", models.CharField(default="system", max_length=16)),
                ("safe_code", models.CharField(max_length=64)),
                (
                    "previous_event_hash",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "previous_case_hash",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("resulting_case_hash", models.CharField(max_length=64)),
                ("event_hash", models.CharField(max_length=64)),
            ],
        ),
        migrations.RemoveConstraint(
            model_name="correspondencecorrectionoutbox",
            name="corrcorrection_outbox_state_ck",
        ),
        migrations.AddField(
            model_name="correspondencecorrectionoutbox",
            name="claim_token",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="correspondencecorrectionoutbox",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            backfill_correction_outbox_leases,
            reverse_correction_outbox_leases,
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectionoutbox",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("claim_token__isnull", True),
                        ("claimed_at__isnull", True),
                        ("completed_at__isnull", True),
                        ("lease_expires_at__isnull", True),
                        ("status", "pending"),
                    ),
                    models.Q(
                        ("attempt_count__gt", 0),
                        ("claim_token__isnull", False),
                        ("claimed_at__isnull", False),
                        ("completed_at__isnull", True),
                        ("lease_expires_at__isnull", False),
                        ("status", "processing"),
                    ),
                    models.Q(
                        ("attempt_count__gt", 0),
                        ("claim_token__isnull", False),
                        ("claimed_at__isnull", False),
                        ("completed_at__isnull", False),
                        ("lease_expires_at__isnull", False),
                        ("status", "completed"),
                    ),
                    models.Q(
                        ("attempt_count__gt", 0),
                        ("claim_token__isnull", False),
                        ("claimed_at__isnull", False),
                        ("completed_at__isnull", False),
                        ("lease_expires_at__isnull", False),
                        ("safe_code__gt", ""),
                        ("status", "failed_terminal"),
                    ),
                    _connector="OR",
                ),
                name="corrcorrection_outbox_state_ck",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(app_label)s_%(class)s_created_by",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="current_submission",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="current_correspondence_correction_cases",
                to="emr.formsubmission",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="frozen_submission",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="frozen_correspondence_correction_cases",
                to="emr.formsubmission",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="latest_source_correction",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="correspondence_correction_cases",
                to="emr.correspondencesourcecorrection",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="original_compilation",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="correction_case",
                to="emr.correspondencecompilation",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="original_delivery",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="correction_case",
                to="emr.correspondencedelivery",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="original_review",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="correction_case",
                to="emr.correspondencereview",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="replacement_artifact",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="replacement_correction_cases",
                to="emr.reportupload",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="replacement_compilation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="replacement_correction_cases",
                to="emr.correspondencecompilation",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="replacement_delivery",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="replacement_correction_cases",
                to="emr.correspondencedelivery",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="replacement_review",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="replacement_correction_cases",
                to="emr.correspondencereview",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="replacement_revision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="replacement_correction_cases",
                to="emr.correspondenceletterrevision",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="resolved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="resolved_correspondence_correction_cases",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="source_head",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="correspondence_correction_cases",
                to="emr.formsubmissionserieshead",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectioncase",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(app_label)s_%(class)s_updated_by",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectionevent",
            name="actor",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="correspondence_correction_events",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectionevent",
            name="case",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="events",
                to="emr.correspondencecorrectioncase",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectionevent",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(app_label)s_%(class)s_created_by",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectionevent",
            name="delivery_event",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="correction_case_events",
                to="emr.correspondencedeliveryevent",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectionevent",
            name="previous_event",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="next_events",
                to="emr.correspondencecorrectionevent",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectionevent",
            name="source_correction",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="case_events",
                to="emr.correspondencesourcecorrection",
            ),
        ),
        migrations.AddField(
            model_name="correspondencecorrectionevent",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(app_label)s_%(class)s_updated_by",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectioncase",
            constraint=models.UniqueConstraint(
                fields=("original_review",), name="corrcase_review_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectioncase",
            constraint=models.UniqueConstraint(
                fields=("original_delivery",), name="corrcase_delivery_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectioncase",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("frozen_version__gt", 0),
                    ("current_version__gt", models.F("frozen_version")),
                    ("resource_version__gt", 0),
                    ("source_head_hash__regex", "^[0-9a-f]{64}$"),
                    ("frozen_snapshot_hash__regex", "^[0-9a-f]{64}$"),
                    ("current_snapshot_hash__regex", "^[0-9a-f]{64}$"),
                    ("latest_source_correction_hash__regex", "^[0-9a-f]{64}$"),
                    ("change_set_hash__regex", "^[0-9a-f]{64}$"),
                    ("case_hash__regex", "^[0-9a-f]{64}$"),
                ),
                name="corrcase_lineage_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectioncase",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "delivery_certainty__in",
                        [
                            "not_attempted",
                            "attempting",
                            "acknowledged",
                            "not_delivered",
                            "unknown",
                        ],
                    ),
                    (
                        "delivery_state__in",
                        [
                            "dispatch_pending",
                            "dispatching",
                            "acknowledged",
                            "failed_retryable",
                            "failed_terminal",
                            "outcome_unknown",
                        ],
                    ),
                    (
                        "notification_status__in",
                        [
                            "not_required",
                            "required",
                            "pending",
                            "acknowledged",
                            "failed",
                            "unknown",
                        ],
                    ),
                    (
                        "paper_reconciliation_status__in",
                        ["not_required", "required", "acknowledged"],
                    ),
                    (
                        "replacement_status__in",
                        [
                            "not_started",
                            "compiling",
                            "draft",
                            "finalized",
                            "delivery_pending",
                            "acknowledged",
                            "failed",
                        ],
                    ),
                ),
                name="corrcase_states_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectioncase",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("resolved_at__isnull", True),
                        ("resolved_by__isnull", True),
                        ("status", "open"),
                    ),
                    models.Q(
                        ("notification_status__in", ["acknowledged", "not_required"]),
                        ("replacement_status", "acknowledged"),
                        ("resolved_at__isnull", False),
                        ("resolved_by__isnull", False),
                        ("status", "resolved"),
                        models.Q(
                            ("paper_reconciliation_status", "required"), _negated=True
                        ),
                    ),
                    _connector="OR",
                ),
                name="corrcase_resolution_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectioncase",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("replacement_artifact__isnull", True),
                        ("replacement_compilation__isnull", True),
                        ("replacement_delivery__isnull", True),
                        ("replacement_review__isnull", True),
                        ("replacement_revision__isnull", True),
                        ("replacement_status", "not_started"),
                    ),
                    models.Q(("replacement_status", "not_started"), _negated=True),
                    _connector="OR",
                ),
                name="corrcase_replacement_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectioncase",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("replacement_review__isnull", True),
                        ("replacement_compilation__isnull", False),
                        _connector="OR",
                    ),
                    models.Q(
                        ("replacement_revision__isnull", True),
                        ("replacement_review__isnull", False),
                        _connector="OR",
                    ),
                    models.Q(
                        ("replacement_artifact__isnull", True),
                        ("replacement_revision__isnull", False),
                        _connector="OR",
                    ),
                    models.Q(
                        ("replacement_delivery__isnull", True),
                        ("replacement_artifact__isnull", False),
                        _connector="OR",
                    ),
                    models.Q(
                        models.Q(("replacement_status", "acknowledged"), _negated=True),
                        ("replacement_delivery__isnull", False),
                        _connector="OR",
                    ),
                ),
                name="corrcase_replace_prefix_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectionevent",
            constraint=models.UniqueConstraint(
                fields=("case", "sequence"), name="correvent_case_sequence_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectionevent",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source_correction__isnull", False)),
                fields=("case", "source_correction"),
                name="correvent_case_source_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectionevent",
            constraint=models.UniqueConstraint(
                condition=models.Q(("delivery_event__isnull", False)),
                fields=("case", "delivery_event"),
                name="correvent_case_delivery_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectionevent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("sequence__gt", 0),
                    (
                        "event_type__in",
                        ["opened", "source_advanced", "delivery_classified"],
                    ),
                    ("actor_type__in", ["user", "system"]),
                    models.Q(("safe_code", ""), _negated=True),
                    ("resulting_case_hash__regex", "^[0-9a-f]{64}$"),
                    ("event_hash__regex", "^[0-9a-f]{64}$"),
                ),
                name="correvent_state_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectionevent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("actor__isnull", False), ("actor_type", "user")),
                    models.Q(("actor__isnull", True), ("actor_type", "system")),
                    _connector="OR",
                ),
                name="correvent_actor_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectionevent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("previous_case_hash", ""),
                        ("previous_event__isnull", True),
                        ("previous_event_hash", ""),
                        ("sequence", 1),
                    ),
                    models.Q(
                        ("previous_case_hash__regex", "^[0-9a-f]{64}$"),
                        ("previous_event__isnull", False),
                        ("previous_event_hash__regex", "^[0-9a-f]{64}$"),
                        ("sequence__gt", 1),
                    ),
                    _connector="OR",
                ),
                name="correvent_chain_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="correspondencecorrectionevent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("delivery_event__isnull", True),
                        ("event_type__in", ["opened", "source_advanced"]),
                        ("source_correction__isnull", False),
                    ),
                    models.Q(
                        ("delivery_event__isnull", False),
                        ("event_type", "delivery_classified"),
                        ("source_correction__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="correvent_source_ck",
            ),
        ),
    ]
