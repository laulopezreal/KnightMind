---
last_edited_at: 2026-09-03T16:38:58+02:00
---
# KnightMind Operations

## Canonical paths

- App/deploy SSOT: `/home/lauureal/apps/knightmind/`
- Project docs/status capsule: `/home/lauureal/projects/knightmind/`
- Upstream repo: `https://github.com/laulopezreal/KnightMind`
- Live DB backup folder: `/home/lauureal/backups/knightmind/`

## Frontend worker readiness

Before dispatching frontend work, run the repository preflight from the clean linked
worktree. The supported preparation path installs from that worktree's lockfile, never
from the canonical checkout:

```bash
WT=/absolute/path/to/isolated-worktree
REF=$(git -C "$WT" rev-parse HEAD)
python "$WT/scripts/frontend_worktree_preflight.py" \
  --worktree "$WT" --ref "$REF" --prepare \
  --test-target src/utils/trainEntry.test.ts
python "$WT/scripts/frontend_worktree_preflight.py" \
  --worktree "$WT" --ref "$REF" --verify-marker
```

`--prepare` runs `npm ci --ignore-scripts` inside `apps/web`, then the preflight checks
the clean/ref state, local dependency evidence, and an actual passing focused Vitest
result. The marker becomes stale after HEAD, lockfile, test-target, or dependency drift.
This is a practical workflow readiness control. It is not an adversarial sandbox,
integrity attestation, or defense against a same-UID actor able to alter the candidate
or its evidence.

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
  - Docker healthcheck: `curl -f http://localhost:8000/ops/ready || exit 1`
    - **`/ops/ready`, not `/ops/health`.** This container's status describes
      THIS container: database reachable and Stockfish available. A dead queue
      does NOT mark the API unhealthy, deliberately — the worker is a separate
      container, and stopping it should not make the API look broken while it
      is serving traffic perfectly.
    - the **deploy gate** curls `/ops/health` directly, so a dead queue is
      still caught there. `/ops/health` adds the `worker` field, read from the
      heartbeat rows: `stale` = up but stopped looping, `not_running` = no
      worker has ever beaten, `stalled` = beating but the queue is not moving,
      `unknown` = the heartbeat table could not be read. All return 503.
    - so: **a 503 from `/ops/health` does not mean the API is down**, and a
      healthy API container does not mean jobs are running. They answer
      different questions on purpose.
  - `--workers 1` is deliberate, and the reason is the connection budget.

    Postgres allows **97** non-superuser connections (`max_connections=100`
    minus 3 reserved). Current demand:

    | consumer | ceiling | why |
    |---|---|---|
    | API | 50 | `POOL_SIZE 10 + MAX_OVERFLOW 40`, matching anyio's 40-thread pool — each thread can hold a session |
    | worker | 10 | sized down in compose; it runs one job at a time |
    | deploys / operators | ~10 | `alembic upgrade`, `compose run` one-offs, `psql` |
    | **total** | **70** | 27 spare |

    A second uvicorn worker adds another 50 and does not fit. It is also not the
    concurrency lever it appears to be: the 40-thread pool already gives 40-way
    concurrency for blocking handlers inside one process, so more processes
    mostly buy more connections.

    If request concurrency genuinely runs out, in order: raise the threadpool
    and the DB pool together in one process; then put PgBouncer (transaction
    mode) in front so app-side pooling stops mapping 1:1 onto server
    connections; raise `max_connections` last, since each connection is a
    backend process with its own memory.
