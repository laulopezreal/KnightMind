#!/usr/bin/env bash
# ============================================================================
# KnightMind – Hetzner Server Bootstrap
# ============================================================================
# Idempotent setup script for a fresh Ubuntu 24.04 LTS VPS.
# Run as root:  bash deploy/setup.sh
#
# What it does:
#   1. System updates
#   2. Installs Docker + Docker Compose plugin
#   3. Uses project-local Docker Caddy for public API ingress
#   4. Leaves firewall/DNS changes to the operator unless explicitly enabled
#   5. Uses the existing app/deploy SSOT at /home/lauureal/apps/knightmind
#   6. Sets up backup cron
#   7. Creates backup/log directories
#
# What it does NOT do:
#   - Start the app (you need to create .env.docker and run docker compose)
#   - Set up DNS (point your domain to this server's IP first)
#   - Configure SSH keys (do that before running this script)
# ============================================================================

set -euo pipefail

# --- Colors ---
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[setup]${NC} $*"; }

# --- Guard ---
if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash deploy/setup.sh"
    exit 1
fi

# ============================================================================
# 1. System updates
# ============================================================================
info "Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

# ============================================================================
# 2. Docker
# ============================================================================
if command -v docker &> /dev/null; then
    info "Docker already installed: $(docker --version)"
else
    info "Installing Docker..."
    apt-get install -y -qq ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable --now docker
    info "Docker installed: $(docker --version)"
fi

# ============================================================================
# 3. Caddy
# ============================================================================
warn "Current live ingress uses Docker Caddy at ${APP_DIR:-/home/lauureal/apps/knightmind}/deploy/public-caddy, not system Caddy."
warn "Do not overwrite the live ingress without checking OPERATIONS.md."

# ============================================================================
# 4. Firewall (UFW)
# ============================================================================
warn "Skipping UFW changes. Current claw-home UFW is inactive; Docker/Caddy ingress is documented in OPERATIONS.md."
warn "If this is a fresh server, configure firewall manually after confirming the public ingress plan."

# ============================================================================
# 5. App directory
# ============================================================================
APP_DIR="/home/lauureal/apps/knightmind"
if [ -d "${APP_DIR}/.git" ]; then
    info "Repo already cloned at ${APP_DIR}"
else
    info "Cloning repo to ${APP_DIR}..."
    warn "You may need to configure git credentials / deploy keys first."
    warn "Skipping clone — run manually:"
    warn "  git clone <your-repo-url> ${APP_DIR}"
fi

# ============================================================================
# 6. Directories & permissions
# ============================================================================
info "Creating directories..."
mkdir -p /var/log/caddy
mkdir -p /home/lauureal/backups/knightmind

# ============================================================================
# 7. Backup cron
# ============================================================================
CRON_LINE="0 3 * * * ${APP_DIR}/deploy/postgres-backup.sh >> /home/lauureal/backups/knightmind/knightmind-backup.log 2>&1"
if crontab -l 2>/dev/null | grep -q "postgres-backup.sh"; then
    info "Backup cron already exists."
else
    info "Adding daily backup cron (03:00)..."
    # `|| true`: on a fresh server `crontab -l` exits 1 (no crontab yet),
    # which would kill the script under `set -euo pipefail`.
    (crontab -l 2>/dev/null || true; echo "${CRON_LINE}") | crontab -
fi

# ============================================================================
# Done
# ============================================================================
echo ""
info "=========================================="
info "  Server bootstrap complete!"
info "=========================================="
echo ""
info "Next steps:"
info "  1. Clone the repo:          git clone <url> ${APP_DIR}"
info "  2. Create env file:         cp ${APP_DIR}/.env.example ${APP_DIR}/.env.docker"
info "  3. Edit secrets:            nano ${APP_DIR}/.env.docker"
info "  4. Start API/db stack:       cd ${APP_DIR} && docker compose --env-file .env.docker up -d"
info "  5. Start public Caddy:       cd ${APP_DIR}/deploy/public-caddy && docker compose up -d"
info "  6. Verify API:               curl https://api.guessme.world/ops/ping"
info "  7. Run migrations:          cd ${APP_DIR} && docker compose --env-file .env.docker exec api alembic -c services/api/alembic.ini upgrade head"
info "     (always pass --env-file .env.docker: compose only auto-loads a file named .env)"
info "  8. See ops doc:             ${APP_DIR}/OPERATIONS.md"
echo ""
