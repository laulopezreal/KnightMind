"""Persistence for mistake diagnoses.

The interesting part of this module is :meth:`DiagnosisRepository.pending_puzzle_ids`.
It is both the backfill's work query and its resume cursor: "needs work" is a
predicate over the version columns, not a stored progress marker. A crashed,
canceled, or budget-exhausted run simply re-queries and continues, and there is
no cursor that can drift out of step with what is actually stored.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from services.api.diagnosis.causes import RULE_VERSION
from services.api.diagnosis.evidence import EXTRACTION_VERSION
from services.api.models import DiagnosisStatus, Puzzle, PuzzleDiagnosis


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

    def pending_puzzle_ids(self, username: str, limit: int | None = None) -> list[str]:
        """Puzzles with no current diagnosis, most recent first.

        Recency ordering is the backfill's priority rule: a mistake from last
        week says more about how the user plays now than one from two years
        ago, so a run that only gets partway through has still done the part
        that matters most.
        """
        stmt = self._pending_query(username).order_by(Puzzle.created_at.desc())
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
