# ruff: noqa: T201, INP001  (operator script: prints its report; not a package)
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
else:
    sys.exit(f"unknown phase {phase}")
print(f"VERIFY OK ({phase})")