- `knightmind-worker-1`
  - image: `knightmind-api` — the *same tag* as the API, set explicitly on both
    services. Without that, compose builds a second image and a deploy that
    rebuilds only `api` leaves this container on stale code indefinitely.
  - command: `python -m services.api.worker_main`
  - host mapping: none — it serves nothing
  - liveness: it writes a row to `worker_heartbeats` on its own timer (every 5s,
    independent of the job loop, so a long job does not read as death); the API
    reports that as the `worker` field of `/ops/health`. The image's HTTP
    healthcheck is explicitly disabled for this container — it serves no HTTP,
    so it would curl a dead port and sit permanently `unhealthy`.
  - it also runs the hourly housekeeping loop: abandoned-session cleanup, the
    AI audit retention sweep, the rate-limit hit purge and the dead-heartbeat
    purge. That lives with whichever process runs the worker, so there is
    exactly one of it. `KNIGHTMIND_WORKER_DISABLED=true` is honoured by both
    containers: the API stops judging worker health and the worker idles
    without claiming anything. **Housekeeping keeps running while it idles** —
    it is started before the switch is read, deliberately. Sweeping is not job
    claiming, and the switch documented for a spend incident used to be the
    same switch that stopped the rate-limit purge while the API kept writing to
    that table.
  - **the worker idles rather than exits when disabled.** `restart:
    unless-stopped` restarts a container on ANY exit status, including 0, so
    exiting cleanly here produced a crash loop rather than a quiet container.

    **The two ways to stop it differ, and neither is strictly better.**

    | | housekeeping | `/ops/health` | deploy gate |
    |---|---|---|---|
    | `KNIGHTMIND_WORKER_DISABLED=true` | keeps running | `disabled`, **200 OK** | passes |
    | `docker compose stop worker` | **stops** | `not_running`, 503 | fails |

    The flag keeps the four sweeps alive — including the AI-audit retention
    sweep and the `rate_limit_hits` purge, which the API keeps feeding
    regardless. The cost is that health reports `disabled` as healthy, so a
    paused queue is invisible to the gate and to monitoring. Stopping the
    container is louder but silently stops collecting.

    Use the flag for a long pause you are watching (a spend incident); use
    `stop worker` when you want the outage to be obvious. Either way the queue
    is not being served — `/ops/health` alone will not remind you.
  - **green health does not mean jobs are completing.** `/ops/health` reads the
    freshest heartbeat, and the beat is written ~0.6s after the process starts,
    before any job is attempted. `worker: "stalled"` (HTTP 503) catches the
    case where jobs have been QUEUED for over five minutes and no job holds a
    live lease — a worker that is beating and polling but getting nothing done.

    What it does NOT tell you is when a job last *finished*. Nothing does: no
    endpoint reports time-since-last-terminal-state. If throughput is the
    question, ask `/ops/status` for `active_job` and `recent_jobs` and read the
    timestamps yourself.

    A job orphaned in RUNNING (worker OOM-killed or SIGKILLed mid-job) no
    longer hides a stalled queue — the stall check ignores RUNNING rows whose
    15-minute lease has expired, the same lease crash recovery reclaims on.
    Recovery itself now runs hourly with the other sweeps, not only at worker
    startup, so an orphan is reclaimed without restarting the container.

  - **there is no operator command to clear a stuck queue, and the wait is up
    to ~75 minutes.** Recovery needs the 15-minute lease to expire AND the
    hourly housekeeping sweep to come round — and the sweep runs its passes
    then sleeps 3600s, so a job orphaned just after one runs waits nearly the
    full hour on top of the lease. The worker's startup sweep does not help: it
    fires at t=0, when the orphan is still fresh. Restarting the worker is the
    only way to force it sooner. Earlier wording said 15 minutes; that is the
    lease alone, not the latency.
  - the 120s grace period covers a typical job, not every job: `max_games` goes
    to 2000, and a large generation will exceed it and be killed, leaving the
    job for the 15-minute crash-recovery lease.
  - restart: `docker compose --env-file .env.docker restart worker`
  - logs: `docker compose --env-file .env.docker logs -f worker`
  - stopping it is safe mid-job: it handles SIGTERM and finishes the running job
    first, with a 120s grace period. Killing it instead leaves the job for crash
    recovery, which reclaims it only after the lease expires.
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

Three third parties, and **which container reaches them now matters**: the job
worker runs in its own container, so everything reachable from a job handler
egresses from `knightmind-worker-1`, not from the API. Both containers get the
proxy env from `docker-compose.override.yml`. All are best-effort: a failure
degrades one feature and never takes the service down.

| Host | Container | Used by | On failure |
| --- | --- | --- | --- |
| `api.chess.com` | **api** | game import, rating snapshots | the import or snapshot fails and is reported to the caller |
| `explorer.lichess.ovh` | **api** | `/openings/baseline` | serves a stale cached row if one exists, else 503; the Openings page simply omits the comparison |
| `api.anthropic.com` | **worker** | AI diagnosis prose, AI puzzle naming | diagnosis falls back to the rules-based cause; naming leaves the puzzle pending and retries later |

Debugging "AI stopped working" therefore means checking the **worker**'s proxy
env, not the API's — the API no longer calls Anthropic at all.

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
- **Kill switches, plural**: `KNIGHTMIND_AI_DIAGNOSIS=0` stops the *diagnosis*
  model call — no request, no audit row, diagnosis falls back to rules. It does
  **not** stop AI puzzle naming, which has its own `KNIGHTMIND_AI_NAMING` flag.
  Pulling only the first lever during a spend incident leaves naming calling
  Anthropic.
