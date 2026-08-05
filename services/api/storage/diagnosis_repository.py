"""Persistence for mistake diagnoses.

The interesting part of this module is :meth:`DiagnosisRepository.pending_puzzle_ids`.
It is both the backfill's work query and its resume cursor: "needs work" is a
predicate over the version columns, not a stored progress marker. A crashed,
canceled, or budget-exhausted run simply re-queries and continues, and there is
no cursor that can drift out of step with what is actually stored.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session

from services.api.analytics_confidence import (
    MIN_ATTEMPTS_FOR_MOTIF_RANK,
    MIN_DIAGNOSES_FOR_CAUSE_RANK,
    MIN_PUZZLES_FOR_CAUSE_ACCURACY,
)
from services.api.diagnosis.causes import RULE_VERSION
from services.api.diagnosis.clusters import (
    ClusterKey,
    MatchTier,
    key_for,
    tiers_for,
)
from services.api.diagnosis.evidence import EXTRACTION_VERSION
from services.api.models import (
    DiagnosisStatus,
    Game,
    Puzzle,
    PuzzleDiagnosis,
    PuzzleResult,
    PuzzleReview,
    PuzzleStats,
)
from services.api.usernames import canonical_username

# Username convention: every public method here folds its ``username`` once with
# ``canonical_username`` before it reaches a query. This module previously did
# no folding at all and trusted the caller — which was true for HTTP traffic
# (the ``Username`` annotation folds at the request boundary) and unverifiable
# for everything else. See the "storage-boundary rule" section of
# ``services.api.usernames``.

# How far back a game counts as "recent" for pattern prioritisation. Long
# enough that a casual player still has a populated window, short enough that a
# habit fixed six months ago stops being weighted as current.
RECENT_WINDOW_DAYS = 90


@dataclass(frozen=True)
class CauseStat:
    """How often one cause explains this user's mistakes, and how they fare on it.

    Two independent numbers, deliberately not merged:

    ``mistakes`` is always trustworthy — every puzzle in the corpus is a real
    blunder from a real game, so this is a direct count, not a sample.

    ``accuracy`` is a proportion over *server-verified* training attempts only,
    and is None until there are enough of them. Self-reported results are
    excluded on purpose: the codebase already refuses to present them as
    verified skill (see ``PuzzleReview.verified``), and a pass rate is exactly
    the kind of claim that would launder them.
    """

    cause: str
    mistakes: int
    dominant_phase: str | None
    verified_attempts: int
    # Distinct puzzles behind those attempts. Attempts alone are not
    # independent observations: six tries at one puzzle measure recall of that
    # puzzle, not competence at the cause.
    verified_puzzles: int
    verified_passes: int
    accuracy: float | None
    insufficient_data: bool
    # The opening family this cause shows up in most, when one dominates. Same
    # strict-majority rule as the phase: a plurality would let 4 of 10 games
    # name the cause "your Sicilian problem" while six other openings disagree.
    dominant_opening: str | None = None
    # Mistakes from games played in the recent window. Keyed on the GAME's
    # end_time, not the puzzle's created_at: puzzles are all generated at import
    # time, so created_at clusters into a few moments and says nothing about
    # when the habit was actually happening.
    recent_mistakes: int = 0


@dataclass(frozen=True)
class DiagnosisWrite:
    """One diagnosis to persist. Mirrors the model, minus bookkeeping."""

    puzzle_id: str
    username: str
    status: str = DiagnosisStatus.OK
    error: str | None = None
    primary_motif: str | None = None
    primary_cause: str | None = None
    secondary_causes: tuple[str, ...] = ()
    primary_strength: float | None = None
    insufficient_evidence: bool = False
    phase: str | None = None
    # The opening family the game reached, when it was classified at all.
    opening_family: str | None = None
    opening_name: str | None = None
    opening_eco: str | None = None
    evidence: tuple[dict, ...] = ()
    evidence_hash: str | None = None
    source: str = "rules"
    model_version: str | None = None
    model_confidence: float | None = None
    agreed_with_rules: bool | None = None
    explanation: str | None = None
    training_recommendation: str | None = None


class DiagnosisRepository:
    def __init__(self, db: Session):
        self.db = db

    # -- reads ---------------------------------------------------------

    def get(self, username: str, puzzle_id: str) -> PuzzleDiagnosis | None:
        return self.db.get(PuzzleDiagnosis, (puzzle_id, canonical_username(username)))

    def _stale_clause(self):
        """A stored row that no longer reflects the current code.

        Only the version columns are checkable in SQL — comparing the evidence
        would mean re-extracting it. That is fine: a row selected here is about
        to be re-extracted anyway, and :meth:`upsert` then decides whether
        anything actually changed.
        """
        return or_(
            PuzzleDiagnosis.extraction_version.is_(None),
            PuzzleDiagnosis.rule_version.is_(None),
            PuzzleDiagnosis.extraction_version != EXTRACTION_VERSION,
            PuzzleDiagnosis.rule_version != RULE_VERSION,
        )

    def _pending_query(self, username: str) -> Select:
        # Private: callers below have already folded.
        join = (PuzzleDiagnosis.puzzle_id == Puzzle.id) & (
            PuzzleDiagnosis.username == username
        )
        return (
            select(Puzzle.id)
            .outerjoin(PuzzleDiagnosis, join)
            .where(
                Puzzle.username == username,
                or_(PuzzleDiagnosis.puzzle_id.is_(None), self._stale_clause()),
            )
        )

    def opening_practice_counts(
        self, username: str, opening_name: str
    ) -> tuple[int, int, str]:
        """How many puzzles this opening can offer, at line and family level.

        Returns ``(line_count, family_count, family)``. The caller decides which
        to offer; this only reports what exists, so the UI never has to guess
        and never has to re-derive the family itself.

        The family is computed here, from the same ``split(":", 1)`` the
        extraction uses, precisely so the frontend does not. A second copy of
        that rule in TypeScript is how the two drift the first time the
        derivation changes.
        """
        family = opening_name.split(":", 1)[0].strip()
        base = (
            PuzzleDiagnosis.username == canonical_username(username),
            PuzzleDiagnosis.status == DiagnosisStatus.OK,
        )
        line_count = (
            self.db.scalar(
                select(func.count())
                .select_from(PuzzleDiagnosis)
                .where(
                    *base,
                    func.lower(PuzzleDiagnosis.opening_name) == opening_name.lower(),
                )
            )
            or 0
        )
        family_count = (
            self.db.scalar(
                select(func.count())
                .select_from(PuzzleDiagnosis)
                .where(
                    *base,
                    func.lower(PuzzleDiagnosis.opening_family) == family.lower(),
                )
            )
            or 0
        )
        return line_count, family_count, family

    def puzzle_ids_for_opening(
        self, username: str, opening_name: str, *, family: bool = False
    ) -> set[str]:
        """Puzzles from one opening line, or from its whole family.

        A set, like ``puzzle_ids_for_cause``: the caller uses it as a
        membership test when ordering an existing candidate list. A focus
        re-orders the trainable puzzles, it does not select them.
        """
        column = (
            PuzzleDiagnosis.opening_family if family else PuzzleDiagnosis.opening_name
        )
        target = (
            opening_name.split(":", 1)[0].strip() if family else opening_name.strip()
        )
        stmt = select(PuzzleDiagnosis.puzzle_id).where(
            PuzzleDiagnosis.username == canonical_username(username),
            PuzzleDiagnosis.status == DiagnosisStatus.OK,
            func.lower(column) == target.lower(),
        )
        return set(self.db.scalars(stmt).all())

    def puzzle_ids_for_cause(self, username: str, cause: str) -> set[str]:
        """Every puzzle this user's diagnoses attribute to one cause.

        A set, not a list: the caller uses it as a membership test when
        ordering an existing candidate list, and returning an ordered result
        would invite treating it as the queue itself. It is not — a focus
        re-orders the trainable puzzles, it does not select them.

        Same predicate as the Insights counts and the library filter:
        correction over computed cause, analysable rows only.
        """
        cause_col = func.coalesce(
            PuzzleDiagnosis.user_confirmed_cause, PuzzleDiagnosis.primary_cause
        )
        stmt = select(PuzzleDiagnosis.puzzle_id).where(
            PuzzleDiagnosis.username == canonical_username(username),
            PuzzleDiagnosis.status == DiagnosisStatus.OK,
            cause_col == cause,
        )
        return set(self.db.scalars(stmt).all())

    def pending_puzzle_ids(self, username: str, limit: int | None = None) -> list[str]:
        """Puzzles with no current diagnosis, most diagnostic first.

        The ordering is the backfill's priority rule, and it decides which
        puzzles get AI enrichment when a run is cut short — by cancellation, by
        the batch limit, or by the daily budget. So it ranks by how much each
        mistake reveals:

        1. **Re-failed in training** — the blindspot survived a second
           exposure, which is the strongest signal in the corpus. Recency alone
           would bury a puzzle failed four times behind a fresh one failed
           never.
        2. **Recent** — a mistake from last week says more about how the user
           plays now than one from two years ago.
        3. **Largest swing** — among equals, the costliest blunders first.
        """
        # Both helpers are private and take the folded value; fold once here.
        username = canonical_username(username)
        stmt = self._diagnostic_order(self._pending_query(username), username)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def _diagnostic_order(self, stmt: Select, username: str) -> Select:
        """The backfill's priority rule: most diagnostic puzzle first.

        Private: ``username`` arrives already folded.

        Shared by the pending and re-enrichment queries so a run cut short — by
        cancellation, the batch limit, or the daily budget — spends what it has
        on the same puzzles in either mode.
        """
        stats_join = (PuzzleStats.puzzle_id == Puzzle.id) & (
            PuzzleStats.username == username
        )
        return stmt.outerjoin(PuzzleStats, stats_join).order_by(
            # COALESCE so a puzzle never attempted (no stats row) sorts as
            # zero failures rather than last-by-NULL on Postgres.
            func.coalesce(PuzzleStats.fail_count, 0).desc(),
            Puzzle.created_at.desc(),
            Puzzle.swing.desc(),
        )

    def unenriched_puzzle_ids(
        self, username: str, limit: int | None = None
    ) -> list[str]:
        """Diagnoses that were produced without the model, most diagnostic first.

        This is the "a key just arrived" query. Version-based staleness cannot
        see this case by design: adding ``ANTHROPIC_API_KEY`` changes no data
        version, so a corpus diagnosed while the key was absent stays
        rules-only forever without an explicit sweep.

        Deliberately NOT folded into ``_stale_clause``. A row whose model
        response is rejected keeps ``model_version = NULL``, so a standing rule
        would re-attempt that puzzle every run for the life of the deployment,
        spending budget daily on an answer that keeps failing validation. This
        is an operator action instead: it re-attempts on demand, and re-running
        it is how a transient failure gets retried.

        UNAVAILABLE rows are excluded — a puzzle that could not be analysed at
        all has nothing for the model to explain.
        """
        # One fold shared by the join predicate, the WHERE and the ordering
        # helper: three places that must agree on who the user is.
        username = canonical_username(username)
        stmt = (
            select(Puzzle.id)
            .join(
                PuzzleDiagnosis,
                (PuzzleDiagnosis.puzzle_id == Puzzle.id)
                & (PuzzleDiagnosis.username == username),
            )
            .where(
                Puzzle.username == username,
                PuzzleDiagnosis.status == DiagnosisStatus.OK,
                PuzzleDiagnosis.model_version.is_(None),
            )
        )
        stmt = self._diagnostic_order(stmt, username)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def unenriched_count(self, username: str) -> int:
        # Delegates to a public method, which folds. No second fold here.
        return len(self.unenriched_puzzle_ids(username))

    def pending_count(self, username: str) -> int:
        return (
            self.db.scalar(
                select(func.count()).select_from(
                    self._pending_query(canonical_username(username)).subquery()
                )
            )
            or 0
        )

    @staticmethod
    def _is_same_diagnosis(row: PuzzleDiagnosis, write: DiagnosisWrite) -> bool:
        """True when re-running produced exactly the diagnosis already stored.

        Compares the *outcome*, not just the evidence hash. Identical evidence
        is not sufficient: a rule-version bump is precisely the case where the
        same facts can yield a different cause, so keying "unchanged" on the
        hash alone would report no change on the runs that most often produce
        one.
        """
        return (
            row.status == write.status
            and row.primary_cause == write.primary_cause
            and list(row.secondary_causes or []) == list(write.secondary_causes)
            and row.primary_strength == write.primary_strength
            and row.insufficient_evidence == write.insufficient_evidence
            and row.evidence_hash == write.evidence_hash
            # Prose is part of the diagnosis the user reads, so a re-run that
            # only changes the wording is still a change.
            and row.explanation == write.explanation
            and row.training_recommendation == write.training_recommendation
        )

    # -- writes --------------------------------------------------------

    def upsert(self, write: DiagnosisWrite) -> tuple[PuzzleDiagnosis, bool]:
        """Insert or update in place. Returns ``(row, changed)``.

        The version columns are always written, so a row can never stay
        perpetually pending after a version bump. ``updated_at`` moves only when
        the diagnosis itself differs, which keeps it meaning "when this
        diagnosis last changed" rather than "when a job last looked at it".

        ``user_confirmed_cause`` is never touched here: a re-run of the rules
        must not silently discard a label the user fixed by hand.
        """
        # Folded once here so the lookup and the INSERT cannot disagree: an
        # upsert that probes under one key and inserts under another would
        # create a duplicate diagnosis on every run instead of updating.
        username = canonical_username(write.username)
        row = self.get(username, write.puzzle_id)
        changed = True
        if row is None:
            row = PuzzleDiagnosis(puzzle_id=write.puzzle_id, username=username)
            self.db.add(row)
        else:
            changed = not self._is_same_diagnosis(row, write)

        row.status = write.status
        row.error = write.error
        row.primary_motif = write.primary_motif
        row.primary_cause = write.primary_cause
        row.secondary_causes = list(write.secondary_causes)
        row.primary_strength = write.primary_strength
        row.insufficient_evidence = write.insufficient_evidence
        row.phase = write.phase
        row.opening_family = write.opening_family
        row.opening_name = write.opening_name
        row.opening_eco = write.opening_eco
        row.evidence_json = list(write.evidence)
        row.evidence_hash = write.evidence_hash
        row.source = write.source
        row.model_version = write.model_version
        row.model_confidence = write.model_confidence
        row.agreed_with_rules = write.agreed_with_rules
        row.explanation = write.explanation
        row.training_recommendation = write.training_recommendation
        row.extraction_version = EXTRACTION_VERSION
        row.rule_version = RULE_VERSION
        if changed:
            row.updated_at = datetime.now(timezone.utc)
        return row, changed

    def confirm_cause(
        self, username: str, puzzle_id: str, cause: str
    ) -> PuzzleDiagnosis | None:
        """Record the user's own label alongside — not over — the computed one.

        Keeping both is what makes rule accuracy measurable against real
        feedback; overwriting would destroy the only ground truth available.
        """
        row = self.get(username, puzzle_id)
        if row is None:
            return None
        row.user_confirmed_cause = cause
        row.confirmed_at = datetime.now(timezone.utc)
        return row

    # -- clusters ------------------------------------------------------

    def cluster_key_for(self, username: str, puzzle_id: str) -> ClusterKey | None:
        """The weakness coordinates of one puzzle, or None if it has no cause.

        Reads the correction over the computed cause, the same way every other
        cause-facing surface does — a cluster that disagreed with Insights
        about what a mistake *was* would read as a bug, not a feature.
        """
        row = self.get(username, puzzle_id)
        if row is None or row.status != DiagnosisStatus.OK:
            return None
        return key_for(
            row.user_confirmed_cause or row.primary_cause,
            row.primary_motif,
            row.phase,
        )

    def similar_puzzle_ids(
        self, username: str, puzzle_id: str, key: ClusterKey, limit: int
    ) -> tuple[list[str], MatchTier | None]:
        """Other puzzles sharing this weakness, tightest match that has any.

        Widens through the tiers only until one returns something, and reports
        which tier answered so the caller can say how close the match really
        is. Returning a cause-only match labelled as an exact one would be the
        easy lie here.

        Ordered by when the *puzzle* was created, newest first — a weakness you
        showed last week is more useful to revisit than the same weakness from
        two years ago. Deliberately not ``PuzzleDiagnosis.created_at``, which is
        the diagnosis job's timestamp: a backfill stamps a whole corpus within
        seconds, in scan order, so ordering by it is arbitrary and can even run
        opposite to puzzle recency — the two are unrelated facts, and only one
        of them is about the user. ``puzzle_id`` breaks ties so the order is
        total.
        """
        cause_col = func.coalesce(
            PuzzleDiagnosis.user_confirmed_cause, PuzzleDiagnosis.primary_cause
        )
        folded = canonical_username(username)
        for tier in tiers_for(key):
            conditions = [
                PuzzleDiagnosis.username == folded,
                PuzzleDiagnosis.status == DiagnosisStatus.OK,
                PuzzleDiagnosis.puzzle_id != puzzle_id,
                cause_col == key.cause,
            ]
            if tier in (MatchTier.EXACT, MatchTier.CAUSE_AND_MOTIF):
                conditions.append(PuzzleDiagnosis.primary_motif == key.motif)
            if tier in (MatchTier.EXACT, MatchTier.CAUSE_AND_PHASE):
                conditions.append(PuzzleDiagnosis.phase == key.phase)

            stmt = (
                select(PuzzleDiagnosis.puzzle_id)
                .join(
                    Puzzle,
                    (Puzzle.id == PuzzleDiagnosis.puzzle_id)
                    & (Puzzle.username == PuzzleDiagnosis.username),
                )
                .where(*conditions)
                .order_by(Puzzle.created_at.desc(), PuzzleDiagnosis.puzzle_id)
                .limit(limit)
            )
            found = list(self.db.scalars(stmt).all())
            if found:
                return found, tier
        return [], None

    # -- aggregates ----------------------------------------------------

    def cause_counts(self, username: str) -> list[tuple[str, int]]:
        """(cause, count) over analysable diagnoses, most frequent first.

        Rows the user has corrected are counted under their correction, and
        UNAVAILABLE rows are excluded — they carry no cause to count.
        """
        cause = func.coalesce(
            PuzzleDiagnosis.user_confirmed_cause, PuzzleDiagnosis.primary_cause
        )
        stmt = (
            select(cause.label("cause"), func.count().label("n"))
            .where(
                PuzzleDiagnosis.username == canonical_username(username),
                PuzzleDiagnosis.status == DiagnosisStatus.OK,
                cause.isnot(None),
            )
            .group_by(cause)
            .order_by(func.count().desc(), cause)
        )
        return [(row.cause, row.n) for row in self.db.execute(stmt).all()]

    def cause_breakdown(self, username: str) -> list[CauseStat]:
        """Per-cause counts, dominant phase, and verified accuracy.

        Assembled from two small queries rather than one clever join. The
        corpus is a few hundred rows per user, so the readable version costs
        nothing measurable — and a mode-of-phase plus a verified-only pass rate
        expressed in SQL would be considerably harder to check than to write.
        """
        cause_col = func.coalesce(
            PuzzleDiagnosis.user_confirmed_cause, PuzzleDiagnosis.primary_cause
        )
        # Three queries below share one fold: the cause rows, the verified
        # reviews and the recent-game set must all describe the same user, or
        # the accuracy column is computed against somebody else's reviews.
        username = canonical_username(username)
        rows = self.db.execute(
            select(
                PuzzleDiagnosis.puzzle_id,
                cause_col.label("cause"),
                PuzzleDiagnosis.phase,
                PuzzleDiagnosis.opening_family,
            ).where(
                PuzzleDiagnosis.username == username,
                PuzzleDiagnosis.status == DiagnosisStatus.OK,
                cause_col.isnot(None),
            )
        ).all()
        if not rows:
            return []

        # Server-verified attempts only — see CauseStat's docstring.
        verified = self.db.execute(
            select(
                PuzzleReview.puzzle_id,
                func.count().label("attempts"),
                func.sum(
                    case((PuzzleReview.result == PuzzleResult.PASS, 1), else_=0)
                ).label("passes"),
            )
            .where(
                PuzzleReview.username == username,
                PuzzleReview.verified.is_(True),
            )
            .group_by(PuzzleReview.puzzle_id)
        ).all()
        by_puzzle = {r.puzzle_id: (r.attempts or 0, r.passes or 0) for r in verified}

        # Which puzzles came from recently-played games.
        cutoff = int(
            (
                datetime.now(timezone.utc) - timedelta(days=RECENT_WINDOW_DAYS)
            ).timestamp()
        )
        recent_ids = {
            row[0]
            for row in self.db.execute(
                select(Puzzle.id)
                .join(
                    Game,
                    (Game.game_id == Puzzle.source_game_id)
                    & (Game.username == Puzzle.username),
                )
                .where(Puzzle.username == username, Game.end_time >= cutoff)
            ).all()
        }

        buckets: dict[str, dict] = {}
        for row in rows:
            bucket = buckets.setdefault(
                row.cause,
                {
                    "mistakes": 0,
                    "phases": {},
                    "openings": {},
                    "attempts": 0,
                    "passes": 0,
                    "puzzles": 0,
                    "recent": 0,
                },
            )
            bucket["mistakes"] += 1
            if row.puzzle_id in recent_ids:
                bucket["recent"] += 1
            if row.phase:
                bucket["phases"][row.phase] = bucket["phases"].get(row.phase, 0) + 1
            if row.opening_family:
                bucket["openings"][row.opening_family] = (
                    bucket["openings"].get(row.opening_family, 0) + 1
                )
            attempts, passes = by_puzzle.get(row.puzzle_id, (0, 0))
            bucket["attempts"] += attempts
            bucket["passes"] += passes
            if attempts:
                bucket["puzzles"] += 1

        stats = [
            CauseStat(
                cause=cause,
                mistakes=b["mistakes"],
                # Only when one phase actually dominates; a 3/3 split names no
                # phase rather than picking one by dictionary order.
                dominant_phase=_dominant(b["phases"]),
                dominant_opening=_dominant(b["openings"], b["mistakes"]),
                verified_attempts=b["attempts"],
                verified_puzzles=b["puzzles"],
                verified_passes=b["passes"],
                # Two gates, because they fail differently. The attempt floor
                # is the usual small-sample guard. The distinct-puzzle floor
                # exists because attempts concentrated on one puzzle are not
                # independent: solving the same position six times says you
                # remember it, not that you have stopped making the mistake.
                accuracy=(
                    b["passes"] / b["attempts"]
                    if b["attempts"] >= MIN_ATTEMPTS_FOR_MOTIF_RANK
                    and b["puzzles"] >= MIN_PUZZLES_FOR_CAUSE_ACCURACY
                    else None
                ),
                insufficient_data=b["mistakes"] < MIN_DIAGNOSES_FOR_CAUSE_RANK,
                recent_mistakes=b["recent"],
            )
            for cause, b in buckets.items()
        ]
        # Most frequent first; name breaks ties so the order is stable across
        # requests rather than following dict insertion.
        stats.sort(key=lambda s: (-s.mistakes, s.cause))
        return stats


def _dominant(counts: dict[str, int], total: int | None = None) -> str | None:
    """The value a cause genuinely concentrates in, or None.

    Requires a strict majority, not a plurality. A plurality is too fragile for
    what this drives: it is rendered as "mostly middlegame" and it selects the
    pattern's *name*, so at 4-3 a single new puzzle would flip a user's named
    weakness from "King Safety Blind Spot" to "Back Rank Neglect" with no
    explanation — the same week-to-week drift that keeping names out of the
    model was meant to prevent.

    Above half, the claim is true and stable. Below it, saying nothing is both
    more honest and less jumpy.

    ``total`` is the denominator to judge the majority against, for values that
    are not always present. Openings are the case: two classified Sicilians out
    of four mistakes is a majority of what was *classified* and not of what
    happened, and "mostly Sicilian" on half-unknown data is exactly the
    overclaim this function exists to refuse. Phases are always populated, so
    they leave it unset and the counted total is the real one.
    """
    if not counts:
        return None
    total = sum(counts.values()) if total is None else total
    phase, count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    return phase if count * 2 > total else None
