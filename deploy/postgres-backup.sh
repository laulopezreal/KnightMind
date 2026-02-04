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
#   0 3 * * * /opt/knightmind/deploy/postgres-backup.sh >> /var/log/knightmind-backup.log 2>&1
#
# Environment variables (or defaults):
#   BACKUP_DIR        – where to store dumps (default: /var/backups/knightmind)
#   RETENTION_DAYS    – delete backups older than N days (default: 14)
#   POSTGRES_DB       – database name (default: knightmind)
#   POSTGRES_USER     – database user (default: knightmind)
# ============================================================================

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/knightmind}"
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
docker compose -f /opt/knightmind/docker-compose.yml exec -T db \
    pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
    | gzip > "${TMP_DUMP_FILE}"
mv "${TMP_DUMP_FILE}" "${DUMP_FILE}"

FILESIZE=$(du -h "${DUMP_FILE}" | cut -f1)
echo "[$(date -Iseconds)] Backup complete: ${DUMP_FILE} (${FILESIZE})"

# Prune old backups
DELETED=$(find "${BACKUP_DIR}" -name "${POSTGRES_DB}_*.sql.gz" -mtime +"${RETENTION_DAYS}" -delete -print | wc -l)
if [ "${DELETED}" -gt 0 ]; then
    echo "[$(date -Iseconds)] Pruned ${DELETED} backup(s) older than ${RETENTION_DAYS} days."
fi

echo "[$(date -Iseconds)] Done."
