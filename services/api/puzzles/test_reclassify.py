"""Tests for the motif reclassification CLI (scripts/reclassify_motifs.py).

These seed puzzles whose PuzzleStats are frozen at the pre-#195 fallback
("blunder" / "The Missed Win") for positions the current classifier recognises
as a fork and a back-rank mate, then assert:

    - the reclassify pass rewrites those stale rows to the real motif/title,
    - a --dry-run reports the change but writes nothing,
    - a second real run is a no-op (idempotency).
"""

from datetime import datetime

from scripts.reclassify_motifs import reclassify_motifs
from services.api.models import Game, Puzzle, PuzzleStats

# Positions whose current-classifier motif is NOT "blunder".
FORK_FEN = "3q3k/8/8/4N3/8/8/8/6K1 w - - 0 1"
FORK_BEST = "e5f7"  # Nf7 forks king + queen -> "fork"

BACK_RANK_FEN = "6k1/5ppp/8/8/8/8/8/4R1K1 w - - 0 1"
BACK_RANK_BEST = "e1e8"  # Re8# -> "back_rank"

# A genuinely quiet solution the classifier legitimately leaves as "blunder".
QUIET_FEN = "8/8/8/8/8/5k2/8/6K1 w - - 0 1"
QUIET_BEST = "g1f1"

# What the current namer produces for each of the fixtures above. Titles are
# composed from the position now, not looked up from the motif, so they are
# spelled out here rather than derived — a template change should fail a test,
# not silently agree with itself.
FORK_TITLE = "The f7 Knight Fork"
BACK_RANK_TITLE = "Back Rank on e8"
QUIET_TITLE = "The King to f1"


def _ensure_game(db, game_id, username):
    """The games row a puzzle's composite FK points at."""
    if db.get(Game, (game_id, username)) is not None:
        return
    db.add(
        Game(
            game_id=game_id,
            url="",
            username=username,
            white_username=username,
            black_username="",
            white_result="",
            black_result="",
            time_control="",
            end_time=0,
        )
    )
    db.flush()


def _seed_puzzle(
    db,
    *,
    puzzle_id,
    username,
    fen,
    best_move,
    with_stats=True,
    motif=None,
    title=None,
):
    """Insert a Puzzle and optionally seed its PuzzleStats identity row."""
    # puzzles(source_game_id, username) is a real FK. See conftest for why the
    # parent row cannot be skipped any more.
    _ensure_game(db, f"game-{puzzle_id}", username)
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
    if with_stats:
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
    """Return the stored motif/title identity tuple for a puzzle stats row."""
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
    assert _motif_of(db_session, "p_fork") == ("fork", FORK_TITLE)
    assert _motif_of(db_session, "p_backrank") == ("back_rank", BACK_RANK_TITLE)
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
        with_stats=False,
    )

    summary = reclassify_motifs(db_session)

    assert summary["total"] == 1
    assert summary["created"] == 1
    assert summary["changed_existing"] == 0
    assert summary["affected"] == 1
    assert summary["reclassified"] == 1
    assert summary["before"]["<missing_stats>"] == 1
    assert summary["after"]["fork"] == 1
    stats = db_session.get(PuzzleStats, "p_missing_stats")
    assert stats is not None
    assert stats.username == "lauureal"
    assert (stats.primary_motif, stats.title) == ("fork", FORK_TITLE)


def test_dry_run_reports_missing_stats_create_without_writing(db_session):
    """--dry-run reports missing PuzzleStats rows but does not insert them."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_missing_stats",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
        with_stats=False,
    )

    summary = reclassify_motifs(db_session, dry_run=True)

    assert summary["total"] == 1
    assert summary["created"] == 1
    assert summary["changed_existing"] == 0
    assert summary["affected"] == 1
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
    assert _motif_of(db_session, "p_fork") == ("fork", FORK_TITLE)


def test_reclassify_is_idempotent_after_creating_missing_stats(db_session):
    """A second real run makes zero changes after creating missing stats."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_missing_stats",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
        with_stats=False,
    )

    first = reclassify_motifs(db_session)
    assert first["created"] == 1
    assert first["changed_existing"] == 0
    assert first["affected"] == 1

    second = reclassify_motifs(db_session)
    assert second["created"] == 0
    assert second["changed_existing"] == 0
    assert second["affected"] == 0
    assert second["reclassified"] == 0
    assert _motif_of(db_session, "p_missing_stats") == ("fork", FORK_TITLE)


