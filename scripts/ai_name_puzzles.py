"""Operator CLI: name puzzles with the model, one call per puzzle.

Why this exists
---------------
Puzzle titles used to come from ``generate_puzzle_title``, a seven-entry motif
table. On the real corpus that produced ``The Missed Win`` 150 times — every
puzzle whose motif classifier fell through to its ``blunder`` default. Seven
strings was the hard ceiling, however many puzzles existed.

This pass replaces those with names written from each puzzle's own position.
It is deliberately NOT in the request path: puzzle generation imports whole
games at a time, and putting a per-puzzle API call there would move provider
latency and provider outages into the import.

The result is cached in ``puzzle_stats.title`` with ``title_source='ai'``, so a
second run skips what it already named and only the new puzzles cost anything.

Properties:
    - Resumable: already-named puzzles are skipped, so an interrupted run is
      restarted by running it again.
    - Never destructive of a human's work: ``title_source='user'`` is never
      overwritten, and ``--force`` does not change that.
    - Degrades: when the model is unavailable, disabled, or its answer is
      rejected, the puzzle gets the deterministic position-derived name instead
      of nothing.
    - Budget-capped, from the same audit-log ledger diagnosis uses, under a
      separate ``call_type`` so a backfill cannot spend diagnosis's allowance.

Usage:
    # See what it would write, without writing or calling the model:
    python -m scripts.ai_name_puzzles --username lauureal --dry-run

    # Name for real:
    python -m scripts.ai_name_puzzles --username lauureal

    # Re-name puzzles that already have an AI name (after a prompt change):
    python -m scripts.ai_name_puzzles --username lauureal --force

Requires:
    - DATABASE_URL set (Postgres).
    - ANTHROPIC_API_KEY set, and KNIGHTMIND_AI_NAMING=1. Without either, every
      puzzle falls back to its deterministic name and no call is made.
"""

import argparse
import dataclasses
import logging
import sys
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.ai import config
from services.api.db import SessionLocal
from services.api.models import Game, Puzzle, PuzzleDiagnosis, PuzzleStats
from services.api.puzzles import ai_naming
from services.api.puzzles.identity import assign_primary_motif
from services.api.puzzles.position_names import (
    PositionFacts,
    compose_position_name,
    disambiguate,
)
from services.api.storage.ai_audit_repository import AIAuditRepository, AuditWrite
from services.api.usernames import canonical_username

logger = logging.getLogger("ai_name_puzzles")

# How many previously-used names to show the model. Enough to steer it off a
# phrasing it just used, short enough not to dominate the cached prefix.
AVOID_WINDOW = 25


def _evidence_value(evidence, item_id: str) -> str | None:
    """Pull one fact out of a stored ``evidence_json`` list."""
    for item in evidence or []:
        if isinstance(item, dict) and item.get("id") == item_id:
            return item.get("value")
    return None


def _move_time_seconds(evidence) -> float | None:
    raw = _evidence_value(evidence, "clock.move_time")
    if raw is None:
        return None
    try:
        return float(str(raw).split()[0])
    except (ValueError, IndexError):
        return None


def _user_won(game, username: str) -> bool | None:
    """True when the user won this game, None when it cannot be determined."""
    if game is None:
        return None
    user = canonical_username(username)
    if canonical_username(game.white_username) == user:
        return game.white_result == "win"
    if canonical_username(game.black_username) == user:
        return game.black_result == "win"
    return None


def build_facts(puzzle, diagnosis, game) -> ai_naming.NameFacts:
    """Assemble what the model may see. No handle goes in here — by design.

    ``NameFacts`` has no field for a username or an opponent, so this function
    could not leak one even if it tried. ``user_won`` is a bool derived from
    the game locally; the handles it was derived from stay here.
    """
    evidence = diagnosis.evidence_json if diagnosis else None
    return ai_naming.NameFacts(
        fen=puzzle.fen or "",
        played_move_san=ai_naming.san_or_uci(puzzle.fen, puzzle.played_move_uci or ""),
        best_move_san=ai_naming.san_or_uci(puzzle.fen, puzzle.best_move_uci or ""),
        move_number=(puzzle.ply or 0) // 2 + 1,
        phase=diagnosis.phase if diagnosis else None,
        primary_motif=None,  # filled by the caller, which knows the stats row
        opening_name=diagnosis.opening_name if diagnosis else None,
        move_time_seconds=_move_time_seconds(evidence),
        user_won=_user_won(game, puzzle.username),
    )


