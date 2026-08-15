# Design: puzzle identity, and what may be seen before an attempt

Status: draft, revision 5. Two adversarial reviews have run against it; §12
records what each changed, because the deltas are most of the argument.
Supersedes the naming half of `#364`'s plan. Nothing here is implemented.

Revision 5 reconciles the document against `dev` at `b691baf` and against live
production on 2026-08-15, after `#377`, `#378`, `#384` and `#385` shipped. Every
code reference below was re-resolved against that tree; the naming
implementation moved under those PRs and revision 4's line numbers no longer
pointed at what they claimed. The substantive change is in §7: **when this was
written every title in production was deterministic, and now 320 of 348 are
AI-written**, which inverts what step 1 clears.

## 1. The problem, measured per tenant

The complaint was "all the puzzles have the same name". The cause:
`generate_puzzle_title` was `MOTIF_TITLES.get(motif)` — seven entries, so seven
names was the ceiling however large the corpus.

Revision 1 of this document measured one user and called the result settled.
There are two, and they are not alike. Measured against **live production**:

```
                        lauureal   alfi3sr
puzzles                      318        30
ever attempted                23         1
failed at least once          20         0
primary_motif='blunder'      156         —
puzzle_diagnoses rows        318         0     <-- 0%, not "missing", zero
```

`alfi3sr`'s library is `The Missed Win` ×19, `The Fork` ×7. That is the reported
bug in its purest form, and it belongs to the tenant with **no diagnosis rows at
all** — so any design deriving identity from `puzzle_diagnoses` fixes nothing
for the user who most has the problem. Revision 1 did exactly that.

### 1.1 The zero is a backfill gap, not missing data

Worth separating, because it changes what to build versus what to run. Measured:

```
alfi3sr   161 games,  161 with pgn_blob,  161 with time_control
           30 puzzles, 0 with a NULL fen / best move / played move
```

Nothing is missing. The rules-only diagnosis job was run against a copy of
production for that user and completed cleanly:

```
{'diagnosed': 30, 'unchanged': 0, 'unavailable': 0, 'canceled': False}

puzzle_diagnoses:  30 rows,  30 with opening_name,  0 unavailable
```

**30 of 30**, no model calls, no failures — `Sicilian Defense B27`,
`Horwitz Defense A40`, `Nimzowitsch Defense B00`. The old schema is not the
obstacle.

Note what it does *not* buy: provenance distinctness for that user moves from
27/30 (date + move) to 28/30 (date + opening + move). The backfill is worth
running for the cause chips, the post-mortem panel and motif recall — not for
naming, which the date already carries.

The diagnosis job simply never ran for that user. It auto-chains only from
puzzle generation (`worker.py:531`), and those puzzles predate the diagnosis
feature — so **any user who stopped importing games before diagnosis shipped has
no diagnosis rows, and nothing will ever give them any.** There is no
backfill-on-deploy for it, and `POST /users/{u}/diagnose` is manual.

That is a defect in its own right, independent of naming, and §11 schedules it.
The design still does not *depend* on opening coverage — a freshly generated
puzzle has no diagnosis row either, because diagnosis is asynchronous — but the
coverage argument in §3.1 is now about timing rather than about a tenant being
permanently unreachable.

A third fact came from the UI. `Puzzles.tsx` renders the motif chip beside the
title *while the player is solving* (`:1139` mobile, `:1337` desktop, neither
gated on resolution). The codebase already believes this is wrong —
`LibraryPuzzle.tsx:54` keeps similar-puzzles post-mortem because "naming the
shared motif before the attempt would hand over the tactic" — it just never
applied that rule to the puzzle's own identity.

## 2. Why one label cannot work

A label is asked to do two jobs that pull apart:

- **Identify** — "which puzzle is this". Needed *before* the attempt.
- **Recall** — "the one where the bishop had bigger plans". Valuable *after*,
  and what makes a spaced-repetition library navigable.

Good recall means distinctive; distinctive means it talks about what happened,
which is the answer. This branch has three measured attempts at one label doing
both: withholding the tactic pushed the model onto the clock (13 of 40 names
became "N Seconds of…"), then onto the played move (11 of 19 became
"\<Piece\> \<verb\> to \<square\>"). The fix is not a better prompt.

