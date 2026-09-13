#!/usr/bin/env bash
# Update the running server to the latest pushed code. Run ON THE SERVER:
#
#   bash ~/care-suriname/care/deploy/update.sh
#
# Pulls both repos from GitHub, rebuilds the backend if it changed and
# restarts it. The frontend is built on the laptop and shipped as an image
# (deploy/ship-frontend.sh); this script only tells you when that is due. Database migrations run in celery-beat before the API starts,
# as on every start. The site keeps serving the old build while the new one
# compiles; the switch itself takes seconds.

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
what="${1:-all}"
root=$(cd ../.. && pwd)

pull() {
  local repo="$1" before after
  before=$(git -C "$repo" rev-parse --short HEAD)
  git -C "$repo" pull -q --ff-only
  after=$(git -C "$repo" rev-parse --short HEAD)
  echo "$(basename "$repo"): $before → $after"
  [ "$before" != "$after" ]
}

changed_backend=false; changed_frontend=false
case "$what" in
  all|backend)  pull "$root/care" && changed_backend=true ;;
esac
case "$what" in
  all|frontend) pull "$root/care_fe" && changed_frontend=true ;;
esac

if ! $changed_backend && ! $changed_frontend; then
  echo "Nothing new on GitHub; server already up to date."
  exit 0
fi

export FRONTEND_COMMIT=$(git -C "$root/care_fe" rev-parse --short HEAD)
export FRONTEND_BRANCH=$(git -C "$root/care_fe" branch --show-current)

services=()
$changed_backend  && services+=(backend celery-worker celery-beat)
# The frontend is never compiled here: the server cannot afford it while
# serving. It arrives as a finished image from the laptop (ship-frontend.sh).
if $changed_frontend && ! $changed_backend; then
  echo "care_fe changed. Build and ship it from the laptop:"
  echo "    bash care/deploy/ship-frontend.sh"
  exit 0
elif $changed_frontend; then
  echo "care_fe changed too; after this, run from the laptop: bash care/deploy/ship-frontend.sh"
fi

echo "Building: ${services[*]}"
docker compose build "${services[@]}"
echo "Restarting: ${services[*]}"
docker compose up -d --no-build "${services[@]}"
docker image prune -f >/dev/null

echo
docker compose ps --format '{{.Service}}\t{{.Status}}'
echo
echo "Live frontend: $(curl -s "https://${CARE_DOMAIN:-$(grep ^CARE_DOMAIN= .env | cut -d= -f2)}/build-meta.json" | grep -o '"commit":"[^"]*"')"
