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

Every puzzle gets two derived strings.

| | Shown | Answers | Register | Example |
|---|---|---|---|---|
| **Comic name** | always, incl. pre-attempt | *where is this from?* | funny | `The 2 A.M. Opinion` |
| **Insight phrase** | post-resolution only | *what did I miss?* | factual | `Missed fork on the loose rook` |

The comic name is the puzzle's identity: Library row, session heading, detail
`<h1>`, browser title. The insight phrase replaces today's title in its only
legitimate role — telling you what the tactic was — and moves to where saying it
is not cheating.

**Two rules make the humour work rather than break things.**

**Rule 1 — the joke has to be true.** The name is not a canned gag attached to a
puzzle; it is *the most absurd real fact about that puzzle, stated plainly*.
A generator that invents a joke produces noise, and noise repeats as badly as
seven canned titles do. Section 3.3 is therefore a salience picker over facts,
not a joke list.

**Rule 2 — never joke about the tactic.** "The Fork Fiasco" is funnier than "The
Fork" and leaks exactly as much. Every humour source in 3.3 is drawn from
provenance and damage — move number, opening, clock, time of day, eval swing —
all of which are spoiler-free. The motif stays behind the resolution gate where
§2 put it. Comedy about the *mistake* is allowed in the insight phrase, after
the attempt, where it costs nothing.

Together these give the funny names the same uniqueness guarantee as the dry
ones: they are still composed from the (game, ply) key the schema already
enforces as unique (3.2), just in a different register.

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

### 3.3 Comic grammar — a salience picker, not a joke list

New pure module `services/api/puzzles/naming.py`. Each rule scores how comically
loaded its fact is *for this puzzle*; the highest score wins; ties break on
`hash(puzzle_id)` so the choice is deterministic.

| Salience | Fact | Condition | Template | Example |
|---|---|---|---|---|
| 5 | opening | `opening_family` in `ABSURD_OPENINGS` | `{Opening} Incident` | `Bongcloud Incident` |
| 5 | swing | `swing >= 7.0` | `The {word(n)}-Pawn Donation` | `The Nine-Pawn Donation` |
| 4 | clock | game ended 00:00–04:59 local | `The {h} A.M. Opinion` | `The 2 A.M. Opinion` |
| 4 | ply | move `>= 50` | `Move {n} Meltdown` | `Move 61 Meltdown` |
| 4 | ply | move `<= 8` | `Already? Move {n}` | `Already? Move 6` |
| 3 | time control | `classify_time_control() == "bullet"` | `Bullet, Obviously` (+ move) | `Bullet, Obviously — move 22` |
| 2 | phase | `phase == "endgame"` | `Endgame {Epithet}` | `Endgame Wobble` |
| 1 | — | always available | `The Move {n} {Epithet}` | `The Move 24 Shrug` |

Notes:

- **`ABSURD_OPENINGS`** is where chess does the work for us: Bongcloud,
  Orangutan, Fried Liver, Monkey's Bum, Hippopotamus, Elephant Gambit,
  Halloween Gambit, Latvian. A short curated set — everything not in it falls
  through to the next rule rather than being forced into a joke.
- **`EPITHETS`** is the seeded fallback vocabulary: *Shrug, Wobble, Faceplant,
  Detour, Daydream, Mirage, Hiccup, Lapse, Sigh, Brainfog, …* Seeded on
  `puzzle_id`, so it is stable across renders and sessions — never
  `random`/`Math.random()`.
- Rule 1 is the floor and it still carries the move number, so **it is unique by
  3.2 and funny at the same time.** "Puzzle" never renders again.
