#!/usr/bin/env bash
# Nightly backup of the production stack, run ON THE SERVER.
#
#   bash deploy/server-backup.sh
#
# Same shape as scripts/care-suriname-backup.sh on the laptop: a PostgreSQL
# dump plus an archive of the two MinIO buckets, verified, checksummed, kept
# for a fixed number of nights. The laptop pulls these sets home every morning
# (scripts/pull-server-backup.sh), which is what keeps the clinic independent
# of the hosting provider.
#
# Environment:
#   CARE_BACKUP_DIR              destination (default ~/care-suriname-backups)
#   CARE_BACKUP_RETENTION_DAYS   prune older sets (default 14)

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

backup_root="${CARE_BACKUP_DIR:-$HOME/care-suriname-backups}"
retention_days="${CARE_BACKUP_RETENTION_DAYS:-14}"
stamp=$(date +%Y%m%d-%H%M%S)
target="$backup_root/$stamp"
compose="docker compose"

fail() {
  echo "Backup FAILED: $*" >&2
  [ -d "$target" ] && touch "$target/INCOMPLETE" 2>/dev/null || true
  exit 1
}

# shellcheck disable=SC1091
set -a; . ./.env; set +a
db_user="${POSTGRES_USER:?}"; db_name="${POSTGRES_DB:?}"
minio_volume="care-suriname_minio-data"

mkdir -p "$target" || fail "cannot create $target"
echo "CARE Suriname server backup → $target"

# PostgreSQL -----------------------------------------------------------------
echo "  database: dumping $db_name"
$compose exec -T db pg_dump -U "$db_user" -Fc "$db_name" > "$target/database.dump" ||
  fail "pg_dump failed"
[ -s "$target/database.dump" ] || fail "database dump is empty"

echo "  database: verifying archive is readable"
dump_entries=$($compose exec -T db pg_restore --list < "$target/database.dump" 2>/dev/null | grep -c ';' || true)
[ "${dump_entries:-0}" -gt 0 ] || fail "dump is unreadable by pg_restore"

# MinIO ----------------------------------------------------------------------
# The MinIO image has no tar, so a throwaway Alpine container reads the volume.
# .minio.sys is deliberately excluded: it is instance state, not clinical data,
# and restoring it onto another instance causes trouble.
echo "  files: archiving MinIO buckets"
docker run --rm -v "$minio_volume":/data:ro -v "$target":/out alpine:3 \
  tar -czf /out/minio.tar.gz -C /data patient-bucket facility-bucket ||
  fail "MinIO archive failed"
tar -tzf "$target/minio.tar.gz" > /dev/null 2>&1 || fail "MinIO archive is corrupt"

# Manifest -------------------------------------------------------------------
echo "  manifest: recording checksums"
(cd "$target" && sha256sum database.dump minio.tar.gz > checksums.sha256) ||
  fail "checksums could not be written"
{
  echo "care-suriname-backup v1 (server)"
  echo "created: $(date -Iseconds)"
  echo "host: $(hostname)"
  echo "domain: ${CARE_DOMAIN:-unknown}"
  echo "database: $db_name"
  echo "dump_entries: $dump_entries"
  echo "verify: cd '$target' && sha256sum --check checksums.sha256"
} > "$target/manifest.txt"
(cd "$target" && sha256sum --check --status checksums.sha256) ||
  fail "checksum verification failed immediately after writing"

# Retention ------------------------------------------------------------------
pruned=0
if [ "$retention_days" -gt 0 ]; then
  while IFS= read -r old; do
    [ -f "$old/INCOMPLETE" ] && continue
    rm -rf -- "$old"; pruned=$((pruned + 1))
  done < <(find "$backup_root" -mindepth 1 -maxdepth 1 -type d -mtime "+$retention_days" 2>/dev/null || true)
fi

echo "Backup OK: database $(du -h "$target/database.dump" | cut -f1), files $(du -h "$target/minio.tar.gz" | cut -f1), $dump_entries archive entries"
echo "  location: $target"
echo "  retained: $(find "$backup_root" -mindepth 1 -maxdepth 1 -type d | wc -l) set(s), pruned $pruned older than ${retention_days}d"
