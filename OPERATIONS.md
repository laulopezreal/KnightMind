---
last_edited_at: 2026-07-10T11:40:25+02:00
---
# KnightMind Operations

## Canonical paths

- App/deploy SSOT: `/home/lauureal/apps/knightmind/`
- Project docs/status capsule: `/home/lauureal/projects/knightmind/`
- Upstream repo: `https://github.com/laulopezreal/KnightMind`
- Live DB backup folder: `/home/lauureal/backups/knightmind/`

## Infra organization from now on

There is exactly one intended KnightMind app/deploy instance on claw-home.

- **Source and deploy root:** `/home/lauureal/apps/knightmind/`
  - This is the only place to run KnightMind `docker compose` commands.
  - This repo may be ahead of GitHub with local operations-only commits.
  - `.env.docker` lives here, is ignored, must stay mode `0600`, and must never be printed or committed.
- **Project docs:** `/home/lauureal/projects/knightmind/`
  - Planning/status only. Do not run app commands from here.
- **Runtime:** Docker Compose project `knightmind`
  - Services: `api`, `db`
  - Containers: `knightmind-api-1`, `knightmind-db-1`
  - Network: `knightmind_default`
  - DB volume: `knightmind_pgdata`
- **Backups:** `/home/lauureal/backups/knightmind/`
  - Take a fresh backup before any Compose, migration, rebuild, or ingress change.
- **Public frontend:** Cloudflare Pages currently serving `https://guessme.world` and `https://knightmind.pages.dev`.
- **Public API:** not considered repaired until a chosen API hostname returns JSON from `/ops/health` and browser API calls work.

Do not create `/opt/knightmind`, `/home/lauureal/git/knightmind`, another Compose project, another Postgres volume, or another API container unless Lau explicitly approves a migration plan.

## Instance audit on 2026-07-10

Read-only scan found no second KnightMind runtime on claw-home.

Found live runtime:

- Docker containers: `knightmind-api-1`, `knightmind-db-1`
- Docker image: `knightmind-api:latest`
- Docker volume: `knightmind_pgdata`
- Docker network: `knightmind_default`
- Compose project: `knightmind`, config `/home/lauureal/apps/knightmind/docker-compose.yml`
- API process: one Uvicorn process for `services.api.main:app` on port 8000 inside the container
- DB processes: Postgres sessions for the KnightMind DB

No duplicate found in:

- `/home/lauureal/git/`
- `/opt/`
- `/var/www/`
- `/srv/`
- systemd services or unit files
- Docker containers/images/volumes/networks beyond the expected `knightmind` set
- user/system timers

Related but not a KnightMind app instance:

- `/home/lauureal/.openclaw/workspace/scripts/chess_daily_backup.sh` is a personal Chess.com daily rating/journal cron, not KnightMind infrastructure.
- `/home/lauureal/Lauland/Work/Pet Projects/Knightmind/` is an Obsidian/project idea note, not runtime infrastructure.
- Historical OpenClaw dream/memory/TODO references mention the old migration, but they are not live app instances.

## Current live stack

Docker Compose project: `knightmind`

Live containers:

- `knightmind-api-1`
  - image: `knightmind-api`
  - command: `uvicorn services.api.main:app --host 0.0.0.0 --port 8000 --workers 1`
  - host mapping: `0.0.0.0:8000 -> 8000/tcp`
  - Docker healthcheck: `curl -f http://localhost:8000/ops/health || exit 1`
- `knightmind-db-1`
  - image: `postgres:16-alpine`
  - host mapping: `0.0.0.0:5432 -> 5432/tcp`
  - Docker volume: `knightmind_pgdata -> /var/lib/postgresql/data`

Known live database contents at restoration time:

- `games`: 370
- `puzzles`: 60
- `jobs`: 2
- `rating_snapshots`: 2
- Alembic current/head in live image: `a1b2c3d4e5f6`

## Environment file

The production-like Compose env file is:

`/home/lauureal/apps/knightmind/.env.docker`

It was reconstructed from the live containers on 2026-07-10 and is intentionally untracked. Keep it mode `0600`.

Do not print secrets into chat, logs, docs, or commits.

Every Compose command must pass the env file explicitly:

```bash
docker compose --env-file .env.docker ps
```

## Backup first rule

Before any command that can recreate, stop, remove, migrate, or rebuild the live stack, create a fresh DB backup.

Current backup command:

```bash
TS=$(date +%Y%m%dT%H%M%S%z)
BACKUP_DIR=/home/lauureal/backups/knightmind
mkdir -p "$BACKUP_DIR"
docker exec knightmind-db-1 sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$BACKUP_DIR/knightmind-db-$TS.dump"
sha256sum "$BACKUP_DIR/knightmind-db-$TS.dump" > "$BACKUP_DIR/knightmind-db-$TS.dump.sha256"
```

