"""Tests for the motif reclassification CLI (scripts/reclassify_motifs.py).

These seed puzzles whose PuzzleStats are frozen at the pre-#195 fallback
("blunder" / "The Missed Win") for positions the current classifier recognises
as a fork and a back-rank mate, then assert:

    - the reclassify pass rewrites those stale rows to the real motif/title,
    - a --dry-run reports the change but writes nothing,
    - a second real run is a no-op (idempotency).
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scripts.reclassify_motifs import reclassify_motifs
from services.api.db import Base
from services.api.models import Puzzle, PuzzleStats

# Positions whose current-classifier motif is NOT "blunder".
FORK_FEN = "3q3k/8/8/4N3/8/8/8/6K1 w - - 0 1"
FORK_BEST = "e5f7"  # Nf7 forks king + queen -> "fork"

BACK_RANK_FEN = "6k1/5ppp/8/8/8/8/8/4R1K1 w - - 0 1"
BACK_RANK_BEST = "e1e8"  # Re8# -> "back_rank"

# A genuinely quiet solution the classifier legitimately leaves as "blunder".
QUIET_FEN = "8/8/8/8/8/5k2/8/6K1 w - - 0 1"
QUIET_BEST = "g1f1"


@pytest.fixture
def db_session():
    """In-memory SQLite session with the full schema."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def _seed_puzzle(db, *, puzzle_id, username, fen, best_move, motif=None, title=None):
    """Insert a Puzzle and, when motif/title are supplied, a PuzzleStats row."""
    db.add(
        Puzzle(
            id=puzzle_id,
            username=username,
            source_game_id=f"game-{puzzle_id}",
            ply=10,
            fen=fen,
            side_to_move="white",
            played_move_uci="a2a3",
            best_move_uci=best_move,
            eval_before=0.0,
            eval_after=-3.0,
            swing=3.0,
        )
    )
    if motif is not None or title is not None:
        db.add(
            PuzzleStats(
                puzzle_id=puzzle_id,
                username=username,
                title=title,
                primary_motif=motif,
            )
        )
    db.commit()


def _motif_of(db, puzzle_id):
    stats = db.get(PuzzleStats, puzzle_id)
    return stats.primary_motif, stats.title


def test_reclassify_updates_stale_rows(db_session):
    """Stale 'blunder' rows for a fork / back-rank mate get the real motif."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_fork",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
        motif="blunder",
        title="The Missed Win",
    )
    _seed_puzzle(
        db_session,
        puzzle_id="p_backrank",
        username="lauureal",
        fen=BACK_RANK_FEN,
        best_move=BACK_RANK_BEST,
        motif="blunder",
        title="The Missed Win",
    )

    summary = reclassify_motifs(db_session)

    assert summary["total"] == 2
    assert summary["reclassified"] == 2
    assert _motif_of(db_session, "p_fork") == ("fork", "The Fork")
    assert _motif_of(db_session, "p_backrank") == ("back_rank", "Back Rank Panic")
    # before was all fallback, after is the two real motifs
    assert summary["before"]["blunder"] == 2
    assert summary["after"]["fork"] == 1
    assert summary["after"]["back_rank"] == 1


def test_dry_run_changes_nothing(db_session):
    """--dry-run reports the pending change but leaves the DB untouched."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_fork",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
        motif="blunder",
        title="The Missed Win",
    )

    summary = reclassify_motifs(db_session, dry_run=True)

    assert summary["reclassified"] == 1  # it WOULD change
    # ...but nothing was written.
    assert _motif_of(db_session, "p_fork") == ("blunder", "The Missed Win")


def test_reclassify_creates_missing_stats_row(db_session):
    """A puzzle without PuzzleStats gets an identity row on a real run."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_missing_stats",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
    )

    summary = reclassify_motifs(db_session)

    assert summary["total"] == 1
    assert summary["created"] == 1
    assert summary["changed_existing"] == 0
    assert summary["reclassified"] == 1
    assert summary["before"]["<missing_stats>"] == 1
    assert summary["after"]["fork"] == 1
    stats = db_session.get(PuzzleStats, "p_missing_stats")
    assert stats is not None
    assert stats.username == "lauureal"
    assert (stats.primary_motif, stats.title) == ("fork", "The Fork")


def test_dry_run_reports_missing_stats_create_without_writing(db_session):
    """--dry-run reports missing PuzzleStats rows but does not insert them."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_missing_stats",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
    )

    summary = reclassify_motifs(db_session, dry_run=True)

    assert summary["total"] == 1
    assert summary["created"] == 1
    assert summary["changed_existing"] == 0
    assert summary["reclassified"] == 1
    assert summary["before"]["<missing_stats>"] == 1
    assert db_session.get(PuzzleStats, "p_missing_stats") is None


