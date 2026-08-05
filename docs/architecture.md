# KnightMind Architecture

## Overview

KnightMind is a personal chess intelligence platform. It imports a player's Chess.com
game history, uses Stockfish to find the positions where they went wrong, turns those
into personalised puzzles, and schedules the puzzles for spaced-repetition review —
with dashboards tracking whether any of it is working.

The whole backend is a single FastAPI process. There is no separate engine service and
no message broker: Stockfish is a local binary invoked in-process, and background work
runs on an in-process worker backed by a `jobs` table.

## System Components

```
┌──────────────────────────────────────────────────────────────────┐
│                       Frontend (apps/web)                        │
│              React 19 + Vite + TypeScript + Tailwind             │
│   Home · Dashboard · Puzzles · Library · Openings · Engine       │
│   Insights · Rating Insights · How It Works · Ops (gated)        │
└────────────────────────────┬─────────────────────────────────────┘
                             │ HTTPS / REST (shared /api base)
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                    API service (services/api)                    │
│                            FastAPI                               │
│                                                                  │
│   main.py — HTTP surface, request/response models                │
│                                                                  │
│   engine/     Stockfish wrapper + version-aware eval cache       │
│   puzzles/    blunder detection, puzzle identity, generation     │
│   diagnosis/  why the mistake happened (rules + evidence)        │
│   ai/         Anthropic client, prompts, structured-output schema│
│   openings/   ECO lookup, tree builder, explorer + cache         │
│   storage/    repositories over the SQLAlchemy models            │
│   analysis/   scheduler evaluation                               │
│   jobs/       session cleanup                                    │
│   worker.py   in-process job runner (JOB_HANDLERS registry)      │
│   identity.py / ratelimit.py / auth.py — access + abuse control  │
└──────┬──────────────────────┬──────────────────┬─────────────────┘
       │                      │                  │
       ▼                      ▼                  ▼
┌──────────────┐      ┌───────────────┐   ┌──────────────┐
│    Ingest    │      │   Postgres    │   │  Stockfish   │
│ (services/   │      │ (SQLite for   │   │ local binary │
│  ingest)     │      │  local dev)   │   │ STOCKFISH_   │
│              │      │   Alembic     │   │ PATH         │
└──────┬───────┘      └───────────────┘   └──────────────┘
       │
       │  outbound (all three egress the container)
       ▼
┌──────────────┐      ┌───────────────┐   ┌──────────────┐
│  Chess.com   │      │    Lichess    │   │  Anthropic   │
│  public API  │      │   explorer    │   │  (optional — │
│  game import │      │   baselines   │   │  prose only) │
└──────────────┘      └───────────────┘   └──────────────┘
```

Three external services are reached at runtime, all of them best-effort — a failure
degrades one feature and never takes the API down:

| Host | Used by | On failure |
|---|---|---|
| `api.chess.com` | game import, rating snapshots | the import or snapshot fails and is reported to the caller |
| `explorer.lichess.ovh` | `/openings/baseline` | serves a stale cached row if one exists, else 503; the Openings page just omits the comparison |
| Anthropic API | diagnosis prose (optional) | falls back to the rules-based diagnosis |

No user data goes to the Lichess explorer — the request carries a position and a rating
band, no username or game. Its cached rows have a 30-day TTL and are still served when
the explorer is unreachable, so blocking egress to it is a supported configuration.

`OPERATIONS.md` is the source of truth for these, including the egress-proxy allowlist
that has to name each host for it to be reachable at all.

## Directory Structure

```
KnightMind/
├── apps/
│   └── web/              # React frontend (colocated *.test.tsx)
├── services/
│   ├── api/              # FastAPI backend
│   │   ├── main.py       # HTTP surface
│   │   ├── models.py     # SQLAlchemy models
│   │   ├── alembic/      # migrations
│   │   ├── ai/           # Anthropic client, prompts, schema
│   │   ├── analysis/     # scheduler evaluation
│   │   ├── benchmarks/   # puzzle-generation benchmarks (mock engine)
│   │   ├── diagnosis/    # causes, patterns, evidence, planner
│   │   ├── engine/       # Stockfish wrapper
│   │   ├── jobs/         # session cleanup
│   │   ├── openings/     # ECO, tree builder, explorer, cache
│   │   ├── puzzles/      # generator, identity
│   │   └── storage/      # repositories
│   └── ingest/           # Chess.com game import
├── deploy/               # Caddy, egress proxy, systemd units
├── scripts/              # migration + ops scripts
└── docs/                 # this directory
```

