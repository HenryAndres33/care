#!/usr/bin/env bash
# Back up the CARE Suriname development stack: PostgreSQL and MinIO together.
#
#   bash scripts/care-suriname-backup.sh
#
# Both halves matter. The database holds clinical records, the Smart Text
# catalogs clinicians author through the UI, and the correspondence delivery
# ledgers that pilot decision D1 requires be preserved. MinIO holds the
# generated PDFs and uploads those records point at. A database backup without
# its files restores rows that reference documents which no longer exist.
#
# Writes outside the repository checkout so a git clean, branch switch or
# repository move cannot take the backups with it.
#
# Environment:
#   CARE_BACKUP_DIR              destination (default ~/care-suriname-backups)
#   CARE_BACKUP_MIRROR_DIR       second device, e.g. a USB drive (optional)
#   CARE_BACKUP_RETENTION_DAYS   prune older sets (default 14)

set -euo pipefail

care_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
backup_root="${CARE_BACKUP_DIR:-$HOME/care-suriname-backups}"
mirror_dir="${CARE_BACKUP_MIRROR_DIR:-}"
retention_days="${CARE_BACKUP_RETENTION_DAYS:-14}"
db_name="${POSTGRES_DB:-care}"
db_user="${POSTGRES_USER:-postgres}"
minio_data="$care_dir/care/media/minio"

stamp=$(date +%Y%m%d-%H%M%S)
target="$backup_root/$stamp"

fail() {
  echo "Backup FAILED: $*" >&2
  # Leave the partial set in place rather than deleting evidence of what broke,
  # but mark it so it can never be mistaken for a usable backup.
  [ -d "$target" ] && touch "$target/INCOMPLETE"
  exit 1
}

cd -- "$care_dir"

# Preflight ------------------------------------------------------------------

[ -f docker-compose.yaml ] || fail "docker-compose.yaml not found in $care_dir"

docker compose ps --status running --quiet db 2>/dev/null | grep -q . ||
  fail "the development database container is not running"

[ -d "$minio_data" ] || fail "MinIO data directory not found at $minio_data"

mkdir -p "$target"

echo "CARE Suriname backup → $target"

# PostgreSQL -----------------------------------------------------------------
# Streamed to the host rather than written inside the container, so the backup
# does not depend on the ./care-backups bind mount and can live off the repo.

echo "  database: dumping $db_name"
docker compose exec -T db pg_dump -U "$db_user" -Fc "$db_name" \
  > "$target/database.dump" || fail "pg_dump failed"

[ -s "$target/database.dump" ] || fail "database dump is empty"

# A dump that cannot be read back is not a backup. Verify the archive's table of
# contents before reporting success; pg_restore runs in the container because
# this host has no PostgreSQL client tools.
echo "  database: verifying archive is readable"
dump_entries=$(docker compose exec -T db pg_restore --list < "$target/database.dump" 2>/dev/null | grep -c ';' || true)
[ "${dump_entries:-0}" -gt 0 ] || fail "dump is unreadable by pg_restore"

# MinIO ----------------------------------------------------------------------

echo "  files: archiving MinIO buckets"
tar -czf "$target/minio.tar.gz" -C "$(dirname "$minio_data")" \
  "$(basename "$minio_data")" || fail "MinIO archive failed"

tar -tzf "$target/minio.tar.gz" > /dev/null 2>&1 ||
  fail "MinIO archive is corrupt"

# Manifest -------------------------------------------------------------------
# Checksums let a later restore prove it read the same bytes that were written.

echo "  manifest: recording checksums"
# Checksums go in their own file so `sha256sum --check` can consume it directly;
# a human-readable manifest with header lines would not verify.
(cd "$target" && sha256sum database.dump minio.tar.gz > checksums.sha256) ||
  fail "checksums could not be written"

{
  echo "care-suriname-backup v1"
  echo "created: $(date -Iseconds)"
  echo "host: $(hostname)"
  echo "source: $care_dir"
  echo "database: $db_name"
  echo "dump_entries: $dump_entries"
  echo "verify: cd '$target' && sha256sum --check checksums.sha256"
} > "$target/manifest.txt" || fail "manifest could not be written"

# Re-read what was just written. Catches a truncated or half-flushed write now,
# rather than months later during an emergency restore.
(cd "$target" && sha256sum --check --status checksums.sha256) ||
  fail "checksum verification failed immediately after writing"

# Mirror to a second device --------------------------------------------------
# The primary copy sits on the same disk as the database, so it survives a bad
# migration but not a disk failure or a stolen laptop. The mirror is what makes
# this a real backup.
#
# A missing drive is a warning, not a failure: an unplugged USB stick must never
# mean there was no backup that night.

mirror_status="not configured"
if [ -n "$mirror_dir" ]; then
  if [ -d "$(dirname "$mirror_dir")" ] && mkdir -p "$mirror_dir" 2>/dev/null; then
    mirror_target="$mirror_dir/$stamp"
    if cp -r "$target" "$mirror_target" 2>/dev/null &&
      (cd "$mirror_target" && sha256sum --check --status checksums.sha256); then
      # Verified at the destination. A copy that truncated silently would
      # otherwise look identical to a good one until a restore needed it.
      mirror_status="verified at $mirror_target"
    else
      [ -d "${mirror_target:-}" ] && touch "$mirror_target/INCOMPLETE" 2>/dev/null || true
      mirror_status="FAILED — copy or checksum did not verify"
    fi
  else
    mirror_status="SKIPPED — $mirror_dir unavailable (drive not plugged in?)"
  fi
fi

# Retention ------------------------------------------------------------------
# Prune only complete sets. An INCOMPLETE marker survives until inspected.

prune_old_sets() {
  local root="$1" count=0
  [ -d "$root" ] || return 0
  while IFS= read -r old; do
    [ -f "$old/INCOMPLETE" ] && continue
    rm -rf -- "$old"
    count=$((count + 1))
  done < <(find "$root" -mindepth 1 -maxdepth 1 -type d \
    -mtime "+$retention_days" 2>/dev/null || true)
  echo "$count"
}

pruned=0
if [ "$retention_days" -gt 0 ]; then
  pruned=$(prune_old_sets "$backup_root")
  [ -n "$mirror_dir" ] && [ -d "$mirror_dir" ] && prune_old_sets "$mirror_dir" > /dev/null
fi

# Summary --------------------------------------------------------------------

db_size=$(du -h "$target/database.dump" | cut -f1)
minio_size=$(du -h "$target/minio.tar.gz" | cut -f1)
total_sets=$(find "$backup_root" -mindepth 1 -maxdepth 1 -type d | wc -l)

echo "Backup OK: database $db_size, files $minio_size, $dump_entries archive entries"
echo "  location: $target"
echo "  mirror:   $mirror_status"
echo "  retained: $total_sets set(s), pruned $pruned older than ${retention_days}d"

# A mirror that failed while the drive was present is worth a non-zero exit, so
# `systemctl --user status` shows it rather than reporting a clean run.
case "$mirror_status" in
  FAILED*) exit 1 ;;
esac
