"""Operator CLI: reclassify existing puzzle motifs and titles.

Why this exists
---------------
The earliest puzzles were given identity data (``primary_motif`` + ``title``)
by ``backfill_puzzle_identity`` *before* the real motif classifier landed (see
#195). That backfill only fills rows whose ``title`` is NULL, so once a row has
*any* title it is skipped forever. The result: a large batch of old puzzles is
frozen at the fallback motif ``"blunder"`` / title ``"The Missed Win"`` even
though the current ``assign_primary_motif`` would recognise a fork, a pin, a
back-rank mate, a hanging queen, etc.

This CLI re-runs the *current* ``assign_primary_motif`` / ``generate_puzzle_title``
over EXISTING puzzles and updates ``PuzzleStats.primary_motif`` + ``title`` only
when they differ from what the classifier now produces. If a puzzle has no
``PuzzleStats`` row yet, it creates one with the computed identity. It reads the
position (``fen``) and solution (``best_move_uci``) from the ``puzzles`` table, so
no engine run is required — it is pure reclassification.

Properties:
    - Idempotent: a second run makes zero changes (nothing differs anymore).
    - State-changing: this is NOT wired into app startup on purpose. An operator
      runs it deliberately against prod.
    - ``--dry-run`` reports the before/after motif distribution and the number of
      rows that WOULD change, without writing anything.

Usage:
    # Dry run over every puzzle (report only, no writes):
    python -m scripts.reclassify_motifs --dry-run

    # Reclassify every puzzle for real:
    python -m scripts.reclassify_motifs

    # Restrict to one Chess.com handle:
    python -m scripts.reclassify_motifs --username lauureal
    python -m scripts.reclassify_motifs --username lauureal --dry-run

Requires:
    - DATABASE_URL set (or KNIGHTMIND_DEV_SQLITE=1 for local dev).
    - The puzzles / puzzle_stats tables to exist (alembic upgrade head).
"""

import argparse
import logging
import sys
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.db import SessionLocal
from services.api.models import Puzzle, PuzzleStats
from services.api.puzzles.identity import (
    assign_primary_motif,
    generate_puzzle_title,
)
from services.api.usernames import canonical_username

logger = logging.getLogger("reclassify_motifs")


def _format_distribution(counter: Counter) -> str:
    """Render a motif->count distribution as a stable, sorted one-liner."""
    if not counter:
        return "(none)"
    # Sort by descending count, then motif name, for a deterministic report.
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{motif}={count}" for motif, count in items)


def reclassify_motifs(
    db: Session,
    username: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Re-run the current classifier over existing puzzles and update stale rows.

    Args:
        db: Open SQLAlchemy session.
        username: Optional Chess.com handle filter, folded to its canonical
            storage key (whitespace-stripped, lowercased) before scoping.
        dry_run: When True, compute and report but write nothing.

    Returns:
        A summary dict with ``total``, ``created``, ``changed_existing``,
        ``reclassified``, ``before`` (Counter), and ``after`` (Counter).
        ``before``/``after`` are keyed by motif, with ``None`` collapsed to the
        literal string ``"<unclassified>"`` and missing stats rows counted as
        ``"<missing_stats>"`` in ``before``.
    """
    stmt = select(Puzzle, PuzzleStats).outerjoin(
        PuzzleStats, PuzzleStats.puzzle_id == Puzzle.id
    )
    if username:
        # Fold to the same canonical storage key every API entry point uses, so a
        # handle with stray whitespace/case still scopes to the stored rows
        # instead of silently matching nothing.
        stmt = stmt.where(Puzzle.username == canonical_username(username))

    rows = db.execute(stmt).all()

    before: Counter = Counter()
    after: Counter = Counter()
    created = 0
    changed_existing = 0

    for puzzle, stats in rows:
        # assign_primary_motif reads .fen / .best_move_uci off the ORM row.
        new_motif = assign_primary_motif(puzzle)
        new_title = generate_puzzle_title(new_motif)

        if stats is None:
            before["<missing_stats>"] += 1
        else:
            before[
                (
                    stats.primary_motif
                    if stats.primary_motif is not None
                    else "<unclassified>"
                )
            ] += 1
        after[new_motif] += 1

        if stats is None:
            created += 1
            if not dry_run:
                db.add(
                    PuzzleStats(
                        puzzle_id=puzzle.id,
                        username=puzzle.username,
                        primary_motif=new_motif,
                        title=new_title,
                    )
                )
        elif new_motif != stats.primary_motif or new_title != stats.title:
            changed_existing += 1
            if not dry_run:
                stats.primary_motif = new_motif
                stats.title = new_title

    if dry_run:
        db.rollback()
    else:
        db.commit()

    return {
        "total": len(rows),
        "created": created,
        "changed_existing": changed_existing,
        "reclassified": created + changed_existing,
        "before": before,
        "after": after,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reclassify existing puzzle motifs/titles using the current "
            "classifier. Idempotent; state-changing (run manually, not on "
            "startup)."
        )
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Only reclassify puzzles for this Chess.com handle (default: all).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report before/after distribution and change count; write nothing.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    scope = f"username={args.username}" if args.username else "all usernames"
    mode = "DRY RUN (no writes)" if args.dry_run else "APPLYING CHANGES"
    logger.info("Reclassifying puzzle motifs [%s] — %s", scope, mode)

    db = SessionLocal()
    try:
        summary = reclassify_motifs(db, username=args.username, dry_run=args.dry_run)
    finally:
        db.close()

    logger.info("Puzzles scanned:          %d", summary["total"])
    logger.info("Stats rows to create / created: %d", summary["created"])
    logger.info("Existing rows to update / updated: %d", summary["changed_existing"])
    logger.info(
        "Motif distribution BEFORE: %s", _format_distribution(summary["before"])
    )
    logger.info("Motif distribution AFTER:  %s", _format_distribution(summary["after"]))
    if args.dry_run:
        logger.info("Dry run complete — no rows were modified.")
    else:
        logger.info(
            "Reclassification complete — %d stats rows created, %d existing rows updated.",
            summary["created"],
            summary["changed_existing"],
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