def name_puzzles(
    db: Session,
    username: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    limit: int | None = None,
) -> dict:
    """Name every in-scope puzzle, model-first with a deterministic fallback."""
    stmt = (
        select(Puzzle, PuzzleStats, PuzzleDiagnosis, Game)
        .outerjoin(
            PuzzleStats,
            # Both halves of the ownership key — see #360.
            (PuzzleStats.puzzle_id == Puzzle.id)
            & (PuzzleStats.username == Puzzle.username),
        )
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

    rows = db.execute(stmt).all()

    audit = AIAuditRepository(db)
    # One budget per user, read on first sight and decremented locally
    # thereafter: re-reading per puzzle would cost a query for a number only
    # this loop moves. Keyed by user rather than read once, because without
    # --username the rows span users and a single budget would be charged
    # against whichever handle happened to be asked for — leaving the per-user
    # cap unenforced for everyone else.
    budgets: dict[str, object] = {}

    def budget_for(handle: str):
        key = canonical_username(handle)
        if key not in budgets:
            budgets[key] = audit.budget_last_24h(key, call_type=ai_naming.CALL_TYPE)
        return budgets[key]

    outcomes: Counter = Counter()
    used: set[str] = set()
    recent: list[str] = []
    samples: list[tuple[str, str]] = []
    named = 0

    for puzzle, stats, diagnosis, game in rows:
        if limit is not None and named >= limit:
            break

        source = stats.title_source if stats else None
        # A name the user typed is never touched, with or without --force.
        if source == "user":
            outcomes["kept_user_title"] += 1
            if stats and stats.title:
                used.add(stats.title)
            continue
        # An existing AI name is the cache. --force is how you invalidate it
        # after changing the prompt.
        if source == "ai" and not force:
            outcomes["already_named"] += 1
            if stats and stats.title:
                used.add(stats.title)
            continue

        motif = (stats.primary_motif if stats else None) or assign_primary_motif(puzzle)
        fallback = compose_position_name(
            PositionFacts(
                fen=puzzle.fen or "",
                best_move_uci=puzzle.best_move_uci or "",
                primary_motif=motif,
                move_number=(puzzle.ply or 0) // 2 + 1,
            )
        )

        if dry_run:
            # No model call: a dry run must be free and must not consume
            # budget. It shows the deterministic floor, which is the worst
            # case for any puzzle.
            name, title_source = fallback, "position"
            outcomes["dry_run"] += 1
        elif budget_for(puzzle.username).exhausted:
            name, title_source = fallback, "position"
            outcomes["budget_exhausted"] += 1
            audit.record(
                AuditWrite(
                    username=puzzle.username,
                    call_type=ai_naming.CALL_TYPE,
                    puzzle_id=puzzle.id,
                    status=ai_naming.SKIPPED,
                    reason="budget_exhausted",
                )
            )
        else:
            facts = dataclasses.replace(
                build_facts(puzzle, diagnosis, game), primary_motif=motif
            )
            outcome = ai_naming.name_puzzle(facts, avoid=recent[-AVOID_WINDOW:])

            if outcome.status in (ai_naming.ACCEPTED, ai_naming.REJECTED):
                key = canonical_username(puzzle.username)
                budgets[key] = budgets[key].spend(1)
            audit.record(
                AuditWrite(
                    username=puzzle.username,
                    call_type=ai_naming.CALL_TYPE,
                    puzzle_id=puzzle.id,
                    status=outcome.status,
                    reason=outcome.reason,
                    model_version=outcome.model_version,
                    response_json=outcome.raw_response,
                    input_tokens=outcome.input_tokens,
                    output_tokens=outcome.output_tokens,
                )
            )
            outcomes[outcome.status] += 1
            if outcome.usable:
                name, title_source = outcome.name, "ai"
            else:
                name, title_source = fallback, "position"

        # The model is asked not to repeat itself but cannot be trusted to
        # succeed at it, so uniqueness is enforced here regardless of source.
        name = disambiguate(name, used, (puzzle.ply or 0) // 2 + 1)
        used.add(name)
        recent.append(name)
        named += 1
        if len(samples) < 40:
            samples.append((title_source, name))

        if dry_run:
            continue

        if stats is None:
            # 88 puzzles in the live corpus have no stats row at all. Without
            # this they would be permanently unnameable.
            db.add(
                PuzzleStats(
                    puzzle_id=puzzle.id,
                    username=puzzle.username,
                    primary_motif=motif,
                    title=name,
                    title_source=title_source,
                    attempts=0,
                    pass_count=0,
                    fail_count=0,
                    ease_factor=2.0,
                )
            )
            outcomes["stats_row_created"] += 1
        else:
            stats.title = name
            stats.title_source = title_source

    if dry_run:
        db.rollback()
    else:
        db.commit()

    return {
        "total": len(rows),
        "named": named,
        "outcomes": outcomes,
        "distinct": len(used),
        "samples": samples,
        # The tightest remaining allowance across the users actually touched;
        # None when nothing was ever charged (a dry run, or an empty scope).
        "budget_remaining": (
            min(b.remaining for b in budgets.values()) if budgets else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Name puzzles with the model, falling back to a deterministic "
            "position-derived name. Resumable; state-changing."
        )
    )
    parser.add_argument("--username", default=None, help="Scope to one handle.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write nothing and call nothing; show the deterministic names.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-name puzzles that already have an AI name. Never touches a "
        "name the user typed.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after naming this many puzzles (for a costed trial run).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.dry_run and not config.naming_is_enabled():
        logger.warning(
            "KNIGHTMIND_AI_NAMING is not set — every puzzle would get its "
            "deterministic name and no model call would be made. Set it to 1, "
            "or pass --dry-run if that is what you meant."
        )

    db = SessionLocal()
    try:
        summary = name_puzzles(
            db,
            username=args.username,
            dry_run=args.dry_run,
            force=args.force,
            limit=args.limit,
        )
    finally:
        db.close()

    logger.info("Puzzles in scope: %d", summary["total"])
    logger.info("Named this run:   %d", summary["named"])
    logger.info("Distinct names:   %d", summary["distinct"])
    remaining = summary["budget_remaining"]
    logger.info(
        "Naming budget left: %s",
        "n/a (nothing charged)" if remaining is None else remaining,
    )
    logger.info("")
    logger.info("Outcomes:")
    for outcome, count in summary["outcomes"].most_common():
        logger.info("  %-20s %6d", outcome, count)

    if summary["samples"]:
        logger.info("")
        logger.info("Sample of what was written:")
        for source, name in summary["samples"]:
            logger.info("  [%-8s] %s", source, name)

    return 0


if __name__ == "__main__":
    sys.exit(main())
