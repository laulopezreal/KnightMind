# Design: puzzle identity, and the modes that decide what it may say

Status: draft, reviewed once adversarially (§11 records what that changed).
Supersedes the naming half of `#364`'s plan. Not yet reflected in `#366`.

## 1. The problem, measured

The complaint was "all the puzzles have the same name". The cause was that
`generate_puzzle_title` was `MOTIF_TITLES.get(motif)` — a seven-entry table, so
seven names was the ceiling however large the corpus. On the real corpus that
produced `The Missed Win` 150 times.

Measuring to size the fix turned up two larger facts:

```
puzzles                 318
ever attempted           23     7%
failed at least once     20
failed twice or more     15
primary_motif='blunder' 156     the classifier's "could not tell"
opening_name known      318     100%
```

**93% of the library has never been opened**, and the motif — the thing a name
most wants to talk about — is unknown for about two thirds of it.

A third fact came from reading the UI rather than the data. `Puzzles.tsx`
renders the motif as a chip beside the title *while the player is solving*
(`:1139` mobile, `:1337` desktop, neither gated on resolution). The codebase
already believes this is wrong — `LibraryPuzzle.tsx:54` keeps similar-puzzles
post-mortem because "naming the shared motif before the attempt would hand over
the tactic" — it simply never applied that rule to the puzzle's own identity.

## 2. Why one label cannot work

A puzzle's label is asked to do two jobs that pull in opposite directions:

- **Identify** — "which puzzle is this". Needed *before* the attempt.
- **Recall** — "the one where the bishop had bigger plans". Valuable *after*,
  and what makes a spaced-repetition library navigable.

A label good at recall is distinctive, and distinctive means it talks about what
happened — which is the answer. This branch has three measured attempts at
making one label do both: withholding the tactic pushed the model onto the clock
(13 of 40 names became "N Seconds of…"), then onto the played move (11 of 19
became "\<Piece\> \<verb\> to \<square\>"). The fix is not a better prompt.

## 3. Two labels

| | job | example | derived from | cost |
|---|---|---|---|---|
| **Provenance** | identify | `Sicilian Defense · move 18` | opening + move number | no model call |
| **Nickname** | recall | `Bishop Had Bigger Plans` | model call over the puzzle's facts | one call, only where earned |

Provenance is inherently non-revealing: an opening and a move number say where
you are, never what to play. The nickname is free to say anything, because it is
never shown before an attempt.

This is what dissolves the spoiler problem. **No content gate is required** —
not the tactic-word list, not the answer-square check. The constraint moves from
*what a name may say* to *where a name may appear*, which a code path can
enforce and a string inspection cannot.

### 3.1 Provenance is identifying, not unique

Measured: 272 distinct `(opening, move)` pairs across 318 puzzles; worst
collision three (`Sicilian Defense · move 15`).

Acceptable **because of where it appears**: blind mode shows one puzzle at a
time beside a position counter (`3 / 20`). Provenance never has to distinguish
two rows in a list — the nickname does that, and it is unique per user by
database constraint. If provenance ever needs to appear in a list, it gains the
game date.

### 3.2 It is not free — it needs a join

The session payload builds from `Puzzle` + `PuzzleStats` and **does not join
`puzzle_diagnoses`** (`puzzles_routes.py:623-636`, `:834-851`), which is where
`opening_name` lives. The Library list already joins it (`:1239`); the session
endpoints must too. One batched join, not a per-puzzle lookup.

Diagnosis rows are written asynchronously, so a freshly generated puzzle has
none. Fallback ladder, every rung non-revealing:

    opening + move  →  move number alone ("Move 18")  →  puzzle id prefix

## 4. One field, computed server-side

Clients must never choose between nickname and provenance, and must never
receive a value they are expected not to render.

The API returns **`display_name`**: the nickname when the surface permits it and
one exists, else provenance. In blind mode the response carries provenance and
the motif is absent from the payload entirely.

