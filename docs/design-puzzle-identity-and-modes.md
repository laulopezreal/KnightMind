# Design: puzzle identity, and what may be seen before an attempt

Status: draft, revision 3. Two adversarial reviews have run against it; §12
records what each changed, because the deltas are most of the argument.
Supersedes the naming half of `#364`'s plan. Nothing here is implemented and no
PR is open.

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
*and* unreachable for the other.

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
it sorts first. Copy moves from "Hint (n/3)" to "(n/4)" (`a11yCopy.ts:48`).

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
review was right to call it: `worker.py:358` early-returns unless the job is
`PUZZLE_GENERATION`, and the review handler enqueues nothing. Fail a puzzle,
never import another game, and no nickname would ever be written.

So this design adds the path explicitly:

- `POST /puzzles/{id}/review` enqueues naming best-effort when a review sets
  `next_due_at` on a puzzle with `fail_count > 0` and no nickname — the same
  shape as `_enqueue_diagnosis`, wrapped so a follow-up failure cannot fail the
  review that already succeeded.
- Selection and the re-queue predicate share **one** expression of "needs a
  nickname". Today `name_puzzles` (`naming_pass.py:147`) and `pending_count`
  (`:411`) express it independently; narrowing one and not the other makes the
  worker re-queue forever on rows the pass will never take. That is the loop
  this branch already fixed once, from the other side.

~20 puzzles today rather than 318, scaling with engagement rather than corpus.

### 6.1 A failed call writes nothing

When the model is disabled, over budget, or rejected, the pass currently writes
the deterministic name with `title_source='position'` (`naming_pass.py:254,
261, 264, 299`). Under this design that would bless a generated identifier as a
nickname — the exact thing being removed.

Instead: **write nothing, leave `title` NULL, show provenance.** "A puzzle with
no nickname shows provenance" is then true without exception, and
`title_source` genuinely narrows to `ai | user`.

## 7. Data model and the migrations this costs

- `puzzle_stats.title` — the nickname. NULL until earned. Unique per user
  (`uq_puzzle_stats_username_title`, already built in `#366`).
- `puzzle_stats.title_source` — `ai | user` only.
- Provenance is **derived, never stored**.

Three things must change together, and revision 1 named only the first:

1. **Clear the generated titles.** They are identifiers, not nicknames.
2. **`save_puzzle` stops writing a title.** A puzzle is born with provenance.
3. **`backfill_puzzle_identity` stops writing titles.** This is the one that
   makes the other two real. It runs in `lifespan` on **every API boot**
   (`main.py:117-129`), selects exactly the rows step 1 clears
   (`identity.py:190`, `title IS NULL`) and rewrites them with
   `title_source='position'` (`:255-256`). Without this, clearing titles is
   undone by the next deploy. `scripts/reclassify_motifs.py` needs the same
   treatment for its operator-run path.

`title` NULL for most rows is safe for the unique index (Postgres treats NULLs
as distinct; `title_registry.py:47` already filters them out of `taken_titles`),
and no code sorts or exports by title.

## 8. `display_name`, and the surfaces that assume a title exists

The API returns **`display_name`** — nickname when the gate permits and one
exists, else provenance. Clients never branch, and never receive a value they
are trusted not to render.

Two consumers currently assume a non-null title and must move to it:

- `GET /{username}/puzzles/tricky` (`dashboard.py:453`) declares `title: str`
  **non-null** (`:111`) and survives only via `title=stat.title or "Untitled
  Puzzle"` (`:499`). It filters `fail_count >= 2` — precisely the puzzles that
  earn nicknames, and precisely the ones that will not have one yet. Without
  `display_name` the Dashboard's "Recently Tricky" card becomes a column of
  `Untitled Puzzle`: the original bug, on the surface dedicated to it.
- Library search is `lower(title) LIKE …` (`puzzles_routes.py:1245`). With
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

Note `puzzles/test_generator.py:659` asserts `stats.title == "The Queen to d5"`
and `title_source == "position"` directly on the creation path §7 removes. It is
a spec, not a fixture, and it changes with the design — 19 assertions of that
shape exist across `services/api/`.

## 11. Rollout

Each step ships and reverts independently.

1. `display_name` + provenance helper; every puzzle route through one
   serializer. Nickname still wins wherever it exists — nothing visibly changes.
2. Move `tricky` and Library search onto it (§8).
3. Turn on the resolution gate (§4). Motif, nickname, queue-reason pattern and
   diagnosis prose become post-resolution.
4. Overrides for themed / focus intent (§5); puzzle-scoped hint endpoint (§5.1).
5. Post-resolution panel (`#364` §4.1), reusing `MistakeDiagnosisCard`.
6. Retire title-writing in `save_puzzle`, `backfill_puzzle_identity` and
   `reclassify_motifs`; clear generated titles; move the naming trigger; delete
   the content gate (§6, §7).
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

One correction to review 2: it read ~100 rows with `title_source='ai'` as
production state. Those are writes from this branch's trial runs against a
scratch copy; production has no `title_source` column at all. Its substantive
point survives in a different form — 24 of those names contain their own answer
square, and they are what the *current* prompt produces, so the leak is
prospective rather than shipped. Names generated before the content gate existed
should be re-earned rather than kept.

## 13. Where this is still judgement

1. **The trigger clock.** "Scheduled to return after a failure" is better argued
   than first-failure, but it is still a guess about when a cue helps. Cheap to
   move.
2. **Clearing existing titles.** Coherent with the model, and they are the bug —
   but they are strings a user may have seen. Grandfathering them is defensible
   and messier.
3. **Whether hinted solves should schedule differently** (§9). Needs usage data
   that does not exist.
4. **Whether `alfi3sr` having no diagnosis rows is a bug in its own right.** This
   design routes around it. If the real answer is "that user should have been
   diagnosed", the provenance ladder still stands but §3.1's coverage argument
   is solving a symptom.

Everything else is settled by measurement against production, or by a decision
already made in the codebase.