## 3. Two labels

| | job | example | source | cost |
|---|---|---|---|---|
| **Provenance** | identify | `12 Mar · Sicilian · move 18` | game date + move, opening when known | no model call |
| **Nickname** | recall | `Bishop Had Bigger Plans` | model call over the puzzle's facts | one call, only where earned |

### 3.1 Provenance is built on the one thing every puzzle has

`puzzles.source_game_id` is a real FK, so **every puzzle has a game**, and every
game has an `end_time`. Measured on production: 348 of 348 puzzles resolve to a
game with a usable timestamp; zero manual puzzles exist.

So the base is **date + move number**, available for 100% of both tenants. The
opening is *added when a diagnosis row exists*, never depended upon.

Distinctness of `(date, opening, move)`, per tenant, measured:

```
lauureal   318 / 318   100%
alfi3sr     27 /  30    3 collisions (no opening, so effectively date+move)
```

When two puzzles still collide, they are disambiguated by their position within
that day's games — `(source_game_id, ply)` is unique per user by existing
constraint, so a deterministic tiebreak always exists.

Revision 1 used opening + move and measured 272/318 on one tenant. That is worse
*and*, until the backfill in §1.1 runs, unavailable for the other.

The base stays date + move even after that backfill, because the gap is not only
historical: diagnosis is asynchronous, so a puzzle generated a minute ago has no
opening either. Provenance must be answerable at insert time, and only date and
move number are.

## 4. The gate is the puzzle's state, not the session's mode

Revision 1 gated on session mode. That was wrong on its primary axis: **mode is
per-session and known at one endpoint; the leak surface is per-puzzle and spread
across five.**

- `GET /puzzles/list` (`:1078`) — `title` and `primary_motif` unconditional at
  `:1365-1367`; `status` filter admits `new` at `:1255`.
- `GET /puzzles/{id}` (`:1407`) — `primary_motif` unconditional at `:1470`.
- `GET /puzzles/{id}/similar` (`:1657`), `GET /puzzles/{id}/diagnosis` (`:1588`).
- `GET /puzzles/due` — and `queue_reason.pattern` (`:675-682`) names the
  diagnosed cause *inside the scored pre-attempt payload*, so it would have
  slipped past a strip list naming only "motif and nickname".

Worse, `/puzzles/due` is fetched **before** the session is created
(`usePuzzleSession.ts:502` then `:532`), so "read the mode from the session" is
unavailable at the one request that matters most. And `session_data` is
client-supplied (`sessions.py:110`), so recording intent there does not make it
unforgeable — revision 1 claimed it did.

**So the gate is a property of the puzzle, not the request:**

> A puzzle's *revealing* fields — nickname, motif, diagnosis prose, queue
> reason — are served only when that `(user, puzzle)` is **resolved**:
> `attempts > 0`, or the solution was explicitly revealed.

One predicate, applied in one serializer that every puzzle-returning route uses.
It survives deep links, a second tab, back-navigation, and a session resumed
days later *by construction*, because it describes the thing being protected
rather than the request asking for it.

`PuzzleStats.attempts` is already selected into the very response that leaks the
title (`puzzles_routes.py:1378`) and no handler consults it.

This also settles a contradiction revision 1 carried: it claimed nicknames are
"never shown before an attempt" while its own mode table showed them in the
Library. Under a per-puzzle gate the Library obeys the same rule as the trainer,
so browsing on Monday can no longer pre-spoil Friday's blind attempt.

## 5. Modes become overrides, not the axis

Targeted practice and spoiler-freeness are the same knob — "practise your forks"
and "don't tell me it's a fork" cannot both hold. Intent still matters; it just
*relaxes* the gate rather than defining it.

| intent | effect on the gate |
|---|---|
| Blind session (default) | none — resolution rule applies |
| Themed (`?motif=`, weakest-motifs, "more like this") | motif revealed; the user named it, so it is not a spoiler |
| Focus bias (`?focus_cause=`, `?focus_opening=`) | that cause/opening revealed — it is already in `queue_reason` |
| Warmup (`?warmup=true`) | none |
| Explicit reveal / solved | full unlock (existing behaviour) |

