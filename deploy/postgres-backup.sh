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
#
# Recorded as a BARE FILENAME, not the absolute path: sha256sum -c resolves the
# name relative to the working directory, so a checksum holding an absolute path
# breaks the moment the file is moved. That is not hypothetical -- two dumps
# relocated on 2026-08-06 carried their old directory and failed `sha256sum -c`
# while being perfectly intact. A bare name verifies from inside the backup
# directory wherever that directory happens to live.
( cd "${BACKUP_DIR}" && sha256sum "$(basename "${DUMP_FILE}")" > "$(basename "${DUMP_FILE}").sha256" )

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

# --- Retention -------------------------------------------------------------
#
# Two rules, because "delete anything older than N days" was both too narrow and
# too broad.
#
# Too narrow: it matched only ${POSTGRES_DB}_*.sql.gz, so the 14 hand-taken
# `knightmind-db-<ISO>.dump` files were never eligible and sat there forever.
# Stating a 14-day retention that silently applies to 4 of 18 files is worse
# than stating none, because it reads as a guarantee that old copies are gone.
#
# Too broad: applied to everything it would delete the LABELLED dumps -- the
# `-pre-merge-`, `-pre-alembic-stamp-`, `-pre-pr217-merge-` ones taken
# deliberately before a risky change. Those are the copies you want years later;
# age is exactly the wrong criterion for them. They are kept indefinitely and
# pruned by hand.
#
# So: routine dumps are the two auto-generated shapes, and only they age out.
# MIN_KEEP is a floor beneath the age rule, because backups here are manual and
# irregular -- without it a quiet fortnight would age out every routine copy and
# leave only whichever dump this run just wrote.
MIN_KEEP="${MIN_KEEP:-3}"

# Labelled dumps are deliberate, but not immortal -- an earlier version of this
# policy exempted them entirely and the directory ended up with four copies of
# ONE schema revision, kept forever because each had a nice name. A dump's
# restore value decays as the schema moves past it: recovering to a revision
# 26 days and several migrations back means replaying all of them onto data
# that old, which nobody chooses outside a catastrophe.
#
# 90 days, with a floor of 2, so the horizon is long enough to cover "what did
# this look like before that release" while still bounded. Prune further by hand
# when several labelled dumps share a revision; the script cannot tell which
# label matters.
MILESTONE_RETENTION_DAYS="${MILESTONE_RETENTION_DAYS:-90}"
MILESTONE_MIN_KEEP="${MILESTONE_MIN_KEEP:-2}"

is_routine() {
    # knightmind_20260806_000531.sql.gz  (this script)
    # knightmind-db-20260805T144157+0200.dump  (legacy hand-taken -Fc)
    # A label after the prefix (`knightmind-db-pre-...`) fails both patterns and
    # is therefore never pruned.
    case "${1##*/}" in
        "${POSTGRES_DB}"_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9].sql.gz) return 0 ;;
        "${POSTGRES_DB}"-db-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9]*.dump) return 0 ;;
        *) return 1 ;;
    esac
}

# Newest first, so each floor keeps the most recent rather than whatever the
# directory order happened to be. The two classes are counted independently:
# a burst of routine dumps must not push labelled ones past their floor.
DELETED=0
MILESTONES_DELETED=0
ROUTINE_INDEX=0
LABELLED_INDEX=0
while IFS= read -r dump; do
    [ -n "${dump}" ] || continue
    if is_routine "${dump}"; then
        ROUTINE_INDEX=$((ROUTINE_INDEX + 1))
        [ "${ROUTINE_INDEX}" -le "${MIN_KEEP}" ] && continue
        keep_days="${RETENTION_DAYS}"
    else
        LABELLED_INDEX=$((LABELLED_INDEX + 1))
        [ "${LABELLED_INDEX}" -le "${MILESTONE_MIN_KEEP}" ] && continue
        keep_days="${MILESTONE_RETENTION_DAYS}"
    fi
    # -mtime +N is "strictly older than N days", matching the documented rule.
    if [ -n "$(find "${dump}" -maxdepth 0 -mtime +"${keep_days}" -print 2>/dev/null)" ]; then
        rm -f "${dump}"
        if is_routine "${dump}"; then
            DELETED=$((DELETED + 1))
        else
            MILESTONES_DELETED=$((MILESTONES_DELETED + 1))
        fi
    fi
done <<EOF
$(find "${BACKUP_DIR}" -maxdepth 1 -type f \( -name '*.dump' -o -name '*.sql.gz' \) -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-)
EOF

if [ "${DELETED}" -gt 0 ]; then
    echo "[$(date -Iseconds)] Pruned ${DELETED} routine backup(s) older than ${RETENTION_DAYS} days (keeping the newest ${MIN_KEEP})."
fi
if [ "${MILESTONES_DELETED}" -gt 0 ]; then
    echo "[$(date -Iseconds)] Pruned ${MILESTONES_DELETED} labelled backup(s) older than ${MILESTONE_RETENTION_DAYS} days (keeping the newest ${MILESTONE_MIN_KEEP})."
fi

# Then drop any .sha256 whose dump is gone, plus the legacy .meta.txt sidecars.
# Keyed to the dump's absence rather than to the sidecar's own age, so it also
# clears orphans left by a manual delete and cannot itself leave litter.
ORPHANS=0
for sidecar in "${BACKUP_DIR}"/*.sha256 "${BACKUP_DIR}"/*.meta.txt; do
    # The glob matching nothing yields the literal pattern; -e rejects it.
    [ -e "${sidecar}" ] || continue
    case "${sidecar}" in
        *.sha256)   dump="${sidecar%.sha256}" ;;
        *.meta.txt) dump="${sidecar%.meta.txt}.dump" ;;
    esac
    if [ ! -e "${dump}" ]; then
        rm -f "${sidecar}"
        ORPHANS=$((ORPHANS + 1))
    fi
done
if [ "${ORPHANS}" -gt 0 ]; then
    echo "[$(date -Iseconds)] Removed ${ORPHANS} orphaned sidecar file(s)."
fi

echo "[$(date -Iseconds)] Done."
