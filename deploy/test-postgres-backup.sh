#!/usr/bin/env bash
# ============================================================================
# Fixture tests for deploy/postgres-backup.sh
# ============================================================================
# The backup script deletes files. Nothing ran against it until 2026-08-06, and
# two of the defects found by review that day were only visible by running it:
# a doc-cited dump deleted on age alone, and a corrupt dump left in place with a
# valid checksum. Both are asserted here.
#
# Self-contained: no Postgres, no docker, no network. Runs against temporary
# directories only, and never reads or writes the real backup directory.
#
#   ./deploy/test-postgres-backup.sh
#
# `Ops CI` (.github/workflows/ci-ops.yaml) runs this on any change under
# `deploy/`. Run it by hand as well while you are editing the backup script --
# it needs nothing installed and finishes in about a second.
# ============================================================================

set -uo pipefail

SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Sourcing defines the helpers without taking a backup.
# shellcheck source=/dev/null
source "${SELF_DIR}/postgres-backup.sh"
set +e

PASS=0
FAIL=0

ok() { PASS=$((PASS + 1)); printf '  ok   %s\n' "$1"; }
no() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1"; }

assert_eq() {
    if [ "$2" = "$3" ]; then ok "$1"; else no "$1"; printf '       expected: %s\n       actual:   %s\n' "$3" "$2"; fi
}
assert_file() {
    if [ -e "$2" ]; then ok "$1"; else no "$1"; printf '       missing: %s\n' "$2"; fi
}
assert_no_file() {
    if [ ! -e "$2" ]; then ok "$1"; else no "$1"; printf '       should not exist: %s\n' "$2"; fi
}

# Defaults the script would otherwise read from .env.docker.
POSTGRES_DB=knightmind
RETENTION_DAYS=14
MIN_KEEP=3
MILESTONE_RETENTION_DAYS=90
MILESTONE_MIN_KEEP=2

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# Make a dump plus its checksum, aged N days.
mkdump() {
    local name="$1" age="$2" stamp
    stamp="$(date -d "-${age} days" '+%Y-%m-%d %H:%M')"
    printf 'x' > "${BACKUP_DIR}/${name}"
    printf 'x' > "${BACKUP_DIR}/${name}.sha256"
    touch -d "${stamp}" "${BACKUP_DIR}/${name}" "${BACKUP_DIR}/${name}.sha256"
}

fresh_dir() {
    BACKUP_DIR="${WORK}/$1"
    mkdir -p "${BACKUP_DIR}"
}

# ---------------------------------------------------------------------------
echo "classification"
# ---------------------------------------------------------------------------
for name in knightmind_20260806_000531.sql.gz \
            knightmind-db-20260805T144157+0200.dump; do
    if is_routine "${name}"; then ok "routine: ${name}"; else no "routine: ${name}"; fi
done
for name in knightmind-pre-release-pr343-20260806T000531.sql.gz \
            knightmind-pre-hardening-20260720T145709+0200.dump \
            knightmind-pre-restoration-20260710T113350+0200.dump \
            knightmind-pre-strip-flag-20260805T103703+0200.dump \
            knightmind-db-pre-merge-20260701T101010+0200.dump; do
    if is_routine "${name}"; then no "labelled: ${name}"; else ok "labelled: ${name}"; fi
done

# ---------------------------------------------------------------------------
echo "retention: routine ages out at RETENTION_DAYS, labelled at MILESTONE_RETENTION_DAYS"
# ---------------------------------------------------------------------------
CITATION_PATHS="${WORK}/empty-docs"
mkdir -p "${WORK}/empty-docs"
fresh_dir retention
mkdump knightmind_20260806_000531.sql.gz 0
mkdump knightmind_20260805_120000.sql.gz 1
mkdump knightmind_20260804_120000.sql.gz 2
mkdump knightmind_20260701_120000.sql.gz 40
mkdump knightmind_20260601_120000.sql.gz 70
mkdump knightmind-db-20260710T113350+0200.dump 30
mkdump knightmind-labelled-recent.dump 1
mkdump knightmind-labelled-second.dump 2
mkdump knightmind-labelled-inside-horizon.dump 60
mkdump knightmind-labelled-past-horizon.dump 100
prune_backups > /dev/null
assert_eq "3 routine pruned" "${DELETED}" "3"
assert_eq "1 labelled pruned" "${MILESTONES_DELETED}" "1"
assert_file "newest 3 routine kept (MIN_KEEP)" "${BACKUP_DIR}/knightmind_20260804_120000.sql.gz"
assert_no_file "40-day routine pruned" "${BACKUP_DIR}/knightmind_20260701_120000.sql.gz"
assert_no_file "legacy .dump is routine, pruned at 30 days" "${BACKUP_DIR}/knightmind-db-20260710T113350+0200.dump"
assert_file "60-day labelled inside its horizon kept" "${BACKUP_DIR}/knightmind-labelled-inside-horizon.dump"
assert_no_file "100-day labelled past its horizon pruned" "${BACKUP_DIR}/knightmind-labelled-past-horizon.dump"