Backend tests live next to the code they cover (`services/api/test_*.py` and
`<subpackage>/test_*.py`), not in a separate tree.

## Frontend routes

| Route | Page |
|-------|------|
| `/login` | Login (the only route outside `RequireAuth`) |
| `/` | Home — connect account, sync, entry point |
| `/dashboard` | Momentum, streaks, focus, motif trends |
| `/puzzles` | Training session |
| `/library`, `/library/:puzzleId` | Browse and replay past puzzles |
| `/openings` | Opening tree explorer (D3) |
| `/engine` | Interactive position analysis |
| `/insights` | Mistake patterns and causes |
| `/rating-insights` | Rating history and change explanations |
| `/how-it-works` | Product explainer |
| `/ops` | Admin utilities — gated behind `OPS_ENABLED`, otherwise redirects |

## Data Flow

### Game import

1. User sets a Chess.com username and hits **Sync**.
2. Frontend calls `POST /import/chesscom?username=...`.
3. `services/ingest/chesscom.py` reads the player's archive index from the public
   Chess.com API and fetches the per-month archives (`.../games/YYYY/MM`). Archives are
   append-only, so an incremental sync can start from the month of the last import
   instead of refetching everything.
4. Games are parsed and upserted into `games`, keyed by a SHA256 of the game URL, so
   re-syncing is idempotent — an unchanged history reports "No new games found".
5. `import_summaries` records the last import per user for the UI's sync status.

### Puzzle generation

1. `POST /puzzles/generate` enqueues a `puzzle_generation` row in `jobs` and returns
   immediately.
2. The in-process worker picks it up and replays each game, scanning with Stockfish at
   `STOCKFISH_DEPTH` for moves whose evaluation swing clears `SWING_THRESHOLD`.
3. Candidates are **re-analysed at a deeper confirm depth** before being accepted: the
   swing must still hold, the solution must stay stable, and the best move must beat the
   next non-equivalent move by `PUZZLE_UNIQUENESS_MARGIN` — so toss-up positions are
   rejected rather than shipped as puzzles.
4. Survivors are written to `puzzles`, unique on `(username, source_game_id, ply)`.
5. The frontend polls `GET /jobs/{job_id}` for progress.

Engine evaluations are cached in `fen_eval_cache`. Cache keys fold in the scheme, the
eval-conversion, and the engine version, so entries self-invalidate rather than serving
stale numbers after an engine or scoring change.

### Training and spaced repetition

1. `GET /puzzles/due` returns puzzles ordered by schedule — due first, then new.
2. `POST /puzzles/{id}/review` re-checks the submitted move server-side; the client's
   claim of pass/fail is never trusted. `/check` and `/reveal` are likewise authoritative.
3. `puzzle_stats` holds the schedule (interval, ease factor, next due), and
   `puzzle_reviews` is an append-only audit of every attempt. `training_sessions` groups
   attempts into sessions.

Whether the solution fields are withheld from browse and training payloads is a separate
rollout flag, `KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS`, which **currently defaults to OFF**.
With it off, `/puzzles/due` and `/daily-puzzle-sessions` still include the solution, and
`/puzzles/list` and `/puzzles/{id}` return it regardless of `?reveal` — the pre-audit
behaviour, kept so the API could deploy before the grading frontend went live. Turning it
on strips those payloads and gates the browse routes behind `?reveal=true`. Server-side
verification is unaffected either way.