Similarity is unaffected: it is post-mortem today, and "more like this" is an
explicit request.

Because the base rule is per-puzzle, an override that is forged or arrives by a
shared link grants only what it names — the *other* puzzles in the session stay
gated. That is the property revision 1 could not get from session mode.

### 5.1 The gate has an exit

An escalating hint ladder exists (`utils/puzzle-clue.ts`, rungs named in
`a11yCopy.ts`):

    0  name the piece to move   1  highlight the destination   2  reveal the solution

The motif becomes a **new rung 0** — it reveals less than naming the piece, so
it sorts first. Copy moves from "Hint (n/3)" to "(n/4)" (`a11yCopy.ts:47-49`).

Revision 1 claimed `sessions.py:306` already delivers this. It does not: that
endpoint takes `session_id` + `username`, has **no `puzzle_id`**, and only
increments a counter. Since §4 strips the motif from the payload, a hint rung
needs a puzzle-scoped endpoint that returns it and records the ask. **That is
new work and this design budgets it** — one endpoint, reusing the existing
counter.

## 6. When a nickname is earned, and how it is actually enqueued

Not at creation. Not at first failure either — that names the puzzle when the
user has just seen the solution and needs no cue, and puts a model call on a
review POST.

**A nickname is written when a failed puzzle is scheduled to return.**

Revision 1 said "the existing job chain picks this up". It does not, and the
review was right to call it: `worker.py:531` early-returns unless the job is
`PUZZLE_GENERATION`, and the review handler enqueues nothing. Fail a puzzle,
never import another game, and no nickname would ever be written.

So this design adds the path explicitly:

- `POST /puzzles/{id}/review` enqueues naming best-effort when a review sets
  `next_due_at` on a puzzle with `fail_count > 0` and no nickname — the same
  shape as `_enqueue_diagnosis` (`worker.py:480`), wrapped so a follow-up
  failure cannot fail the review that already succeeded.
- **Narrowing selection must not strand the re-queue predicate.** Revision 4
  asked for one shared expression of "needs a nickname", because `name_puzzles`
  and `pending_count` each carried their own. That is no longer the failure
  mode. `pending_count` (`naming_pass.py:430`) is now written against the
  **audit log**: a puzzle the model has already answered for — accepted or
  rejected — is excluded, while errors and budget skips stay eligible so an
  outage retries later. It reaches zero because the audit row is the record of
  the attempt, not because it mirrors the selection query in `name_puzzles`
  (`:132`).

  The requirement therefore changes shape rather than going away: **whatever
  the trigger narrows to, an excluded puzzle must be excluded from
  `pending_count` too, or written into the audit log.** A puzzle that the new
  trigger will never select, that has no audit row, and whose `title_source` is
  outside `("ai", "user")` is counted pending forever — one pass plus N audit
  rows every 15 minutes, indefinitely. That is the residual loop already
  recorded as accepted on `dev`, and this design must not widen it.

~20 puzzles today rather than 348, scaling with engagement rather than corpus.
Measured on production 2026-08-15: `fail_count > 0` is **20 rows for `lauureal`
and 0 for `alfi3sr`**, against 318 and 30 puzzles respectively.

### 6.1 A failed call writes nothing

When the model is disabled, over budget, or rejected, the pass currently writes
the deterministic name with `title_source='position'` (`naming_pass.py:271,
278, 281, 345`). Under this design that would bless a generated identifier as a
nickname — the exact thing being removed.

Instead: **write nothing, leave `title` NULL, show provenance.** "A puzzle with
no nickname shows provenance" is then true without exception, and
`title_source` genuinely narrows to `ai | user`.

This is compatible with the audit-log `pending_count` described in §6, and the
compatibility is not an accident of it: that predicate already treats
`title_source IS NULL` as pending and already hard-codes `("ai", "user")` as
the set that counts as done (`naming_pass.py:482-483`). So a puzzle left NULL
by a rejected call is excluded by its audit row, and one left NULL by an outage
or an exhausted budget stays eligible and retries — which is the behaviour this
section wants. The half of §7's target model that concerns `title_source` is
therefore already expressed on `dev`; what is missing is that the write path
still populates `position`.

