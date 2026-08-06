"""Operator CLI: report what the naming module would call your puzzles.

Why this exists
---------------
``puzzles.naming`` picks a name by scoring the puzzle's facts for comic salience
and taking the winner. Every individual rule is testable in isolation and all of
them pass — but the thing that decides whether the feature *works* is the
**distribution**, and no unit test can see it.

The failure mode this guards against (plan F2): if the high-salience rules
almost never fire, everything falls through to the floor rule and a Library of
500 puzzles reads "The Move 24 Shrug / The Move 31 Wobble / The Move 17 Shrug".
That is the original "all the puzzles have the same names" complaint wearing a
costume. It is a question about real data, so it gets answered against real data
**before** the migration in PR 3, not after.

This is read-only. There is no non-dry-run mode; ``--dry-run`` is accepted and
ignored so the invocation matches ``reclassify_motifs.py`` muscle memory.

What it cannot see
------------------
``user_castled`` is not persisted anywhere — it is computed during the PGN
replay in ``diagnosis.pgn_context`` and only survives as an input to the cause
rules. So the ``uncastled`` rung reports as unavailable rather than as zero, and
the report says so explicitly. Reporting "0%" for a fact we never looked up
would be a lie that reads like a measurement.

Usage:
    python -m scripts.name_puzzles --dry-run
    python -m scripts.name_puzzles --username lauureal --samples 40

Requires:
    - DATABASE_URL set (Postgres; `make docker-up` starts one locally).
    - puzzles / puzzle_stats / puzzle_diagnoses / games to exist.
"""

import argparse
import logging
import sys
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.db import SessionLocal
from services.api.models import Game, Puzzle, PuzzleDiagnosis, PuzzleStats
from services.api.puzzles.naming import PuzzleFacts, compose_name, select_rule
from services.api.time_control import classify_time_control
from services.api.usernames import canonical_username

logger = logging.getLogger("name_puzzles")

# The share of a corpus landing on the floor rule above which the vocabulary
# needs widening before any of this ships. Not a law — a threshold to argue
# with once there is a number to argue about.
FLOOR_SHARE_BUDGET = 0.40

MANUAL_GAME_ID = "__manual__"


def _evidence_value(evidence, item_id: str) -> str | None:
    """Pull one fact out of a stored ``evidence_json`` list.

    Evidence rows are ``{"id", "label", "value"}`` dicts (see
    ``diagnosis.evidence.to_evidence_items``). Only *measured* facts are stored,
    so a missing id means "not known for this puzzle", never "zero".
    """
    for item in evidence or []:
        if isinstance(item, dict) and item.get("id") == item_id:
            return item.get("value")
    return None


def _move_time_seconds(evidence) -> float | None:
    raw = _evidence_value(evidence, "clock.move_time")
    if raw is None:
        return None
    try:
        # Stored as a formatted string; strip a trailing unit if one is present.
        return float(str(raw).split()[0])
    except (ValueError, IndexError):
        return None


def _user_won(game: Game | None, username: str) -> bool | None:
    """True when the user won this game, None when it cannot be determined."""
    if game is None:
        return None
    user = canonical_username(username)
    if canonical_username(game.white_username) == user:
        return game.white_result == "win"
    if canonical_username(game.black_username) == user:
        return game.black_result == "win"
    return None


def _opponent(game: Game | None, username: str) -> str | None:
    if game is None:
        return None
    user = canonical_username(username)
    if canonical_username(game.white_username) == user:
        return game.black_username
    if canonical_username(game.black_username) == user:
        return game.white_username
    return None


def build_facts(
    puzzle: Puzzle,
    diagnosis: PuzzleDiagnosis | None,
    game: Game | None,
) -> PuzzleFacts:
    """Assemble the facts naming is allowed to see, from what is persisted.

    Deliberately reads only stored columns and ``evidence_json`` — no PGN
    replay. A report that takes an hour does not get run, and the point is to
    look at the numbers before committing to a migration.
    """
    evidence = diagnosis.evidence_json if diagnosis else None
    return PuzzleFacts(
        puzzle_id=puzzle.id,
        ply=puzzle.ply,
        opponent=_opponent(game, puzzle.username),
        user_won=_user_won(game, puzzle.username),
        time_control_class=(
            classify_time_control(game.time_control) if game is not None else None
        ),
        move_time_seconds=_move_time_seconds(evidence),
        # Not persisted anywhere — see the module docstring.
        user_castled=None,
        opening_family=diagnosis.opening_family if diagnosis else None,
        opening_name=diagnosis.opening_name if diagnosis else None,
        phase=diagnosis.phase if diagnosis else None,
        is_manual=puzzle.source_game_id == MANUAL_GAME_ID,
    )


