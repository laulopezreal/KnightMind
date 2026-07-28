# Mistake Cause Intelligence — Implementation Plan

**Status:** proposed
**Date:** 2026-07-27
**Base:** `dev` @ a3ba139 · migration head `e2f3a4b5c6d7`

## Goal

Move KnightMind from *"this puzzle is a fork"* to *"you missed this because you don't
scan for loose pieces before calculating — and here is the 4th time this month."*

Three layers per failed puzzle:

| Layer | Source | Example |
| --- | --- | --- |
| 1. Tactical motif | deterministic (exists today) | `fork` |
| 2. Mistake cause | deterministic rules, LLM-ranked | `loose_piece_awareness` |
| 3. Personal pattern | batch clustering over layer 2 | "Loose Piece Syndrome" |

---

## Guiding decisions

These are the load-bearing choices. Everything else follows from them.

### D1 — Rules first, LLM second. The LLM never originates a cause.

The proposed taxonomy is mostly *computable*. Loose-piece awareness, forcing-move
blindness, quiet-move blindness, recapture assumption, time-pressure collapse and
king-safety blindness are all decidable from the FEN, the played/best moves, the PV,
and the PGN clock tags.

So the pipeline produces a **candidate set with evidence** deterministically, and the
LLM's job is constrained to:

- ranking primary vs secondary **within that candidate set**, and
- writing the user-facing explanation and training recommendation.

The LLM cannot return a cause absent from the candidate set — that is enforced by
server-side validation, not by prompt wording. A response that cites an evidence id
not present in the packet, or a cause not in the candidates, is **rejected and the
rule-only diagnosis is stored instead**.

*Why:* it gives a shipping feature with zero AI dependency and zero cost, a real
fallback when the API is down, and a labeled baseline — rule/LLM disagreement rate
becomes a debuggable metric rather than a vibe.

### D2 — Diagnosis is an honesty-contract citizen.

New thresholds go in [`analytics_confidence.py`](../services/api/analytics_confidence.py),
alongside the existing ones, not scattered in the new modules. Every diagnosis and
pattern response carries `insufficient_data` / `confidence` exactly like
`MotifPerformance` and `MotifTrend` do today.

Specifically: a *pattern* is not shown as a pattern below `MIN_DIAGNOSES_FOR_PATTERN`
(proposed default 4). A single failure is a failure, not a tendency.

Diagnoses derived from **unverified** reviews (`PuzzleReview.verified = False`) are
weighted out of pattern clustering. The repo already refuses to present self-reported
results as verified skill; a "pattern" built from them would launder that.

### D3 — The LLM never sees a username.

The evidence packet is a typed dataclass with **no** identity field. Redaction is a
consequence of the type, not of a prompt instruction or a `.replace()` call. The
packet carries chess facts and aggregate counts only.

### D4 — Never let pattern-targeting corrupt spaced repetition.

