# Design: puzzle identity, and what may be seen before an attempt

Status: draft, revision 9. Six adversarial reviews have run against it; §12
records what the first two changed, because the deltas are most of the argument.
Supersedes the naming half of `#364`'s plan. Nothing here is implemented.

**Revision 9 is review 6's response, and it found that two of this document's
own proposals do not survive contact with the schema.**

**§4's gate cannot be implemented as written.** "Resolved" is defined as
`attempts > 0` *or the solution was explicitly revealed*, and nothing persists a
reveal: the endpoint writes nothing and `puzzle_stats` has no column for it. So
the second disjunct is unimplementable, and backing it needs a migration —
which falsifies §7.1's headline "this costs no new migration". That claim was
true of the naming half and false of the gate; §7.1 now says which.

**§4.1's candidate fix was never true.** Revision 7 proposed comparing the last
attempt against `next_due_at`. Those two are written in the same statement with
an interval floored at one day, so `last_reviewed_at < next_due_at` holds for
every attempted puzzle in both states the predicate must separate. An
implementer following it literally ships a gate that never opens. The predicate
has to read the clock: `attempts > 0 AND now() < next_due_at`.

Review 6 also caught revision 8 fixing the stale 27/30 in §1.1 while leaving it
in §3.1, four sections apart, in the same revision that boasted of correcting
it. Plus: the audit-log guard belongs in `name_puzzles` rather than only in the
trigger, since the chained job reaches the pass independently; "each step ships
and reverts independently" contradicted two constraints this document puts in
bold; the reversibility claim for step 6 is wrong twice over; and §8's search
fix needs the gate on the `WHERE` clause, not just the payload.

**Revision 8 is review 5's response.** Two findings are structural. **§7.3 was
missing a fourth title writer** — `spaced_repetition.py` writes a `MOTIF_TITLES`
string with no `title_source` on the first review of a puzzle with no stats row,
which means §6.1's and §7's invariants each have a live counterexample on `dev`
today, on a path no revision had listed. And **§11 step 6 omitted the
`pending_count` change** that §6 and §13.6 both required in bold to ship in that
same commit — the executable checklist contradicted the section it points at,
which is precisely how a same-commit requirement gets split.

Review 5 also caught this document doing the thing it keeps accusing itself of:
§1.1 claimed the backfill's projected 27/30 → 28/30 gain "has come true", while
§3.1's own measured table said date + move alone was already 28/30. The
prediction did not come true; the opening buys that tenant nothing. Corrected,
along with an undercount in step 7 (five leak sites, not three) and a §13.2
justification that is false in production.

