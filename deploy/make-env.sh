#!/usr/bin/env bash
# Create deploy/.env for a server from .env.example with fresh random secrets.
#
#   bash make-env.sh care.example.sr
#
# Refuses to overwrite an existing .env: regenerating secrets on a running
# server would lock the API out of its own database and log every user out.

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

domain="${1:-}"
[ -n "$domain" ] || { echo "usage: bash make-env.sh <domain>" >&2; exit 1; }
[ ! -e .env ] || { echo ".env already exists; remove it deliberately first." >&2; exit 1; }

rand() { openssl rand -hex "$1"; }

# The JWKS is an RSA key set; generate it with the backend's own helper so the
# format matches exactly what settings expect. Needs the backend image, which
# `docker compose build backend` produces.
if ! docker image inspect care-suriname-backend:local >/dev/null 2>&1; then
  echo "Building backend image first (needed to generate the signing key)…"
  # Plain docker build: compose would insist on the .env this script is about
  # to write.
  docker build -f ../docker/prod.Dockerfile -t care-suriname-backend:local ..
fi
jwks=$(docker run --rm --entrypoint python care-suriname-backend:local \
  -c 'from care.utils.jwks.generate_jwk import generate_encoded_jwks; print(generate_encoded_jwks())')

pg_pw=$(rand 24)

sed \
  -e "s|<domain>|$domain|g" \
  -e "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$(rand 48)|" \
  -e "s|^JWKS_BASE64=.*|JWKS_BASE64=$jwks|" \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$pg_pw|" \
  -e "s|<POSTGRES_PASSWORD>|$pg_pw|" \
  -e "s|^BUCKET_SECRET=.*|BUCKET_SECRET=$(rand 24)|" \
  .env.example > .env
chmod 600 .env

if grep -v '^#' .env | grep -q '<generated>\|<domain>'; then
  echo "Some placeholders were not filled; inspect .env before use." >&2
  exit 1
fi
echo "Wrote .env for https://$domain (secrets generated, file mode 600)."