## 7. Data model, and what this actually costs

- `puzzle_stats.title` — the nickname. NULL until earned. Unique per user
  (`uq_puzzle_stats_username_title`, already built in `#366`).
- `puzzle_stats.title_source` — `ai | user` only.
- Provenance is **derived, never stored**.

### 7.1 This costs no new migration

Revision 4 budgeted for schema work that has since shipped. Both columns and
the index exist in production, and the deployed head is `d1e2f3a4b5c6`:

| migration | what | state |
|---|---|---|
| `b1c2d3e4f5a6` | `title_source`, and `call_type` on the audit log | deployed |
| `c7d8e9f0a1b2` | `uq_puzzle_stats_username_title` | deployed, verified `(username, title)` |
| `a335ae9eeced` | worker heartbeats | deployed, unrelated |
| `399a35540403` | cross-process rate-limit hits | deployed, unrelated |
| `d1e2f3a4b5c6` | active-job index repair | deployed, unrelated |

The last three are the release this document is being rebased over. **None of
them touch identity**, so nothing in §7 conflicts with what shipped — the
reconcile confirms the section rather than changing it. What remains here is a
data change and three code changes, not DDL.

### 7.2 What the data change actually clears

Revision 4 said "clear the generated titles; they are identifiers, not
nicknames", written when all 348 titles were deterministic. **That premise is
gone.** Measured on production 2026-08-15:

```
              ai   position   total
lauureal     292         26     318
alfi3sr       28          2      30
             320         28     348      0 rows with NULL title
```

Both tenants now hold AI titles, including the one whose library was
`The Missed Win` ×19. The reported bug is *already fixed in production* by the
CLI naming pass — which is precisely what makes the clear contentious, because
under §6 none of those 320 names were earned. A corpus-wide CLI pass is not "a
failed puzzle scheduled to return".

So the clear splits in two, and the halves are not equally settled:

1. **Clear the 28 `position` rows.** Uncontested. They are generated
   identifiers, the thing §3 replaces with provenance, and no argument in this
   document defends keeping them.
2. **Grandfather the 320 `ai` rows** — *decided here, reversible, and the one
   judgement call in this section.*

**The case for grandfathering.** Under §4 a nickname is never served before the
puzzle is resolved. An unearned nickname on an unattempted puzzle is therefore
invisible, and becomes visible only at the moment §3 wants a nickname to exist.
Clearing costs a user-visible regression to buy a definitional property nobody
can observe. The names are also gate-clean. Measured against production on
2026-08-15, applying `#385`'s own rule (substring match on the best move's
destination square, chars 3-4 of `best_move_uci` so promotions do not skew it):
**0 of 320 AI titles contain their answer square, and 0 of 320 have an
unparseable best move.** That second zero matters because the gate is disabled
outright when `answer_square` is `None`, so it is the population where a leak
could hide unchecked.

**The case against, recorded so it can be re-opened.** §12 concluded that names
generated before the content gate existed "should be re-earned rather than
kept". That still applies to any row written before `#377`, and this document
does not establish which of the 320 predate it. If that split matters, the
audit log (`call_type`, `created_at`) can date them; if it does not, the
gate-clean measurement covers the concern the re-earn rule was protecting.

Grandfathering makes the migration **28 rows, not 348** — small enough to run
inside rollout step 6 rather than as its own operation.

### 7.3 The three code changes that make it stick

1. **`save_puzzle` stops writing a title.** A puzzle is born with provenance.
2. **`backfill_puzzle_identity` stops writing titles.** This is the one that
   makes the others real. It runs in `lifespan` on **every API boot**
   (`main.py:110-116`), selects exactly the rows §7.2 clears
   (`identity.py:190`, `title IS NULL`) and rewrites them with
   `title_source='position'` (`:257-258`). Without this, clearing titles is
   undone by the next deploy.