**Revision 7 is review 4's response, and it is the one that found real design
defects rather than stale numbers.** Two are open and block rollout steps:
**§4.1** — the gate is monotonic, so every spaced-repetition *repeat* attempt is
pre-spoiled, which is most of what the gate exists to prevent. **§6** — the
narrowed trigger plus §7.3's `save_puzzle` change makes `pending_count`
permanently positive with no breaker, producing a two-second worker spin loop,
not the fifteen-minute drip revision 6 costed. Neither is fixed here; both are
written up with a candidate resolution, because choosing is Lau's call. Review 4
also found four smaller defects (§6's re-fire, §7.3's lost `user` path and
unbounded boot backfill, §11 step 1's unbudgeted scope) which are corrected in
place.

Revision 5 reconciled the document against `dev` at `b691baf` and against live
production on 2026-08-15, after `#377`, `#378`, `#384` and `#385` shipped. Every
code reference below was re-resolved against that tree; the naming
implementation moved under those PRs and revision 4's line numbers no longer
pointed at what they claimed. The substantive change is in §7: **when this was
written every title in production was deterministic, and now 320 of 348 are
AI-written**, which inverts what the clear touches.

Revision 6 is review 3's response, and it found that revision 5 had reconciled
too narrowly. Scoping the pass to §§6–7 left §§1, 3.1 and 11 asserting a
production state that no longer exists — most importantly **the zero diagnosis
rows that §1's whole argument was built on, which is now 30 of 30.** Every
production figure in this document was re-measured for revision 6 and is dated.
Two weaknesses in revision 5's own §7.2 are also now stated rather than
implied: the grandfather decision depends on §4 shipping first, and it widens
§6's population.

## 1. The problem, measured per tenant

The complaint was "all the puzzles have the same name". The cause:
`generate_puzzle_title` was `MOTIF_TITLES.get(motif)` — seven entries, so seven
names was the ceiling however large the corpus.

Revision 1 of this document measured one user and called the result settled.
There are two, and they are not alike. Measured against **live production**,
with the 2026-08-12 column kept because the argument was built on it:

```
                        lauureal   alfi3sr      lauureal   alfi3sr
                         (12 Aug)  (12 Aug)     (15 Aug)  (15 Aug)
puzzles                      318        30           318        30
ever attempted                23         1            23         1
failed at least once          20         0            20         0
primary_motif='blunder'      156         —            204        19
puzzle_diagnoses rows        318         0            318        30   <-- was 0
```

On 12 August `alfi3sr`'s library was `The Missed Win` ×19, `The Fork` ×7 — the
reported bug in its purest form — and it belonged to the tenant with **no
diagnosis rows at all**, so any design deriving identity from `puzzle_diagnoses`
fixed nothing for the user who most had the problem. Revision 1 did exactly
that.

**Both halves of that have since changed, and the design has to be read against
what is true now rather than what motivated it.** The diagnosis backfill has run
in production (§1.1) and that tenant is at 30/30. The naming pass has run too,
so the duplicate titles are gone as well (§7.2).

The argument survives the loss of its motivating zero, but on a weaker and more
general footing: **provenance must not depend on `puzzle_diagnoses` because
diagnosis is asynchronous**, so a puzzle generated a minute ago has no diagnosis
row whatever the backfill state. That was always the load-bearing reason. The
zero made it vivid; it was never the whole argument. What the zero *did*
uniquely establish — that a tenant can sit permanently at 0% — is now a
statement about the mechanism rather than about the data, and §1.1 keeps it.

### 1.1 The zero was a backfill gap, not missing data — and it has now been run

Worth separating, because it changes what to build versus what to run. Measured:

```
alfi3sr   161 games,  161 with pgn_blob,  161 with time_control
           30 puzzles, 0 with a NULL fen / best move / played move
```

Nothing was missing. On 12 August the rules-only diagnosis job was run against a
copy of production for that user and completed cleanly:

```
{'diagnosed': 30, 'unchanged': 0, 'unavailable': 0, 'canceled': False}

puzzle_diagnoses:  30 rows,  30 with opening_name,  0 unavailable
```

**30 of 30**, no model calls, no failures — `Sicilian Defense B27`,
`Horwitz Defense A40`, `Nimzowitsch Defense B00`. The old schema was not the
obstacle.

**It has since been run against production itself.** Measured 2026-08-15:
`alfi3sr` 30 rows / 30 with `opening_name`, `lauureal` 318 / 318. Both tenants
are at full coverage.

**The rehearsal's prediction did not come true, and revision 6 wrongly reported
that it had.** The rehearsal projected `alfi3sr` moving from 27/30 (date + move)
to 28/30 (date + opening + move). Measured against production now: date + move
alone is **28/30**, and date + opening + move is also **28/30** (§3.1). The
opening buys that tenant nothing. Revision 6 kept the projected 27 next to a
freshly measured 28 and read the pair as confirmation, which is the same
prose-outruns-measurement failure this document keeps catching in itself.

What the backfill did *not* buy, corrected: nothing at all for `alfi3sr`
provenance. It was worth running for the
cause chips, the post-mortem panel and motif recall, not for naming, which the
date already carries.

**The data is fixed; the mechanism is not, and that is the part this design
still has to care about.** Diagnosis auto-chains only from puzzle generation
(`worker.py:531`), those puzzles predated the diagnosis feature, and nothing has
changed that: there is still no backfill-on-deploy (`main.py`'s `lifespan` runs
`backfill_puzzle_identity` only), and `POST /users/{u}/diagnose` is still
manual. So **any user who stops importing games remains permanently
un-diagnosable, and today's 100% coverage is the residue of a manual run rather
than a property the system maintains.**

That is a defect in its own right, independent of naming, and §11 step 0
schedules it — as mechanism work only, since the data half is already done. The
design still does not *depend* on opening coverage — a freshly generated puzzle
has no diagnosis row either, because diagnosis is asynchronous — so the coverage
argument in §3.1 is about timing, not about a tenant being permanently
unreachable.

A third fact came from the UI. `Puzzles.tsx` renders the motif chip beside the
title *while the player is solving* (`:1139` mobile, `:1337` desktop, neither
gated on resolution). The codebase already believes this is wrong —
`LibraryPuzzle.tsx:51-52` keeps similar-puzzles post-mortem because "naming the
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
game has an `end_time` column. Measured on production: 348 of 348 puzzles
resolve to a game with a usable timestamp; zero manual puzzles exist.

**"Every game has an `end_time`" is true of the column and false of the value on
one path.** `POST /puzzles/manual` inserts a synthetic game with `end_time=0`
(`puzzles_routes.py:469-483`) — 1 Jan 1970 — and `GameRepository` excludes
`MANUAL_GAME_ID` from `get_users`, `get_game_count` and `get_all_metadata`
(`game_repository.py:148,159,181`), so a provenance builder going through that
repository finds no game row at all. Today the measurement covers it: there are
no manual puzzles. But §7.3 deliberately keeps the manual-save path alive as the
only writer of `title_source='user'`, so the combination is reachable by design:
save a manual puzzle, have its `user` title withheld pre-attempt under §4, and
`display_name` falls back to provenance reading `1 Jan 1970 · move N`. The
provenance helper in rollout step 1 must special-case a missing or zero
`end_time` rather than formatting it.

So the base is **date + move number**, available for 100% of both tenants. The
opening is *added when a diagnosis row exists*, never depended upon.

Distinctness of `(date, opening, move)`, per tenant, measured 2026-08-15, after
the §1.1 backfill:

```
lauureal   318 / 318   100%
alfi3sr     28 /  30    2 collisions
```

The backfill did **not** move the second row. Revision 8 retracted that claim in
§1.1 and left it standing here; both are corrected now. `alfi3sr` measures 28/30
with openings and 28/30 without, so the opening component buys that tenant
nothing at all.

For contrast, the bare base without the opening component:

```
lauureal   276 / 318   date + move alone
alfi3sr     28 /  30   date + move alone
```

The opening is what carries `lauureal` from 276 to 318, so it is doing real work
where it exists. Counting the rows actually involved rather than the difference
between the two cardinalities: **80 `lauureal` rows sit in 38 colliding
`(date, move)` groups, and 4 `alfi3sr` rows sit in 2 such groups.**

The opening resolves every one of `lauureal`'s 38 groups — which is what
318/318 means. It does not resolve `alfi3sr`'s 2, which is why that tenant stays
at 28/30 even with openings present on all 30 rows. Those 4 rows are the ones
that fall through to the `(source_game_id, ply)` tiebreak below, and they are
the reason the tiebreak has to exist rather than being a theoretical backstop.

When two puzzles still collide, they are disambiguated by their position within
that day's games — `(source_game_id, ply)` is unique per user by existing
constraint, so a deterministic tiebreak always exists.

Revision 1 used opening + move and measured 272/318 on one tenant. That is worse
than date + opening + move, and at the time it was also unavailable for the
other tenant.

The base stays date + move now that the §1.1 backfill has run, because the gap
was never only historical: diagnosis is asynchronous, so a puzzle generated a
minute ago has no opening either, and the mechanism that would keep coverage at
100% still does not exist. Provenance must be answerable at insert time, and
only date and move number are.

## 4. The gate is the puzzle's state, not the session's mode

Revision 1 gated on session mode. That was wrong on its primary axis: **mode is
per-session and known at one endpoint; the leak surface is per-puzzle and spread
across five.**

- `GET /puzzles/list` (`:1078`) — `title` and `primary_motif` unconditional at
  `:1365-1367`; `status` filter admits `new` at `:1255`.
- `GET /puzzles/{id}` (`:1407`) — `primary_motif` unconditional at `:1470`.
- `GET /puzzles/{id}/similar` (`:1657`), `GET /puzzles/{id}/diagnosis` (`:1588`).
- `GET /puzzles/due` — serves `title` **and** `primary_motif` unconditionally
  (`:835-836`, with the `None` defaults at `:851-852`). Revisions 4-7 listed
  only `queue_reason.pattern` here, which is the *narrow* leak and the
  self-permitted one: `pattern` is populated only when `in_focus and focus_name`
  (`:675-682`, `:855-856`), i.e. only when the caller passed `focus_cause`,
  which §5's table already allows. Auditing this route against the old bullet
  would have stripped `pattern` and left the nickname and motif in place, on
  the request §4 itself calls the one that matters most. `pattern` still
  belongs on the list — it names the diagnosed cause inside a scored
  pre-attempt payload, so it would slip past a strip list naming only "motif
  and nickname" — but it is the smaller half.

Worse, `/puzzles/due` is fetched **before** the session is created
(`usePuzzleSession.ts:502` then `:531`), so "read the mode from the session" is
unavailable at the one request that matters most. And `session_data` is
client-supplied (`sessions.py:110`), so recording intent there does not make it
unforgeable — revision 1 claimed it did.

**So the gate is a property of the puzzle, not the request:**

> A puzzle's *revealing* fields — nickname, motif, diagnosis prose, queue
> reason — are served only when that `(user, puzzle)` is **resolved**:
> `attempts > 0`, or the solution was explicitly revealed.

**The second disjunct is not implementable as written, and this costs a
migration.** Nothing persists a reveal. `POST /puzzles/{id}/reveal`
(`puzzles_routes.py:2076-2100`) checks ownership, reads the puzzle and returns
the solution — it writes nothing — and `PuzzleStats` (`models.py:234-255`) has
`attempts`, `last_reviewed_at` and `last_result` but no reveal column. So a user
who hits reveal, reads the solution and opens the step-5 post-mortem panel still
sees everything withheld, because `attempts` is 0 and the reveal left no trace.

Backing it needs a `revealed_at` column and a write in that endpoint, which
**falsifies §7.1's "this costs no new migration"** — that claim is true of the
naming half of this design and false of the gate. §7.1 is corrected accordingly.

Note also that §5's table lists "Explicit reveal / solved" as a *request-level
override*, which contradicts this section's "property of the puzzle, not the
request". Persisting the reveal is what resolves that contradiction rather than
papering over it: once stored, it is a property of the puzzle like the rest.

One predicate, applied in one serializer that every puzzle-returning route uses.
It survives deep links, a second tab, back-navigation, and a session resumed
days later *by construction*, because it describes the thing being protected
rather than the request asking for it.

`PuzzleStats.attempts` is already selected into the very response that leaks the
title (`puzzles_routes.py:1378`) and no handler consults it.

### 4.1 The gate as stated is monotonic, and that breaks the repeat attempt

**This is a hole in the central invariant, found by review 4, and it is not yet
fixed.** It is written here rather than quietly patched because the resolution
is a design choice.

`attempts > 0` never goes back to false. Combine that with §6 and the two rules
collide exactly where the product lives:

- §6 writes a nickname **only** for a puzzle that has been failed and
  rescheduled. So every puzzle that *has* a nickname necessarily has
  `attempts > 0`.
- §4 opens the gate permanently at `attempts > 0`. So for every puzzle that has
  a nickname, the gate is already open.

Therefore `/puzzles/due` serves the motif and a deliberately recall-optimised
nickname — "Bishop Had Bigger Plans" — on the review card **immediately before
the repeat attempt.** This is a spaced-repetition library: the second attempt is
the whole point, and it is the one the design spoils. §1 cites
`LibraryPuzzle.tsx:51-52` to condemn precisely this, and §10's test 1 cannot catch
it, because it seeds an *unresolved* puzzle and the failure needs a resolved one.

§2's framing hid it. "Recall is valuable *after* the attempt" is true of attempt
1 and false of every attempt after, because after attempt 1 *is* before attempt
2.

**Candidate resolution, not yet chosen.** Make resolution per-exposure rather
than lifetime: a puzzle is resolved while the user is looking at the outcome,
and closes again when it is next scheduled to return. The gate reads "resolved
since it last came due" instead of "ever resolved", with `attempts > 0` kept as
the cheap first term.

**Mechanically it must compare *now* against `next_due_at`, not the last
attempt.** Revision 7 said the latter and it is never true:
`spaced_repetition.py:260,262` sets `next_due_at = reviewed_at + interval` and
`last_reviewed_at = reviewed_at` in the same write, and `calculate_next_interval`
floors the interval at 1 day, so `last_reviewed_at < next_due_at` holds for
*every* attempted puzzle — in both of the states the predicate has to tell
apart. The previous due timestamp is overwritten and stored nowhere. So the
predicate is:

    attempts > 0 AND now() < next_due_at

which is genuinely the "slightly richer predicate" §13.5 promises, but only
because it reads the clock; an implementer following revision 7 literally would
have shipped a gate that never opens.

That keeps §4's one-predicate-in-one-serializer property and every
by-construction benefit above; it changes only what the predicate says. It also
means §10's test 1 must be parametrised over *puzzle state* (never attempted,
resolved and current, re-due after a prior attempt) rather than over routes
alone, which is the shape that would have caught this.

