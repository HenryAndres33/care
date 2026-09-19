# ruff: noqa: T201, INP001, S108, SIM115, PTH123, SLF001, FBT003
# Operator script: prints its report, uses a scratch file in the container, reads _meta.
"""Assertions for the Phase 2 rehearsals. Run inside the backend container.

    python scripts/phase2/verify_state.py before|after|empty

`before`: schema as of emr 0106 (link on ReportUpload). `after`: emr 0107
(link on the revision). `empty`: fresh database, structure only. Exits 1 on
the first failed assertion.
"""

import os
import sys
from pathlib import Path

import django

# Run as a plain script from any cwd: make the repository importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from django.db import connection  # noqa: E402


def q(sql):
    with connection.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def column_exists(table, column):
    with connection.cursor() as cur:
        cur.execute(
            "select 1 from information_schema.columns "
            "where table_name=%s and column_name=%s",
            [table, column],
        )
        return cur.fetchone() is not None


def check(condition, message):
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        sys.exit(1)


phase = sys.argv[1]
letter_rows = q(
    "select count(*) from emr_reportupload r "
    "where r.template_id is null and r.form_submission_id is null "
    "and r.generated_at is not null"
)[0][0]
finalized = q(
    "select count(*) from emr_correspondenceletterrevision where status='finalized'"
)[0][0]
print(f"letter artifacts={letter_rows} finalized revisions={finalized}")

if phase == "before":
    check(
        column_exists("emr_reportupload", "correspondence_revision_id"),
        "link column on reportupload",
    )
    check(
        not column_exists("emr_correspondenceletterrevision", "final_artifact_id"),
        "no link column on revision",
    )
    linked = q(
        "select count(*) from emr_reportupload where correspondence_revision_id is not null"
    )[0][0]
    check(
        linked == letter_rows, f"every letter artifact linked ({linked}/{letter_rows})"
    )
elif phase == "after":
    check(
        not column_exists("emr_reportupload", "correspondence_revision_id"),
        "link column gone from reportupload",
    )
    check(
        column_exists("emr_correspondenceletterrevision", "final_artifact_id"),
        "link column on revision",
    )
    linked = q(
        "select count(*) from emr_correspondenceletterrevision where final_artifact_id is not null"
    )[0][0]
    check(
        linked == letter_rows,
        f"every letter artifact linked from its revision ({linked}/{letter_rows})",
    )
    dangling = q(
        "select count(*) from emr_correspondenceletterrevision v "
        "left join emr_reportupload r on r.id=v.final_artifact_id "
        "where v.final_artifact_id is not null and r.id is null"
    )[0][0]
    check(dangling == 0, "no dangling artifact links")
    dup = q(
        "select count(*) from (select final_artifact_id from emr_correspondenceletterrevision "
        "where final_artifact_id is not null group by 1 having count(*)>1) d"
    )[0][0]
    check(dup == 0, "one revision per artifact")
    mismatch = q(
        "select count(*) from emr_correspondenceletterrevision v "
        "join emr_reportupload r on r.id=v.final_artifact_id "
        "join emr_correspondenceletter l on l.id=v.letter_id "
        "where r.patient_id<>l.patient_id or r.encounter_id<>l.encounter_id "
        "or r.source_version<>v.resource_version or r.source_snapshot_hash<>v.revision_hash"
    )[0][0]
    check(mismatch == 0, "provenance on the artifact matches its revision")
    ck = q(
        "select count(*) from pg_constraint where conname='formartifact_provenance_ck'"
    )[0][0]
    check(ck == 1, "provenance check constraint present")
    old = q(
        "select count(*) from pg_constraint where conname='corrartifact_revision_uniq'"
    )[0][0]
    check(old == 0, "old unique constraint removed")
elif phase == "empty":
    check(
        column_exists("emr_correspondenceletterrevision", "final_artifact_id"),
        "link column on revision (fresh)",
    )
    check(
        not column_exists("emr_reportupload", "correspondence_revision_id"),
        "no link column on reportupload (fresh)",
    )
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    pending = len(executor.migration_plan(executor.loader.graph.leaf_nodes()))
    check(pending == 0, f"no unapplied migrations ({pending})")
elif phase in ("snapshot", "moved", "back"):
    pass  # handled below
else:
    sys.exit(f"unknown phase {phase}")
if phase not in ("snapshot", "moved", "back"):
    print(f"VERIFY OK ({phase})")


# ---------------------------------------------------------------------------
# Step 2 (model move): snapshot / moved / restored-back phases.
# ---------------------------------------------------------------------------
import json  # noqa: E402

SNAPSHOT = "/tmp/phase2_step2_snapshot.json"
MOVED_MODELS = [
    "AdmissionDocumentation", "ClinicalTermTranslation", "ClinicalTextResource",
    "ConsultClosure", "ConsultClosureCommand", "ConsultClosureRecoveryTask",
    "CorrespondenceCompilation", "CorrespondenceCompileCommand",
    "CorrespondenceCorrectionCase", "CorrespondenceCorrectionCommand",
    "CorrespondenceCorrectionEvent", "CorrespondenceCorrectionOutbox",
    "CorrespondenceDelivery", "CorrespondenceDeliveryAttempt",
    "CorrespondenceDeliveryEvent", "CorrespondenceLetter",
    "CorrespondenceLetterCommand", "CorrespondenceLetterRevision",
    "CorrespondencePaperReconciliationAttestation", "CorrespondenceRecipient",
    "CorrespondenceRecipientCommand", "CorrespondenceReplacementAttempt",
    "CorrespondenceReview", "CorrespondenceReviewCommand",
    "CorrespondenceSourceCorrection", "CorrespondenceSyntheticProviderInvocation",
    "CorrespondenceSyntheticProviderReceipt", "EmergencyAdmission",
    "EncounterDischargeCommand", "FormSubmissionArtifactCommand",
    "FormSubmissionCommand", "FormSubmissionLabLink", "FormSubmissionSeriesHead",
    "OperationPlan",
]  # fmt: skip
TABLES = [f"emr_{m.lower()}" for m in MOVED_MODELS]


