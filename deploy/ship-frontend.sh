#!/usr/bin/env bash
# Build the frontend ON THE LAPTOP and ship the finished image to the server.
#
#   bash deploy/ship-frontend.sh                  # care.openemrsu.com, host from .env-less default
#   CARE_DOMAIN=x.example CARE_SERVER=henry@1.2.3.4 bash deploy/ship-frontend.sh
#
# The server (2 vCPU / 4 GB) cannot compile the bundle while also serving the
# clinic: the two fight for memory and the build thrashes for an hour. The
# laptop does it in ~2 minutes. Output goes to a scratch directory so the
# laptop's own build/ (served on :4000) is never pointed at production.
#
# Run from the laptop with the care_fe checkout as a sibling of care/.

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

domain="${CARE_DOMAIN:-care.openemrsu.com}"
server="${CARE_SERVER:-henry@34.74.242.186}"
remote_deploy="${CARE_REMOTE_DEPLOY:-care-suriname/care/deploy}"
fe=$(cd ../../care_fe && pwd)
out=$(mktemp -d /tmp/care-fe-prod.XXXXXX)
trap 'rm -rf "$out"' EXIT

commit=$(git -C "$fe" rev-parse --short HEAD)
branch=$(git -C "$fe" branch --show-current)
if [ -n "$(git -C "$fe" status --porcelain)" ]; then
  echo "care_fe has uncommitted changes; commit (and push) first so the server runs known code." >&2
  exit 1
fi
if ! git -C "$fe" merge-base --is-ancestor HEAD "@{upstream}" 2>/dev/null &&
   [ "$(git -C "$fe" rev-list --count '@{push}..HEAD' 2>/dev/null || echo 1)" != 0 ]; then
  echo "Warning: care_fe HEAD ($commit) is not pushed; pushing keeps GitHub the record of what runs." >&2
fi

echo "1/4 building care_fe $commit for https://$domain"
(
  cd "$fe"
  export REACT_CARE_API_URL="https://$domain" REACT_PUBLIC_URL="https://$domain"
  export GIT_COMMIT="$commit" GIT_BRANCH="$branch"
  npm run -s build:meta && npm run -s supported-browsers
  npx cross-env NODE_ENV=production vite build --outDir "$out/build" --logLevel warn
)
[ -f "$out/build/index.html" ] || { echo "build produced no index.html" >&2; exit 1; }
grep -q "https://$domain" "$out/build/assets/"*.js || { echo "bundle does not reference https://$domain" >&2; exit 1; }

echo "2/4 packing nginx image"
cp "$fe/nginx/nginx.conf" "$out/nginx.conf"
cat > "$out/Dockerfile" <<'DF'
FROM nginx:stable-alpine
COPY build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
DF
docker build -q -t "care-suriname-frontend:$commit" "$out" >/dev/null

echo "3/4 sending image to $server"
docker save "care-suriname-frontend:$commit" | gzip | ssh "$server" 'gunzip | docker load -q'

echo "4/4 switching the server to it"
ssh "$server" "docker tag care-suriname-frontend:$commit care-suriname-frontend:local &&
  cd $remote_deploy && docker compose up -d --no-build frontend >/dev/null 2>&1 &&
  docker image prune -f >/dev/null && docker compose ps --format '{{.Service}}\t{{.Status}}' frontend"
sleep 2
echo "Live: $(curl -s "https://$domain/build-meta.json" | grep -o '"commit":"[^"]*"')"