`get_trainable_puzzle_ids` deliberately excludes not-yet-due puzzles, and the
docstring in [spaced_repetition.py:329](../services/api/storage/spaced_repetition.py:329)
explains why (early PASS inflates intervals, early FAIL punishes the user for the
scheduler's own choice). Pattern bias re-ranks *within* the trainable set. It never
widens it.

### D5 — AI diagnosis ships ON, with the kill switch and the budget wired first.

**Decided: `KNIGHTMIND_AI_DIAGNOSIS` defaults ON.** This departs from the other flags
in the codebase (`KNIGHTMIND_REQUIRE_AUTH`, `KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS` both
default OFF), so the safety properties that a dark launch would have provided must be
built into the feature itself rather than deferred:

1. **Missing key is not an outage.** If `ANTHROPIC_API_KEY` is unset the API must
   start normally and serve rules-only diagnoses. The key is checked at call time,
   never at startup. (Contrast `DATABASE_URL`, which correctly fails fast — that one
   is load-bearing, this one is enrichment.)
2. **The disagreement metric ships with the feature, not after it.** Since we no
   longer get a quiet period to measure rule/LLM agreement before exposure, every
   diagnosis records whether the LLM's primary cause matched the top rule candidate.
   Surfaced on `/ops/status` as a rolling rate. A sudden drop is the signal that a
   prompt or model change regressed.
3. **The budget ceiling is a hard stop, not a warning.** Per-user daily cap and a
   global daily cap, both enforced before the call. On exhaustion: rules-only, logged,
   no error surfaced to the user.
4. **`KNIGHTMIND_AI_DIAGNOSIS=0` must be a complete kill switch** — verified by test,
   so an incident can be resolved with an env change and a restart rather than a
   rollback.

Rules-only diagnosis is always on once shipped and is unaffected by this flag.

---

## Blocking prerequisites found in the codebase

Two must be fixed before any background diagnosis work can run.

### P1 — The worker is hard-wired to one job type

[`worker.py:211`](../services/api/worker.py:211) `execute_job` never reads `job.type`;
it unconditionally calls `generate_puzzles`. A `diagnosis` job queued today would
silently run puzzle generation.

**Fix:** introduce a small handler registry (`JOB_HANDLERS: dict[str, Callable]`),
dispatch on `job.type`, and fail the job explicitly on an unknown type. Keep the
existing guarded-UPDATE success/failure semantics and the heartbeat lease untouched —
they are well-reasoned and heavily commented.

### P2 — One active job per username, globally

[`models.py:91`](../services/api/models.py:91) defines `ix_jobs_active_username` as a
*unique* partial index on `username` where `status IN ('queued','running')`. A
diagnosis job cannot coexist with a generation job.

**Fix:** widen the index to `(username, type)`. This preserves the real invariant
("no two concurrent generations for one user") while allowing one diagnosis job
alongside. Requires a migration that drops and recreates the index on both dialects.

*Alternative considered and rejected:* chaining diagnosis onto the tail of the
generation job. It couples two unrelated failure modes — a diagnosis error would mark
a successful generation as FAILED.

### P3 — No opening-family detection exists

`openings/tree_builder.py` builds ply-indexed move trees; there is no ECO mapping.
`opening_family` is therefore **nullable and deferred to Phase 3**, populated by a
small static ECO-prefix table. The MVP does not claim opening-level causes.

### P4 — No LLM SDK in the project

`pyproject.toml` has no `anthropic` dependency. Added in Phase 4, not before, so
Phases 1–3 ship with no new runtime dependency at all.

---

## Stage 1 — Deterministic evidence extraction

**New:** `services/api/diagnosis/evidence.py` (pure functions, no DB, no engine calls)

Everything below is derivable from data already persisted. **No new Stockfish calls**
— the generator already confirmed `eval_before`/`eval_after`/`swing`/`solution_pv` at
`confirmed_depth`. Re-evaluating would be the single most expensive mistake available
here.

```
EvidencePacket                    (frozen dataclass — no username field, see D3)
  position:  fen, side_to_move, ply, move_number, phase
  moves:     played_move_uci, best_move_uci, accept_moves, solution_pv
  eval:      eval_before, eval_after, swing, confirmed_depth
  played:    is_capture, is_check, is_quiet, is_recapture, target_value
  best:      is_capture, is_check, is_quiet, is_zwischenzug_like
  loose:     own_undefended[], opp_undefended[], own_undefended_value_total
  threats:   legal_checks_count, legal_captures_count, opp_forcing_replies
  king:      user_castled, king_ring_attackers, escape_squares, back_rank_boxed
  game:      time_control, user_color, game_result, rated
  clock:     seconds_left, increment, is_time_pressure     (from PGN [%clk])
  history:   puzzle_fail_count, motif_fail_rate_30d, similar_cause_count_30d
```

Each fact is emitted as an `EvidenceItem(id, label, value)` so the citation
enforcement in Stage 3 has stable ids to check against.

**Notes on the harder extractions:**

- **Phase** — from `ply` plus non-pawn material count, not `ply` alone.
- **Recapture assumption** — requires the previous move; replay `Game.pgn_blob` to
  `ply - 1`. Cheap, and the blob is already stored.
- **Time pressure** — chess.com PGNs carry `[%clk H:MM:SS]` per move. Parse the tag
  at this ply and the increment from `Game.time_control`. This is the only source of
  the clock signal and it is *already in the database*.
- **Zwischenzug-like** — best move is a check/capture that is not the "expected"
  recapture on the contested square. Heuristic; label it as such in the docstring.

**Tests:** golden corpus of ~20 hand-labeled positions, mirroring the existing
[`puzzles/test_golden_corpus.py`](../services/api/puzzles/test_golden_corpus.py)
pattern. Pure functions, fast, no fixtures.

---

## Stage 2 — Rule-based cause classifier

**New:** `services/api/diagnosis/causes.py`

```python
CAUSE_TAXONOMY = {          # slug -> (human label, rule_confidence_ceiling)
    "loose_piece_awareness", "king_safety_blindness",
    "calculation_stopped_early", "forcing_move_blindness",
    "quiet_move_blindness", "recapture_assumption",
    "opening_pattern_gap", "endgame_technique_gap",
    "time_pressure_collapse", "own_threat_tunnel_vision",
    "missed_opponent_resource", "alignment_blindness",
    "pawn_structure_misunderstanding",
}
```

Each rule is a small predicate over the packet returning
`CauseCandidate(cause, strength, evidence_ids)`. Examples:

| Cause | Rule sketch |
| --- | --- |
| `loose_piece_awareness` | ≥2 own undefended pieces of value ≥3 **and** best move / opponent refutation attacks ≥2 of them |
| `forcing_move_blindness` | best move is check-or-capture, played move is quiet, ≥1 forcing move available |
| `quiet_move_blindness` | best move is quiet, played move is check-or-capture |
| `time_pressure_collapse` | `seconds_left < max(10, 0.1 × base)` — **never** primary alone; it modulates |
| `calculation_stopped_early` | `len(solution_pv) ≥ 3` and played move is the PV's move 1 prefix, or refutation is at PV depth ≥3 |
| `own_threat_tunnel_vision` | played move creates a threat, ignores a larger opponent forcing reply |
| `endgame_technique_gap` | phase == endgame **and** swing is conversion-shaped (won→drawn) |

`RULE_VERSION` is a module constant, persisted per diagnosis so a rule change
invalidates cached rows exactly the way `EVAL_CONVERSION_VERSION` does for the engine
cache in [`engine/stockfish.py`](../services/api/engine/stockfish.py).

Output when no rule fires: `cause = "unclassified"` with `insufficient_evidence =
True`. That is an honest answer, and it is what gets rendered — not a guess.

**Tests:** one per rule, positive and negative; plus a corpus test asserting the
unclassified rate stays under a documented ceiling.

---

## Stage 3 — Persistence + API (rules-only, no AI)

### Migration (down_revision `e2f3a4b5c6d7`)

**`puzzle_diagnoses`**

| column | notes |
| --- | --- |
| `puzzle_id`, `username` | composite PK; mirrors `PuzzleStats` tenancy |
| `primary_motif` | denormalized from `PuzzleStats` for single-query reads |
| `primary_cause`, `secondary_causes` (JSON) | |
| `phase`, `opening_family` | `opening_family` NULL until Phase 3 (P3) |
| `confidence` (float), `insufficient_evidence` (bool) | |
| `evidence_json`, `evidence_hash` | hash is part of the cache key |
| `source` | `"rules"` \| `"llm"` — never blend silently |
| `rule_version`, `model_version` | both nullable; `model_version` NULL for rules |
| `explanation`, `training_recommendation` | NULL when source == rules and no template |
| `user_confirmed_cause`, `confirmed_at` | manual correction (risk control) |
| `created_at`, `reviewed_at` | |

**`mistake_pattern_clusters`** + **`puzzle_diagnosis_clusters`** (link table).
A link table rather than the proposed `representative_puzzle_ids` JSON: the training
planner needs "give me trainable puzzles in cluster X" as an indexed join, and JSON
containment is not portable across SQLite and Postgres.

**`ix_jobs_active_username`** → recreated as `(username, type)` per P2.

### Endpoints

- `GET /puzzles/{id}/diagnosis` — returns the stored diagnosis, or
  `{status: "pending"}`. **Never** computes on the request path (risk control:
  "no AI call during page load"). Rules-only diagnosis *is* cheap enough to compute
  lazily, but keeping one code path avoids a latency cliff when AI turns on.
- `POST /users/{username}/diagnose` — enqueues a `diagnosis` job. Rate-limited via
  the existing [`ratelimit.py`](../services/api/ratelimit.py) `rate_limit` dependency,
  same as `/puzzles/generate`.
- `POST /puzzles/{id}/diagnosis/confirm` — user corrects the cause label.

`assert_owns_username` on every route, per the existing pattern.

### Worker handler

`services/api/diagnosis/job.py` — iterate the user's failed puzzles lacking a current
diagnosis (`evidence_hash`/`rule_version` mismatch counts as missing), extract, classify,
upsert. Reuses the existing progress + heartbeat callbacks.

---

## Stage 4 — Puzzle detail UI (still no AI)

`apps/web/src/components/MistakeDiagnosisCard.tsx`, rendered on
[`LibraryPuzzle.tsx`](../apps/web/src/pages/LibraryPuzzle.tsx) below the board.

Five honest states — this is the part most likely to be under-built:

1. **Diagnosed** — motif · cause · evidence bullets · training CTA
2. **Tentative** — low confidence: visibly marked, no CTA
3. **Unclassified** — "we can see the motif but not a clear cause"
4. **Pending** — queued, with what will happen
5. **Unavailable** — never solved / no evidence

Follows [`DESIGN_GUIDE.md`](../apps/web/DESIGN_GUIDE.md): `bg-primary/5 border
border-primary/10 rounded-sm p-6`, `font-serif` heading, `font-sans text-primary/60`
body, `font-mono` for move notation. Reuse `ConfidenceBadge` and the `DataState*`
family rather than inventing new state components.

> ⚠️ Known gotcha: unregistered `--color-*` tokens make `bg-primary`/`bg-accent`
> **fills** emit no CSS. Use the runtime-var `@utility` approach for any new fill.

**This is the MVP milestone.** Real, visible, coach-like value with no LLM, no API
key, and no per-user cost.

---

## Stage 5 — AI diagnosis layer

**New:** `services/api/ai/` — `client.py`, `prompts.py`, `schema.py`

- `anthropic` added to `pyproject.toml`. Model id pinned in config and written to
  `model_version` on every row.
- Structured output via a tool definition, so the schema is enforced by the API rather
  than parsed out of prose.
- **Validation gate (D1):** reject if the returned cause ∉ candidate set, if any cited
  evidence id ∉ packet, if confidence is missing, or if the explanation mentions a
  fact class absent from the packet (clock claims with no clock evidence). On
  rejection: log, keep the rules row, do not retry blindly.
- Cache key: `(puzzle_id, evidence_hash, rule_version, model_version)`. Same
  version-folding discipline as the FEN eval cache.
- Cost control: per-user daily diagnosis budget **and** a global daily cap, both
  checked before the call, through the existing limiter (D5.3). A batch cap per job
  run on top.
- Failure mode: API down → rules-only diagnosis, card renders state 1 or 3. Never an
  error state for a background enrichment.

### Audit retention — 30 days, size-capped

**Decided.** New table `diagnosis_audit_log`, deliberately separate from
`puzzle_diagnoses` so the hot read path never carries prompt blobs:

| column | notes |
| --- | --- |
| `id`, `created_at` | `created_at` indexed — the sweep predicate |
| `puzzle_id`, `username` | tenancy; nullable if the diagnosis row is later deleted |
| `model_version`, `rule_version`, `evidence_hash` | reproduce the call |
| `prompt_hash` | full prompt is reconstructible from packet + version, so store the hash |
| `response_json` | truncated at **16 KB**, with `truncated` bool |
| `rejected`, `rejection_reason` | the interesting rows: what validation caught |
| `agreed_with_rules` | feeds the D5.2 disagreement metric |

Retention is enforced by extending the existing
[`jobs/cleanup_sessions.py`](../services/api/jobs/cleanup_sessions.py) sweep — it is
already wired into the lifespan loop at [`main.py:137`](../services/api/main.py:137),
so this needs no new scheduler. `DELETE WHERE created_at < now() - 30d`, batched.

`DIAGNOSIS_AUDIT_RETENTION_DAYS` is env-overridable (default 30) following the
`analytics_confidence.py` env-threshold convention. **Rejected** rows are worth
keeping longer than accepted ones — they are the debugging corpus — but a single
uniform window is simpler and 30 days is enough to investigate any incident.

**Tests:** all mocked — schema conformance, each rejection path, cache-hit behavior,
a "provider unavailable" path asserting the rules row survives untouched, a
"no API key" path asserting startup and rules-only service, a kill-switch test
(D5.4), and a retention-sweep test.

---

## Stage 5b — Historical backfill

**Decided: backfill historical mistakes rather than diagnosing forward only.**

### Scoping correction: the corpus is larger than "failed puzzles"

Worth stating plainly, because it changes the volume by an order of magnitude.
[`generator.py`](../services/api/puzzles/generator.py) creates a puzzle *only* at a
ply where `_is_user_move` is true and the swing clears `SWING_THRESHOLD` (default 2.0).
So **every puzzle in the corpus already is a mistake the user made in a real game** —
`played_move_uci` is literally what they played.

That gives two distinct populations, and conflating them would be a design error:

| Population | Meaning | Use |
| --- | --- | --- |
| All puzzles | a blunder in a real game | **diagnose all of these** — this is the evidence base |
| `PuzzleStats.fail_count > 0` | *also* missed when re-shown in training | priority ordering + heavier pattern weight |

The second is the stronger signal — the blindspot survived a second exposure — but
restricting diagnosis to it would discard most of the user's actual mistake history.
So: diagnose the whole corpus, weight by re-failure.

### Two-pass, resumable

**Pass A — rules-only, whole corpus.** No LLM, no marginal cost, CPU-bound and fast
(no engine calls, per Stage 1). Runs to completion for every user.

**Pass B — AI enrichment, budget-ordered.** Same rows, revisited in priority order:

1. re-failed in training (`fail_count` desc) — strongest signal
2. recent (`Puzzle.created_at` desc) — most relevant to current play
3. largest `swing` — biggest mistakes
4. everything else

Pass B consumes the per-user daily budget from D5.3 and simply stops when exhausted,
resuming on the next run. A large corpus therefore enriches over days rather than
failing or blowing the cost ceiling — and because Pass A already ran, **every puzzle
has a usable diagnosis the whole time**; Pass B only upgrades prose and ranking.

### Resume needs no cursor

The "needs work" predicate is already the resume state: a row is stale when its
`rule_version`, `model_version`, or `evidence_hash` differs from current, or is
absent. A crashed, canceled, or budget-exhausted job re-queries and continues exactly
where it stopped. This is the same self-invalidating discipline as the FEN eval cache —
no separate progress table, and no risk of a cursor disagreeing with reality.

The job reuses the existing heartbeat and progress callbacks, so cancellation and
crash recovery work unchanged.

### Trigger

`POST /users/{username}/diagnose?scope=backfill`, rate-limited. Also enqueued
automatically once after the first puzzle-generation job succeeds, so a new user's
history is diagnosed without them knowing the endpoint exists.

> **Sizing, resolved 2026-07-28:** prod holds **238 puzzles**, so Pass B over the whole
> corpus is a one-time run of roughly **$8** at Opus 5 rates — see *Resolved decisions*
> §4 for the arithmetic and the agreed caps. The "enriches over days" behaviour the
> two-pass design allows for simply won't trigger at this size; Pass B finishes in one
> run. The split still earns its keep as the failure mode: if the model is unavailable
> or the budget is exhausted, Pass A has already left every puzzle diagnosed.

## Stage 6 — Insights: top mistake causes

Extend [`Insights.tsx`](../apps/web/src/pages/Insights.tsx) with a
`TopMistakeCausesCard` above the existing `TacticalRadar`.

New endpoint `GET /users/{username}/mistake-causes` returning, per cause: failure
count, pass rate, dominant phase, `insufficient_data`, and a practice CTA deep-linking
to `/puzzles?cause=…`.

Add `MIN_DIAGNOSES_FOR_CAUSE_RANK` to `analytics_confidence.py` with the same
documented-rationale comment style as its neighbours.

---

## Stage 7 — Pattern clustering

`services/api/diagnosis/clustering.py`, run as a `pattern_clustering` job type.

Deliberately **not** embeddings for v1. Group by `(primary_cause, phase)` with a
motif-affinity merge, require `MIN_DIAGNOSES_FOR_PATTERN` members, score by
`frequency × failure_rate × recency`. Deterministic, debuggable, explainable — and
it answers the actual product question. Embeddings can come later if grouping proves
too coarse; they are not what makes this feature good.

Human names ("Loose Piece Syndrome") come from a **static table keyed by cause**, with
the LLM optionally personalizing the description. A generated cluster *name* that
drifts between runs would make the Insights page feel unreliable.

---

## Stage 8 — Training planner + Dashboard focus

- Extend `get_adaptive_puzzles` with an optional pattern-bonus term in `sort_key`.
  Re-ranks within the trainable set only (D4).
- `training_queue_reasons` returned alongside the daily session so each puzzle can say
  *why* it was chosen.
- `TodaysFocusCard` on [`Home.tsx`](../apps/web/src/pages/Home.tsx) / Dashboard.
- Library filters: `cause`, `phase`, `pattern`, extending the existing
  `available_motifs` mechanism in [`main.py:1569`](../services/api/main.py:1569) with
  an `available_causes` sibling.

---

## PR sequence

All target `dev`, per AGENTS.md.

| # | Scope | Ships |
| --- | --- | --- |
| 1 | `diagnosis/evidence.py` + golden corpus | — |
| 2 | `diagnosis/causes.py` + rule tests | — |
| 3 | Worker type dispatch + job index migration (P1, P2) | — |
| 4 | Diagnosis tables, repository, job handler, endpoints | API |
| 5 | `MistakeDiagnosisCard` + LibraryPuzzle integration | **MVP visible** |
| 6 | AI layer + audit log + retention sweep + budget caps + kill-switch tests | prose |
| 7 | Backfill job (Pass A + Pass B), auto-enqueue after generation | history |
| 8 | Insights top-causes card + threshold | Insights |
| 9 | Clustering job + patterns endpoint + pattern cards | patterns |
| 10 | Training planner reasons + Today's Focus + Library filters | loop closed |

PRs 1–3 are pure infrastructure with no user-visible change; 1 and 2 are pure
functions and should review quickly.

---

## Risk register

| Risk | Control |
| --- | --- |
| LLM invents an unsupported cause | Candidate-set + evidence-id validation, server-side (D1) |
| Diagnosis presented as fact when thin | `confidence`, `insufficient_evidence`, tentative UI state (D2) |
| Username leaks to the model | Packet type has no identity field (D3) |
| Patterns built from self-reported results | Weight by `PuzzleReview.verified` (D2) |
| Pattern bias corrupts SR intervals | Re-rank only inside the trainable set (D4) |
| Cost blowout | Per-user **and** global daily caps checked pre-call; batch caps; backfill Pass B yields to the budget rather than failing |
| AI ships ON with no dark-launch period | Disagreement metric ships with the feature (D5.2); complete kill switch, test-verified (D5.4) |
| `ANTHROPIC_API_KEY` missing in prod | Checked at call time, never at startup — degrades to rules-only, API starts normally (D5.1) |
| Audit log grows unbounded | 30-day sweep on the existing `cleanup_sessions` lifespan loop; responses truncated at 16 KB |
| Backfill starves live diagnosis | Backfill is a distinct `job.type`; the `(username, type)` index lets one of each run |
| Stale diagnoses after a rule change | `rule_version` + `model_version` + `evidence_hash` in the cache key |
| Page-load latency | Diagnosis is always read-only on the request path |
| AI provider outage | Rules-only fallback; enrichment failure is never a page error |

## Resolved decisions (2026-07-27)

1. **AI default state — ON.** Departs from the OFF-by-default convention of the other
   `KNIGHTMIND_*` flags, so the safety properties a dark launch would have given are
   built into the feature instead: call-time key check, disagreement metric shipped
   with the feature, hard budget ceilings, test-verified kill switch. See **D5**.
2. **Audit retention — 30 days, size-capped.** Separate `diagnosis_audit_log` table,
   16 KB response truncation, swept by the existing `cleanup_sessions` loop. See
   **Stage 5**.
3. **Backfill — yes, historical.** Two-pass and resumable: rules over the whole corpus
   (free), then budget-ordered AI enrichment that yields to the daily cap rather than
   failing. Note the scoping correction — every puzzle already *is* a real game
   mistake, so the corpus is larger than "failed puzzles". See **Stage 5b**.

4. **AI daily caps — resolved 2026-07-28.** Prod corpus is **238 puzzles**, which
   settles the one genuinely unbounded cost in this design: a full AI backfill is a
   one-time run in the single-digit dollars, not an open-ended bill.

   Sizing, at Opus 5 rates ($5/MTok in, $25/MTok out). Per diagnosis the input is the
   evidence packet plus taxonomy, schema and system prompt; the output is the
   structured JSON. **Thinking is on by default on Opus 5 and bills as output**, so the
   output estimate must budget for it rather than just the JSON:

   | Per-call shape | $/puzzle | Full 238-puzzle backfill |
   | --- | --- | --- |
   | lean (1.5k in / 0.6k out) | $0.023 | **$5.36** |
   | mid (2.0k in / 1.0k out) | $0.035 | **$8.33** |
   | heavy (3.0k in / 1.8k out) | $0.060 | **$14.28** |

   **Caps: 500 diagnoses/user/day, 1,000/day globally** (~$17 and ~$35 at the mid
   shape). Generous enough that a full backfill finishes in one run, low enough that a
   runaway loop is capped at tens of dollars rather than thousands. Steady state is a
   handful of new puzzles per import — pennies.

   Two notes for implementation:
   - **Cache the shared prefix.** Taxonomy, schema and system prompt are byte-identical
     across every call, and Opus 5's cache minimum is 512 tokens (halved from 1024 on
     Opus 4.8), so even a modest prefix caches. Worth ~15% ($8.33 → $7.04 on a full
     backfill) — small in absolute terms here, but free.
   - **Re-measure before trusting these.** The numbers above are estimates from the
     packet's shape. Run `client.messages.count_tokens()` on a real packet during PR 6
     and reset the caps from the measurement rather than from this table.

### Still open

- **Per-account AI opt-out.** With the flag ON globally, is a per-account override
  needed, or is the env kill switch sufficient for now? Deferred — not needed for the
  single-user deployment, revisit if `KNIGHTMIND_REQUIRE_AUTH` is turned on.
