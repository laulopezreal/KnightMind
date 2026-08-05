---
last_edited_at: 2026-08-06T00:49:34+02:00
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
- **Backups:** `/home/lauureal/backups/knightmind/` — the only backup location. See "Backup first rule".
  - Take a fresh backup before any Compose, migration, rebuild, ingress change, or release merge.
  - `/home/lauureal/apps/knightmind/backups/` is NOT a backup location. Two stray dumps lived there until 2026-08-06 and were moved here; the directory was removed. Backups do not belong inside the deploy clone, which `deploy.yaml` runs `git reset --hard` in.
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
  - host mapping: `127.0.0.1:5432 -> 5432/tcp`
  - Docker volume: `knightmind_pgdata -> /var/lib/postgresql/data`

Known live database contents at restoration time:

- `games`: 370
- `puzzles`: 60
- `jobs`: 2
- `rating_snapshots`: 2
- Alembic current/head in live image: `a1b2c3d4e5f6`

## Outbound dependencies

The API makes egress calls to exactly three third parties. All are best-effort:
a failure degrades one feature and never takes the API down.

| Host | Used by | On failure |
| --- | --- | --- |
| `api.chess.com` | game import, rating snapshots | the import or snapshot fails and is reported to the caller |
| `explorer.lichess.ovh` | `/openings/baseline` | serves a stale cached row if one exists, else 503; the Openings page simply omits the comparison |
| `api.anthropic.com` | AI diagnosis prose | diagnosis falls back to the rules-based cause; the written explanation is simply absent |

This table must stay in step with `ALLOWED_HOST_SUFFIXES` in
`deploy/egress-proxy/connect_proxy.py`, currently
`("chess.com", "anthropic.com", "lichess.ovh")`. A host missing from that
allowlist is unreachable from the container regardless of what the app expects.

Notes for the lichess explorer:

- **No user data leaves the box.** The request carries a chess position and a
  rating band. No username, game, or identifier.
