#!/usr/bin/env bash
# ============================================================================
# KnightMind – Migrate Supabase Postgres → Self-Hosted Postgres
# ============================================================================
# Dumps the Supabase database and restores it into the target Postgres
# (Docker Compose or bare-metal). This is a one-time migration.
#
# Prerequisites:
#   - pg_dump and pg_restore installed locally (apt install postgresql-client)
#   - Source DB (Supabase) accessible
#   - Target DB (Docker or local Postgres) running and empty
#
# Usage:
#   export SOURCE_DATABASE_URL="postgresql://user:pass@db.xxx.supabase.co:5432/postgres"
#   export TARGET_DATABASE_URL="postgresql://knightmind:pass@localhost:5432/knightmind"
#   bash scripts/db-migrate-supabase.sh
#
# Or with Docker Compose target (default):
#   export SOURCE_DATABASE_URL="postgresql://user:pass@db.xxx.supabase.co:5432/postgres"
#   bash scripts/db-migrate-supabase.sh
# ============================================================================

set -euo pipefail

# --- Colors ---
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[migrate]${NC} $*"; }
warn()  { echo -e "${YELLOW}[migrate]${NC} $*"; }
error() { echo -e "${RED}[migrate]${NC} $*"; }

# --- Validate inputs ---
if [ -z "${SOURCE_DATABASE_URL:-}" ]; then
    error "SOURCE_DATABASE_URL is not set."
    echo "  Export your Supabase connection string:"
    echo "  export SOURCE_DATABASE_URL=\"postgresql://user:pass@db.xxx.supabase.co:5432/postgres\""
    exit 1
fi

# Default target: Docker Compose Postgres
TARGET_DATABASE_URL="${TARGET_DATABASE_URL:-postgresql://knightmind:knightmind_dev@localhost:5432/knightmind}"

DUMP_DIR="$(mktemp -d)"
DUMP_FILE="${DUMP_DIR}/knightmind_supabase.dump"

info "Source: ${SOURCE_DATABASE_URL%%@*}@***"
info "Target: ${TARGET_DATABASE_URL%%@*}@***"
info "Dump file: ${DUMP_FILE}"
echo ""

# --- Step 1: Dump from Supabase ---
info "Step 1/4: Dumping from Supabase..."
pg_dump "${SOURCE_DATABASE_URL}" \
    --format=custom \
    --no-owner \
    --no-privileges \
    --no-comments \
    --exclude-schema='extensions' \
    --exclude-schema='auth' \
    --exclude-schema='storage' \
    --exclude-schema='realtime' \
    --exclude-schema='_realtime' \
    --exclude-schema='supabase_*' \
    --exclude-schema='pgsodium*' \
    --exclude-schema='vault' \
    --exclude-schema='graphql*' \
    --exclude-table='_prisma_migrations' \
    > "${DUMP_FILE}"

DUMP_SIZE=$(du -h "${DUMP_FILE}" | cut -f1)
info "Dump complete: ${DUMP_SIZE}"

# --- Step 2: Run Alembic migrations on target to create schema ---
info "Step 2/4: Running Alembic migrations on target..."
DATABASE_URL="${TARGET_DATABASE_URL}" python -m alembic -c services/api/alembic.ini upgrade head
info "Alembic migrations applied."

# --- Step 3: Restore data only (schema already created by Alembic) ---
info "Step 3/4: Restoring data into target..."
pg_restore "${DUMP_FILE}" \
    --dbname="${TARGET_DATABASE_URL}" \
    --data-only \
    --no-owner \
    --no-privileges \
    --disable-triggers \
    --single-transaction \
    || {
        warn "pg_restore reported warnings (this is often normal for partial restores)."
        warn "Check the output above for actual errors."
    }

info "Restore complete."

# --- Step 4: Verify ---
info "Step 4/4: Running verification..."
bash scripts/db-verify.sh "${SOURCE_DATABASE_URL}" "${TARGET_DATABASE_URL}"

# --- Cleanup ---
rm -rf "${DUMP_DIR}"

echo ""
info "=========================================="
info "  Migration complete!"
info "=========================================="
info "Next: update your .env.docker with the target DATABASE_URL."