Until this is settled, §11 must not ship step 3 — turning the gate on while it
is monotonic delivers most of the spoiler surface it exists to remove.

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

  **"No nickname" is not a safe trigger condition on its own.** §6.1 makes NULL
  the *permanent* resting state of a puzzle whose name the gate rejected, and
  the audit-log exclusion that would stop a retry lives in `pending_count`, not
  in the pass: `name_puzzles` skips only `title_source` of `"ai"` or `"user"`
  (`naming_pass.py:238-254`), so a NULL-source row with a rejected audit row
  falls straight through to a fresh model call. Every subsequent review of that
  puzzle then buys another billed call — the per-review cost this section opens
  by refusing.

  **The fix belongs in `name_puzzles`, not only in the trigger.** The pass is
  also reached by the chained diagnosis job (`worker.py:574-585`), entirely
  independently of the review trigger, so guarding only the trigger leaves the
  same billed re-send arriving by the job path: one genuinely-pending puzzle
  keeps `pending_count > 0`, the chain runs a pass, and every previously
  rejected puzzle in scope goes back to the model on that pass and every pass
  after. Selection must consult the audit log for an accepted-or-rejected row,
  exactly as `pending_count` does — which is the same "one expression of needs
  a nickname" this section has been circling since revision 4, now located in
  the pass rather than duplicated across its callers.
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
  outside `("ai", "user")` is counted pending forever.

  **Revision 6 costed that at "one pass plus N audit rows every 15 minutes".
  That was wrong by more than two orders of magnitude, and the error mattered.**
  The 15-minute figure is `ERROR_STREAK_COOLDOWN`, and it only bounds anything
  because *failures write audit rows* — `retry_is_backed_off`
  (`naming_pass.py:491`) opens the breaker off `failing_streak`, which counts
  errored **and skipped** calls (`ai_audit_repository.py:200`: "``skipped``
  counts as a failure here, not just ``error``"). That distinction bounds this
  finding and is worth stating precisely: a puzzle that is *selected* and then
  skipped does write a row, the streak climbs, and the breaker opens as
  designed. The failure case is narrower — a pass that **selects nothing at
  all** makes no calls, writes no rows of any status, so the streak stays 0 and
  the breaker never opens. Meanwhile
  `pending_count` stays positive and `worker.py:574-585` re-queues on it. The
  worker claims the next job **about two seconds later** — the cadence the
  breaker's own docstring describes as "a spin loop… it burns that container
  whole" since `#374` gave the worker its own container.

  So this is not a residual-loop widening. It is a **hard prerequisite**:
  §7.3's change to `save_puzzle` makes NULL/NULL the birth state of every new
  puzzle, and §6's narrowing means most of them never earn an audit row.
  Shipping either without a matching `pending_count` predicate produces a
  silent two-second spin, not a slow drip. **§11 step 6 must change
  `pending_count` in the same commit as the narrowing**, and §10 needs a test
  that asserts `pending_count` reaches zero for a corpus the trigger will never
  select.

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

### 7.1 The naming half costs no new migration; the gate costs one

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
data change and four code changes, not DDL.

**Scope of that claim, corrected after review 6.** It holds for the *naming*
half of this design. It does not hold for the gate: §4's "or the solution was
explicitly revealed" has no backing store, so implementing the gate as written
needs a `revealed_at` column on `puzzle_stats` — one additive nullable column,
but a migration, and it belongs to rollout step 3 rather than to step 6.

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
can observe.

> **This argument has a hard dependency: §4's gate must already be live.** §4 is
> not implemented, and until it is, a grandfathered nickname is served
> pre-attempt — which is the spoiler bug §4 exists to prevent, on 320 rows.
> §11 orders the gate at step 3 and this decision at step 6, so the rollout
> satisfies the dependency, but only because of that ordering. **Grandfathering
> must not ship before step 3, and reverting step 3 while leaving step 6 in
> place re-opens the leak.** If the gate is ever abandoned, revisit this
> decision rather than inheriting it.

**What grandfathering also does, stated plainly.** It is not merely "keep
invisible rows until they are wanted". §6 earns a nickname only when a *failed*
puzzle is scheduled to return — 20 rows today. Keeping 320 means that after step
6 a user resolving a puzzle they have never failed sees a nickname §6's own
trigger would never have written. That is a real widening of the nickname
population beyond what §6 specifies, and it is accepted here rather than
overlooked: a passed puzzle still returns for review, and a recall cue on it is
useful rather than wrong. But it means §6 describes how nicknames are *created
from now on*, not an invariant over the whole corpus, and §10's tests should
assert the former rather than the latter. The names are also gate-clean. Measured against production on
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

### 7.3 The four code changes that make it stick

1. **`save_puzzle` stops writing a *generated* title** — the emphasis matters.
   It must keep writing an **explicitly supplied** one. `puzzle_repository.py:190-202`
   stores a caller-provided title as `title_source='user'`, reached from the
   manual-save route (`puzzles_routes.py:460` → `:532`), and that is the **only
   writer of `user` anywhere in the codebase.** §7 keeps `user` as one of the
   two legal values, so deleting the branch wholesale would both drop the name
   the user typed and orphan a value the model still declares. A puzzle is born
   with provenance *unless the caller named it*.
2. **`backfill_puzzle_identity` stops writing titles.** This is the one that
   makes the others real. It runs in `lifespan` on **every API boot**
   (`main.py:110-116`), selects exactly the rows §7.2 clears
   (`identity.py:190`, `title IS NULL`) and rewrites them with
   `title_source='position'` (`:257-258`). Without this, clearing titles is
   undone by the next deploy.

   **It also needs a new selector, and revision 6 missed this.** `title IS NULL`
   is today a shrinking set that converges to zero. Under §6.1 it becomes the
   steady state of most of the corpus, so the same query would match nearly
   every row on **every boot** — a `get_puzzle` and an `assign_primary_motif`
   per row, awaited in `lifespan` before the API serves its first request,
   growing with the corpus forever. (Revision 6 also charged a `taken_titles`
   and a commit per row; both are wrong — `taken_titles` is memoised per
   username at `identity.py:250-251` and there is a single commit at `:261`.
   The unbounded `select(...).where(title IS NULL)` with no `LIMIT` is the
   problem, and it stands.) It is
   simultaneously *under*-selective for the job it keeps: motif backfill now
   skips the 320 grandfathered rows precisely because they have titles. The
   replacement should select on what it actually still fixes —
   `primary_motif IS NULL` — and stop keying off `title` at all.
3. **`spaced_repetition.py` stops writing titles** — the writer revisions 4-7
   all missed, and the only one that is **already violating this design on
   `dev`**. On the first review of a puzzle with no stats row it creates one
   with `title=unique_title(..., generate_puzzle_title(motif), ...)` and **no
   `title_source` at all** (`storage/spaced_repetition.py:229-243`). That is the
   seven-entry `MOTIF_TITLES` table from §1 — the original bug — still being
   written today, on a path no section of this document listed.

   Two consequences, one of them present tense. **Today:** such a row has a
   non-NULL `title` with a NULL `title_source`, so §6.1's "true without
   exception" and §7's "`title_source` genuinely narrows to `ai | user`" each
   have a live counterexample, and `pending_count`'s `notin_(("ai", "user"))`
   counts the row pending forever with no audit row — §6's own trap, reached by
   a path §6 never names. **After step 6:** a user reviews a puzzle whose stats
   row is missing, gets `The Missed Win` written as its title, and §8's
   `display_name` serves it as a nickname the moment the gate opens.

4. **`scripts/reclassify_motifs.py` stops writing titles.** The same treatment,
   for the operator-run path — but a narrower risk than revision 6 claimed. It
   composes with `compose_position_name` (`:135-142`), not
   `generate_puzzle_title`; only its stale module docstring at `:13` still says
   otherwise. And it already carries
   `keep_title = stats is not None and stats.title_source in ("ai", "user")`
   (`:147`), so it cannot touch the 320 grandfathered rows and cannot undo an AI
   naming pass. It does still rewrite the `position` and NULL population
   (`:183`, `:194`), which is what has to stop.

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
  Puzzle"` (`:499`). It filters `fail_count >= 2` (`:480`).

  Revision 6 justified the move by calling those "precisely the puzzles that
  earn nicknames, and precisely the ones that will not have one yet", which
  contradicts §6: a row at `fail_count >= 2` has been failed and rescheduled at
  least twice, so it has already passed §6's trigger and is among the *most*
  likely to carry a nickname. The real exposure is narrower and still
  sufficient — `tricky` shows a puzzle the moment it reaches two failures, the
  nickname is written asynchronously by a job, and a row whose naming call was
  rejected stays NULL permanently (§6.1). The card renders `Untitled Puzzle` for
  every gap in that race: the original bug, on the surface dedicated to it.
- Library search is `lower(title) LIKE …` (`puzzles_routes.py:1249`). With
  titles NULL it silently degrades to hex-id search while the placeholder still
  says "Search by title or ID" (`Library.tsx:319`).

  **The gate has to reach the search predicate, not just the payload.** §4
  withholds a nickname from the response; the `WHERE` clause still answers
  questions about it. With the gate on and §7.2's 320 nicknames sitting on a
  mostly-unattempted corpus, typing "bishop" returns precisely the unattempted
  puzzles whose hidden nickname contains "bishop" — the field is invisible and
  perfectly queryable, which is a slower version of showing it. Moving search
  onto `display_name` in step 2 does not fix this by itself: gated nicknames
  must be excluded from the predicate, not merely from the payload. Search must
  cover
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

Under §4 it becomes three tests. Revision 6 had two, and §4.1 shows why the
first was not enough:

1. A parametrised test over every puzzle-returning route **× every puzzle
   state** — never attempted, resolved and current, and **re-due after a prior
   attempt** — asserting none of the revealing fields appear where the gate
   should be shut. Revision 6 parametrised over routes only and seeded a single
   *unresolved* puzzle, which is exactly why it could not have caught §4.1: the
   spoiler lives on a puzzle that is resolved and due again.
2. A registry test: every route returning puzzle data goes through the shared
   serializer. This is the one that catches the endpoint added next year.
3. **A termination test for `pending_count`**: seed a corpus the naming trigger
   will never select, run the chain, assert the count reaches zero and the
   worker stops re-queuing. §6's spin loop is invisible to any test that only
   checks naming *output*, because the failure is that nothing is produced,
   forever, at high frequency.

Note `puzzles/test_generator.py:661-662` asserts `stats.title == "Pawn to a3"`
and `title_source == "position"` directly on the creation path §7 removes. It is
a spec, not a fixture, and it changes with the design. Measured on `dev` at
`b691baf`: **24** assertions matching `title ==` or `title_source ==` across
`services/api/**/test_*.py`. Revision 4 said 19 without recording its pattern,
so treat 24 as a fresh count rather than as a trend. The expected string moved
too — `#384` changed the fallback to name the move the player *played*, so
revision 4's `"The Queen to d5"` no longer appears anywhere.

## 11. Rollout

Each step ships independently. **They do not all revert independently**, and
the two exceptions are both stated in bold elsewhere in this document, so the
preamble was actively misleading: §7.2 requires that grandfathering must not
ship before step 3 and that **reverting step 3 while leaving step 6 in place
re-opens the leak** on 320 nicknames, and step 6's `pending_count` change must
land in the same commit as the narrowing. An operator who hits trouble after
step 6, reads "reverts independently" and rolls back step 3 alone gets exactly
the spoiler §4 exists to prevent. Steps 0-2, 5 and 7 do revert cleanly on their
own.

0. **Give diagnosis a mechanism** (§1.1). The *data* half of this step is
   already done: the backfill has been run against production and both tenants
   sit at 100% coverage. What is left is the part that was always the point —
   diagnosis auto-chains only from puzzle generation, so a user who stops
   importing games becomes permanently un-diagnosable and today's 100% decays
   the moment a new tenant arrives. Still independent of everything below,
   still rules-only, still no model calls.

1. `display_name` + provenance helper; every puzzle route through one
   serializer. Nickname still wins wherever it exists — nothing visibly changes.

   **This step is much larger than "nothing visibly changes" suggests, and
   revisions 4-6 costed it as free.** Puzzle payloads are built across at least
   five response models in two routers: `PuzzleListItem` constructed inline at
   two separate call sites (`puzzles_routes.py:1364` and `:1467`),
   `SimilarPuzzleItem` (`:1627`), `DiagnosisResponse`, `DailyPuzzlesResponse`
   and `dashboard.py`'s `TrickyPuzzle`. Worst, `DuePuzzlesResponse.puzzles` is
   declared `list[dict]` (`:114-118`) — untyped, and it is the route §4 calls
   the one that matters most. §10's test 2 cannot pass until all of those are
   unified, so the unification *is* the step rather than a precondition of it.
2. Move `tricky` and Library search onto it (§8).
3. Turn on the resolution gate (§4). Motif, nickname, queue-reason pattern and
   diagnosis prose become post-resolution.
4. Overrides for themed / focus intent (§5); puzzle-scoped hint endpoint (§5.1).
5. Post-resolution panel (`#364` §4.1), reusing `MistakeDiagnosisCard`.
6. Retire title-writing in `save_puzzle`, `backfill_puzzle_identity`,
   `reclassify_motifs` **and `spaced_repetition.py`** (§7.3 — four writers, not
   three); clear the 28 deterministic titles (§7.2 grandfathers the 320 AI
   ones); move the naming trigger; **and narrow `pending_count` in this same
   commit.**

   The `pending_count` change is not optional and was missing from this list
   until revision 8, while §6 and §13.6 both said in bold that it had to ship
   with the narrowing. An implementer working from this checklist — which is
   the executable one — would have produced exactly the two-second spin loop §6
   spends twenty lines establishing. That is how a rollout splits a
   same-commit requirement by accident.

   The clear must not ship *before* the write is removed, or the boot backfill
   re-fills it (§7.3).

   **Deleting the content gate is removed from this step, pending a decision.**
   Revisions 4-7 listed it with a "(§6, §7)" cross-reference that resolves to
   nothing: no section argues for removing it. It cannot simply be dropped in
   either, because §7.2's grandfather case and §12's "measured empty
   population" both rest on the gate having matched squares by substring since
   `#385`. Remove it and every nickname written *after* this step loses that
   guarantee — and while §4.1 is open, such a name is served on the
   `/puzzles/due` review card immediately before the repeat attempt, so
   "Rook Takes on e8" would be a direct answer leak on the surface §4 exists to
   protect.
7. `blunder` stops rendering as a motif. `usable_motif()` already exists
   (`diagnosis/clusters.py:85`) and is applied at `clusters.py:121` and
   `puzzles_routes.py:1736`. Revisions 4-6 called this a one-site change and
   revision 7 corrected that to three; **both undercounted, and the sites that
   were missed are the ones that matter.** Raw `stats.primary_motif` is served
   unfiltered at `puzzles_routes.py:633` (`/daily-puzzle-sessions`), `:836` and
   `:852` (`/puzzles/due` — the route §4 calls the most important), `:1572`
   (`DiagnosisResponse`, fed verbatim from `diagnosis/job.py:326,345`), and at
   `puzzles_routes.py:1367` (`/puzzles/list`) and `:1470`
   (`/puzzles/{id}`) — the two routes §4 already names as leaking.

**Step 6 is cheaper to reverse than revision 4 thought, but revision 7 then
overstated it in the other direction, and review 6 was right to push back.**
Two things are wrong with "`position_names.py` regenerates them deterministically
so the clear is reversible". First, **step 6 deletes every caller that could
regenerate them**: §7.3 removes the composed-title write from `save_puzzle`,
`backfill_puzzle_identity`, `spaced_repetition` and `reclassify_motifs`, and
§6.1 stops the naming fallback writing one — after which no code path writes a
composed title to an existing row at all, so reversal means writing new code
that the same commit removed. Second, `identity.py:252` runs the composer's
output through `disambiguate` against `taken_titles`, and the clear itself
changes that set, so the suffixes need not come back the same. "Reproduces them
exactly" is wrong on uniqueness as well as on reachability.

The honest statement is narrower: the *information* is recoverable, because the
composer is deterministic given a position, but recovering it is a code change
rather than a re-run. That is still a real improvement on revision 4, which
Revision 4 called it irreversible because it cleared all 348 titles including
the AI-written ones, which cannot be regenerated without spending model calls
again. Grandfathering leaves those 320 in place, so the clear touches only the
28 `position` rows — and `position_names.py` composes those deterministically
from the played move, so re-running the composer reproduces them exactly.

It should still run last, once 1–5 have shown the gate holds. The reason is now
blast radius rather than permanence: step 6 removes the title-writing paths that
every earlier step still assumes are populating rows.

## 12. What the first two reviews changed

**Review 1** (self, with the repo open): provenance is not free — the session
payload has no diagnosis join; enforcement belongs in the payload, not the
component; first-failure was the wrong trigger; `?motif=` is forgeable; it does
cost a migration. The hint-rung correction was itself corrected: the motif sorts
*first*, not last.

**Review 2** (independent, adversarial):

- **The gating axis was wrong** — per-session mode cannot cover a per-puzzle
  leak spread over five endpoints, and `/puzzles/due` runs before the session
  exists. Now §4.
- **Single-tenant measurement.** `alfi3sr` **had** 0 diagnosis rows, so revision
  1's provenance was unreachable for the tenant with the worst duplication. Now
  §3.1 — and the zero itself is gone as of 2026-08-15 (§1.1), which is why §1
  now rests on diagnosis being asynchronous rather than on the zero.
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
   the moment a nickname is wanted.

   **Reversible, but not cheaply — revision 6 oversold this as "a one-paragraph
   edit", then revision 7 justified that with a claim that is false in
   production.** Revision 7 said the 320 rows are why `pending_count` is small
   today. They are not: `pending_count` short-circuits to 0 whenever naming is
   disabled (`naming_pass.py:454-457`), `naming_is_enabled()` defaults to False,
   and `OPERATIONS.md:628` mandates that `KNIGHTMIND_AI_NAMING` stays unset on
   the deployed stack. Production's `pending_count` is exactly 0 regardless of
   those rows.

   The conclusion survives on the conditional the whole design assumes anyway —
   that naming is eventually turned on. **Once it is**, clearing the 320 makes
   them NULL with no audit row, dropping them into the permanent pending
   population §6 describes, so flipping this decision is a scheduler change
   rather than a prose change. Reverse it only together with the
   `pending_count` predicate §6 now requires.
3. **Whether hinted solves should schedule differently** (§9). Needs usage data
   that does not exist.
4. ~~Whether `alfi3sr` having no diagnosis rows is a bug in its own right.~~
   **Resolved, and it is.** Measured in §1.1: the games and PGNs are intact and
   the opening derives from 8 of 8 real samples. The job never ran, and nothing
   would ever run it. Fixed as rollout step 0 rather than routed around.

5. **How resolution is scoped** (§4.1). Open, and it blocks rollout step 3.
   Lifetime `attempts > 0` spoils every repeat attempt; per-exposure
   ("resolved since it last came due") fixes it at the cost of a slightly
   richer predicate. The candidate is written up; the choice is not made.
6. **How the "explicitly revealed" disjunct is backed** (§4). Open, and it
   gates rollout step 3 alongside item 5. Either add a `revealed_at` column to
   `puzzle_stats` and write it from `POST /puzzles/{id}/reveal` — one additive
   nullable column, but it makes §7.1 false as originally stated — or drop the
   disjunct and accept that a user who reveals without reviewing sees nothing
   unlocked until they record a result. The second is free and slightly worse
   for the user; the first is a migration and matches what §4 already claims.
7. **Whether `pending_count` is narrowed in the same commit as the trigger**
   (§6). Not really a judgement call — the answer is yes — but it is recorded
   here because shipping the two apart is a two-second spin loop rather than a
   degradation, and that is the kind of thing a rollout splits by accident.

Everything else is settled by measurement against production, or by a decision
already made in the codebase.

**Three of the seven above are open defects rather than open questions.** Items
5 and 7 came from review 4, item 6 from review 6 — the latter against a document
that had by then survived five reviews and read as finished, and which had used
its own opening paragraph to boast about correcting the previous round.

That is the pattern this codebase keeps reproducing, and it is now reproduced in
prose as reliably as in patches: seven consecutive fix commits each introduced a
defect during the naming work, and six consecutive reviews of this document have
each found something real. Review 6's two headline findings were both cases of
this document proposing something the schema does not support. A design doc is
no more exempt than a patch, and the count of reviews it has survived predicts
nothing.