- **Absent key is safe.** `ANTHROPIC_API_KEY` is read at call time, never at
  startup, so an unset key degrades to rules-only instead of blocking boot.
- **Spend ceilings** are counted from `diagnosis_audit_log`:
  `KNIGHTMIND_AI_DAILY_CAP_USER` (bounds one backfill) and
  `KNIGHTMIND_AI_DAILY_CAP_GLOBAL` (backstop against a runaway loop). Naming has
  its own pair, `KNIGHTMIND_AI_NAMING_CAP_USER` / `_CAP_GLOBAL`.
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

That is the only sanctioned mechanism. It writes `knightmind_<YYYYMMDD>_<HHMMSS>.sql.gz`, reads it back with `gzip -t`, and only then writes the matching `.sha256`, so a `.sha256` existing means the dump was readable when written. It then applies the retention policy below.

**A dump that fails `gzip -t` is renamed to `<name>.sql.gz.corrupt` and no checksum is written for it.** `.corrupt` matches no retention glob, so it stays for inspection and is never counted as a backup — delete it by hand once you know why it happened. The script exits non-zero; if you see that, you have no fresh backup, whatever the directory looks like.

`deploy/test-postgres-backup.sh` covers classification, both age rules, the citation guard, the orphan sweep, and the corrupt-dump path against temporary fixture directories. It touches no database and no real backup. `Ops CI` (`.github/workflows/ci-ops.yaml`) runs it, plus `bash -n` over every script in `deploy/`, on any change under `deploy/`. Run it by hand too when you are changing the backup script; it takes a second and needs nothing installed.

### Retention

Two classes, distinguished by filename, each with an age limit and a floor:

| class | shape | kept | floor |
| --- | --- | --- | --- |
| routine | `knightmind_<date>_<time>.sql.gz`, `knightmind-db-<ISO>.dump` | `RETENTION_DAYS`, default 14 | `MIN_KEEP`, default 3 |
| labelled | a word between prefix and timestamp, e.g. `knightmind-pre-release-pr343-...` | `MILESTONE_RETENTION_DAYS`, default 90 | `MILESTONE_MIN_KEEP`, default 2 |

The floors matter because backups here are manual: without them a quiet fortnight ages out every routine copy and leaves only whatever the current run just wrote. The two classes are counted independently, so a burst of routine dumps cannot push labelled ones past their floor.

**Label a dump when you take it before something risky**, and say what it precedes — `pre-release-pr343`, `pre-strip-flag`, `pre-hardening`. That is what buys it the longer horizon. Rename an existing dump to promote it, and rewrite its `.sha256` to match: the checksum records a bare filename, so a rename without it makes `sha256sum -c` fail on an intact file.

**Labelled does not mean immortal.** Until 2026-08-06 they were exempt entirely, and the directory accumulated four dumps of a single schema revision, each kept forever because it had a nice name. A dump's restore value decays as the schema moves past it: recovering to a revision several migrations back means replaying all of them onto data that old. When several labelled dumps share a revision, prune by hand — the script cannot tell which label matters.

**A dump cited in a document is never deleted automatically, whatever its age.** Before removing anything, the script greps the doc trees in `CITATION_PATHS` (default: the repo it lives in, plus `~/projects/knightmind`) for the dump's filename, and skips it if there is a hit, logging `Keeping <name>: past its N-day horizon but cited in a project document.`

The floors do not cover this on their own. This document names three labelled dumps while `MILESTONE_MIN_KEEP` keeps two, so without the guard the oldest cited one is deleted the day it turns 90 — with its filename and SHA256 still printed a few sections down. That is the same dangling-citation failure as below, arriving on a timer instead of by hand.

The consequence to accept: **a cited dump is kept until its citation is removed.** To let one go, edit the citing document first. Every retained citation is logged so the directory cannot quietly grow without anyone noticing.

**The same rule binds you when deleting by hand**, because the script only guards its own deletions:

