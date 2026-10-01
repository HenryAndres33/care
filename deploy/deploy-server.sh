#!/usr/bin/env bash
# Update the server from the laptop in one pass: the whole of
# care_fe/CODING_RULES.md §7 as a script, so no step can be skipped.
#
#   bash care/deploy/deploy-server.sh --check            # plan + health, changes nothing
#   bash care/deploy/deploy-server.sh                    # backup, backend, frontend, checks, BUS line
#   bash care/deploy/deploy-server.sh --allow-migrations # only after the owner agreed to them
#
# Asking the owner first stays a human step: run this only after the owner
# said "update the server" in the current conversation.
#
# Every step stops the run on failure ("STOP: ..."). Nothing is retried
# silently: fix the cause and run the script again; a finished step is
# skipped when it is already done (code already live). A new backup is taken
# on every run that changes code.

set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../.." && pwd)
server="${CARE_SERVER:-henry@34.74.242.186}"
domain="${CARE_DOMAIN:-care.openemrsu.com}"
remote="care-suriname"
backups="${CARE_LOCAL_SERVER_BACKUPS:-$HOME/care-suriname-backups/server}"
bus="$root/care_fe/.agents/BUS.md"
check_only=false allow_migrations=false

for arg in "$@"; do
  case "$arg" in
    --check) check_only=true ;;
    --allow-migrations) allow_migrations=true ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s\n' "$*"; }
fail() { printf '\nSTOP: %s\n' "$*" >&2; exit 1; }
on_server() { ssh -o ConnectTimeout=15 "$server" "$@"; }
live_frontend() {
  curl -fsS "https://$domain/build-meta.json" | grep -o '"commit":"[^"]*"' | cut -d'"' -f4
}
same_commit() { [ -n "$1" ] && [ -n "$2" ] && [[ "$1" == "$2"* || "$2" == "$1"* ]]; }

# 1. Preflight -----------------------------------------------------------------
step "1/6 preflight"
care_head=$(git -C "$root/care" rev-parse HEAD)
fe_head=$(git -C "$root/care_fe" rev-parse HEAD)
for repo in care care_fe; do
  [ -n "$(git -C "$root/$repo" branch -r --contains HEAD)" ] ||
    fail "$repo HEAD is not pushed. Commit and push first (the server pulls from GitHub)."
done
server_care=$(on_server "git -C $remote/care rev-parse HEAD") || fail "cannot reach $server"
server_fe=$(on_server "git -C $remote/care_fe rev-parse HEAD")
served_fe=$(live_frontend) || fail "https://$domain/build-meta.json does not answer"
previous="backend ${server_care:0:9}, frontend $served_fe"
echo "backend : server ${server_care:0:9} → laptop ${care_head:0:9}"
echo "frontend: served ${served_fe} → laptop ${fe_head:0:9} (server checkout ${server_fe:0:9})"

git -C "$root/care" cat-file -e "$server_care^{commit}" 2>/dev/null ||
  fail "server backend ${server_care:0:9} is unknown here; git fetch in care first"
migrations=$(git -C "$root/care" diff --name-only "$server_care" "$care_head" -- '*/migrations/*')
if [ -n "$migrations" ]; then
  echo "new migrations:"; echo "$migrations" | sed 's/^/  /'
  $allow_migrations || $check_only ||
    fail "migrations present. Tell the owner (CODING_RULES §5) and rerun with --allow-migrations once agreed."
else
  echo "no new migrations"
fi

need_backend=true need_frontend=true
same_commit "$server_care" "$care_head" && same_commit "$server_fe" "$fe_head" && need_backend=false
same_commit "$served_fe" "$fe_head" && need_frontend=false
echo "plan: server pull/rebuild=$need_backend, frontend ship=$need_frontend"

