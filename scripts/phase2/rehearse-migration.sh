#!/usr/bin/env bash
# Rehearse a migration step on the isolated test stack (docker-compose.test.yaml,
# project care-test, database care_test on host port 5434). Never touches the
# development or server database.
#
#   bash scripts/phase2/rehearse-migration.sh restored <path/to/database.dump>
#       restore the dump into care_test, migrate forward, verify, migrate back, verify
#   bash scripts/phase2/rehearse-migration.sh empty
#       drop care_test, migrate from zero, verify
#
# Verification = scripts/phase2/verify_state.py inside the test backend.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
compose=(docker compose -f docker-compose.test.yaml)
mode="${1:-}"; dump="${2:-}"

db() { "${compose[@]}" exec -T db "$@"; }
be() { "${compose[@]}" exec -T -w /app backend "$@"; }

recreate_db() {
  db psql -U postgres -d postgres -qc "select pg_terminate_backend(pid) from pg_stat_activity where datname='care_test' and pid<>pg_backend_pid();" >/dev/null
  db dropdb -U postgres --if-exists care_test
  db createdb -U postgres care_test
}

case "$mode" in
  restored)
    [ -f "$dump" ] || { echo "dump not found: $dump" >&2; exit 1; }
    echo "== restore $dump into care_test"
    recreate_db
    db pg_restore -U postgres -d care_test --no-owner --no-privileges < "$dump"
    echo "== state before"; be python manage.py showmigrations emr 2>/dev/null | tail -3
    be python scripts/phase2/verify_state.py before
    echo "== migrate forward"; be python manage.py migrate --noinput | tail -5
    be python scripts/phase2/verify_state.py after
    echo "== migrate back one step and verify reversibility"
    target="${REHEARSE_BACK_TO:-emr 0106_form_submission_lab}"
    # shellcheck disable=SC2086
    be python manage.py migrate --noinput $target | tail -3
    be python scripts/phase2/verify_state.py before
    echo "== forward again"; be python manage.py migrate --noinput | tail -2
    be python scripts/phase2/verify_state.py after
    ;;
  empty)
    echo "== empty database, migrate from zero"
    recreate_db
    be python manage.py migrate --noinput | tail -3
    be python scripts/phase2/verify_state.py empty
    ;;
  *) echo "usage: $0 restored <dump> | empty" >&2; exit 2;;
esac
echo "REHEARSAL OK ($mode)"
