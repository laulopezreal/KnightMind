# Puzzle Naming & Metadata Plan

Status: design, not yet implemented. Branch `feat/puzzle-naming-metadata`.

Two reported problems:

1. Every puzzle carries one of a handful of repeated names ("The Missed Win",
   "Pinned and Lost", "Loose Piece", "The Fork").
2. The puzzle's cause and type (blunder, pin, …) are not visible from the
   training session or from the resolution view.

They look like two bugs. They are one: **the tactical facts are rendered at the
wrong moment, and the provenance facts are never rendered at all.**

---

## 1. Why the names repeat

`services/api/puzzles/identity.py:166`

```python
def generate_puzzle_title(primary_motif: str) -> str:
    return MOTIF_TITLES.get(primary_motif, "Puzzle")
```

`MOTIF_TITLES` has seven entries. The title is a total function of the motif, so
**a corpus of any size has at most seven names.** Nothing is broken — the
function is doing exactly what it says. The design was wrong.

Written at `storage/puzzle_repository.py:161` and
`storage/spaced_repetition.py:223`, read on every Library row, session card and
detail heading.

### Aggravating factor: `blunder` is not a motif

`_classify_from_position` falls back to `"blunder"` whenever it recognises
nothing specific, and `blunder` maps to "The Missed Win". In the Library
screenshot that drives this plan, `blunder` is the majority label — so the
seven-name ceiling is in practice closer to a three-name ceiling.

The codebase already knows `blunder` is not a motif.
`services/api/diagnosis/clusters.py:41`:

> Motif values that carry no tactical information.

`usable_motif()` filters it out for clustering. The naming and rendering paths
never got the same treatment, so "no tactic identified" is presented to the user
as though it were a tactic called *The Missed Win*.

### Prior art: this failure mode already cost us one cleanup

`scripts/reclassify_motifs.py` exists because `backfill_puzzle_identity` only
fills rows where `title IS NULL`. Once a row had *any* title it was skipped
forever, so a generation of puzzles froze at `blunder` / "The Missed Win" even
after the real classifier landed. **Any design that stores a derived name
re-creates that bug.** This plan does not store derived names.

---

## 2. Why the cause and type are invisible

| Surface | Motif | Cause | Gated on resolution? |
|---|---|---|---|
| Library list (`PuzzleListItem`) | ✅ | ✅ `diagnosis_summary` | no |
| Library detail (`LibraryPuzzle.tsx`) | ✅ pre-attempt (leak) | ✅ `MistakeDiagnosisCard` | cause yes, motif no |
| Training session (`Puzzles.tsx`) | ✅ pre-attempt (leak) | ❌ **never** | no |
| Resolution (`ReviewPuzzleResponse`) | ❌ | ❌ | — |

Three distinct findings:

**a. The session payload has no cause at all.** `/daily-puzzle-sessions` and
`/puzzles/due` build their dicts from `Puzzle` + `PuzzleStats`
(`puzzles_routes.py:623-636`, `:834-851`). Neither joins `puzzle_diagnoses`. The
Library list *does* join it (`:1239`) — which is why the cause chip appears
there and nowhere else.

**b. The resolution response carries no identity.** `ReviewPuzzleResponse`
returns scheduling, `puzzle_info` (fen / best_move / side / swing) and `stats`.
No title, no motif, no cause. The moment the user is most receptive to "here is
what you missed and why", we tell them the interval is now 4 days.

**c. The training session shows the motif *before* the attempt and nothing
after.** `Puzzles.tsx:1337` (desktop) and `:1139` (mobile) render the motif
chip while `status === 'solving'`. `LibraryPuzzle.tsx:323` does the same, and
its `<h1>` at `:307` is the motif-derived title. On a puzzle whose motif is
`fork`, the page says **"The Fork"** above the board before you touch a piece.

That contradicts a gate the codebase built deliberately:

- `_SOLUTION_FIELDS` strips `best_move_uci`, `accept_moves_uci`,
  `played_move_uci`, `solution_pv` from every scored payload (audit gate 13).
- `_diagnosis_response` withholds evidence unless `reveal=true`, with a comment
  explaining that defaulting to `True` had already caused a silent bypass.
- `LibraryPuzzle.tsx:54` — *"naming the shared motif before the attempt would
  hand over the tactic."*

The similar-puzzles panel obeys that rule. The puzzle's own title does not.

---

## 3. Design

### 3.1 Two names, one identity

Every puzzle gets two derived strings and never a stored generated title.

| | Shown | Answers | Example |
|---|---|---|---|
| **Provenance name** | always, incl. pre-attempt | *where is this from?* | `Sicilian Najdorf · move 18` |
| **Insight phrase** | post-resolution only | *what did I miss?* | `Missed fork on the loose rook` |