This matters more than it looks. If blind mode were "the component does not
render the chip", the tactic still ships in the JSON — readable in devtools, and
leaked wholesale to any other client. `_strip_solution` already establishes the
pattern for exactly this (audit gate 13 strips `best_move_uci`,
`accept_moves_uci`, `played_move_uci`, `solution_pv` from scored payloads). The
motif and the nickname join that list under blind mode.

Enforcing server-side also means the rule survives a frontend refactor, which a
convention about JSX does not.

## 5. Modes

Targeted practice and spoiler-freeness are the same knob: "practise your forks"
and "don't tell me it's a fork" cannot both hold. So the rule follows the user's
intent rather than applying globally — the resolution Lichess and Chess.com both
reach.

| mode | entry | motif | nickname | scheduling |
|---|---|---|---|---|
| **Blind** | `/puzzles` | stripped from payload | stripped; provenance shown | normal |
| **Themed** | weakest-motifs, `?motif=`, "more like this" | shown — it is the point | shown | normal, session flagged |
| **Exploration** | `/library`, `/library/:id` | shown | shown | already excluded |
| **Post-resolution** | any mode, after the attempt | shown | shown | — |

Exploration needs no change: `LibraryPuzzle.tsx:310` already tells the user
results there are not counted as verified training.

Similarity survives whole. It was never an unsolicited pre-attempt hint — it is
post-mortem in the library today — and "more like this" from a post-mortem panel
is an explicit request, which is themed by definition.

### 5.1 Intent is recorded, not re-read

`?motif=` is a URL parameter, so a shared link or a bookmark can put someone in
themed mode without choosing it. Mode is therefore decided **once, at session
creation**, and stored on `TrainingSession` (`session_data` is JSON and already
exists — no migration). Every later request reads the session, never the query
string. Without this, "intent" is forgeable, and since themed sessions are
flagged for analytics, that would be a data-correctness hole rather than a
cosmetic one.

### 5.2 Blind mode has an exit

A gate with no release valve gets ripped out rather than tuned. An escalating
hint ladder already exists (`utils/puzzle-clue.ts`, rungs named in
`a11yCopy.ts`):

    0  name the piece to move
    1  highlight the destination square
    2  reveal the full solution

**The motif becomes a new rung 0**, pushing the others down — not the last rung,
as an earlier draft had it. Naming the tactic is strictly *less* revealing than
naming the piece that plays it, so putting it last would break the ladder's
monotonicity. The visible affordance goes from "Hint (n/3)" to "(n/4)".

A stuck player can ask, which is consent, and the ask is recorded:
`sessions.py:306` already increments `TrainingSession.hints_used` on every rung.

That also reframes the chip honestly. Today the tactic is given away
unrequested; here it is the cheapest hint you can spend.

## 6. When a nickname is earned

Not at creation, and — after review — not at first failure either.

The nickname's job is retrieval at the **next** encounter. Naming at the moment
of failure creates it when the user has just seen the solution and needs no cue,
and it costs a model call inside a review POST, putting provider latency on the
write path this design has kept clear throughout.

So: **a nickname is written when a failed puzzle is scheduled to return.**
`next_due_at` is set by the review; the existing job chain picks up puzzles with
`fail_count > 0` and a future `next_due_at` and names them asynchronously. The
name exists before the encounter that needs it, and no request waits on it.

~20 puzzles today rather than 318, scaling with engagement instead of corpus
size.

A puzzle with no nickname shows provenance everywhere. That is a complete label,
not a placeholder.

## 7. Data model, and the migration this costs

- `puzzle_stats.title` — the nickname. NULL until earned. Unique per user
  (`uq_puzzle_stats_username_title`, already built in `#366`).
- `puzzle_stats.title_source` — narrows to `ai | user`.
- Provenance is **derived, never stored** — a pure function of `opening_name`
  and `ply`, so it cannot drift and needs no backfill.

Two things follow that an earlier draft of this document got wrong by claiming
"nothing is dropped that took a migration":

