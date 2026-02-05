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
#   3. Installs Caddy (reverse proxy with auto-HTTPS)
#   4. Configures UFW firewall (22, 80, 443 only)
#   5. Clones the repo to /opt/knightmind
#   6. Sets up backup cron
#   7. Creates log directories
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
if command -v caddy &> /dev/null; then
    info "Caddy already installed: $(caddy version)"
else
    info "Installing Caddy..."
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy
    info "Caddy installed: $(caddy version)"
fi

# ============================================================================
# 4. Firewall (UFW)
# ============================================================================
info "Configuring firewall..."
apt-get install -y -qq ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp   comment 'SSH'
ufw allow 80/tcp   comment 'HTTP (Caddy redirect)'
ufw allow 443/tcp  comment 'HTTPS (Caddy)'
ufw --force enable
info "Firewall configured: $(ufw status | head -1)"

# ============================================================================
# 5. App directory
# ============================================================================
APP_DIR="/opt/knightmind"
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
mkdir -p /var/backups/knightmind

# ============================================================================
# 7. Backup cron
# ============================================================================
CRON_LINE="0 3 * * * ${APP_DIR}/deploy/postgres-backup.sh >> /var/log/knightmind-backup.log 2>&1"
if crontab -l 2>/dev/null | grep -q "postgres-backup.sh"; then
    info "Backup cron already exists."
else
    info "Adding daily backup cron (03:00)..."
    (crontab -l 2>/dev/null; echo "${CRON_LINE}") | crontab -
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
info "  4. Deploy Caddy config:     cp ${APP_DIR}/deploy/Caddyfile /etc/caddy/Caddyfile"
info "  5. Install systemd service: cp ${APP_DIR}/deploy/knightmind-api.service /etc/systemd/system/"
info "  6. Start the app:           systemctl enable --now knightmind-api"
info "  7. Run migrations:          cd ${APP_DIR} && docker compose exec api alembic -c services/api/alembic.ini upgrade head"
info "  8. Reload Caddy:            systemctl reload caddy"
echo ""