def report(db: Session, username: str | None = None, samples: int = 25) -> dict:
    """Name every puzzle in scope and summarise the result.

    Returns a summary dict with ``total``, ``rungs`` (Counter), ``distinct``,
    ``repeats`` (name -> count, only names used more than once) and ``sample``.
    """
    stmt = (
        select(Puzzle, PuzzleStats, PuzzleDiagnosis, Game)
        .outerjoin(PuzzleStats, PuzzleStats.puzzle_id == Puzzle.id)
        .outerjoin(
            PuzzleDiagnosis,
            (PuzzleDiagnosis.puzzle_id == Puzzle.id)
            & (PuzzleDiagnosis.username == Puzzle.username),
        )
        .outerjoin(
            Game,
            (Game.game_id == Puzzle.source_game_id)
            & (Game.username == Puzzle.username),
        )
        .order_by(Puzzle.created_at)
    )
    if username:
        stmt = stmt.where(Puzzle.username == canonical_username(username))

    rungs: Counter = Counter()
    names: Counter = Counter()
    sample: list[tuple[str, str]] = []

    for puzzle, stats, diagnosis, game in db.execute(stmt).all():
        facts = build_facts(puzzle, diagnosis, game)
        rung, _ = select_rule(facts)
        # compose_name, not the raw rule output: it is what a user would see,
        # including the user-title override that must never be overwritten.
        name = compose_name(facts, user_title=stats.title if stats else None)

        if stats and stats.title:
            rung = "user_title"
        elif facts.is_manual:
            rung = "manual"

        rungs[rung] += 1
        names[name] += 1
        if len(sample) < samples:
            sample.append((rung, name))

    total = sum(rungs.values())
    return {
        "total": total,
        "rungs": rungs,
        "distinct": len(names),
        "repeats": {n: c for n, c in names.most_common(20) if c > 1},
        "sample": sample,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report the puzzle-name distribution the naming module would "
            "produce. Read-only."
        )
    )
    parser.add_argument("--username", default=None, help="Scope to one handle.")
    parser.add_argument(
        "--samples", type=int, default=25, help="Example names to print."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Accepted and ignored — this command never writes.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    db = SessionLocal()
    try:
        summary = report(db, username=args.username, samples=args.samples)
    finally:
        db.close()

    total = summary["total"]
    if not total:
        logger.info("No puzzles in scope.")
        return 0

    logger.info("Puzzles named: %d", total)
    logger.info(
        "Distinct names: %d (%.1f%%)",
        summary["distinct"],
        100 * summary["distinct"] / total,
    )
    logger.info("")
    logger.info("Salience rung distribution:")
    for rung, count in summary["rungs"].most_common():
        logger.info("  %-16s %6d  %5.1f%%", rung, count, 100 * count / total)
    logger.info("  %-16s %6s  %s", "uncastled", "n/a", "user_castled is not persisted")

    floor_share = summary["rungs"].get("floor", 0) / total
    logger.info("")
    if floor_share > FLOOR_SHARE_BUDGET:
        logger.warning(
            "FLOOR SHARE %.1f%% exceeds the %.0f%% budget — the ladder is collapsing to "
            "its fallback. Widen the vocabulary before the PR 3 migration (plan F2).",
            100 * floor_share,
            100 * FLOOR_SHARE_BUDGET,
        )
    else:
        logger.info(
            "Floor share %.1f%% is within the %.0f%% budget.",
            100 * floor_share,
            100 * FLOOR_SHARE_BUDGET,
        )

    if summary["repeats"]:
        logger.info("")
        logger.info("Most repeated names:")
        for name, count in summary["repeats"].items():
            logger.info("  %4dx  %s", count, name)
    else:
        logger.info("")
        logger.info("No name is used twice.")

    logger.info("")
    logger.info("Sample (are these actually funny? no test can tell you):")
    for rung, name in summary["sample"]:
        logger.info("  [%-14s] %s", rung, name)

    return 0


if __name__ == "__main__":
    sys.exit(main())