def test_reclassify_is_idempotent(db_session):
    """A second real run makes zero further changes."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_fork",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
        motif="blunder",
        title="The Missed Win",
    )

    first = reclassify_motifs(db_session)
    assert first["reclassified"] == 1

    second = reclassify_motifs(db_session)
    assert second["reclassified"] == 0
    assert _motif_of(db_session, "p_fork") == ("fork", "The Fork")


def test_reclassify_is_idempotent_after_creating_missing_stats(db_session):
    """A second real run makes zero changes after creating missing stats."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_missing_stats",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
    )

    first = reclassify_motifs(db_session)
    assert first["created"] == 1
    assert first["changed_existing"] == 0

    second = reclassify_motifs(db_session)
    assert second["created"] == 0
    assert second["changed_existing"] == 0
    assert second["reclassified"] == 0
    assert _motif_of(db_session, "p_missing_stats") == ("fork", "The Fork")


def test_already_correct_row_is_untouched(db_session):
    """A quiet puzzle already labelled 'blunder' stays 'blunder' (no churn)."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_quiet",
        username="lauureal",
        fen=QUIET_FEN,
        best_move=QUIET_BEST,
        motif="blunder",
        title="The Missed Win",
    )

    summary = reclassify_motifs(db_session)

    assert summary["reclassified"] == 0
    assert _motif_of(db_session, "p_quiet") == ("blunder", "The Missed Win")


def test_existing_spaced_repetition_state_is_preserved(db_session):
    """Updating motif/title does not reset review scheduling fields."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_fork",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
        motif="blunder",
        title="The Missed Win",
    )
    reviewed_at = datetime(2026, 7, 20, 12, 0, 0)
    due_at = datetime(2026, 7, 23, 12, 0, 0)
    stats = db_session.get(PuzzleStats, "p_fork")
    stats.attempts = 7
    stats.pass_count = 5
    stats.fail_count = 2
    stats.last_reviewed_at = reviewed_at
    stats.last_result = "pass"
    stats.next_due_at = due_at
    stats.interval_days = 3
    stats.ease_factor = 2.4
    db_session.commit()

    summary = reclassify_motifs(db_session)

    assert summary["created"] == 0
    assert summary["changed_existing"] == 1
    stats = db_session.get(PuzzleStats, "p_fork")
    assert (stats.primary_motif, stats.title) == ("fork", "The Fork")
    assert stats.attempts == 7
    assert stats.pass_count == 5
    assert stats.fail_count == 2
    assert stats.last_reviewed_at == reviewed_at
    assert stats.last_result == "pass"
    assert stats.next_due_at == due_at
    assert stats.interval_days == 3
    assert stats.ease_factor == 2.4


def test_username_filter_is_canonicalized(db_session):
    """--username is folded to the canonical storage key before scoping.

    Puzzle rows are stored under canonical (stripped/lowercased) handles, so an
    operator handle that arrives with surrounding whitespace or mixed case (e.g.
    from shell quoting or copy-paste) must still match — matching the canonical
    boundary every API entry point now enforces. A bare ``.lower()`` would leave
    the whitespace on and silently scan zero rows.
    """
    _seed_puzzle(
        db_session,
        puzzle_id="p_fork",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
        motif="blunder",
        title="The Missed Win",
    )

    summary = reclassify_motifs(db_session, username="  Lauureal  ")

    assert summary["total"] == 1
    assert summary["reclassified"] == 1
    assert _motif_of(db_session, "p_fork") == ("fork", "The Fork")


def test_username_filter_scopes_updates(db_session):
    """--username restricts reclassification to that handle."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_fork_a",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
        motif="blunder",
        title="The Missed Win",
    )
    _seed_puzzle(
        db_session,
        puzzle_id="p_fork_b",
        username="hikaru",
        fen=FORK_FEN,
        best_move=FORK_BEST,
        motif="blunder",
        title="The Missed Win",
    )

    summary = reclassify_motifs(db_session, username="lauureal")

    assert summary["total"] == 1
    assert summary["reclassified"] == 1
    assert _motif_of(db_session, "p_fork_a") == ("fork", "The Fork")
    # The other user's row is left alone.
    assert _motif_of(db_session, "p_fork_b") == ("blunder", "The Missed Win")


def test_username_filter_scopes_missing_stats_creation(db_session):
    """--username creates missing PuzzleStats only for that canonical handle."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_fork_a",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
    )
    _seed_puzzle(
        db_session,
        puzzle_id="p_fork_b",
        username="hikaru",
        fen=FORK_FEN,
        best_move=FORK_BEST,
    )

    summary = reclassify_motifs(db_session, username="  Lauureal  ")

    assert summary["total"] == 1
    assert summary["created"] == 1
    assert _motif_of(db_session, "p_fork_a") == ("fork", "The Fork")
    assert db_session.get(PuzzleStats, "p_fork_b") is None