def test_existing_unclassified_stats_row_is_updated_not_created(db_session):
    """An existing stats row with null identity fields is updated in place."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_unclassified",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
        motif=None,
        title=None,
    )

    summary = reclassify_motifs(db_session)

    assert summary["total"] == 1
    assert summary["created"] == 0
    assert summary["changed_existing"] == 1
    assert summary["affected"] == 1
    assert summary["reclassified"] == 1
    assert summary["before"]["<unclassified>"] == 1
    assert _motif_of(db_session, "p_unclassified") == ("fork", FORK_TITLE)


def test_already_correct_row_is_untouched(db_session):
    """A row already carrying the current motif AND title sees no churn.

    Seeded with the position-derived title rather than the legacy motif one:
    the point of this test is "nothing differs, so nothing is written", and a
    legacy title now genuinely does differ.
    """
    _seed_puzzle(
        db_session,
        puzzle_id="p_quiet",
        username="lauureal",
        fen=QUIET_FEN,
        best_move=QUIET_BEST,
        motif="blunder",
        title=QUIET_TITLE,
    )

    summary = reclassify_motifs(db_session)

    assert summary["reclassified"] == 0
    assert _motif_of(db_session, "p_quiet") == ("blunder", QUIET_TITLE)


def test_ai_and_user_titles_survive_a_reclassify(db_session):
    """Reclassify may fix the motif; it may not undo a naming pass.

    Without the title_source guard this script would silently revert an entire
    AI naming run — and a name the user typed — back to a computed title, and
    nothing would report that it had happened.
    """
    for puzzle_id, source, title in (
        ("p_ai", "ai", "Knight Takes the Scenic Route"),
        ("p_user", "user", "My Nemesis"),
    ):
        _seed_puzzle(
            db_session,
            puzzle_id=puzzle_id,
            username="lauureal",
            fen=FORK_FEN,
            best_move=FORK_BEST,
            motif="blunder",
            title=title,
        )
        db_session.get(PuzzleStats, puzzle_id).title_source = source
    db_session.commit()

    reclassify_motifs(db_session)

    # The stale motif is corrected on both...
    assert db_session.get(PuzzleStats, "p_ai").primary_motif == "fork"
    assert db_session.get(PuzzleStats, "p_user").primary_motif == "fork"
    # ...but neither name is touched.
    assert db_session.get(PuzzleStats, "p_ai").title == "Knight Takes the Scenic Route"
    assert db_session.get(PuzzleStats, "p_user").title == "My Nemesis"


def test_a_position_sourced_title_is_still_rewritten(db_session):
    """The guard must not freeze everything: computed titles still update."""
    _seed_puzzle(
        db_session,
        puzzle_id="p_pos",
        username="lauureal",
        fen=FORK_FEN,
        best_move=FORK_BEST,
        motif="blunder",
        title="The Missed Win",
    )
    db_session.get(PuzzleStats, "p_pos").title_source = "position"
    db_session.commit()

    reclassify_motifs(db_session)

    assert _motif_of(db_session, "p_pos") == ("fork", FORK_TITLE)


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
    assert (stats.primary_motif, stats.title) == ("fork", FORK_TITLE)
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
    assert _motif_of(db_session, "p_fork") == ("fork", FORK_TITLE)


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
    assert _motif_of(db_session, "p_fork_a") == ("fork", FORK_TITLE)
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
        with_stats=False,
    )
    _seed_puzzle(
        db_session,
        puzzle_id="p_fork_b",
        username="hikaru",
        fen=FORK_FEN,
        best_move=FORK_BEST,
        with_stats=False,
    )

    summary = reclassify_motifs(db_session, username="  Lauureal  ")

    assert summary["total"] == 1
    assert summary["created"] == 1
    assert summary["affected"] == 1
    assert _motif_of(db_session, "p_fork_a") == ("fork", FORK_TITLE)
    assert db_session.get(PuzzleStats, "p_fork_b") is None