The provenance name is the puzzle's identity: what appears in the Library list,
the session heading, the detail `<h1>`, the browser title. It is spoiler-free by
construction because it is composed only from facts about the *game*, never
about the *solution*.

The insight phrase replaces today's title in its only legitimate role — telling
you what the tactic was — and moves to where that is not cheating.

### 3.2 Uniqueness argument

Provenance names are near-unique **without a uniqueness query**, because they
are composed from the columns that are already unique:

- `uq_puzzles_username_source_game_id_ply` guarantees one puzzle per
  (user, game, ply).
- Move number is `ply // 2 + 1`. Within one game a user only blunders on their
  own moves, so ply parity is fixed and two puzzles from one game **cannot**
  share a move number.
- Across games, a collision requires same opponent, same date *and* same move
  number.

So: collisions within a game are impossible; collisions across games are rare
and resolved by the disambiguation ladder below. This is the crux of the design —
**we are not inventing uniqueness, we are finally spending the uniqueness the
schema already enforces.**

### 3.3 Provenance grammar

New pure module `services/api/puzzles/naming.py`. First template whose facts are
all present wins:

| # | Template | Example | Requires |
|---|---|---|---|
| 1 | `{opening_name} · move {n}` | `Sicilian Najdorf · move 18` | `PuzzleDiagnosis.opening_name` |
| 2 | `{opening_family} · move {n}` | `Sicilian Defence · move 18` | `opening_family` |
| 3 | `{Phase} vs {opponent} · move {n}` | `Middlegame vs hikaru · move 24` | `phase` + `Game` |
| 4 | `Move {n} vs {opponent}` | `Move 24 vs hikaru` | `Game` |
| 5 | `Move {n} · {date}` | `Move 24 · 12 Mar 2026` | `Game.end_time` |
| 6 | `Position {id[:8]}` | `Position 7677df23` | always |

Disambiguation ladder, applied only when the chosen template collides inside the
user's corpus: append `vs {opponent}` → append `{date}` → append `{id[:6]}`.
Template 6 is the terminal fallback and is still unique, so **"Puzzle" never
renders again**.

Rules:

- Manual puzzles keep the user's title (see 3.5). Provenance is not computed
  for them — `source_game_id == '__manual__'` has no game to cite.
- Opponent is `white_username`/`black_username` whichever is not `username`,
  compared through `canonical_username`.
- Dates use the shared `LOCALE` constant (`utils/locale.ts`) on the web side.
- No opening data → the name degrades to template 3/4, it does not become
  "Puzzle". Every rung is a real name.

### 3.4 Insight grammar

```python
compose_insight_phrase(motif: str | None, cause: str | None, evidence) -> str | None
```

`{Missed|Allowed} {motif} {target clause}` — e.g. `Missed fork on the loose
rook`, `Allowed a back-rank mate`. Returns `None` when `usable_motif()` rejects
the motif *and* there is no cause, rather than fabricating "The Missed Win".
Reuses `humanise_motif` / `humanise_cause` from `diagnosis/clusters.py` and the
target squares already in `evidence_json`.

### 3.5 `PuzzleStats.title` is redefined, not dropped

The column stays. Its meaning becomes **"name the user chose, NULL otherwise"** —
which is what it should always have meant. `POST /puzzles/manual` already accepts
a user-supplied title (`puzzles_routes.py:460`), so manual puzzles are unaffected;
generated puzzles simply stop writing a derived value into it.

No migration. No backfill. Existing generated titles become inert once the read
paths prefer the derived name; a one-line operator script can NULL them out
later if the search change (below) makes them noise.

**Cost to acknowledge:** `GET /puzzles?q=` currently matches title or puzzle id
(`puzzles_routes.py:1080`). Searching a derived string is not a column
comparison. Fix by searching the *components* instead: puzzle id, user title,
`Game` opponent, `PuzzleDiagnosis.opening_name`. This is strictly better than
today — searching "fork" currently returns every fork puzzle, which is a filter
the Library already offers properly, not an identity lookup.

### 3.6 Derive at read time — do not store

Recommended, for three reasons:

1. The best facts (`opening_name`, `phase`) live on `puzzle_diagnoses`, written
   **asynchronously and versioned** by the diagnosis job. A name stored at
   generation time is computed before those facts exist and is permanently worse
   than one computed after.
2. Storing means a backfill, and a backfill means a staleness class. See
   `scripts/reclassify_motifs.py` — we have already paid this bill once.
3. Cost is nil. The list query already outer-joins `puzzle_diagnoses`
   (`:1239`); it needs one added join to `games` for the opponent. The detail
   read is already a single indexed row.