# 5. Checks (also used by --check) --------------------------------------------
health() {
  local ps code_site code_ping
  ps=$(on_server "cd $remote/care/deploy && docker compose ps --format '{{.Service}} {{.Status}}'") || return 1
  code_site=$(curl -s -o /dev/null -w '%{http_code}' "https://$domain/")
  code_ping=$(curl -s -o /dev/null -w '%{http_code}' "https://$domain/ping/")
  echo "$ps" | sed 's/^/  /'
  echo "  site $code_site, /ping/ $code_ping"
  echo "$ps" | grep -qE 'unhealthy|starting|Exit|Restarting' && return 1
  [ "$code_site" = 200 ] && [ "$code_ping" = 200 ]
}

if $check_only; then
  step "health (check only, nothing changed)"
  health && echo "healthy" || echo "NOT healthy"
  exit 0
fi
if ! $need_backend && ! $need_frontend; then
  step "server already runs these commits"
  health || fail "server is up to date but not healthy; read docker compose logs"
  exit 0
fi

# 2. Backup, copied home and verified -----------------------------------------
step "2/6 backup on the server, copy to the laptop, verify"
backup_out=$(on_server "bash $remote/care/deploy/server-backup.sh") || fail "server backup failed:
$backup_out"
echo "$backup_out" | tail -3
location=$(echo "$backup_out" | awk '/location:/ {print $2}')
[ -n "$location" ] || fail "backup location not reported"
stamp=$(basename "$location")
mkdir -p "$backups/$stamp"
scp -q "$server:$location/*" "$backups/$stamp/" || fail "copying the backup home failed"
(cd "$backups/$stamp" && sha256sum --check --quiet checksums.sha256) ||
  fail "backup checksums do not match on the laptop ($backups/$stamp)"
echo "backup $stamp verified on the laptop"

# 3. Backend ------------------------------------------------------------------
if $need_backend; then
  step "3/6 backend: pull and rebuild on the server (several minutes)"
  ssh -A -o ConnectTimeout=15 "$server" "bash $remote/care/deploy/update.sh" ||
    fail "update.sh failed; read its output above"
  server_care=$(on_server "git -C $remote/care rev-parse HEAD")
  same_commit "$server_care" "$care_head" || fail "server backend is ${server_care:0:9}, expected ${care_head:0:9}"
else
  step "3/6 backend: already ${care_head:0:9}"
fi

# 4. Frontend -----------------------------------------------------------------
if $need_frontend; then
  step "4/6 frontend: build on the laptop and ship"
  bash "$here/ship-frontend.sh" || fail "ship-frontend.sh failed"
else
  step "4/6 frontend: already ${fe_head:0:9}"
fi

# 5. Checks, waiting out a slow start (migrations run in celery-beat) ---------
step "5/6 checks"
healthy=false
for attempt in $(seq 1 20); do
  if health; then healthy=true; break; fi
  echo "  not healthy yet (attempt $attempt/20), waiting 30 s"
  sleep 30
done
$healthy || fail "not healthy after 10 minutes. Read: ssh $server 'cd $remote/care/deploy && docker compose logs --tail 80 backend celery-beat'"
served_fe=$(live_frontend)
same_commit "$served_fe" "$fe_head" || fail "site serves frontend $served_fe, expected ${fe_head:0:9}"
echo "  serving backend ${care_head:0:9}, frontend $served_fe"

# 6. Record -------------------------------------------------------------------
step "6/6 record"
line="$(date +%F) — deploy-server.sh: server backend ${care_head:0:9}, frontend ${fe_head:0:9} (was $previous); backup $stamp verified on the laptop; healthy, site and /ping/ 200."
echo "$line" >> "$bus"
echo "BUS: $line"
cat <<EOF

Still to do by hand (needs judgement):
  - docs/plugin-compatibility.md: add a "Server combination" entry: what
    changed, backup $stamp, rollback to the previous combination
    ($previous; frontend image care-suriname-frontend:<that commit>).
  - Server configuration the change needs (roles, permissions): CODING_RULES §7 point 3.
  - Tell the owner what changed and what to check.
EOF
