"""Tests for the motif reclassification CLI (scripts/reclassify_motifs.py).

These seed puzzles whose PuzzleStats are frozen at the pre-#195 fallback
("blunder" / "The Missed Win") for positions the current classifier recognises
as a fork and a back-rank mate, then assert:

    - the reclassify pass rewrites those stale rows to the real motif/title,
    - a --dry-run reports the change but writes nothing,
    - a second real run is a no-op (idempotency).
"""

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


def _seed_puzzle(db, *, puzzle_id, username, fen, best_move, motif, title):
    """Insert a Puzzle + a stale PuzzleStats row."""
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
