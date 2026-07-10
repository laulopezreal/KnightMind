---
last_edited_at: 2026-07-10T14:45:00+02:00
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
  - Network: `knightmind_default` with static Linux bridge name `km-bridge`
  - DB volume: `knightmind_pgdata`
- **Backups:** `/home/lauureal/backups/knightmind/`
  - Take a fresh backup before any Compose, migration, rebuild, or ingress change.
- **Public frontend:** Cloudflare Pages currently serving `https://guessme.world` and `https://knightmind.pages.dev`.
- **Public API:** Caddy container `knightmind-public-caddy` is deployed from `/home/lauureal/apps/knightmind/deploy/public-caddy/`, runs with `network_mode: host`, binds only `${PUBLIC_IP:-65.108.67.53}`, and reverse-proxies `api.guessme.world` to `127.0.0.1:8000`. `https://api.guessme.world/ops/ping` returns JSON and Caddy obtained a Let's Encrypt certificate on 2026-07-10.

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
  - host mapping: `127.0.0.1:8000 -> 8000/tcp`
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

Host-side and public API access are repaired as of 2026-07-10:

```bash
curl --noproxy '*' http://127.0.0.1:8000/ops/health
curl --noproxy '*' http://172.18.0.2:8000/ops/health
curl --noproxy '*' http://api.guessme.world/ops/ping
curl --noproxy '*' https://api.guessme.world/ops/ping
curl --noproxy '*' https://api.guessme.world/ops/health
```

Root-cause evidence gathered during the repair:

- Inside the API container, all worked: `localhost:8000`, `127.0.0.1:8000`, and `172.18.0.2:8000` returned JSON.
- From the DB container, `http://api:8000/ops/ping` and `http://172.18.0.2:8000/ops/ping` returned JSON.
- From the host before repair, `ip route get 172.18.0.2` selected `dev tailscale0 table 52`, not the Docker bridge.
- Tailscale table 52 had a broad default route via `tailscale0` and only a `throw 172.20.0.0/16` Docker/LAN exception. It did not exempt KnightMind's `172.18.0.0/16` Docker network.
- Docker port publishing to `65.108.67.53:80/443` also timed out even though docker-proxy listeners and DNAT rules existed. Host-network Caddy with an explicit `bind {$PUBLIC_IP}` value is the working public-ingress pattern.

Root-level route repair keeps Docker bridge traffic out of the Tailscale exit-node catch-all:

```bash
sudo ip route replace throw 172.17.0.0/16 table 52
sudo ip route replace throw 172.18.0.0/16 table 52
sudo ip route replace throw 172.19.0.0/16 table 52
sudo ip route replace throw 172.20.0.0/16 table 52
sudo ip rule add pref 91 iif km-bridge lookup main
sudo ip route flush cache
```

Helper script prepared at:

`/home/lauureal/apps/knightmind/deploy/tailscale-docker-route-exceptions.sh`

Running it without root fails with `RTNETLINK answers: Operation not permitted`; `sudo -n` also fails because this session does not have a sudo credential. After root applies it, verify:

```bash
ip route get 172.18.0.2
curl --noproxy '*' http://127.0.0.1:8000/ops/ping
curl --noproxy '*' https://api.guessme.world/ops/ping
```

Expected route for `172.18.0.2` should mention `km-bridge`, not `tailscale0 table 52`. Both curl checks should return `{"status":"pong"}`.

Verification after Lau ran the root helper on 2026-07-10:

- Tailscale table 52 now contains `throw` routes for `172.17.0.0/16`, `172.18.0.0/16`, `172.19.0.0/16`, and `172.20.0.0/16`.
- `ip rule show` contains bridge-to-main-table rules for active Docker bridges. KnightMind uses static bridge name `km-bridge`.
- `ip route get 172.18.0.2` should select the Docker bridge, not `tailscale0`.
- `curl --noproxy '*' http://127.0.0.1:8000/ops/ping` returns `200 {"status":"pong"}`.
- `curl --noproxy '*' http://127.0.0.1:8000/ops/health` returns `200` JSON with `db`, `worker`, and `stockfish` all `ok`.
- `curl --noproxy '*' http://172.18.0.2:8000/ops/health` also returns `200` JSON.
- Public `curl --noproxy '*' https://api.guessme.world/ops/ping` returns `200 {"status":"pong"}`.
- Public `curl --noproxy '*' https://api.guessme.world/ops/health` returns `200` JSON with `db`, `worker`, and `stockfish` all `ok`.
- Compose remains healthy: `knightmind-api-1 api running healthy`, `knightmind-db-1 db running healthy`.

Host-local API is repaired. Public API ingress is repaired as of 2026-07-10: `https://api.guessme.world/ops/ping` returns JSON.

## Public surface status after Caddy deploy

Frontend:

- `https://guessme.world` returns the KnightMind frontend.
- `https://knightmind.pages.dev` also returns the frontend.
- The frontend bundle calls `https://api.guessme.world`.

API:

- `api.guessme.world` DNS was moved off the old `openclaw` Cloudflare Tunnel and now resolves to `65.108.67.53`.
- Caddy runs as `knightmind-public-caddy` from `/home/lauureal/apps/knightmind/deploy/public-caddy/` with `network_mode: host`, `bind {$PUBLIC_IP}` defaulting to `65.108.67.53`, and `reverse_proxy 127.0.0.1:8000`.
- Verified on 2026-07-10: `http://api.guessme.world/ops/ping` redirects/reaches API and `https://api.guessme.world/ops/ping` returns `{"status":"pong"}`.
- Verified on 2026-07-10: `https://api.guessme.world/ops/health` returns `{"ok":true,"db":"ok","worker":"ok","stockfish":"ok",...}`.
- Caddy obtained a Let's Encrypt certificate for `api.guessme.world` after the DNS change and host-network redeploy.

Notes:

- Docker port publishing to `65.108.67.53:80/443` via docker-proxy timed out even though DNAT/listeners existed. Host-network Caddy with a configurable public-IP bind is the working pattern on this host.
- The `*.guessme.world -> pixie.porkbun.com` wildcard remains in Cloudflare. It does not affect `api.guessme.world` because the explicit `api` record wins. Do not delete it until there is a separate wildcard cleanup decision.

Public app health is considered restored when both frontend loading and API JSON checks pass. API JSON checks passed on 2026-07-10; browser-level frontend flow should still be checked separately after any frontend rebuild.

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

## Current open follow-up

No public API ingress repair remains after 2026-07-10. Useful follow-ups are operational hardening, not emergency repair:

1. Persist the Tailscale/Docker route helper as a systemd oneshot that runs after Docker and Tailscale.
2. Decide separately whether to keep or remove the `*.guessme.world -> pixie.porkbun.com` wildcard fallback.
3. Verify the browser frontend flow against `https://api.guessme.world` after any frontend rebuild or Cloudflare Pages deploy.
4. If `knightmind.dev` becomes the canonical production domain later, update DNS, Caddy, SEO metadata, and this operations doc together.