```bash
grep -rl "<dump-filename>" ~/git/knightmind/ ~/apps/knightmind/ ~/projects/knightmind/
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

**The target cluster must already have a `knightmind` role.** The dump carries
`OWNER TO knightmind` on every object, so a restore into a cluster without it
stops at the first one:

```
ERROR:  role "knightmind" does not exist
```

Restoring back into `knightmind-db-1` never hits this, and neither does the
throwaway recipe below — but only because it passes `POSTGRES_USER=knightmind`,
which creates the role as the cluster superuser. That argument is load-bearing,
not cosmetic. Change it, or restore into a managed instance whose superuser you
do not get to name, and the restore fails on the first table. Either create the
role first (`CREATE ROLE knightmind LOGIN SUPERUSER;`) or strip ownership with
`pg_restore --no-owner` / `psql` after `sed`-ing the `OWNER TO` lines out.

Measured 2026-08-15, verifying the pre-release backup into a container that used
`POSTGRES_USER=test`: one `CREATE TABLE` ran, the `ALTER TABLE ... OWNER TO`
after it failed, and `ON_ERROR_STOP=1` ended the restore there. Of fifteen
tables, one existed and none held a row.

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

### "Active" does not mean the routes are still there

The unit is `Type=oneshot` with `RemainAfterExit=yes`, so it reports `active`
forever after one successful run. tailscaled rebuilds table 52 when it starts
and deletes the throw routes — so the exceptions can be gone while
`systemctl status` still looks healthy. **Check the routes, not the unit.**

Seen on 2026-08-12: unit last ran 2026-08-01 19:40, tailscaled restarted
2026-08-09 16:59, and table 52 held `default dev tailscale0` with no throw
routes. Every host-to-container connection hung. The symptom is misleading —
docker-proxy still accepts on `127.0.0.1`, so the port probes as **open** and
only the relay to the container fails:

```bash
# looks fine — docker-proxy answers on loopback
timeout 3 bash -c 'cat < /dev/null > /dev/tcp/127.0.0.1/5432' && echo open

# the real test — this is what actually breaks
timeout 3 bash -c 'cat < /dev/null > /dev/tcp/172.17.0.2/5432' && echo open

ip route get 172.17.0.2     # must say `dev docker0`, NOT `dev tailscale0`
```

`docker exec ... psql` keeps working throughout, because it uses no networking
at all — which is what makes this look like an application or database fault.

`PartOf=tailscaled.service` plus `WantedBy=tailscaled.service` now re-run the
helper on every tailscaled start or restart. That fix only applies once the
unit file is reinstalled — the install block above must be re-run after
pulling it.

### Recurrence on 2026-08-26: tailscale rebuilt routing in place, and a heal timer now guards it

On 2026-08-26 the exact symptom recurred even though the unit had run
successfully at boot (status 0/SUCCESS on 2026-08-20). Tailscale rebuilt its
routing **in place** — policy rule `5270: from all lookup 52` stayed, table 52
was rewritten to only `default dev tailscale0`, and both the throw-routes and
the `iif <bridge> lookup main` rules vanished — with **no tailscaled restart**
to re-trigger the unit. Host -> container traffic hung again (Caddy ->
`127.0.0.1:8000` -> docker-proxy -> `172.18.0.4`), exactly like 2026-08-12.

The fix adds a periodic re-assertion layer on top of the existing oneshot, so a
silent in-place rebuild is repaired within the timer interval instead of
waiting for someone to notice:

- `/home/lauureal/apps/knightmind/deploy/tailscale-docker-route-exceptions-heal.service` —
  oneshot that runs the same idempotent script.
- `/home/lauureal/apps/knightmind/deploy/tailscale-docker-route-exceptions-heal.timer` —
  every 2 minutes (`OnCalendar=*:0/2`, `OnBootSec=1min`).

Install once as root:

```bash
sudo cp /home/lauureal/apps/knightmind/deploy/tailscale-docker-route-exceptions-heal.service /etc/systemd/system/
sudo cp /home/lauureal/apps/knightmind/deploy/tailscale-docker-route-exceptions-heal.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tailscale-docker-route-exceptions-heal.timer
```

Verify the cadence:

```bash
systemctl list-timers tailscale-docker-route-exceptions-heal.timer --no-pager
systemctl status tailscale-docker-route-exceptions-heal.timer --no-pager -l
```

Also fixed on 2026-08-26: the script discovered bridge subnets via
`ip -o -4 addr show type bridge`, which on this iproute2 returns **every**
interface's addresses (lo, enp8s0, tailscale0 included), not just bridges. That
added throw-routes for the host public IP, the Tailscale IP and loopback into
table 52. The script now builds the real bridge-name list first
(`ip -o link show type bridge`) and reads each bridge's own subnet, so table 52
only ever receives Docker-bridge throws. Stray throws added by the older
script are not auto-removed by `ip route replace`; delete them once after
deploying the fixed script:

```bash
ip route del throw 65.108.67.53 table 52 2>/dev/null || true
ip route del throw 100.67.182.77 table 52 2>/dev/null || true
ip route del throw 127.0.0.0/8 table 52 2>/dev/null || true
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