The scheduling algorithm is documented in the main [README](../README.md#spaced-repetition-logic).

### Mistake diagnosis

1. Diagnosis runs as its own job type (`diagnosis`) on the same worker.
2. `diagnosis/` derives the cause from rules and position evidence — patterns, PGN
   context, and a training planner.
3. `ai/` optionally adds a written explanation via Anthropic using a structured-output
   schema. This is **prose only**: the rules-based cause, patterns, and training focus
   all work without it.
4. Results land in `puzzle_diagnoses`; prompts and responses go to `diagnosis_audit_log`
   for incident review and are swept by the session-cleanup loop.

AI enrichment is gated by `KNIGHTMIND_AI_DIAGNOSIS` (a full kill switch) and bounded by
per-user and global daily caps counted from the audit log. With no `ANTHROPIC_API_KEY`
the API starts normally and falls back to rules — the key is read at call time, never at
startup.

### Opening analysis

1. Frontend calls `GET /openings?username=...&color=...`, optionally windowed to a
   recent period.
2. The API builds a tree from stored games, labelled via the bundled ECO table
   (`openings/eco.tsv`).
3. D3 renders the interactive tree; a line you are losing can be sent straight to
   practice.
4. `GET /openings/baseline` adds an outside reference point: how a position actually
   scores for players in your rating band, so a line can be judged as a real problem
   rather than just an unfamiliar one.

Two different caches back this, and they are not interchangeable:

| | `openings/cache.py` | `opening_explorer_cache` (table) |
|---|---|---|
| Holds | the built tree for **one user** | aggregates for **one position + rating band** |
| Derived from | that user's stored games | the public Lichess explorer |
| Scope | process-local LRU, per worker | shared across all users |
| Invalidation | key folds in `game_count` + latest game timestamp, so an import invalidates by construction | `key` folds in scheme version, speeds, and rating band |

The tree cache is deliberately **not** a table: the tree is derived data that costs about
a second to rebuild, so it does not justify a migration or the write traffic. The explorer
cache is deliberately **not** process-local: a row is a fact about a public position, so
one user's lookup answers everyone else's and saves an outbound call per selection.

## Background worker

The worker runs **in the API process**, not as a separate service.

- Job types are an enum shared by the model, API, and worker (`JobType`:
  `puzzle_generation`, `diagnosis`). Every type must have a handler in
  `worker.JOB_HANDLERS`; a job with no handler is failed explicitly rather than
  silently running the wrong work.
- Statuses: `queued`, `running`, `succeeded`, `failed`, `canceled`.
- **Run exactly one API instance** (`WEB_CONCURRENCY=1`). Multiple workers would
  double-process the queue.
- **Crash recovery is a liveness lease, not a wall-clock timeout.** A running job bumps
  `heartbeat_at` as it makes progress; `cleanup_stuck_jobs` resets to `queued` only those
  jobs whose heartbeat has gone stale (15 minutes). A genuinely long-running job keeps its
  lease fresh and is left alone, while a crashed worker stops heartbeating and its job is
  recovered. `heartbeat_at` is deliberately decoupled from `updated_at` so status writes
  do not look like liveness.
- Set `KNIGHTMIND_WORKER_DISABLED=true` to run the API without the worker (the backend
  test suite does this).

Puzzle generation over ~30 games legitimately takes minutes, so the frontend's
`useJobPolling` judges a job stuck by **lack of progress** rather than elapsed time: the
stall deadline resets whenever status, progress, message, or `updated_at` advances. A
steadily advancing job is never reported as timed out.

## Persistence

Postgres everywhere: production, local development and the test suite. The
API fails fast at startup when `DATABASE_URL` is unset rather than silently writing to an
ephemeral file. Schema changes go through Alembic (`alembic upgrade head`).

Main tables: `accounts`, `account_chess_usernames`, `games`, `import_summaries`,
`puzzles`, `puzzle_stats`, `puzzle_reviews`, `puzzle_diagnoses`, `diagnosis_audit_log`,
`training_sessions`, `rating_snapshots`, `jobs`, `fen_eval_cache`,
`opening_explorer_cache`.

Column-level detail is in the [README](../README.md#data-schema).

## Access control

- **Auth** is gated by `KNIGHTMIND_REQUIRE_AUTH`, default **off** — the API is
  single-user and the frontend sends no token. When enabled, expensive routes require a
  bearer token and enforce per-user ownership of the target username
  (`services/api/identity.py`).
- **Rate limiting** applies regardless of auth. Per-principal limits and request-size
  caps guard `/engine/eval`, `/puzzles/generate`, `/import/chesscom`, and
  `/ratings/snapshot` (`services/api/ratelimit.py`).
- **CORS** origins come from `KNIGHTMIND_CORS_ORIGINS`. Unset means no cross-origin
  browser requests are allowed.

## Deployment

- **API + Postgres + Stockfish** run on one VPS (claw-home) under Docker Compose. The
  API image bundles the Stockfish binary at `/usr/games/stockfish`.
- **Frontend** is a static build on Cloudflare Pages serving `https://guessme.world`,
  calling the API at `https://api.guessme.world` through a Caddy ingress.
- Push to `main` triggers `.github/workflows/deploy.yaml`, which reaches the host over
  Tailscale.

`OPERATIONS.md` is the operational source of truth and takes precedence over this
document for anything runtime-related.

## Future Enhancements

- **Neo4j graph DB**: store the opening repertoire as a graph for relationship queries
  the relational tree cannot answer cheaply.
- **Multi-user rollout**: auth exists behind a flag; turning it on by default needs
  account provisioning and role-based access for the Ops surface.