1. `b1c2d3e4f5a6` stamped `title_source='motif'` onto 260 live rows, and
   `save_puzzle` writes `'position'` on every creation. Under this design those
   titles are not nicknames — they are the generated identifiers the whole
   effort set out to remove. **They are cleared to NULL** so provenance shows,
   and that is a migration.
2. `save_puzzle` stops writing a title at all. A puzzle is born with provenance
   and earns a nickname later, or never.

`title` is NULL for the overwhelming majority, which the unique index tolerates
(Postgres treats NULLs as distinct) and which `display_name` hides from every
consumer.

## 8. Scoring: what is decided and what is not

**Decided.** A themed session is flagged at creation (§5.1). Reviews inside it
still update `pass_count`, `fail_count` and `ease_factor` exactly as today —
skipping them would mean practice does not count, which is worse than counting
it imperfectly.

**Not decided, deliberately.** Whether a hinted or themed solve should advance
the interval *less* than a blind one. Nothing computes a rating from
`TrainingSession` today; `PuzzleStats.ease_factor` is what actually moves. The
design's job here is to *record* the distinction — mode on the session, hint use
already counted — so the question can be answered later with data. Changing the
scheduler now would be guessing.

## 9. What survives from #366

Kept whole: the model client with its gate/audit/budget/degradation, the
per-user uniqueness constraint and migration, the naming pass, and the job chain
that runs it. The position namer survives as the nickname's offline fallback.

Changed: naming trigger moves from "every puzzle" to "scheduled to return after
a failure"; the content gate is deleted; pre-attempt payloads strip motif and
nickname; `display_name` is introduced.

Costs a migration: clearing the generated titles (§7).

## 10. Rollout

Each step ships and reverts independently.

1. `display_name` + the diagnosis join on the session endpoints. Nothing visibly
   changes yet — nickname still wins wherever it exists.
2. Mode recorded at session creation (§5.1). No behaviour change.
3. Blind mode strips motif and nickname server-side; provenance shows. The
   motif becomes the last hint rung (§5.2).
4. Post-resolution panel (`#364` §4.1), reusing `MistakeDiagnosisCard`.
5. Naming trigger moves to scheduled-return; delete the content gate; clear the
   generated titles.
6. `blunder` stops rendering as a motif — `usable_motif()` already exists in
   `diagnosis/clusters.py` and is used in exactly one place; route the remaining
   renders through it. Smaller than `#364` §5 implied.

## 11. What the adversarial pass changed

Recorded because the deltas are the argument:

- **Provenance is not free** — the session payload has no diagnosis join (§3.2).
  The first draft called it "free, deterministic".
- **Enforce in the payload, not the component** — otherwise the tactic ships in
  JSON and leaks to any other client (§4).
- **First-failure was the wrong trigger** — it names when the cue is least
  needed and puts a model call on the write path (§6).
- **`?motif=` is forgeable**, so intent must be recorded at session creation
  rather than re-read per request (§5.1).
- **A gate needs an exit** — the motif became a hint rung instead of a
  prohibition (§5.2). Verifying that against the existing ladder then corrected
  the correction: it belongs *first*, not last, because it reveals less than
  naming the piece.
- **It does cost a migration** — 260 rows carry generated titles that this model
  says are not nicknames (§7).
- **§8 was asserted, not designed** — now split into what is decided and what is
  deliberately left open.

## 12. Where this is uncertain

Three things are judgement, not measurement, and are the places to push back:

1. **The trigger clock.** "Scheduled to return after a failure" is better
   argued than first-failure, but it is still a guess about when a cue helps. It
   is cheap to move.
2. **Clearing 260 titles.** Coherent with the model, and they are the bug this
   started as — but they are strings a user may have seen. The alternative is
   keeping them as grandfathered nicknames, which is defensible and messier.
3. **Whether hinted solves should schedule differently** (§8). Left open on
   purpose; it needs usage data that does not exist yet.

Everything else here is settled by measurement or by an existing decision in the
codebase.
