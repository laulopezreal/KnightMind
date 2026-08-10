"""Background diagnosis job.

Walks a user's puzzles — every one of which is a blunder they played in a real
game — extracts the deterministic evidence packet, classifies the likely cause,
and stores the result.

No engine calls and no model calls: this is pure board analysis over data
already persisted, so a full-corpus backfill costs CPU and nothing else. The AI
stage layers on top later and enriches these rows rather than replacing them.

Resumability comes from the storage predicate, not from progress state — see
``storage/diagnosis_repository.py``. A run that is canceled, crashes, or stops
early simply leaves rows undone, and the next run finds them again.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, inspect, select
from sqlalchemy.orm import Session

from services.api.ai import client as ai_client
from services.api.ai import config as ai_config
from services.api.ai.prompts import build_user_prompt
from services.api.analytics_confidence import MIN_ATTEMPTS_FOR_MOTIF_RANK
from services.api.db import SessionLocal
from services.api.diagnosis.causes import classify_causes
from services.api.diagnosis.evidence import (
    EvidenceUnavailable,
    GameFacts,
    HistoryFacts,
    PuzzleFacts,
    evidence_hash,
    extract_evidence,
    to_evidence_items,
)
from services.api.diagnosis.pgn_context import (
    EMPTY_GAME_CONTEXT,
    extract_game_context,
    parse_time_control,
)
from services.api.models import (
    DiagnosisStatus,
    Game,
    Puzzle,
    PuzzleResult,
    PuzzleReview,
    PuzzleStats,
)
from services.api.storage.ai_audit_repository import (
    AIAuditRepository,
    AuditWrite,
    Budget,
    prompt_hash,
)
from services.api.storage.diagnosis_repository import (
    DiagnosisRepository,
    DiagnosisWrite,
)
from services.api.storage.game_repository import MANUAL_GAME_ID

logger = logging.getLogger(__name__)

# Puzzles analysed per job run. The default keeps a run short enough to stay
# responsive to cancellation; a backfill of a large corpus is expected to take
# several runs, which is fine because every run leaves the corpus strictly
# better diagnosed than it found it.
# Job scopes. See run_diagnosis for what each selects.
SCOPE_PENDING = "pending"
SCOPE_REENRICH = "reenrich"

DIAGNOSIS_BATCH_DEFAULT = 200
DIAGNOSIS_BATCH_MAX = 5000

# Heartbeat/progress cadence. Both write to the jobs table, so doing either per
# puzzle would generate more write traffic than the analysis itself — the
# generator bounds them the same way.
HEARTBEAT_INTERVAL = 25

# Commit cadence. Without it the whole run is one transaction, so a crash at
# puzzle 4,999 of 5,000 would discard every diagnosis it had produced —
# "resumable" would then mean "starts over". Committing in chunks makes
# progress real and bounds how much a crash can cost.
COMMIT_INTERVAL = 50

MOTIF_HISTORY_DAYS = 30


def run_diagnosis(ctx) -> dict:
    """Job handler for :attr:`JobType.DIAGNOSIS`.

    Args:
        ctx: The worker's ``JobContext``. ``params`` accepts ``limit`` and
            ``scope``.

    ``scope`` selects the work:

    * ``"pending"`` (default) — puzzles with no current diagnosis. The ordinary
      backfill.
    * ``"reenrich"`` — puzzles already diagnosed without the model. Version
      staleness cannot see these (adding an API key moves no data version), so
      this exists for the one operational event that strands a whole corpus:
      the key arriving after the backfill ran.

    Both paths run the same per-puzzle work; only the selection differs. An
    unknown scope falls back to ``"pending"`` rather than failing the job —
    a typo in a manual trigger should do the safe ordinary thing.

    Returns:
        A result dict persisted to ``Job.result_json``.
    """
    limit = _clamp(
        ctx.params.get("limit", DIAGNOSIS_BATCH_DEFAULT), 1, DIAGNOSIS_BATCH_MAX
    )
    scope = ctx.params.get("scope") or SCOPE_PENDING
    username = ctx.username

    diagnosed = unchanged = unavailable = 0
    canceled = False

    enriched = 0

    with SessionLocal() as db:
        if not _diagnosis_tables_ready(db):
            logger.error(
                "Diagnosis job skipped for %s because diagnosis tables are missing; "
                "run Alembic migrations before starting diagnosis workers",
                username,
            )
            return {
                "username": username,
                "diagnosed": 0,
                "unchanged": 0,
                "unavailable": 0,
                "enriched": 0,
                "remaining": 0,
                "canceled": False,
                "skipped": "diagnosis_tables_missing",
            }
        repo = DiagnosisRepository(db)
        audit = AIAuditRepository(db)
        pending = (
            repo.unenriched_puzzle_ids(username, limit)
            if scope == SCOPE_REENRICH
            else repo.pending_puzzle_ids(username, limit)
        )
        total = len(pending)
        if not total:
            return _result(username, db, repo, 0, 0, 0, False, 0)

        motif_rates = _motif_fail_rates(db, username)
        # Read the day's spend once, then track locally between commits. A
        # database count per puzzle would be hundreds of queries for a number
        # that moves by one each time; re-reading at each commit bounds how far
        # the local view can drift when another user's job runs concurrently.
        budget = audit.budget_last_24h(username)

        for index, puzzle_id in enumerate(pending):
            # Bounded cadence: the lease still refreshes often enough that
            # crash recovery cannot mistake a live run for a dead one.
            if index % HEARTBEAT_INTERVAL == 0:
                if ctx.heartbeat():
                    canceled = True
                    logger.info("Diagnosis canceled for %s", username)
                    break
                ctx.progress(index, total)

            outcome, budget, used_ai = _diagnose_one(
                db, repo, audit, username, puzzle_id, motif_rates, budget
            )
            if outcome == "diagnosed":
                diagnosed += 1
            elif outcome == "unchanged":
                unchanged += 1
            else:
                unavailable += 1
            if used_ai:
                enriched += 1

            if (index + 1) % COMMIT_INTERVAL == 0:
                db.commit()
                # Re-anchor on what is actually stored; another user's job may
                # have spent against the global cap since the last read.
                budget = audit.budget_last_24h(username)

        # Flush whatever the last partial chunk produced, including on the
        # cancellation path — a cancel must keep the work already done, not
        # roll it back.
        db.commit()
        return _result(
            username, db, repo, diagnosed, unchanged, unavailable, canceled, enriched
        )


def _diagnosis_tables_ready(db: Session) -> bool:
    """Return whether the diagnosis migrations are present for this database.

    A worker started against an un-migrated database would otherwise fail every
    job with an opaque ProgrammingError; this turns that into one clear log line
    and a recorded skip.
    """

    inspector = inspect(db.get_bind())
    return inspector.has_table("puzzle_diagnoses") and inspector.has_table(
        "diagnosis_audit_log"
    )


def _result(
    username, db, repo, diagnosed, unchanged, unavailable, canceled, enriched
) -> dict:
    return {
        "username": username,
        "diagnosed": diagnosed,
        "unchanged": unchanged,
        "unavailable": unavailable,
        "enriched": enriched,
        "remaining": repo.pending_count(username),
        "canceled": canceled,
    }


def _clamp(value, low: int, high: int) -> int:
    try:
        return min(max(int(value), low), high)
    except (TypeError, ValueError):
        return low


def _diagnose_one(
    db: Session,
    repo: DiagnosisRepository,
    audit: AIAuditRepository,
    username: str,
    puzzle_id: str,
    # `float | None`: _motif_fail_rates returns None for a motif with no
    # attempts, and _history_facts below takes `float | None`. The narrower
    # annotation was simply wrong -- nothing ever relied on it.
    motif_rates: dict[str, tuple[float | None, int]],
    budget: Budget,
) -> tuple[str, Budget, bool]:
    """Diagnose one puzzle. Returns (outcome, budget, used_ai)."""
    # Puzzle and PuzzleStats are keyed on puzzle_id alone (a puzzle id belongs
    # to exactly one user by construction); Game is keyed on (game_id, username)
    # because the same canonical game is imported once per participant.
    puzzle = db.get(Puzzle, puzzle_id)
    if puzzle is None or puzzle.username != username:
        # Deleted between the scan and now, or never this user's to begin with.
        return "unavailable", budget, False

    stats = db.get(PuzzleStats, puzzle_id)
    motif = stats.primary_motif if stats else None
    game = db.get(Game, (puzzle.source_game_id, username))

    try:
        packet = extract_evidence(
            _puzzle_facts(puzzle),
            _game_facts(game, puzzle),
            _game_context(game, puzzle),
            _history_facts(stats, motif, motif_rates),
        )
    except EvidenceUnavailable as exc:
        # Record the negative result. Without a row, every future run would
        # re-attempt this same un-analysable puzzle forever.
        repo.upsert(
            DiagnosisWrite(
                puzzle_id=puzzle_id,
                username=username,
                status=DiagnosisStatus.UNAVAILABLE,
                error=str(exc),
                primary_motif=motif,
                insufficient_evidence=True,
            )
        )
        return "unavailable", budget, False

    digest = evidence_hash(packet)
    assessment = classify_causes(packet)
    ai = _enrich(db, audit, username, puzzle_id, packet, assessment, digest, budget)
    if ai.attempted:
        budget = budget.spend(1)

    # The model may re-rank within the candidate set, so the strength has to be
    # looked up for whichever cause is actually stored. Reading it from the
    # rules' own pick would leave a row asserting one cause with a different
    # cause's strength.
    stored_cause = ai.primary_cause or assessment.primary_cause
    strength = next(
        (c.strength for c in assessment.candidates if c.cause == stored_cause), None
    )

    _, changed = repo.upsert(
        DiagnosisWrite(
            puzzle_id=puzzle_id,
            username=username,
            status=DiagnosisStatus.OK,
            primary_motif=motif,
            primary_strength=strength,
            insufficient_evidence=assessment.insufficient_evidence,
            phase=packet.position.phase,
            opening_family=packet.game.opening_family,
            opening_name=packet.game.opening_name,
            opening_eco=packet.game.opening_eco,
            evidence=tuple(
                {"id": item.id, "label": item.label, "value": item.value}
                for item in to_evidence_items(packet)
            ),
            evidence_hash=digest,
            source=ai.source,
            model_version=ai.model_version,
            model_confidence=ai.confidence,
            agreed_with_rules=ai.agreed_with_rules,
            explanation=ai.explanation,
            training_recommendation=ai.recommendation,
            # The model may re-rank within the rules' candidate set — that is
            # its whole remit. When it does, its ordering is what gets stored.
            primary_cause=stored_cause,
            secondary_causes=ai.secondary_causes or assessment.secondary_causes,
        )
    )
    # Reported as "enriched" only when the model's output was actually accepted
    # and stored. Counting attempts here would report a run of pure rejections
    # as fully enriched.
    return ("diagnosed" if changed else "unchanged"), budget, ai.source == "llm"


@dataclass(frozen=True)
class _Enrichment:
    """What the AI stage contributed, if anything."""

    attempted: bool = False
    source: str = "rules"
    model_version: str | None = None
    confidence: float | None = None
    agreed_with_rules: bool | None = None
    explanation: str | None = None
    recommendation: str | None = None
    primary_cause: str | None = None
    secondary_causes: tuple[str, ...] = ()


_RULES_ONLY = _Enrichment()


def _enrich(db, audit, username, puzzle_id, packet, assessment, digest, budget):
    """Attempt AI enrichment for one diagnosis, recording the attempt.

    Every outcome is audited, including the ones that never call the model —
    "we skipped 200 puzzles because the budget was exhausted" is exactly the
    kind of thing that is invisible until someone asks why the cards are bare.

    Returns rules-only enrichment on any non-accepted path. The rules diagnosis
    is already complete at this point; the model can only add prose and
    re-rank, never block.
    """
    if not ai_config.is_enabled() or not ai_config.api_key():
        # Nothing can run: the kill switch is off, or there is no key. Either
        # way write no audit row — a feature that cannot run should leave no
        # trace, rather than one identical skip row per puzzle for the life of
        # the retention window. /ops/status reports api_key_present, which is
        # where "why are the cards bare" gets answered.
        return _RULES_ONLY

    base = dict(
        username=username,
        puzzle_id=puzzle_id,
        rule_version=assessment.rule_version,
        extraction_version=packet.extraction_version,
        evidence_hash=digest,
    )

    if budget.exhausted:
        # Recorded, not raised: a spent budget is a normal end-state for a big
        # backfill, and the next day's run picks up where this one stopped.
        audit.record(
            AuditWrite(status=ai_client.SKIPPED, reason="budget_exhausted", **base)
        )
        return _RULES_ONLY

    outcome = ai_client.enrich(packet, assessment)

    audit.record(
        AuditWrite(
            status=outcome.status,
            reason=outcome.reason,
            agreed_with_rules=outcome.agreed_with_rules,
            model_version=outcome.model_version,
            prompt_hash=prompt_hash(build_user_prompt(packet, assessment)),
            response_json=outcome.raw_response,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            **base,
        )
    )

    if not outcome.usable:
        # Rejected, refused, malformed, or unreachable — all land here, and all
        # leave the rules diagnosis exactly as it was.
        return _Enrichment(attempted=outcome.status != ai_client.SKIPPED)

    # `usable` is `status == ACCEPTED and diagnosis is not None`, so this
    # holds by construction -- mypy just cannot narrow through a property.
    d = outcome.diagnosis
    assert d is not None
    return _Enrichment(
        attempted=True,
        source="llm",
        model_version=outcome.model_version,
        confidence=d.confidence,
        agreed_with_rules=outcome.agreed_with_rules,
        explanation=d.explanation.strip(),
        recommendation=d.training_recommendation.strip(),
        primary_cause=d.primary_cause,
        secondary_causes=tuple(d.secondary_causes),
    )


def _puzzle_facts(puzzle: Puzzle) -> PuzzleFacts:
    return PuzzleFacts(
        fen=puzzle.fen,
        played_move_uci=puzzle.played_move_uci,
        best_move_uci=puzzle.best_move_uci,
        ply=puzzle.ply,
        eval_before=puzzle.eval_before,
        eval_after=puzzle.eval_after,
        swing=puzzle.swing,
        accept_moves_uci=tuple(
            m for m in (puzzle.accept_moves_uci or "").split(",") if m
        ),
        solution_pv=tuple((puzzle.solution_pv or "").split()),
        confirmed_depth=puzzle.confirmed_depth,
    )


def _game_facts(game: Game | None, puzzle: Puzzle) -> GameFacts:
    """Assemble the game-level facts.

    ``user_is_white`` comes from the PUZZLE, not from matching the username
    against the game's players. A puzzle is by construction a position where it
    was the user's turn, so ``side_to_move`` is the authoritative answer, and it
    stays correct where the game row is not: manually added puzzles all record
    ``white_username = <the user>`` regardless of the position, so a
    black-to-move manual puzzle was being labelled as played with white — a fact
    contradicting its own FEN.
    """
    user_is_white = puzzle.side_to_move == "white"
    if game is None or game.game_id == MANUAL_GAME_ID:
        # No real game behind this position: no clock, no result, not rated.
        # The FEN still carries everything the board analysis needs, so the
        # diagnosis degrades rather than failing.
        return GameFacts(user_is_white=user_is_white)
    return GameFacts(
        user_is_white=user_is_white,
        time_control=parse_time_control(game.time_control),
        user_result=_user_result(game, user_is_white),
        rated=bool(game.rated),
    )


def _user_result(game: Game, user_is_white: bool) -> str | None:
    """ "win" / "loss" / "draw" from the pair of chess.com result strings.

    Chess.com marks exactly one side "win" in a decisive game and neither in a
    draw, so the outcome follows from that alone — no enumeration of the draw
    reasons ("agreed", "repetition", "stalemate", ...) is needed here.
    """
    mine = game.white_result if user_is_white else game.black_result
    theirs = game.black_result if user_is_white else game.white_result
    if mine == "win":
        return "win"
    if theirs == "win":
        return "loss"
    if mine and theirs:
        return "draw"
    return None


def _game_context(game: Game | None, puzzle: Puzzle):
    if game is None or not game.pgn_blob:
        return EMPTY_GAME_CONTEXT
    return extract_game_context(
        game.pgn_blob,
        ply=puzzle.ply,
        # Same authority as _game_facts: the puzzle position, not the game row.
        user_is_white=puzzle.side_to_move == "white",
        time_control=parse_time_control(game.time_control),
    )


def _history_facts(
    stats: PuzzleStats | None,
    motif: str | None,
    motif_rates: dict[str, tuple[float | None, int]],
) -> HistoryFacts:
    rate, sample = motif_rates.get(motif, (None, 0)) if motif else (None, 0)
    return HistoryFacts(
        puzzle_attempts=stats.attempts if stats else 0,
        puzzle_fail_count=stats.fail_count if stats else 0,
        motif=motif,
        motif_fail_rate_30d=rate,
        motif_sample_30d=sample,
    )


def _motif_fail_rates(
    db: Session, username: str
) -> dict[str, tuple[float | None, int]]:
    """30-day per-motif failure rate, computed once per run.

    Rates below ``MIN_ATTEMPTS_FOR_MOTIF_RANK`` are returned as None: the
    sample is too thin to state a rate, and the shared threshold in
    ``analytics_confidence`` is what decides that, not a number invented here.

    Counts all reviews, verified or not. Restricting to server-verified ones
    would discard most history; the stricter treatment belongs to pattern
    clustering, where a repeated *tendency* is claimed.
    """
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=MOTIF_HISTORY_DAYS
    )
    fails = func.sum(case((PuzzleReview.result == PuzzleResult.FAIL, 1), else_=0))
    stmt = (
        select(
            PuzzleStats.primary_motif.label("motif"),
            func.count().label("n"),
            fails.label("fails"),
        )
        .join(
            PuzzleStats,
            (PuzzleStats.puzzle_id == PuzzleReview.puzzle_id)
            & (PuzzleStats.username == PuzzleReview.username),
        )
        .where(
            PuzzleReview.username == username,
            PuzzleReview.reviewed_at >= since,
            PuzzleStats.primary_motif.isnot(None),
        )
        .group_by(PuzzleStats.primary_motif)
    )

    rates: dict[str, tuple[float | None, int]] = {}
    for row in db.execute(stmt).all():
        attempts = row.n or 0
        if attempts < MIN_ATTEMPTS_FOR_MOTIF_RANK:
            rates[row.motif] = (None, attempts)
        else:
            rates[row.motif] = ((row.fails or 0) / attempts, attempts)
    return rates