## `rate_limit_failures` is not always a fault

`/ops/status` exposes `rate_limit_failures`, and the Ops page renders it as an
amber "N requests passed unchecked" banner. The limiter fails open, so this is
the only signal that it broke.

It is also incremented by the **hourly rate-limit purge**. The purge deletes
rows older than an hour; a live check deletes rows older than its window and
then counts. They overlap, so a check that lands during the purge waits out its
2s `lock_timeout` and fails open — correctly, and for a request that was going
to be allowed anyway.

So a small count that grows by roughly the number of concurrent limited
requests, about once an hour, is the purge. A count that climbs continuously,
or whose `rate_limit_last_error` is not a lock timeout, is the limiter. Read
`rate_limit_last_error` before concluding anything; it carries the exception
class and SQLSTATE.

The counter is per-process and resets when the API container restarts.

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

## KNIGHTMIND_AI_NAMING stays unset on the deployed stack

Naming has only ever been run from the operator CLI, deliberately, against a
database someone was watching. **Do not set `KNIGHTMIND_AI_NAMING=1` in
`.env.docker`.**

Setting it arms the background naming pass inside the diagnosis job, and the
job chain re-queues while `naming_pass.pending_count > 0`. The circuit breaker
that bounds that loop opens on a failure streak — but three conditions produce
`skipped` rather than a failure, and a skip is not billed, so the daily cap
cannot bound it either:

- no `ANTHROPIC_API_KEY` (never self-heals)
- the daily cap exhausted (the corpus is larger than one day's allowance)
- a puzzle that is skipped on every run — a missing FEN, say — when nothing
  else in the pass answers either. One bad row among good ones does not spin:
  the pass still names something, so pending falls and the streak resets.

`skipped` now counts toward the breaker, so the loop is damped to one attempt
per cooldown window rather than one every two seconds. That makes it survivable,
not desirable. Run naming from `scripts/ai_name_puzzles.py` and read what it
wrote.

## Unsafe without explicit approval

Do not run these without a fresh DB backup and Lau's explicit approval:

```bash
docker compose --env-file .env.docker down
docker compose --env-file .env.docker up -d
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker exec api alembic -c services/api/alembic.ini upgrade head
docker volume rm knightmind_pgdata
```

## Rolling back a deploy

There is **no automated rollback**. The deploy applies migrations and replaces
containers before the health check runs, so a red workflow means the change
shipped and then failed its check — not that it was prevented.

Three things make this harder than a tag swap, and all three have bitten:

- **No versioned image.** Both services pin `image: knightmind-api` with no
  tag. The previous build survives only as a dangling image reachable by ID
  until something prunes it, so rolling back means a full rebuild.
- **The worker becomes an orphan.** Rolling the compose file back to a revision
  without the `worker` service leaves `knightmind-worker-1` running NEW code
  beside the rolled-back API's in-process worker. `--remove-orphans` is not
  optional here.
- **Do not downgrade the migrations.** All three of `a335ae9eeced`,
  `399a35540403` and `d1e2f3a4b5c6` are additive or no-ops, and old code
  ignores them. Downgrading below `a335ae9eeced` while any worker container
  survives breaks it mid-write.

```bash
cd /home/lauureal/apps/knightmind
COMPOSE="docker compose --env-file .env.docker"

# 1. Stop the worker FIRST, before the schema moves under it.
$COMPOSE stop worker

# 2. Back to the previous main commit, and rebuild — there is no tagged
#    previous image to swap to.
git reset --hard <previous-main-sha>
$COMPOSE build api

# 3. Restore the old topology AND remove the worker, which no longer exists
#    in the rolled-back compose file.
$COMPOSE up -d --remove-orphans

# 4. Verify. Leave the migrations alone.
curl --noproxy '*' http://127.0.0.1:8000/ops/health
```

If only the worker is misbehaving, the fastest mitigation is not a rollback:

- `$COMPOSE stop worker` — loud. `/ops/health` reports `not_running` (503) and
  **every subsequent deploy gate fails** until it is back.
- `KNIGHTMIND_WORKER_DISABLED=true` + restart the worker — quiet. Housekeeping
  keeps running and health reports `disabled`, which counts as healthy, so the
  paused queue is invisible to the gate. See the trade table above.

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