def _state():
    counts = {t: q(f"select count(*) from {t}")[0][0] for t in TABLES}  # noqa: S608
    all_emr_tables = sorted(
        r[0]
        for r in q(
            "select tablename from pg_tables where schemaname='public' and tablename like 'emr\\_%'"
        )
    )
    with connection.cursor() as cur:
        cur.execute(
            "select id, app_label, model from django_content_type where model = any(%s) "
            "and app_label in ('emr','care_suriname') order by model, app_label",
            [[m.lower() for m in MOVED_MODELS]],
        )
        cts = [list(r) for r in cur.fetchall()]
        ct_ids = [c[0] for c in cts]
        cur.execute(
            "select id, content_type_id, codename from auth_permission where content_type_id = any(%s) order by id",
            [ct_ids],
        )
        perms = [list(r) for r in cur.fetchall()]
    return {
        "counts": counts,
        "emr_tables": all_emr_tables,
        "content_types": cts,
        "permissions": perms,
    }


def _post_migrate_is_idempotent():
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions
    from django.contrib.contenttypes.management import create_contenttypes

    before = (
        q("select count(*) from django_content_type")[0][0],
        q("select count(*) from auth_permission")[0][0],
    )
    for cfg in (
        global_apps.get_app_config("care_suriname"),
        global_apps.get_app_config("emr"),
    ):
        create_contenttypes(cfg, verbosity=0)
        create_permissions(cfg, verbosity=0)
    after = (
        q("select count(*) from django_content_type")[0][0],
        q("select count(*) from auth_permission")[0][0],
    )
    return before == after, before, after


if phase == "snapshot":
    st = _state()
    json.dump(st, open(SNAPSHOT, "w"))
    print(
        f"snapshot: {len(st['counts'])} tables, {sum(st['counts'].values())} rows, "
        f"{len(st['content_types'])} content types, {len(st['permissions'])} permissions"
    )
    print("VERIFY OK (snapshot)")
elif phase in ("moved", "back"):
    from django.apps import apps as global_apps

    snap = json.load(open(SNAPSHOT))
    st = _state()
    check(st["counts"] == snap["counts"], "row counts of all 34 tables unchanged")
    check(
        st["emr_tables"] == snap["emr_tables"],
        "set of emr_* tables unchanged (nothing created or dropped)",
    )
    expected_label = "care_suriname" if phase == "moved" else "emr"
    other_label = "emr" if phase == "moved" else "care_suriname"
    by_model = {}
    for ct_id, label, model in st["content_types"]:
        by_model.setdefault(model, []).append((ct_id, label))
    for m in MOVED_MODELS:
        rows = by_model.get(m.lower(), [])
        check(len(rows) == 1, f"exactly one content type for {m} ({rows})")
        check(rows[0][1] == expected_label, f"{m} content type under {expected_label}")
    check(
        all(label != other_label for _, label, _ in st["content_types"]),
        f"no content type left under {other_label}",
    )
    snap_ids = sorted(c[0] for c in snap["content_types"])
    check(
        sorted(c[0] for c in st["content_types"]) == snap_ids,
        "content-type IDs preserved",
    )
    check(
        st["permissions"] == snap["permissions"],
        f"permission rows unchanged ({len(st['permissions'])})",
    )
    if phase == "moved":
        ok, before, after = _post_migrate_is_idempotent()
        check(
            ok,
            f"post_migrate creates nothing new (content types/permissions {before} -> {after})",
        )
    else:
        # After a migration-only rollback the *code* still registers the models
        # under care_suriname, so post_migrate would legitimately create rows
        # there; a real rollback also rolls the code back. Not asserted here.
        print("skip post_migrate idempotency after rollback (code not rolled back)")
    if phase == "moved":
        for m in MOVED_MODELS:
            model = global_apps.get_model("care_suriname", m)
            check(
                model._meta.db_table == f"emr_{m.lower()}",
                f"{m} resolves under care_suriname on table emr_{m.lower()}",
            )
            try:
                global_apps.get_model("emr", m)
                check(False, f"{m} must not resolve under emr")
            except LookupError:
                pass
        from care.audit_log.helpers import exclude_model

        check(
            exclude_model("care_suriname.FormSubmissionCommand"),
            "audit exclusion resolves the new label",
        )
        check(
            exclude_model("care_suriname.CorrespondenceLetter"),
            "audit exclusion glob resolves the new label",
        )

        # Stale = a content-type row with no registered model behind it
        # (what `remove_stale_contenttypes` would offer to delete). Computed
        # directly; that command has no dry-run mode.
        ContentType = global_apps.get_model("contenttypes", "ContentType")
        stale = []
        for ct in ContentType.objects.filter(app_label__in=["emr", "care_suriname"]):
            try:
                global_apps.get_model(ct.app_label, ct.model)
            except LookupError:
                stale.append(f"{ct.app_label}.{ct.model}")
        stale = [row for row in stale if any(m.lower() in row for m in MOVED_MODELS)]
        check(not stale, f"no stale content types for the moved models ({stale[:3]})")
    print(f"VERIFY OK ({phase})")
