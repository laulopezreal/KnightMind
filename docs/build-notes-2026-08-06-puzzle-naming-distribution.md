# Build Notes 2026-08-06: Puzzle naming distribution, measured

Ran `python -m scripts.name_puzzles --username lauureal --samples 40` (PR #364,
branch `feat/puzzle-naming-metadata-6bc47c`) against the live Postgres,
read-only, from a throwaway worktree.

Decision rule being tested: floor share under 40% → the PR 3 migration is worth
doing; over → widen the vocabulary first.

## The script says PASS. It should not.

```
Puzzles named: 318
Distinct names: 81 (25.5%)
  user_title    230  72.3%
  floor          45  14.2%
  won_anyway     19   6.0%
  snap_decision  15   4.7%
  early_move      9   2.8%
Floor share 14.2% is within the 40% budget.
```

### Defect 1 — the floor share divides by the wrong population

`floor_share = rungs["floor"] / total` divides by all 318, including 230 rows
that short-circuit to `user_title` and never reach the ladder.

    318 = 230 titled + 88 ladder-named
    45 / 318 = 14.2%   <- reported
    45 /  88 = 51.1%   <- what the migration would actually write

51.1% is over the 40% budget. As written the metric cannot fail, because the
rung that dominates the denominator is the rung that means "naming did not run".

### Defect 2 — `user_title` does not mean the user titled it

`storage/puzzle_repository.py:161`:

    stats_title = title if title is not None else generate_puzzle_title(motif)

Every puzzle gets a motif-derived title at creation unless a caller supplies
one; `puzzles/identity.py:190` backfills NULLs the same way. Measured against
`generate_puzzle_title(stats.primary_motif)`:

    stats rows:           230
    with a title:         230
      == motif generator: 230   <- auto-written by the old scheme
      bespoke:              0   <- zero user-authored titles

The `compose_name` user-title override is right in principle. On real data it
discriminates nothing, because no human has ever set a title.

Consequence: the 230 are exactly where the original "all the puzzles have the
same name" complaint lives — `The Missed Win` ×150, `Pinned and Lost` ×24,
`The Fork` ×24, `Loose Piece` ×19, `Back Rank Panic` ×10. A migration that
respects `user_title` leaves every one of them untouched and fixes nothing the
user can see.

(The other 88 have no `PuzzleStats` row at all, rather than a NULL title.)

### Defect 3 — the printed sample shows none of the new names

`--samples 40` takes the first 40 rows by `created_at`, all titled. Every
sampled name was a pre-existing motif title; not one line of ladder output
appeared. The sample cannot answer the question its own caption asks.

### Defect 4 — the stats join omits the username predicate

    .outerjoin(PuzzleStats, PuzzleStats.puzzle_id == Puzzle.id)

`PuzzleStats` carries `username`, and the `PuzzleDiagnosis` / `Game` joins in
the same statement do carry the predicate — stats is the only one that doesn't.
Same class as the bug #360 fixed. It does not distort today's numbers (verified:
318 puzzles, 0 puzzle_ids with multiple stats rows, 318 joined rows) but it is
wrong the moment a second user shares a puzzle_id.

## What the ladder actually produces (the 88)

    floor          45  51.1%
    won_anyway     19  21.6%
    snap_decision  15  17.0%
    early_move      9  10.2%
    uncastled       -  structurally dead: user_castled is never persisted

Distinct: 74 of 88 (84.1%). Repetition is **not** the problem.

    [floor] The Move 17 Swerve / The Move 20 Shrug   / The Move 27 Yawn
    [floor] The Move 18 Waver  / The Move 10 Tumble  / The Move 20 Sigh
    [floor] The Move 11 Flub   / The Move 18 Brainfog / The Move 24 Hiccup
    [won_anyway]    Won Anyway, move 9 / move 26 / move 19
    [snap_decision] Two Seconds of Thought, move 36
    [early_move]    Already? Move 8

The floor verbs are already varied — Swerve, Shrug, Yawn, Waver, Tumble, Sigh,
Flub, Brainfog, Hiccup, Misfire, Daydream. So "widen the vocabulary" is the
wrong remedy: the failure is that half the library becomes `The Move N <verb>`,
a template with no chess in it, and more verbs makes that worse. What is missing
is high-salience rules that *fire*. `uncastled` is one rung that cannot fire at
all, because `user_castled` is computed during PGN replay in
`diagnosis.pgn_context` and never persisted.

Minor: `One Seconds of Thought, move 30` — singular/plural bug in the
snap_decision template.

## Verdict

Do not run the migration yet. The number that answers the question is 51.1%,
not 14.2%, and the migration as gated would leave the visible complaint intact.

## Follow-up: what replaced it (PR #366)

The decision was AI naming, with a position-derived fallback. Before shipping,
the same question was asked of the *new* deterministic namer, against the same
live corpus and using only pre-migration columns:

```
puzzles:               318
distinct raw names:    216  (67.9%)
needed a suffix:       102  (32.1%)

blunder-motif puzzles: 238
  distinct names:      154  (64.7%)

most repeated:  5x The Pawn to d5 / 5x Back Rank on d6
                4x The Pawn to e5 / 4x The Knight to f6
```

Read against the thing it replaces: the worst repeat goes from **150 to 5**, and
the fallback motif — the 238 puzzles that used to share one string — now carries
154 distinct names on its own, with no model involved.

The honest caveat is the 32% needing a `, move N` suffix to stay unique. That is
the same shape of tell that sank the #364 ladder, at a third of the rate and
only as a tiebreak rather than as the name itself. It is the number to re-measure
if the AI path is ever turned off for good.

Not measured here: whether the AI names are any good. That needs a real run.
