#!/usr/bin/env bash
# ============================================================================
# KnightMind – Postgres Backup Script
# ============================================================================
# Takes a timestamped, gzip-verified, checksummed pg_dump into BACKUP_DIR, then
# applies the retention policy documented in OPERATIONS.md ("Backup first rule"):
# routine dumps age out, labelled dumps get a longer horizon, and any dump named
# in a project document is never deleted automatically.
#
# Usage (manual):
#   ./deploy/postgres-backup.sh
#
# There is deliberately NO cron entry and no systemd timer — backups are manual,
# and OPERATIONS.md says so. If one is ever installed, this is the line:
#   0 3 * * * /home/lauureal/apps/knightmind/deploy/postgres-backup.sh >> /home/lauureal/backups/knightmind/knightmind-backup.log 2>&1
#
# Environment variables (or defaults):
#   ENV_FILE                  – env file sourced for DB config (default: /home/lauureal/apps/knightmind/.env.docker)
#   BACKUP_DIR                – where to store dumps (default: /home/lauureal/backups/knightmind)
#   RETENTION_DAYS            – age limit for routine dumps (default: 14)
#   MIN_KEEP                  – routine dumps kept regardless of age (default: 3)
#   MILESTONE_RETENTION_DAYS  – age limit for labelled dumps (default: 90)
#   MILESTONE_MIN_KEEP        – labelled dumps kept regardless of age (default: 2)
#   CITATION_PATHS            – colon-separated doc trees searched before any
#                               automatic delete (default: this repo, plus
#                               /home/lauureal/projects/knightmind)
#   POSTGRES_DB               – database name (default: knightmind)
#   POSTGRES_USER             – database user (default: knightmind)
#
# POSTGRES_DB / POSTGRES_USER are read from ENV_FILE (the same file the
# docker compose invocation below uses), so the dump always targets the DB
# the server actually runs. The defaults above only apply to keys the env
# file does not define. Assumption: the env file is simple KEY=VALUE lines
# (as in .env.example); values containing spaces or shell metacharacters
# (& ; | * # $ ...) must be single-quoted, since this file is shell-sourced
# here (docker compose's own --env-file parser is more lenient).
#
# Sourcing this file defines the helpers below WITHOUT taking a backup, so
# deploy/test-postgres-backup.sh can exercise retention against fixture
# directories. Only direct execution runs main.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"

is_routine() {
    # knightmind_20260806_000531.sql.gz  (this script)
    # knightmind-db-20260805T144157+0200.dump  (legacy hand-taken -Fc)
    # A label after the prefix (`knightmind-db-pre-...`) fails both patterns and
    # is therefore treated as labelled.
    case "${1##*/}" in
        "${POSTGRES_DB}"_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9].sql.gz) return 0 ;;
        "${POSTGRES_DB}"-db-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9]*.dump) return 0 ;;
        *) return 1 ;;
    esac
}

# A dump some document names by hand is a dump someone expects to find.
#
# OPERATIONS.md requires grepping both doc trees before deleting a dump BY HAND,
# a rule written after a pruned dump turned out to be cited in
# ~/projects/knightmind/handoffs/ as a session's verified-restorable backup and
# the citation was left dangling. That rule bound humans only: this script is the
# only automated deleter and was held to age alone.
#
# The floors do not cover it. OPERATIONS.md names three labelled dumps and
# MILESTONE_MIN_KEEP keeps two, so the oldest cited dump is deleted the day it
# turns MILESTONE_RETENTION_DAYS old — with its filename and SHA256 still printed
# in the doc.
#
# Consequence to accept: a cited dump is kept until its citation is removed, so
# the directory can only be shrunk past that point by editing the doc first.
# Every retained citation is logged for exactly that reason.
is_cited() {
    local name="${1##*/}" tree
    local -a trees=()
    IFS=':' read -r -a trees <<< "${CITATION_PATHS}"
    for tree in "${trees[@]}"; do
        [ -n "${tree}" ] && [ -d "${tree}" ] || continue
        if grep -rqF --exclude-dir=.git --exclude-dir=node_modules \
                --exclude-dir=dist --exclude-dir=.venv \
                -- "${name}" "${tree}" 2>/dev/null; then
            return 0
        fi
    done
    return 1
}