3. **`scripts/reclassify_motifs.py` stops writing titles.** The same treatment,
   for the operator-run path. It re-runs `generate_puzzle_title` over existing
   rows by design, so leaving it alone hands an operator a one-command undo of
   the other two.

Note the ordering hazard the boot backfill creates: clearing 28 rows and
deploying are the same operation, and the backfill would re-fill them before
the first request. The clear must land in the same release that removes the
write, or after it — never before.

`title` NULL for most rows is safe for the unique index (Postgres treats NULLs
as distinct; `title_registry.py:51` already filters them out of `taken_titles`),
and no code sorts or exports by title.

## 8. `display_name`, and the surfaces that assume a title exists

The API returns **`display_name`** — nickname when the gate permits and one
exists, else provenance. Clients never branch, and never receive a value they
are trusted not to render.

Two consumers currently assume a non-null title and must move to it:

- `GET /{username}/puzzles/tricky` (`dashboard.py:454`) declares `title: str`
  **non-null** (`:111`) and survives only via `title=stat.title or "Untitled
  Puzzle"` (`:499`). It filters `fail_count >= 2` — precisely the puzzles that
  earn nicknames, and precisely the ones that will not have one yet. Without
  `display_name` the Dashboard's "Recently Tricky" card becomes a column of
  `Untitled Puzzle`: the original bug, on the surface dedicated to it.
- Library search is `lower(title) LIKE …` (`puzzles_routes.py:1249`). With
  titles NULL it silently degrades to hex-id search while the placeholder still
  says "Search by title or ID" (`Library.tsx:319`). Search must cover
  provenance, or say what it covers.

## 9. Scoring: decided and deliberately open

**Decided.** Reviews inside a themed or hinted session still update
`pass_count`, `fail_count` and `ease_factor` as today. Skipping them would mean
practice does not count, which is worse than counting it imperfectly. The
distinction is *recorded* — mode on the session, hint use already counted at
`sessions.py:306`.

**Open, on purpose.** Whether a hinted or themed solve should advance the
interval *less*. Nothing computes a rating from `TrainingSession` today;
`ease_factor` is what moves. Changing the scheduler now would be guessing;
recording the distinction is what makes answering it possible later.

## 10. How this is tested

The central claim — "a revealing field is never served for an unresolved
puzzle" — is an invariant over (route × puzzle state). Revision 1 could only
have been tested per endpoint, which is the shape that lets a sixth endpoint be
added later without failing anything.

Under §4 it becomes two tests:

1. A parametrised test over every puzzle-returning route: seed one unresolved
   puzzle, assert none of the revealing fields appear in any response.
2. A registry test: every route returning puzzle data goes through the shared
   serializer. This is the one that catches the endpoint added next year.

Note `puzzles/test_generator.py:661-662` asserts `stats.title == "Pawn to a3"`
and `title_source == "position"` directly on the creation path §7 removes. It is
a spec, not a fixture, and it changes with the design. Measured on `dev` at
`b691baf`: **24** assertions matching `title ==` or `title_source ==` across
`services/api/**/test_*.py`. Revision 4 said 19 without recording its pattern,
so treat 24 as a fresh count rather than as a trend. The expected string moved
too — `#384` changed the fallback to name the move the player *played*, so
revision 4's `"The Queen to d5"` no longer appears anywhere.

## 11. Rollout

Each step ships and reverts independently.

0. **Diagnose the un-diagnosed** (§1.1). Independent of everything below, and
   worth doing whether or not the rest ships: it is rules-only, costs no model
   calls, and turns one tenant's opening coverage from 0% to whatever the
   classifier can reach. Needs a mechanism, not just one manual call — a user
   who stops importing games must not become permanently un-diagnosable.

1. `display_name` + provenance helper; every puzzle route through one
   serializer. Nickname still wins wherever it exists — nothing visibly changes.
2. Move `tricky` and Library search onto it (§8).
3. Turn on the resolution gate (§4). Motif, nickname, queue-reason pattern and
   diagnosis prose become post-resolution.
