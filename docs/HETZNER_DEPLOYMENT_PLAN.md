# Hetzner Deployment Plan

## Overview

Consolidate all KnightMind services onto a single Hetzner VPS (64GB RAM), replacing the current split across local dev + Supabase + Render.

## Current Architecture

| Component | Where | Notes |
|-----------|-------|-------|
| Frontend | Local dev | React/Vite, port 5173 |
| API | Local dev / Render | FastAPI, port 8000 |
| Database | Supabase (free tier, Nano) | PostgreSQL, ~65k games migrated from SQLite |
| Stockfish | Local binary | macOS: `brew install stockfish` |

## Target Architecture

| Component | Where | Notes |
|-----------|-------|-------|
| Frontend | Vercel / Cloudflare Pages | Static site, free tier, CDN-distributed |
| API | Hetzner VPS | FastAPI + uvicorn |
| Database | Hetzner VPS | Self-hosted PostgreSQL |
| Stockfish | Hetzner VPS | `apt install stockfish`, same machine as API |

## Why Hetzner for Everything

- **Zero latency** between API, Stockfish, and Postgres (all localhost)
- **No free-tier limits** — Supabase Nano has 500MB storage cap and limited connections
- **Stockfish performance** — 64GB RAM allows large hash tables for stronger analysis
- **One bill** — no juggling Supabase + Render + other services
- **Full control** — no pooler issues, no connection caps, no CPU throttling

## Why NOT Render for the API

- Free/starter tier has 512MB RAM and limited CPU — Stockfish is CPU-intensive
- Would require splitting API and Stockfish into separate services (added complexity)
- Network latency on every eval call between Render (API) and Hetzner (Stockfish)
- Already paying for Hetzner — no reason to pay Render too

## Why Host Frontend Elsewhere

- React/Vite builds to static files — no server needed
- Vercel/Cloudflare Pages serve static sites for free with global CDN
- Keeps frontend deployment simple and fast (push to deploy)
- Decouples frontend releases from backend

## Migration Steps

### 1. Database (Supabase → Hetzner Postgres)

- [ ] Install PostgreSQL on Hetzner: `apt install postgresql`
- [ ] Create database and user
- [ ] `pg_dump` from Supabase, `pg_restore` to Hetzner Postgres
- [ ] Update `DATABASE_URL` in API `.env` to point to localhost
- [ ] Verify data integrity
- [ ] Enable RLS or configure `pg_hba.conf` for security

### 2. API + Stockfish (→ Hetzner)

- [ ] Install Stockfish: `apt install stockfish`
- [ ] Install Python 3.13+ and project dependencies
- [ ] Clone repo, `pip install .`
- [ ] Configure `.env` with local `DATABASE_URL` and `STOCKFISH_PATH`
- [ ] Run with uvicorn behind a reverse proxy (nginx/caddy)
- [ ] Set up HTTPS (Let's Encrypt via certbot or Caddy auto-TLS)
- [ ] Configure systemd service for auto-restart

### 3. Frontend (→ Vercel or Cloudflare Pages)

- [ ] Connect GitHub repo to Vercel/Cloudflare
- [ ] Set build command: `cd apps/web && npm run build`
- [ ] Set output directory: `apps/web/dist`
- [ ] Configure `VITE_API_URL` to point to Hetzner API domain
- [ ] Set up custom domain if desired

### 4. DNS & Networking

- [ ] Point API domain (e.g., `api.knightmind.dev`) to Hetzner IP
- [ ] Point frontend domain (e.g., `knightmind.dev`) to Vercel/Cloudflare
- [ ] Configure CORS on FastAPI to allow frontend domain
- [ ] Firewall: only expose ports 80, 443, 22

### 5. Post-Migration

- [ ] Run `make preflight` to verify everything works
- [ ] Load test the API with concurrent eval requests
- [ ] Set up automated backups for Postgres (pg_dump cron or pgBackRest)
- [ ] Monitor with basic health checks (`/ops/health` endpoint)
- [ ] Decommission Supabase free tier once stable

## Hetzner Server Setup Checklist

```bash
# System
apt update && apt upgrade -y

# PostgreSQL
apt install postgresql postgresql-contrib -y
sudo -u postgres createdb knightmind
sudo -u postgres createuser knightmind_user

# Stockfish
apt install stockfish -y
stockfish --version  # verify

# Python
apt install python3.13 python3.13-venv python3-pip -y

# App
git clone <repo> /opt/knightmind
cd /opt/knightmind
python3.13 -m venv .venv
source .venv/bin/activate
pip install .

# Reverse proxy (Caddy — auto HTTPS)
apt install caddy -y
# Configure Caddyfile for api.knightmind.dev → localhost:8000
```

## Cost Comparison

| Setup | Monthly Cost |
|-------|-------------|
| Current (Supabase free + Render free + local) | $0 (but limited) |
| Target (Hetzner + Vercel free) | ~Hetzner VPS cost only |
| Alternative (Supabase Pro + Render Pro) | ~$50+/mo |

## Open Questions

- [ ] Hetzner server OS — Ubuntu 24.04 LTS recommended
- [ ] Domain setup — custom domain or subdomain?
- [ ] Backup strategy — daily pg_dump to object storage?
- [ ] Monitoring — Uptime Kuma, Grafana, or simple cron health checks?
