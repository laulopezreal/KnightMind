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
import logging
import sys

from services.api.ai import config
from services.api.db import SessionLocal
from services.api.puzzles.naming_pass import name_puzzles

logger = logging.getLogger("ai_name_puzzles")


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
