#!/usr/bin/env bash
# ============================================================================
# KnightMind – Postgres Backup Script
# ============================================================================
# Creates a timestamped pg_dump and prunes backups older than RETENTION_DAYS.
#
# Usage (manual):
#   ./deploy/postgres-backup.sh
#
# Cron (daily at 03:00):
#   0 3 * * * /home/lauureal/apps/knightmind/deploy/postgres-backup.sh >> /home/lauureal/backups/knightmind/knightmind-backup.log 2>&1
#
# Environment variables (or defaults):
#   ENV_FILE          – env file sourced for DB config (default: /home/lauureal/apps/knightmind/.env.docker)
#   BACKUP_DIR        – where to store dumps (default: /home/lauureal/backups/knightmind)
#   RETENTION_DAYS    – delete backups older than N days (default: 14)
#   POSTGRES_DB       – database name (default: knightmind)
#   POSTGRES_USER     – database user (default: knightmind)
#
# POSTGRES_DB / POSTGRES_USER are read from ENV_FILE (the same file the
# docker compose invocation below uses), so the dump always targets the DB
# the server actually runs. The defaults above only apply to keys the env
# file does not define. Assumption: the env file is simple KEY=VALUE lines
# (as in .env.example); values containing spaces or shell metacharacters
# (& ; | * # $ ...) must be single-quoted, since this file is shell-sourced
# here (docker compose's own --env-file parser is more lenient).
# ============================================================================

set -euo pipefail

ENV_FILE="${ENV_FILE:-/home/lauureal/apps/knightmind/.env.docker}"

if [ ! -f "${ENV_FILE}" ]; then
    echo "[$(date -Iseconds)] ERROR: env file not found: ${ENV_FILE}" >&2
    echo "Refusing to back up with default credentials — they may target the wrong database." >&2
    exit 1
fi

# Export every variable the env file defines so pg_dump below uses the
# server's real DB config instead of cron-environment defaults.
set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

BACKUP_DIR="${BACKUP_DIR:-/home/lauureal/backups/knightmind}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
POSTGRES_DB="${POSTGRES_DB:-knightmind}"
POSTGRES_USER="${POSTGRES_USER:-knightmind}"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DUMP_FILE="${BACKUP_DIR}/${POSTGRES_DB}_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

echo "[$(date -Iseconds)] Starting backup of ${POSTGRES_DB}..."

# Dump via docker compose to a temp file first, then move atomically.
# This prevents partial/empty backup files if pg_dump fails.
TMP_DUMP_FILE="${DUMP_FILE}.tmp.$$"
# Clean up the temp file on any exit (set -e abort, signal, etc.) so failed
# backups don't leave orphaned *.tmp.* files behind. The successful path
# renames it first, so the trap is then a no-op.
trap 'rm -f "${TMP_DUMP_FILE}"' EXIT
# --env-file: compose only auto-loads a file literally named ".env" for
# ${POSTGRES_*} interpolation; this project uses .env.docker.
docker compose -f /home/lauureal/apps/knightmind/docker-compose.yml \
    --env-file "${ENV_FILE}" exec -T db \
    pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
    | gzip > "${TMP_DUMP_FILE}"
mv "${TMP_DUMP_FILE}" "${DUMP_FILE}"

# Checksum every dump. The ad-hoc dumps taken by hand have always carried a
# .sha256 beside them; the ones this script produced did not, so the sanctioned
# mechanism was the only one whose output could not be checked for bit rot or a
# truncated copy. Written after the atomic move, so a .sha256 existing implies a
# complete dump.
sha256sum "${DUMP_FILE}" > "${DUMP_FILE}.sha256"

# Read it straight back. gzip -t catches a truncated or corrupt stream that
# pg_dump exited 0 on, which is the failure this file exists to detect --
# verifying the checksum we just computed against the file we just wrote would
# prove nothing on its own.
if ! gzip -t "${DUMP_FILE}"; then
    echo "[$(date -Iseconds)] ERROR: ${DUMP_FILE} failed gzip integrity check" >&2
    exit 1
fi

FILESIZE=$(du -h "${DUMP_FILE}" | cut -f1)
echo "[$(date -Iseconds)] Backup complete: ${DUMP_FILE} (${FILESIZE}), gzip verified"

# Prune old backups
DELETED=$(find "${BACKUP_DIR}" -name "${POSTGRES_DB}_*.sql.gz" -mtime +"${RETENTION_DAYS}" -delete -print | wc -l)
if [ "${DELETED}" -gt 0 ]; then
    echo "[$(date -Iseconds)] Pruned ${DELETED} backup(s) older than ${RETENTION_DAYS} days."
fi

# Then drop any .sha256 whose dump is gone. Deliberately keyed to the dump's
# absence rather than to the checksum's own age: the prune above matches only
# *.sql.gz, so without this every retention cycle would leave its checksums
# behind and the directory would fill with references to files that no longer
# exist. Self-healing, so it also clears orphans left by a manual delete.
ORPHANS=0
for checksum in "${BACKUP_DIR}"/${POSTGRES_DB}_*.sql.gz.sha256; do
    # The glob matching nothing yields the literal pattern; -e rejects it.
    [ -e "${checksum}" ] || continue
    if [ ! -e "${checksum%.sha256}" ]; then
        rm -f "${checksum}"
        ORPHANS=$((ORPHANS + 1))
    fi
done
if [ "${ORPHANS}" -gt 0 ]; then
    echo "[$(date -Iseconds)] Removed ${ORPHANS} orphaned checksum(s)."
fi

echo "[$(date -Iseconds)] Done."