# Two age rules, because "delete anything older than N days" was both too narrow
# and too broad.
#
# Too narrow: it matched only ${POSTGRES_DB}_*.sql.gz, so the 14 hand-taken
# `knightmind-db-<ISO>.dump` files were never eligible and sat there forever.
# Stating a 14-day retention that silently applies to 4 of 18 files is worse
# than stating none, because it reads as a guarantee that old copies are gone.
#
# Too broad: applied to everything it would delete the LABELLED dumps — the
# `-pre-merge-`, `-pre-release-pr343-`, `-pre-hardening-` ones taken deliberately
# before a risky change.
#
# So labelled dumps get their own, longer horizon rather than an exemption. An
# earlier version of this policy exempted them entirely and the directory ended
# up with four copies of ONE schema revision, each kept forever because it had a
# nice name. A dump's restore value decays as the schema moves past it:
# recovering to a revision several migrations back means replaying all of them
# onto data that old, which nobody chooses outside a catastrophe. 90 days covers
# "what did this look like before that release" while staying bounded.
#
# Both floors exist because backups here are manual and irregular — without them
# a quiet fortnight would age out every routine copy and leave only whichever
# dump this run just wrote. The two classes are counted independently, so a burst
# of routine dumps cannot push labelled ones past their floor.
prune_backups() {
    local dump keep_days routine
    local routine_index=0 labelled_index=0
    DELETED=0
    MILESTONES_DELETED=0
    CITED_KEPT=0

    # Newest first, so each floor keeps the most recent rather than whatever the
    # directory order happened to be. A heredoc rather than a pipe: the loop must
    # run in this shell or the counters below are lost with the subshell.
    while IFS= read -r dump; do
        [ -n "${dump}" ] || continue
        if is_routine "${dump}"; then
            routine=1
            routine_index=$((routine_index + 1))
            [ "${routine_index}" -le "${MIN_KEEP}" ] && continue
            keep_days="${RETENTION_DAYS}"
        else
            routine=0
            labelled_index=$((labelled_index + 1))
            [ "${labelled_index}" -le "${MILESTONE_MIN_KEEP}" ] && continue
            keep_days="${MILESTONE_RETENTION_DAYS}"
        fi
        # -mtime +N is "strictly older than N days", matching the documented rule.
        [ -n "$(find "${dump}" -maxdepth 0 -mtime +"${keep_days}" -print 2>/dev/null)" ] || continue
        if is_cited "${dump}"; then
            echo "[$(date -Iseconds)] Keeping ${dump##*/}: past its ${keep_days}-day horizon but cited in a project document."
            CITED_KEPT=$((CITED_KEPT + 1))
            continue
        fi
        rm -f "${dump}"
        if [ "${routine}" -eq 1 ]; then
            DELETED=$((DELETED + 1))
        else
            MILESTONES_DELETED=$((MILESTONES_DELETED + 1))
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
    if [ "${CITED_KEPT}" -gt 0 ]; then
        echo "[$(date -Iseconds)] Kept ${CITED_KEPT} cited backup(s) past their horizon. Remove the citation first if one should go."
    fi
}

# Drop any .sha256 whose dump is gone, plus the legacy .meta.txt sidecars.
# Keyed to the dump's absence rather than to the sidecar's own age, so it also
# clears orphans left by a manual delete and cannot itself leave litter.
sweep_orphans() {
    local sidecar dump base
    ORPHANS=0
    for sidecar in "${BACKUP_DIR}"/*.sha256 "${BACKUP_DIR}"/*.meta.txt; do
        # The glob matching nothing yields the literal pattern; -e rejects it.
        [ -e "${sidecar}" ] || continue
        case "${sidecar}" in
            *.sha256)
                dump="${sidecar%.sha256}"
                ;;
            *.meta.txt)
                # Legacy sidecars drop the dump's extension
                # (knightmind-db-<ts>.meta.txt -> knightmind-db-<ts>.dump), but a
                # sidecar written beside a .sql.gz keeps the whole filename.
                # Assuming only the legacy shape deleted live sidecars of the other.
                base="${sidecar%.meta.txt}"
                if [ -e "${base}" ]; then dump="${base}"; else dump="${base}.dump"; fi
                ;;
        esac
        if [ ! -e "${dump}" ]; then
            rm -f "${sidecar}"
            ORPHANS=$((ORPHANS + 1))
        fi
    done
    if [ "${ORPHANS}" -gt 0 ]; then
        echo "[$(date -Iseconds)] Removed ${ORPHANS} orphaned sidecar file(s)."
    fi
}

# Read the dump straight back BEFORE checksumming it, then checksum it. gzip -t
# catches a truncated or corrupt stream that pg_dump exited 0 on, which is the
# failure this step exists to detect; verifying a checksum against the file it
# was just computed from would prove nothing on its own.
#
# A failure quarantines the file and writes no checksum. Until 2026-08-06 this
# ran AFTER the checksum and simply exited, leaving a corrupt dump in the
# directory under a valid name, with a valid .sha256 beside it, as the NEWEST
# routine dump — so MIN_KEEP pinned it in place and it was exactly what someone
# glancing at the directory would take as the fresh pre-release backup. The
# rename puts it outside every retention glob, so it stays for inspection and
# can never be mistaken for a backup.
#
# Checksums record a BARE FILENAME, not the absolute path: sha256sum -c resolves
# the name relative to the working directory, so a checksum holding an absolute
# path breaks the moment the file is moved. That is not hypothetical -- two dumps
# relocated on 2026-08-06 carried their old directory and failed `sha256sum -c`
# while being perfectly intact. A bare name verifies from inside the backup
# directory wherever that directory happens to live.
verify_and_checksum() {
    local dump_file="$1"
    if ! gzip -t "${dump_file}"; then
        mv -f "${dump_file}" "${dump_file}.corrupt"
        echo "[$(date -Iseconds)] ERROR: dump failed gzip integrity check, quarantined as ${dump_file}.corrupt" >&2
        echo "No usable backup was taken. Investigate before running anything that needs one." >&2
        return 1
    fi
    ( cd "$(dirname "${dump_file}")" && sha256sum "${dump_file##*/}" > "${dump_file##*/}.sha256" )
}