- **Answers are cached in `opening_explorer_cache`**, keyed by position and
  band and shared across all accounts (the rows are public aggregates, not
  anyone's data). TTL is 30 days; a stale row is still served when the explorer
  is unreachable, because these figures move at the speed of millions of games.
- **Blocking egress to it is a supported configuration.** The Openings page
  drops the "vs expected" line and stays fully usable.
- Per-principal rate limit: `RATE_LIMIT_OPENINGS_BASELINE` (default 60/min).

Notes for the Anthropic API:

- **Chess data does leave the box here**, unlike the explorer: the prompt carries
  position and game context so the model can explain the mistake. No account
  credentials are sent.
- **Kill switch**: `KNIGHTMIND_AI_DIAGNOSIS=0` stops the model call entirely — no
  request, no audit row, diagnosis falls back to rules.
- **Absent key is safe.** `ANTHROPIC_API_KEY` is read at call time, never at
  startup, so an unset key degrades to rules-only instead of blocking boot.
- **Spend ceilings** are counted from `diagnosis_audit_log`:
  `KNIGHTMIND_AI_DAILY_CAP_USER` (bounds one backfill) and
  `KNIGHTMIND_AI_DAILY_CAP_GLOBAL` (backstop against a runaway loop).
- **Prompts and responses are retained** in `diagnosis_audit_log` for incident
  review and swept by the session-cleanup loop.
- **Blocking egress to it is a supported configuration**, same as the explorer:
  diagnoses keep working, just without the written explanation.

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

**Merging a `dev` -> `main` release PR counts.** `deploy.yaml` fires on push to `main` and runs build -> `alembic upgrade head` -> traffic switch, unattended, and takes no backup of its own. The backup is a manual step *before* the merge; nothing in the pipeline waits for it.

### The one location, the one command

Backups live in **`/home/lauureal/backups/knightmind/`** and nowhere else. Take one with:

```bash
cd /home/lauureal/apps/knightmind && ./deploy/postgres-backup.sh
```

That is the only sanctioned mechanism. It writes `knightmind_<YYYYMMDD>_<HHMMSS>.sql.gz` plus a matching `.sha256`, verifies the gzip stream before reporting success, and applies the retention policy below.

### Retention

Two classes, distinguished by filename, each with an age limit and a floor:

| class | shape | kept | floor |
| --- | --- | --- | --- |
| routine | `knightmind_<date>_<time>.sql.gz`, `knightmind-db-<ISO>.dump` | `RETENTION_DAYS`, default 14 | `MIN_KEEP`, default 3 |
| labelled | a word between prefix and timestamp, e.g. `knightmind-pre-release-pr343-...` | `MILESTONE_RETENTION_DAYS`, default 90 | `MILESTONE_MIN_KEEP`, default 2 |

The floors matter because backups here are manual: without them a quiet fortnight ages out every routine copy and leaves only whatever the current run just wrote. The two classes are counted independently, so a burst of routine dumps cannot push labelled ones past their floor.

**Label a dump when you take it before something risky**, and say what it precedes — `pre-release-pr343`, `pre-strip-flag`, `pre-hardening`. That is what buys it the longer horizon. Rename an existing dump to promote it, and rewrite its `.sha256` to match: the checksum records a bare filename, so a rename without it makes `sha256sum -c` fail on an intact file.

**Labelled does not mean immortal.** Until 2026-08-06 they were exempt entirely, and the directory accumulated four dumps of a single schema revision, each kept forever because it had a nice name. A dump's restore value decays as the schema moves past it: recovering to a revision several migrations back means replaying all of them onto data that old. When several labelled dumps share a revision, prune by hand — the script cannot tell which label matters.

**Before deleting a dump by hand, grep for it in BOTH doc trees**, because a dump cited somewhere is a dump someone expects to find:

```bash
grep -rl "<dump-filename>" ~/git/knightmind/ ~/projects/knightmind/
```

The repo alone is not enough. On 2026-08-06 a pruned dump turned out to be cited in `~/projects/knightmind/handoffs/` as a session's verified-restorable backup, and the reference was left dangling. If a citation exists and the dump is going anyway, annotate the citing document with a substitute — and verify the substitute restores before naming it, rather than assuming a neighbouring dump is equivalent.

To see what a dump would restore to:

```bash
zcat BACKUP.sql.gz | grep -A1 'COPY public.alembic_version' | sed -n 2p   # plain SQL
pg_restore -f - < BACKUP.dump | grep -A1 'COPY public.alembic_version' | sed -n 2p   # -Fc
```

There is **no cron entry and no systemd timer**, so a backup exists only when someone runs this. Do not add a second mechanism: until 2026-08-06 this section documented an inline `pg_dump -Fc` instead of the script, so the directory accumulated two formats that need two different restore commands and only one of which was pruned.

### Restoring

The two formats in the directory restore differently. Check the extension first.

```bash
# knightmind_*.sql.gz  -- plain SQL, written by the script
zcat BACKUP.sql.gz | docker exec -i knightmind-db-1 psql -U knightmind -d knightmind -v ON_ERROR_STOP=1

# knightmind-db-*.dump -- legacy pg_dump custom format (-Fc), taken by hand before 2026-08-06
docker exec -i knightmind-db-1 pg_restore -U knightmind -d knightmind --clean --if-exists < BACKUP.dump
```

### A dump is not verified until it has been restored

`sha256sum -c` proves the file did not rot. It does not prove the dump is loadable or complete. Before relying on a backup for a risky change, restore it into a throwaway container and compare row counts to live:

```bash
docker run -d --name km-restorecheck -e POSTGRES_USER=knightmind -e POSTGRES_PASSWORD=knightmind \
  -e POSTGRES_DB=restore_test postgres:16
# wait for a real connection, not pg_isready -- that answers during initdb's temporary server
until docker exec km-restorecheck psql -U knightmind -d restore_test -c 'SELECT 1' >/dev/null 2>&1; do sleep 1; done
zcat BACKUP.sql.gz | docker exec -i km-restorecheck psql -U knightmind -d restore_test -v ON_ERROR_STOP=1
docker exec km-restorecheck psql -U knightmind -d restore_test -At -c 'SELECT count(*) FROM games;'
docker rm -f km-restorecheck
```

Counting `COPY` blocks in the gzipped dump is not a substitute; it over-counts and has given wrong figures for every table.

Most recent restoration safety backup:

- `/home/lauureal/backups/knightmind/knightmind-pre-restoration-20260710T113350+0200.dump`
- SHA256: `80797430297a808f54f97ba364f818f65c676350f4a5470c86772fb4e26660d6`

Most recent hardening safety backup before the 2026-07-20 DB/API recreation:

- `/home/lauureal/backups/knightmind/knightmind-pre-hardening-20260720T145709+0200.dump`
- SHA256 recorded in `/home/lauureal/backups/knightmind/knightmind-pre-hardening-20260720T145709+0200.dump.sha256` and verified with `sha256sum -c` before Compose changes.

## Security hardening status

Applied on 2026-07-20 after taking and verifying the backup above:

- Postgres is now loopback-only: `ss -tlnp | grep 5432` shows only `127.0.0.1:5432`, and `docker compose --env-file .env.docker ps db` shows `127.0.0.1:5432->5432/tcp`.
- Public Caddy strips spoofed Tailscale identity headers. Public probe `curl -H 'Tailscale-User-Login: spoof@x' https://api.guessme.world/ops/status` returns `404 {"detail":"Not Found"}`.
- API image was rebuilt and recreated with the unprivileged Docker user `knightmind`; `docker compose --env-file .env.docker exec -T api id` returns `uid=100(knightmind) gid=101(knightmind)`.
- API and DB are healthy after recreation; public `https://api.guessme.world/ops/ping` returns `200 {"status":"pong"}`.
- Multi-user auth remains intentionally disabled until Lau provisions the account and flips `KNIGHTMIND_REQUIRE_AUTH=true` in `.env.docker`.

Applied on 2026-08-05, after taking backup `knightmind-pre-strip-flag-20260805T103703+0200.dump`:

- **`KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS=1` is now set** in `.env.docker`. Before this,
  the flag defaulted OFF and `/puzzles/due` shipped `best_move_uci`,
  `accept_moves_uci`, `played_move_uci` and `solution_pv` straight to the browser —
  the answer arrived with the question.
- The flag was OFF by design, not by oversight: it let the API deploy before the
  grading frontend, in either order. It was safe to flip once the deployed frontend
  stopped reading solutions from the training payload. Confirmed against the **live**
  Cloudflare Pages bundle (`assets/puzzles-*.js` contains `/puzzles/{id}/check`,
  `/puzzles/{id}/reveal`, and `reveal:"true"`), not just the repo source — the two can
  differ, and only the deployed bundle decides whether flipping breaks users.
- Verified after the flip: `/puzzles/due` omits all four fields; `/puzzles/list` and
  `/puzzles/{id}` return them as null; `/puzzles/{id}?reveal=true` still populates them
  (the Library detail page depends on this); `POST /reveal` returns the solution (the
  training board depends on this); `POST /check` still grades correctly.
- **To roll back**, set `KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS=0` (or remove the line) and
  run `docker compose --env-file .env.docker up -d`. No rebuild or migration needed —
  the flag is read at request time.
- Server-side verification (`/check`, `/reveal`, `/review`) is unaffected by this flag
  in either position. Flipping it closed an information leak; it never was the thing
  preventing forged pass/fail results.
- `README.md` states that puzzle solutions are not sent with the training payload.
  That statement was false while the flag was OFF and is true now — if the flag is ever
  turned back off, correct the README in the same change.

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

Root-level route repair keeps Docker bridge traffic out of the Tailscale exit-node catch-all.

First diagnostic order when host/public/tailnet API calls time out but the API container is healthy:

```bash
systemctl status tailscale-docker-route-exceptions.service --no-pager -l
ip route get 172.18.0.2
ip route show table 52 | grep 'throw 172.'
ip rule show | grep 'iif .* lookup main'
```

The durable helper is:

`/home/lauureal/apps/knightmind/deploy/tailscale-docker-route-exceptions.sh`

It dynamically discovers Docker bridge networks and Linux bridge addresses, adds `throw` routes for each local Docker bridge subnet into Tailscale table 52, and adds bridge-to-main-table policy rules for all Docker bridges. It must run as root.

The matching systemd unit template is tracked beside it:

`/home/lauureal/apps/knightmind/deploy/tailscale-docker-route-exceptions.service`

Install once as root:

```bash
sudo cp /home/lauureal/apps/knightmind/deploy/tailscale-docker-route-exceptions.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tailscale-docker-route-exceptions.service
```

Manual one-shot repair is only the fallback if the service is missing or inactive:

```bash
sudo /home/lauureal/apps/knightmind/deploy/tailscale-docker-route-exceptions.sh
```

Running it without root fails; `sudo -n` also fails from Hermes when the session does not have a sudo credential. After root applies it, verify:

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

## Enabling AI diagnosis on an existing corpus

`ANTHROPIC_API_KEY` goes in `.env.docker` (mode `0600`, never printed or
committed). `KNIGHTMIND_AI_DIAGNOSIS` defaults ON, so the key is the only
required setting; `KNIGHTMIND_AI_DIAGNOSIS=0` is the kill switch.

The container must be recreated — a restart does not re-read the env file:

```bash
docker compose --env-file .env.docker up -d --force-recreate api
```

Confirm it arrived without printing it:

```bash
docker compose --env-file .env.docker exec -T api printenv ANTHROPIC_API_KEY | wc -c
```

**A key added after the backfill does not enrich what already exists.**
Staleness is a predicate over data versions (`extraction_version`,
`rule_version`); setting a key moves neither, so diagnoses produced while the
key was absent stay rules-only and no ordinary run revisits them. Check the
backlog and sweep it once:

```bash
curl -s "$API/users/$USER/diagnosis/pending"          # {"pending":N,"unenriched":M}
curl -sX POST "$API/users/$USER/diagnose?scope=reenrich"
```

The sweep respects the same daily caps as any other run
(`KNIGHTMIND_AI_DAILY_CAP_USER`, default 500) and converges: an enriched row
records its `model_version` and drops out of the query. Re-running is how a
rejected response gets retried — that is deliberately a manual decision, since
a standing rule would re-attempt a persistently-rejected puzzle every day.

## Unsafe without explicit approval

Do not run these without a fresh DB backup and Lau's explicit approval:

```bash
docker compose --env-file .env.docker down
docker compose --env-file .env.docker up -d
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker exec api alembic -c services/api/alembic.ini upgrade head
docker volume rm knightmind_pgdata
```

## Migration downgrade preconditions

Some migrations are not freely reversible. Check here before running `alembic downgrade`.

- **`b2c3d4e5f6a7` (per-user game ownership)** — the downgrade collapses the composite `games` primary key `(game_id, username)` back to `game_id` alone. It is safe **only while no game is dual-owned**. Once both participants of a physical game have imported it, two rows share one `game_id` and the downgrade fails on the single-column uniqueness (Postgres: duplicate key; SQLite: UNIQUE constraint on the recreated table). Before downgrading, confirm there are no duplicates and resolve any by hand — dropping one is destructive:
  ```sql
  SELECT game_id, COUNT(*) FROM games GROUP BY game_id HAVING COUNT(*) > 1;
  ```

## Current open follow-up

The Tailscale/Docker route regression recurred on 2026-08-01 because table 52 lost the Docker `throw` routes and bridge rules. The unit is now installed, `enabled` and `active` on claw-home, and the route was verified back on `km-bridge`.

1. **A deploy reverts uncommitted work in the deploy checkout.** `deploy.yaml`
   runs `git reset --hard origin/main` before building, so any local edit to
   `deploy/tailscale-docker-route-exceptions.sh` is silently replaced with
   whatever `main` carries — while the installed unit keeps executing that
   path. Land script changes in `main` before deploying, or the running fix
   quietly regresses to an older version.
2. Decide separately whether to keep or remove the `*.guessme.world -> pixie.porkbun.com` wildcard fallback.
3. Verify the browser frontend flow against `https://api.guessme.world` after any frontend rebuild or Cloudflare Pages deploy.
4. If `knightmind.dev` becomes the canonical production domain later, update DNS, Caddy, SEO metadata, and this operations doc together.
