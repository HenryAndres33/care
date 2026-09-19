#!/usr/bin/env bash
# Phase 2 step 2 rehearsal on the isolated test stack (project care-test).
#
#   bash scripts/phase2/rehearse-step2.sh restored <database.dump taken at emr 0106>
#   bash scripts/phase2/rehearse-step2.sh empty
#
# restored: restore, assert the exact plan the server will receive
#           (emr.0107, emr.0108, care_suriname.0001), apply 0107, snapshot
#           schema+state, apply the rest, assert schema byte-identical and
#           state assertions, migrate back to 0107, assert, forward again.
# empty:    migrate from zero, assert final state and no pending migrations.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
compose=(docker compose -f docker-compose.test.yaml)
mode="${1:-}"; dump="${2:-}"; out=/tmp/claude-1000/phase2-step2; mkdir -p "$out"
db() { "${compose[@]}" exec -T db "$@"; }
be() { "${compose[@]}" exec -T -w /app backend "$@"; }
schema() { db pg_dump -U postgres -s --no-owner --no-privileges care_test | grep -vE '^--|^$|^\\(un)?restrict ' > "$1"; }
recreate_db() {
  db psql -U postgres -d postgres -qc "select pg_terminate_backend(pid) from pg_stat_activity where datname='care_test' and pid<>pg_backend_pid();" >/dev/null
  db dropdb -U postgres --if-exists care_test; db createdb -U postgres care_test
}
case "$mode" in
  restored)
    [ -f "$dump" ] || { echo "dump not found: $dump" >&2; exit 1; }
    echo "== restore"; recreate_db; db pg_restore -U postgres -d care_test --no-owner --no-privileges < "$dump" 2>/dev/null || true
    echo "== exact plan the server will receive from this state"
    be python manage.py showmigrations --plan 2>/dev/null | grep '\[ \]' | tee "$out/plan.txt"
    diff <(sed -E 's/^\[ \]\s+//' "$out/plan.txt") <(printf '%s\n' emr.0107_letter_artifact_link_on_revision emr.0108_move_models_to_care_suriname care_suriname.0001_move_models_to_care_suriname) && echo "plan matches exactly"
    echo "== apply 0107 (step 1) and snapshot"
    be python manage.py migrate emr 0107 --noinput | tail -1
    schema "$out/schema-after-0107.sql"; be python scripts/phase2/verify_state.py snapshot
    echo "== apply the rest (step 2, state-only)"
    be python manage.py migrate --noinput | tail -3
    schema "$out/schema-after-step2.sql"
    if diff -q "$out/schema-after-0107.sql" "$out/schema-after-step2.sql" >/dev/null; then echo "ok   database schema byte-identical before and after step 2 ($(wc -l < "$out/schema-after-step2.sql") lines)"; else echo "FAIL schema differs:"; diff "$out/schema-after-0107.sql" "$out/schema-after-step2.sql" | head -20; exit 1; fi
    be python scripts/phase2/verify_state.py moved
    echo "== migrate back to emr 0107 (unapplies care_suriname.0001 and emr.0108)"
    be python manage.py migrate care_suriname zero --noinput | tail -1
    be python manage.py migrate emr 0107 --noinput | tail -1
    schema "$out/schema-after-back.sql"; diff -q "$out/schema-after-0107.sql" "$out/schema-after-back.sql" >/dev/null && echo "ok   schema identical after rollback"
    be python scripts/phase2/verify_state.py back
    echo "== fail-closed check: a stray care_suriname content type must abort the relabel"
    db psql -U postgres -d care_test -qc "insert into django_content_type(app_label, model) values ('care_suriname','consultclosure');"
    if be python manage.py migrate --noinput >"$out/failclosed.log" 2>&1; then echo "FAIL migration succeeded despite a duplicate content type"; exit 1; fi
    grep -q "ContentTypeRelabelError" "$out/failclosed.log" && echo "ok   relabel aborted with ContentTypeRelabelError (transaction rolled back)"
    be python manage.py showmigrations care_suriname 2>/dev/null | grep -q "\[ \] 0001" && echo "ok   care_suriname.0001 not recorded as applied"
    db psql -U postgres -d care_test -qc "delete from django_content_type where app_label='care_suriname' and model='consultclosure';"
    echo "== forward again (duplicate removed)"
    be python manage.py migrate --noinput | tail -1
    be python scripts/phase2/verify_state.py moved
    ;;
  empty)
    echo "== empty database from zero"; recreate_db
    be python manage.py migrate --noinput | tail -2
    be python scripts/phase2/verify_state.py empty
    be python scripts/phase2/verify_state.py snapshot >/dev/null
    be python scripts/phase2/verify_state.py moved
    ;;
  *) echo "usage: $0 restored <dump> | empty" >&2; exit 2;;
esac
echo "REHEARSAL OK (step2 $mode)"