main() {
    ENV_FILE="${ENV_FILE:-/home/lauureal/apps/knightmind/.env.docker}"

    if [ ! -f "${ENV_FILE}" ]; then
        echo "[$(date -Iseconds)] ERROR: env file not found: ${ENV_FILE}" >&2
        echo "Refusing to back up with default credentials — they may target the wrong database." >&2
        exit 1
    fi

    # Export every variable the env file defines so pg_dump below uses the
    # server's real DB config instead of cron-environment defaults. Resolved
    # BEFORE the defaults below, so the env file can override them.
    set -a
    # shellcheck disable=SC1090
    . "${ENV_FILE}"
    set +a

    BACKUP_DIR="${BACKUP_DIR:-/home/lauureal/backups/knightmind}"
    RETENTION_DAYS="${RETENTION_DAYS:-14}"
    MIN_KEEP="${MIN_KEEP:-3}"
    MILESTONE_RETENTION_DAYS="${MILESTONE_RETENTION_DAYS:-90}"
    MILESTONE_MIN_KEEP="${MILESTONE_MIN_KEEP:-2}"
    CITATION_PATHS="${CITATION_PATHS:-${REPO_ROOT}:/home/lauureal/projects/knightmind}"
    POSTGRES_DB="${POSTGRES_DB:-knightmind}"
    POSTGRES_USER="${POSTGRES_USER:-knightmind}"

    local timestamp dump_file tmp_dump_file filesize
    timestamp=$(date +%Y%m%d_%H%M%S)
    dump_file="${BACKUP_DIR}/${POSTGRES_DB}_${timestamp}.sql.gz"

    mkdir -p "${BACKUP_DIR}"

    echo "[$(date -Iseconds)] Starting backup of ${POSTGRES_DB}..."

    # Dump via docker compose to a temp file first, then move atomically.
    # This prevents partial/empty backup files if pg_dump fails.
    tmp_dump_file="${dump_file}.tmp.$$"
    # Clean up the temp file on any exit (set -e abort, signal, etc.) so failed
    # backups don't leave orphaned *.tmp.* files behind. The successful path
    # renames it first, so the trap is then a no-op.
    # Capture the path in the trap command now: tmp_dump_file is local to main
    # and is otherwise out of scope when EXIT runs after main returns.
    trap "rm -f -- $(printf '%q' "${tmp_dump_file}")" EXIT
    # --env-file: compose only auto-loads a file literally named ".env" for
    # ${POSTGRES_*} interpolation; this project uses .env.docker.
    docker compose -f /home/lauureal/apps/knightmind/docker-compose.yml \
        --env-file "${ENV_FILE}" exec -T db \
        pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
        | gzip > "${tmp_dump_file}"
    mv "${tmp_dump_file}" "${dump_file}"
    trap - EXIT

    # The ad-hoc dumps taken by hand have always carried a .sha256 beside them;
    # the ones this script produced did not, so the sanctioned mechanism was the
    # only one whose output could not be checked for bit rot or a truncated copy.
    verify_and_checksum "${dump_file}" || exit 1

    filesize=$(du -h "${dump_file}" | cut -f1)
    echo "[$(date -Iseconds)] Backup complete: ${dump_file} (${filesize}), gzip verified"

    prune_backups
    sweep_orphans

    echo "[$(date -Iseconds)] Done."
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    main "$@"
fi