Most recent restoration safety backup:

- `/home/lauureal/backups/knightmind/knightmind-db-20260710T113350+0200.dump`
- SHA256: `80797430297a808f54f97ba364f818f65c676350f4a5470c86772fb4e26660d6`

## Health checks

Internal container health currently passes:

```bash
docker exec knightmind-api-1 curl -sS http://localhost:8000/ops/health
```

Host-side access was broken during the 2026-07-10 investigation:

```bash
curl --noproxy '*' http://127.0.0.1:8000/ops/health
curl --noproxy '*' http://172.18.0.2:8000/ops/health
```

Root-cause evidence gathered later the same session:

- Inside the API container, all worked: `localhost:8000`, `127.0.0.1:8000`, and `172.18.0.2:8000` returned JSON.
- From the DB container, `http://api:8000/ops/ping` and `http://172.18.0.2:8000/ops/ping` returned JSON.
- From the host, `ip route get 172.18.0.2` selected `dev tailscale0 table 52`, not the Docker bridge.
- Tailscale table 52 had a broad default route via `tailscale0` and only a `throw 172.20.0.0/16` Docker/LAN exception. It did not exempt KnightMind's `172.18.0.0/16` Docker network.

Current hypothesis: host-to-KnightMind-container access is broken because the Tailscale exit-node routing table hijacks `172.18.0.0/16`, so Docker's host-side proxy cannot reach the API container. Fix requires root-level routing/Tailscale/Docker network change, not application code.

Approved repair path A, matching the working Open Wearables pattern, is to add `throw` exceptions in Tailscale table 52 for Docker bridge subnets so host traffic falls back to the Docker bridge routes:

```bash
sudo ip route replace throw 172.17.0.0/16 table 52
sudo ip route replace throw 172.18.0.0/16 table 52
sudo ip route replace throw 172.19.0.0/16 table 52
sudo ip route replace throw 172.20.0.0/16 table 52
sudo ip route flush cache
```

Helper script prepared at:

`/home/lauureal/apps/knightmind/deploy/tailscale-docker-route-exceptions.sh`

Running it without root fails with `RTNETLINK answers: Operation not permitted`; `sudo -n` also fails because this session does not have a sudo credential. After root applies it, verify:

```bash
ip route get 172.18.0.2
curl --noproxy '*' http://127.0.0.1:8000/ops/ping
```

Expected route should mention `br-46f5c9c9df08`, not `tailscale0 table 52`, and curl should return `{"status":"pong"}`.

## Public surface status at restoration time

Frontend:

- `https://guessme.world` returns the KnightMind frontend.
- `https://knightmind.pages.dev` returns the same frontend.

API:

- The frontend bundle calls `https://api.guessme.world`.
- `https://api.guessme.world/ops/health` returned Cloudflare error `1033` during investigation.
- `https://guessme.world/ops/health` and `https://guessme.world/api/ops/health` returned frontend HTML, not API JSON.
- `https://knightmind-api.onrender.com/ops/health` timed out from this host.
- Public API repair should happen after host-side Docker access is repaired, then a single canonical API hostname should be wired to the claw-home API.

Do not call the public app fully healthy until browser API calls are verified against JSON endpoints.

## Safe inspection commands

```bash
cd /home/lauureal/apps/knightmind
git status --short --branch
docker compose --env-file .env.docker ps
docker inspect knightmind-api-1 knightmind-db-1 --format '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
docker logs --tail 80 knightmind-api-1
docker exec knightmind-db-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select tablename from pg_tables where schemaname='"'"'public'"'"' order by tablename;"'
```

## Unsafe without explicit approval

Do not run these without a fresh DB backup and Lau's explicit approval:

```bash
docker compose --env-file .env.docker down
docker compose --env-file .env.docker up -d
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker exec api alembic -c services/api/alembic.ini upgrade head
docker volume rm knightmind_pgdata
```

## Repair notes

The live containers were originally created from the now-restored path `/home/lauureal/apps/knightmind`, but before 2026-07-10 that path was missing. Recreating the checkout at the Docker label path is intended to reduce future deployment drift.

Next repair work should focus on:

1. Root-level inspection of Docker bridge/firewall/NAT rules.
2. Choosing canonical domains: `guessme.world` and `api.guessme.world` versus `knightmind.dev` and `api.knightmind.dev`.
3. Wiring public API ingress only after host-side `127.0.0.1:8000/ops/health` works.
4. Rebuilding the frontend with the chosen API base after API ingress is verified.