# ---------------------------------------------------------------------------
echo "citation guard: a dump named in a doc is never deleted on age alone"
# ---------------------------------------------------------------------------
# OPERATIONS.md cites three labelled dumps by name while MILESTONE_MIN_KEEP
# keeps two, so the oldest cited one is past the floor and exposed the day it
# turns MILESTONE_RETENTION_DAYS old. Before the citation guard this deleted it.
CITATION_PATHS="${WORK}/docs"
mkdir -p "${WORK}/docs"
cat > "${WORK}/docs/OPERATIONS.md" <<'DOC'
Most recent restoration safety backup:
- `/home/lauureal/backups/knightmind/knightmind-pre-restoration-cited.dump`
DOC
fresh_dir cited
mkdump knightmind-pre-release-newest.sql.gz 1
mkdump knightmind-pre-hardening-second.dump 2
mkdump knightmind-pre-restoration-cited.dump 100
mkdump knightmind-pre-uncited.dump 100
prune_backups > /dev/null
assert_file "cited dump kept past its horizon" "${BACKUP_DIR}/knightmind-pre-restoration-cited.dump"
assert_eq "the keep is counted and reported" "${CITED_KEPT}" "1"
assert_no_file "an uncited dump of the same age still goes" "${BACKUP_DIR}/knightmind-pre-uncited.dump"

# Same fixture, citation removed: the guard must not simply pin everything.
fresh_dir uncited
CITATION_PATHS="${WORK}/empty-docs"
mkdump knightmind-pre-release-newest.sql.gz 1
mkdump knightmind-pre-hardening-second.dump 2
mkdump knightmind-pre-restoration-cited.dump 100
prune_backups > /dev/null
assert_no_file "with no citation, the same dump is pruned" "${BACKUP_DIR}/knightmind-pre-restoration-cited.dump"

# The guard covers routine dumps too — the dump whose deletion left a dangling
# citation in a handoff was a routine one.
fresh_dir cited-routine
CITATION_PATHS="${WORK}/docs-routine"
mkdir -p "${WORK}/docs-routine"
echo 'verified restorable: knightmind_20260101_000000.sql.gz' > "${WORK}/docs-routine/handoff.md"
mkdump knightmind_20260806_000531.sql.gz 0
mkdump knightmind_20260805_120000.sql.gz 1
mkdump knightmind_20260804_120000.sql.gz 2
mkdump knightmind_20260101_000000.sql.gz 200
prune_backups > /dev/null
assert_file "cited routine dump kept past 14 days" "${BACKUP_DIR}/knightmind_20260101_000000.sql.gz"

# ---------------------------------------------------------------------------
echo "orphan sweep"
# ---------------------------------------------------------------------------
fresh_dir orphans
CITATION_PATHS="${WORK}/empty-docs"
mkdump knightmind_20260806_000531.sql.gz 0
printf 'x' > "${BACKUP_DIR}/knightmind-gone.dump.sha256"
printf 'x' > "${BACKUP_DIR}/knightmind-db-20260101T000000+0100.meta.txt"
printf 'x' > "${BACKUP_DIR}/knightmind-db-20260102T000000+0100.dump"
printf 'x' > "${BACKUP_DIR}/knightmind-db-20260102T000000+0100.meta.txt"
printf 'x' > "${BACKUP_DIR}/knightmind_20260806_000531.sql.gz.meta.txt"
sweep_orphans > /dev/null
assert_no_file "orphaned .sha256 removed" "${BACKUP_DIR}/knightmind-gone.dump.sha256"
assert_no_file "orphaned legacy .meta.txt removed" "${BACKUP_DIR}/knightmind-db-20260101T000000+0100.meta.txt"
assert_file "legacy .meta.txt with its .dump kept" "${BACKUP_DIR}/knightmind-db-20260102T000000+0100.meta.txt"
assert_file "live dump's own .sha256 kept" "${BACKUP_DIR}/knightmind_20260806_000531.sql.gz.sha256"
# Before the fix, .meta.txt always mapped to "${base}.dump", so a sidecar beside
# a .sql.gz was deleted with its dump sitting right there.
assert_file ".meta.txt beside a live .sql.gz kept" "${BACKUP_DIR}/knightmind_20260806_000531.sql.gz.meta.txt"

