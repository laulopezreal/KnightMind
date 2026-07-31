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
        return self.db.get(PuzzleDiagnosis, (puzzle_id, username))

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
            PuzzleDiagnosis.username == username,
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
        stats_join = (PuzzleStats.puzzle_id == Puzzle.id) & (
            PuzzleStats.username == username
        )
        stmt = (
            self._pending_query(username)
            .outerjoin(PuzzleStats, stats_join)
            .order_by(
                # COALESCE so a puzzle never attempted (no stats row) sorts as
                # zero failures rather than last-by-NULL on Postgres.
                func.coalesce(PuzzleStats.fail_count, 0).desc(),
                Puzzle.created_at.desc(),
                Puzzle.swing.desc(),
            )
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def pending_count(self, username: str) -> int:
        return (
            self.db.scalar(
                select(func.count()).select_from(
                    self._pending_query(username).subquery()
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
        row = self.get(write.username, write.puzzle_id)
        changed = True
        if row is None:
            row = PuzzleDiagnosis(puzzle_id=write.puzzle_id, username=write.username)
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
                PuzzleDiagnosis.username == username,
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
        rows = self.db.execute(
            select(
                PuzzleDiagnosis.puzzle_id,
                cause_col.label("cause"),
                PuzzleDiagnosis.phase,
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


def _dominant(phases: dict[str, int]) -> str | None:
    """The phase a cause genuinely concentrates in, or None.

    Requires a strict majority, not a plurality. A plurality is too fragile for
    what this drives: it is rendered as "mostly middlegame" and it selects the
    pattern's *name*, so at 4-3 a single new puzzle would flip a user's named
    weakness from "King Safety Blind Spot" to "Back Rank Neglect" with no
    explanation — the same week-to-week drift that keeping names out of the
    model was meant to prevent.

    Above half, the claim is true and stable. Below it, saying nothing is both
    more honest and less jumpy.
    """
    if not phases:
        return None
    total = sum(phases.values())
    phase, count = max(phases.items(), key=lambda kv: (kv[1], kv[0]))
    return phase if count * 2 > total else None
