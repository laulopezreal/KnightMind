#!/usr/bin/env bash
# ============================================================================
# KnightMind – Database Verification Script
# ============================================================================
# Compares row counts between source and target databases to verify migration.
#
# Usage:
#   bash scripts/db-verify.sh <SOURCE_URL> <TARGET_URL>
#
# Or standalone against a single database:
#   bash scripts/db-verify.sh <DATABASE_URL>
# ============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[verify]${NC} $*"; }
warn()  { echo -e "${YELLOW}[verify]${NC} $*"; }
error() { echo -e "${RED}[verify]${NC} $*"; }

# KnightMind application tables
TABLES=(
    "games"
    "puzzles"
    "puzzle_stats"
    "puzzle_reviews"
    "jobs"
    "training_sessions"
    "rating_snapshots"
    "import_summaries"
    "fen_eval_cache"
)

count_rows() {
    local db_url="$1"
    local table="$2"
    psql "${db_url}" -t -A -c "SELECT COUNT(*) FROM ${table};" 2>/dev/null || echo "ERROR"
}

if [ $# -eq 2 ]; then
    # Compare mode: source vs target
    SOURCE_URL="$1"
    TARGET_URL="$2"

    info "Comparing source and target databases..."
    echo ""
    printf "  %-25s %10s %10s %s\n" "TABLE" "SOURCE" "TARGET" "STATUS"
    printf "  %-25s %10s %10s %s\n" "-------------------------" "----------" "----------" "------"

    all_ok=true
    for table in "${TABLES[@]}"; do
        src_count=$(count_rows "${SOURCE_URL}" "${table}")
        tgt_count=$(count_rows "${TARGET_URL}" "${table}")

        if [ "${src_count}" = "ERROR" ] || [ "${tgt_count}" = "ERROR" ]; then
            status="${RED}ERROR${NC}"
            all_ok=false
        elif [ "${src_count}" = "${tgt_count}" ]; then
            status="${GREEN}OK${NC}"
        else
            status="${RED}MISMATCH${NC}"
            all_ok=false
        fi

        printf "  %-25s %10s %10s " "${table}" "${src_count}" "${tgt_count}"
        echo -e "${status}"
    done

    echo ""
    if [ "${all_ok}" = true ]; then
        info "All tables match."
    else
        error "Some tables have mismatches. Review the output above."
        exit 1
    fi

elif [ $# -eq 1 ]; then
    # Single database mode: just show row counts
    DB_URL="$1"

    info "Row counts for database..."
    echo ""
    printf "  %-25s %10s\n" "TABLE" "ROWS"
    printf "  %-25s %10s\n" "-------------------------" "----------"

    for table in "${TABLES[@]}"; do
        count=$(count_rows "${DB_URL}" "${table}")
        printf "  %-25s %10s\n" "${table}" "${count}"
    done

else
    echo "Usage:"
    echo "  Compare:  bash scripts/db-verify.sh <SOURCE_URL> <TARGET_URL>"
    echo "  Count:    bash scripts/db-verify.sh <DATABASE_URL>"
    exit 1
fi