- Manual puzzles keep the user's own title (3.5); `source_game_id ==
  '__manual__'` has no game to be funny about.
- Opponent, where a template uses it, is `white_username`/`black_username`
  whichever is not `username`, compared through `canonical_username`.
- Local time for the clock rule comes from `Game.end_time` (epoch int) rendered
  through the shared `LOCALE` constant (`utils/locale.ts`).

### 3.4 Earned nicknames

`PuzzleStats.fail_count` is the best comic material in the schema, because it is
about persistence rather than stupidity — a puzzle you have failed four times
has *earned* a name.

But a name that changes as you fail is a broken identity. So: **stable root,
earned decoration.** The 3.3 name never changes; a suffix is appended by the
render layer at thresholds:

| fail_count | Suffix |
|---|---|
| 3 | ` (nemesis)` |
| 6 | ` (arch-nemesis)` |

The root stays searchable and stable; the decoration is a badge the puzzle wins.

### 3.5 Tone

One rule: **punch at the position, not the player.** These are names for the
user's own blunders, shown at the moment they have just failed the same puzzle
for the fourth time. Self-deprecating is right; mocking is not.

Concretely — no second person anywhere in a name ("The Move 24 Shrug", never
"Your Move 24 Shrug"), and nothing in `EPITHETS` that rates the person rather
than the moment. *Wobble, Daydream, Mirage* are in. *Idiotic, Clueless,
Embarrassing* are out.

### 3.6 Insight grammar stays factual

```python
compose_insight_phrase(motif: str | None, cause: str | None, evidence) -> str | None
```

`{Missed|Allowed} {motif} {target clause}` — e.g. `Missed fork on the loose
rook`, `Allowed a back-rank mate`. Returns `None` when `usable_motif()` rejects
the motif *and* there is no cause, rather than fabricating "The Missed Win".
Reuses `humanise_motif` / `humanise_cause` from `diagnosis/clusters.py` and the
target squares already in `evidence_json`.

This one stays deadpan on purpose. It is the teaching surface, and it is read
right after a failure — the name already carried the joke. If a quip is wanted
here, it goes on its own line *beneath* the phrase, never replacing it.

### 3.7 A toggle, because humour is not universal

`knightmind:puzzle_names` — `playful` (default) | `plain` — persisted with the
existing `useLocalStorage` hook behind a context provider, exactly as
`PuzzleModeContext` does for session type.

`plain` renders the same facts in the dry register: `Sicilian Najdorf · move
18`, `Middlegame vs hikaru · move 24`, `Move 24 · 12 Mar 2026`. **One module,
one fact-extraction pass, two output registers** — not two naming systems. That
also gives non-English users a sane fallback, since the humour is English-only
and does not translate.

### 3.8 `PuzzleStats.title` is redefined, not dropped

The column stays. Its meaning becomes **"name the user chose, NULL otherwise"** —
which is what it should always have meant. `POST /puzzles/manual` already accepts
a user-supplied title (`puzzles_routes.py:460`), so manual puzzles are unaffected;
generated puzzles simply stop writing a derived value into it.

No migration. No backfill. Existing generated titles become inert once the read
paths prefer the derived name; a one-line operator script can NULL them out
later if the search change (below) makes them noise.

### 3.9 Store the comic name — the funny requirement flips this

An earlier draft of this plan recommended deriving names at read time and never
storing them. **Funny names change that call, because of search.**

A dry name is looked up by its facts, so component search over (puzzle id, user
title, opponent, `opening_name`) covers it. A funny name is looked up *by
itself* — that is the whole point of it being memorable. The user who wants "the
2 A.M. one" or "the meltdown one" types `meltdown`, and no component search will
ever find it, because "meltdown" exists only in the rendered string.

So: **new nullable column `puzzle_stats.display_name`**, written by the
diagnosis job, plus `name_version: int` folded into the job's existing staleness
predicate.

This does **not** re-create the `backfill_puzzle_identity` bug from §1. That bug
was *"skip any row whose title is non-NULL"* — a permanent skip with no way back.
The diagnosis job's staleness is a **predicate over version columns**
(`extraction_version` / `rule_version`, `models.py:PuzzleDiagnosis` docstring), so
bumping `name_version` re-runs every affected row automatically. Tuning the
`EPITHETS` list or adding an entry to `ABSURD_OPENINGS` becomes a version bump,
not an operator script. That is the pattern the repo already chose, and it is the
right one to join.

Consequences:

- `GET /puzzles?q=` keeps working as a plain column match, now against a name
  worth searching. Extend it to `display_name OR title OR id`.
- The job already runs and already recomputes; naming rides along at no extra
  query cost.
- The comic/plain toggle (3.7) is a **render-time** choice, so `display_name`
  stores the playful string and `plain` recomposes from facts client-side or via
  the same pure module. Store one, derive the other.

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
| 2 | `puzzles/naming.py` (pure, both registers) + its tests | — | yes, no wiring |
| 3 | `display_name` + `name_version` column, diagnosis job writes it, `?q=` matches it | **Problem 1** | yes, **migration** |
| 4 | Web: render `display_name`, earned-nickname suffix, `knightmind:puzzle_names` toggle | **Problem 1** visible | none |
| 5 | Motif detectors + `usable_motif()` at every render; operator runs reclassify | name/insight quality | yes, no migration |
| 6 | Library row redesign | polish | none |

PR 1 first on purpose: it is web-only, needs no schema or endpoint change, and
closes the pre-exposure leak the rest would otherwise carry forward.

PR 2 lands `naming.py` alone and unwired. The comic vocabulary is the part most
likely to want a second opinion, and a pure module with a table-driven test file
is the cheapest possible thing to argue about — much cheaper than arguing about
it inside a migration.

Rehearse PR 3's migration against a restored replica from prod's real revision,
downgrade included, per `docs/` migration practice.

## 7. Test obligations

- `naming.py` is pure — table-driven tests over every salience rule, the
  tie-break, both registers, missing-opening / missing-game / missing-clock
  rows, and the manual puzzle case.
- **Determinism test**: the same puzzle id yields the same epithet across
  processes. Guards against anyone reaching for `random` to add variety.
- **Uniqueness property test**: over a generated corpus, no two puzzles from one
  game share a name — including at the salience-1 fallback, which is where
  collisions would actually show up.
- **Tone test** (cheap and worth it): assert no `EPITHETS` entry and no template
  contains a second-person pronoun. §3.5 is a rule the vocabulary can drift out
  of silently otherwise.
- Earned-nickname suffix: assert the *root* is unchanged at `fail_count` 0/3/6,
  so the badge never becomes part of the identity.
- **Spoiler regression test** (the one that matters): assert no motif, title or
  cause string is in the session DOM while `status === 'solving'`, and that all
  three appear once resolved. Parameterise across `Puzzles.tsx` and
  `LibraryPuzzle.tsx` so a future surface cannot regress just one.
- `MistakeDiagnosisCard` already has coverage for `pending` / `unclear` /
  `unavailable`; extend the session tests to hit those branches.
- Run web tests from `apps/web` (from the repo root the suite loses jsdom).
- Backend: `black --check .` and `ruff` are CI merge gates.