4. Overrides for themed / focus intent (§5); puzzle-scoped hint endpoint (§5.1).
5. Post-resolution panel (`#364` §4.1), reusing `MistakeDiagnosisCard`.
6. Retire title-writing in `save_puzzle`, `backfill_puzzle_identity` and
   `reclassify_motifs`; clear the 28 deterministic titles (§7.2 grandfathers
   the 320 AI ones); move the naming trigger; delete the content gate (§6, §7).
   The clear must not ship *before* the write is removed, or the boot backfill
   re-fills it (§7.3).
7. `blunder` stops rendering as a motif — `usable_motif()` already exists in
   `diagnosis/clusters.py` and is used in exactly one place.

Step 6 is the only irreversible one. It runs last, once 1–5 have shown the
gate holds.

## 12. What the two reviews changed

**Review 1** (self, with the repo open): provenance is not free — the session
payload has no diagnosis join; enforcement belongs in the payload, not the
component; first-failure was the wrong trigger; `?motif=` is forgeable; it does
cost a migration. The hint-rung correction was itself corrected: the motif sorts
*first*, not last.

**Review 2** (independent, adversarial):

- **The gating axis was wrong** — per-session mode cannot cover a per-puzzle
  leak spread over five endpoints, and `/puzzles/due` runs before the session
  exists. Now §4.
- **Single-tenant measurement.** `alfi3sr` has 0 diagnosis rows, so revision 1's
  provenance was unreachable for the tenant with the worst duplication. Now §3.1.
- **The migration did not stick** — `backfill_puzzle_identity` runs every boot
  and rewrites exactly what was cleared. Now §7.3.
- **The naming trigger had no path** — nothing a review does enqueues naming.
  Now §6.
- **Narrowing the trigger without narrowing `pending_count`** re-creates the
  infinite re-queue. Now §6.
- **`queue_reason.pattern`** leaks the diagnosed cause in the scored payload and
  is neither motif nor nickname. Now §4.
- **The hint rung had no delivery mechanism** — `use_hint` has no `puzzle_id`.
  Now budgeted in §5.1.
- **`title_source` contradiction** — the fallback writes `position`. Now §6.1.
- **`tricky` requires a non-null title**; search degrades. Now §8.
- **Testability** — now §10.

One correction to review 2, **itself now overtaken by events**: at revision 4 it
read ~100 rows with `title_source='ai'` as production state, and the correction
was that those were trial-run writes against a scratch copy, production having
no `title_source` column at all.

That is no longer true and the correction should not be carried forward. The
column shipped in `b1c2d3e4f5a6` and production now holds **320 `ai` rows**
(§7.2). Review 2 was describing something real, a release early. Its
substantive point — that 24 of those trial names contained their own answer
square — was closed by `#377`, `#384` and `#385`: the gate now matches squares
by substring rather than by whitespace token, and **0 of the 320 shipped AI
titles contain their answer square** (measured 2026-08-15; see §7.2 for the
rule applied). So "names generated before the content
gate existed should be re-earned rather than kept" is a rule with a measured
empty population, which is why §7.2 grandfathers rather than clears.

## 13. Where this is still judgement

1. **The trigger clock.** "Scheduled to return after a failure" is better argued
   than first-failure, but it is still a guess about when a cue helps. Cheap to
   move.
2. ~~**Clearing existing titles.**~~ **Decided in §7.2: grandfather the 320 AI
   titles, clear only the 28 deterministic ones.** Revision 4 left this open
   when every title was deterministic and the choice was all-or-nothing. It is
   no longer that: the AI titles are gate-clean and, under §4, invisible until
   the moment a nickname is wanted. Still reversible, and §7.2 records the
   argument against.
3. **Whether hinted solves should schedule differently** (§9). Needs usage data
   that does not exist.
4. ~~Whether `alfi3sr` having no diagnosis rows is a bug in its own right.~~
   **Resolved, and it is.** Measured in §1.1: the games and PGNs are intact and
   the opening derives from 8 of 8 real samples. The job never ran, and nothing
   would ever run it. Fixed as rollout step 0 rather than routed around.

Everything else is settled by measurement against production, or by a decision
already made in the codebase.