# ---------------------------------------------------------------------------
echo "verify_and_checksum"
# ---------------------------------------------------------------------------
fresh_dir verify
GOOD="${BACKUP_DIR}/knightmind_20260806_120000.sql.gz"
printf 'SELECT 1;\n' | gzip > "${GOOD}"
if verify_and_checksum "${GOOD}" > /dev/null 2>&1; then ok "a readable dump verifies"; else no "a readable dump verifies"; fi
assert_file "checksum written for a good dump" "${GOOD}.sha256"
( cd "${BACKUP_DIR}" && sha256sum -c --status ./*.sha256 ) \
    && ok "checksum records a bare filename and verifies in place" \
    || no "checksum records a bare filename and verifies in place"

# A gzip header followed by garbage: gzip exits 0 writing it, and -t rejects it.
BAD="${BACKUP_DIR}/knightmind_20260806_130000.sql.gz"
printf '\037\213\010\000\000\000\000\000\000\003truncated' > "${BAD}"
if verify_and_checksum "${BAD}" > /dev/null 2>&1; then no "a corrupt dump fails"; else ok "a corrupt dump fails"; fi
# Before the fix the checksum was written first and only the exit code changed,
# so the directory kept a corrupt dump under a valid name, with a valid
# checksum, as the newest routine dump.
assert_no_file "corrupt dump does not survive under its dump name" "${BAD}"
assert_no_file "no checksum is written for a corrupt dump" "${BAD}.sha256"
assert_file "corrupt dump quarantined for inspection" "${BAD}.corrupt"
# .corrupt matches no retention glob, so prune must ignore it entirely.
prune_backups > /dev/null
assert_file "quarantined file is not swept" "${BAD}.corrupt"

# ---------------------------------------------------------------------------
echo "direct execution cleanup"
# ---------------------------------------------------------------------------
FAKE_BIN="${WORK}/fake-bin"
mkdir -p "${FAKE_BIN}"
cat > "${FAKE_BIN}/docker" <<'SH'
#!/usr/bin/env bash
printf 'SELECT 1;\n'
[ "${FAKE_DOCKER_FAIL:-0}" -eq 0 ]
SH
chmod +x "${FAKE_BIN}/docker"

DIRECT_ENV="${WORK}/direct.env"
cat > "${DIRECT_ENV}" <<'ENV'
POSTGRES_DB=knightmind_test
POSTGRES_USER=knightmind_test
ENV

DIRECT_BACKUPS="${WORK}/direct-success"
if PATH="${FAKE_BIN}:${PATH}" \
        ENV_FILE="${DIRECT_ENV}" \
        BACKUP_DIR="${DIRECT_BACKUPS}" \
        CITATION_PATHS="${WORK}/empty-docs" \
        "${SELF_DIR}/postgres-backup.sh" > "${WORK}/direct-success.log" 2>&1; then
    ok "successful direct execution exits zero"
else
    no "successful direct execution exits zero"
fi
if compgen -G "${DIRECT_BACKUPS}/knightmind_test_*.sql.gz" > /dev/null; then
    ok "successful direct execution leaves the completed dump"
else
    no "successful direct execution leaves the completed dump"
fi
if ! compgen -G "${DIRECT_BACKUPS}/*.tmp.*" > /dev/null; then
    ok "successful direct execution leaves no temporary dump"
else
    no "successful direct execution leaves no temporary dump"
fi

FAILED_BACKUPS="${WORK}/direct-failure"
if PATH="${FAKE_BIN}:${PATH}" \
        FAKE_DOCKER_FAIL=1 \
        ENV_FILE="${DIRECT_ENV}" \
        BACKUP_DIR="${FAILED_BACKUPS}" \
        CITATION_PATHS="${WORK}/empty-docs" \
        "${SELF_DIR}/postgres-backup.sh" > "${WORK}/direct-failure.log" 2>&1; then
    no "failed direct execution exits non-zero"
else
    ok "failed direct execution exits non-zero"
fi
if ! compgen -G "${FAILED_BACKUPS}/*.tmp.*" > /dev/null; then
    ok "failed direct execution cleans its temporary dump"
else
    no "failed direct execution cleans its temporary dump"
fi

# ---------------------------------------------------------------------------
printf '\n%s passed, %s failed\n' "${PASS}" "${FAIL}"
[ "${FAIL}" -eq 0 ]