If name search on large corpora later proves too slow, denormalise into a
`puzzle_stats.display_name` column recomputed by the diagnosis job, whose
`extraction_version` / `rule_version` staleness predicate already solves the
problem the old backfill got wrong. Not now.

---

## 4. Surface changes

### 4.1 Training session — `Puzzles.tsx`

**Pre-attempt** (`status === 'solving'`): provenance name, side to move,
difficulty, position counter. **Remove the motif chip** at `:1139` and `:1337`
and the motif-derived title at `:1327`.

**Post-resolution** (`correct` / `incorrect` / `revealed`): the status area
expands into a "What you missed" panel:

- insight phrase as the heading
- motif chip (`formatMotifName`) + cause chip (`primary_cause_label`)
- the pattern's one-line description from `diagnosis.patterns.identify()`
- `<MistakeDiagnosisCard />` — mounted verbatim, no new component
- existing `lastFeedback` line and scheduling result

**No backend change is required for this.** Fetch
`GET /puzzles/{id}/diagnosis?reveal=true` on resolution, exactly as
`LibraryPuzzle.tsx:50-56` already does. Existing endpoint, existing gate,
existing component, existing card states (`pending` / `unclear` /
`unavailable`) — a puzzle the diagnosis job has not reached yet renders an
honest state rather than an empty box.

Pass `reveal=true` explicitly even though `KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS`
is currently OFF (making it a no-op), so the panel keeps working when the flag
is turned on.

### 4.2 Library detail — `LibraryPuzzle.tsx`

`<h1>` becomes the provenance name (`:307`). The raw motif chip at `:323` moves
inside the resolved branch alongside the existing `MistakeDiagnosisCard`. Note
the chip currently renders `puzzle.primary_motif` un-humanised — it should use
`formatMotifName` like everywhere else.

### 4.3 Library list — `Library.tsx`

Row heading becomes the provenance name. Drop the motif chip; keep the cause
chip. Rationale: Library is exploration, not scored (the page says so at
`LibraryPuzzle.tsx:310`), and the cause is a coaching label rather than a move —
the existing `_puzzle_diagnosis_summary` docstring already calls this subset
safe. The motif names the tactic and is the stronger tell, so it goes.

> Open tension worth revisiting if Library rows ever become a launch point for a
> *scored* session: at that point the cause chip needs the same gate.

Screenshot row, before and after:

```
before   The Missed Win  NEW  MEDIUM  blunder  Cause: Loose piece awareness  B
after    Sicilian Najdorf · move 18   NEW  MEDIUM  Loose piece awareness     B
```

---

## 5. Motif recall (secondary, separable)

Independent of naming, worth doing because it improves the insight phrase and
the cause clustering:

- **Stop rendering `blunder` as a motif.** Route every render through
  `usable_motif()`; when it returns `None`, show the cause instead. "We could
  not identify the tactic" is an honest state — `DiagnosisResponse` already
  models exactly that with `unclear`.
- **Add detectors** to `_classify_from_position` for the common misses:
  skewer, discovered attack, deflection / removal of the defender, trapped
  piece, promotion, zwischenzug.
- **Measure first.** Run `python -m scripts.reclassify_motifs --dry-run`
  against prod to get the real before/after distribution. The plan above assumes
  `blunder` dominates because the screenshot shows it; confirm the number before
  sizing the detector work.

---

## 6. Sequencing

| PR | Scope | Fixes | Backend? |
|---|---|---|---|
| 1 | Session + detail: gate motif behind resolution, mount `MistakeDiagnosisCard` in the session | **Problem 2**, end to end | none |
| 2 | `puzzles/naming.py`, wire into list/detail/session, redefine `title`, component search | **Problem 1** | yes, no migration |
| 3 | Motif detectors + `usable_motif()` at every render; operator runs reclassify | name/insight quality | yes, no migration |
| 4 | Library row redesign | polish | none |

PR 1 first on purpose: it is web-only, needs no schema or endpoint change, and
closes the pre-exposure leak that PR 2 would otherwise carry forward.

## 7. Test obligations

- `naming.py` is pure — table-driven tests over the six templates, each
  disambiguation rung, missing-opening and missing-game rows, and the manual
  puzzle case.
- Uniqueness property test: over a generated corpus, no two puzzles from one
  game share a name.
- **Spoiler regression test** (the one that matters): assert no motif, title or
  cause string is in the session DOM while `status === 'solving'`, and that all
  three appear once resolved. Parameterise across `Puzzles.tsx` and
  `LibraryPuzzle.tsx` so a future surface cannot regress just one.
- `MistakeDiagnosisCard` already has coverage for `pending` / `unclear` /
  `unavailable`; extend the session tests to hit those branches.
- Run web tests from `apps/web` (from the repo root the suite loses jsdom).
- Backend: `black --check .` and `ruff` are CI merge gates.
