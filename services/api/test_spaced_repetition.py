from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.api.models import Base, PuzzleStats
from services.api.storage.spaced_repetition import (
    _utcnow_naive,
    get_adaptive_puzzles,
    get_due_puzzle_count,
    get_next_due_date,
    get_puzzle_stats,
    insert_puzzle_review,
    update_puzzle_stats,
)

# Use in-memory SQLite for tests
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_record_pass_review(db_session):
    puzzle_id = "test-puzzle-1"
    username = "testuser"

    # Record a pass review
    review = insert_puzzle_review(
        db_session, puzzle_id, username, "pass", time_spent_ms=5000
    )
    stats = update_puzzle_stats(db_session, puzzle_id, username, "pass")
    db_session.commit()

    assert review.puzzle_id == puzzle_id
    assert review.username == username
    assert review.result == "pass"
    assert review.time_spent_ms == 5000

    assert stats.puzzle_id == puzzle_id
    assert stats.attempts == 1
    assert stats.pass_count == 1
    assert stats.fail_count == 0
    assert stats.last_result == "pass"
    assert stats.last_reviewed_at is not None


def test_record_fail_review(db_session):
    puzzle_id = "test-puzzle-2"
    username = "testuser"

    # Record a fail review
    update_puzzle_stats(db_session, puzzle_id, username, "fail")
    db_session.commit()
    stats = get_puzzle_stats(db_session, puzzle_id, username)

    assert stats.attempts == 1
    assert stats.pass_count == 0
    assert stats.fail_count == 1
    assert stats.last_result == "fail"


def test_update_puzzle_stats_preserves_existing_identity(db_session):
    """Review updates preserve existing puzzle title and motif identity fields."""

    db_session.add(
        PuzzleStats(
            puzzle_id="test-puzzle-identity",
            username="testuser",
            title="Manual title",
            primary_motif="manual_motif",
            attempts=0,
            pass_count=0,
            fail_count=0,
            ease_factor=2.0,
        )
    )
    db_session.commit()

    stats = update_puzzle_stats(db_session, "test-puzzle-identity", "testuser", "pass")
    db_session.commit()

    assert stats.attempts == 1
    assert stats.pass_count == 1
    assert stats.fail_count == 0
    assert stats.title == "Manual title"
    assert stats.primary_motif == "manual_motif"


def test_sequential_reviews(db_session):
    puzzle_id = "test-puzzle-3"
    username = "testuser"

    # 1. First review: Fail
    update_puzzle_stats(db_session, puzzle_id, username, "fail")
    db_session.commit()

    # 2. Second review: Pass
    stats = update_puzzle_stats(db_session, puzzle_id, username, "pass")
    db_session.commit()

    assert stats.attempts == 2
    assert stats.pass_count == 1
    assert stats.fail_count == 1
    assert stats.last_result == "pass"


def test_get_puzzle_stats_none(db_session):
    stats = get_puzzle_stats(db_session, "non-existent", "testuser")
    assert stats is None


def test_utcnow_naive_is_naive_utc():
    """SQL 'due' comparisons must use a naive-UTC bound (see module note).

    Guards the timezone-consistency fix: get_due_puzzle_count / get_next_due_date
    compare against naive-UTC columns, so the bound must be naive (no tzinfo).
    A tz-aware bound is fine on SQLite but reinterprets naive columns on Postgres
    when the session TimeZone != UTC, shifting the due boundary.
    """
    now = _utcnow_naive()
    assert now.tzinfo is None
    # Sane: within a minute of the aware UTC wall clock
    aware = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((aware - now).total_seconds()) < 60


def test_due_paths_agree_with_adaptive_classification(db_session):
    """The three due-paths must agree on which puzzles are due.

    Seeds naive-UTC next_due_at values (as read back from the DB) spanning
    clearly-due, clearly-future, and just-past-boundary, and asserts that
    get_due_puzzle_count and get_next_due_date are consistent with
    get_adaptive_puzzles' due/future classification.
    """
    from services.api.models import PuzzleStats

    now = datetime.now(timezone.utc)
    naive = lambda dt: dt.replace(tzinfo=None)  # noqa: E731 - stored form

    db_session.add_all(
        [
            PuzzleStats(  # clearly due
                puzzle_id="due-old",
                username="u",
                attempts=1,
                pass_count=1,
                ease_factor=2.0,
                interval_days=1,
                next_due_at=naive(now - timedelta(days=2)),
            ),
            PuzzleStats(  # just past boundary (due)
                puzzle_id="due-edge",
                username="u",
                attempts=1,
                pass_count=1,
                ease_factor=2.0,
                interval_days=1,
                next_due_at=naive(now - timedelta(seconds=5)),
            ),
            PuzzleStats(  # clearly future (not due)
                puzzle_id="future",
                username="u",
                attempts=1,
                pass_count=1,
                ease_factor=2.0,
                interval_days=1,
                next_due_at=naive(now + timedelta(days=3)),
            ),
        ]
    )
    db_session.commit()

    # Python path: classify due (base_priority == 0) via the sort key
    ordered, all_stats = get_adaptive_puzzles(
        db_session, "u", ["due-old", "due-edge", "future"], n=10
    )
    py_due = set()
    check_now = datetime.now(timezone.utc)
    for pid, st in all_stats.items():
        nd = st.next_due_at
        nd = nd if nd.tzinfo else nd.replace(tzinfo=timezone.utc)
        if nd <= check_now:
            py_due.add(pid)

    assert py_due == {"due-old", "due-edge"}

    # SQL count agrees with the Python due set
    assert get_due_puzzle_count(db_session, "u") == len(py_due)

    # Next due date is the earliest *future* row, and it is not in the due set
    next_due = get_next_due_date(db_session, "u")
    assert next_due is not None
    nd_aware = next_due if next_due.tzinfo else next_due.replace(tzinfo=timezone.utc)
    assert nd_aware > check_now


def test_get_adaptive_puzzles_accuracy_goal_sorting(db_session):
    from datetime import timedelta

    from services.api.models import PuzzleStats

    now = datetime.now(timezone.utc)

    stats_high_accuracy = PuzzleStats(
        puzzle_id="p1",
        username="testuser",
        attempts=10,
        pass_count=9,
        fail_count=1,
        ease_factor=2.0,
        interval_days=1,
        next_due_at=now - timedelta(days=1),
    )
    stats_low_accuracy = PuzzleStats(
        puzzle_id="p2",
        username="testuser",
        attempts=10,
        pass_count=5,
        fail_count=5,
        ease_factor=2.0,
        interval_days=1,
        next_due_at=now - timedelta(days=1),
    )
    db_session.add_all([stats_high_accuracy, stats_low_accuracy])
    db_session.commit()

    ordered_ids, _ = get_adaptive_puzzles(
        db_session,
        "testuser",
        ["p1", "p2"],
        n=2,
        session_type="accuracy_goal",
        target_accuracy=80,
    )

    assert ordered_ids == ["p1", "p2"]


class TestFocusBias:
    """A training focus re-orders the trainable set. It must never widen it.

    This is the load-bearing guarantee of the pattern planner: clicking "train
    this pattern" may change *which puzzle comes first*, and nothing else. It
    cannot make a not-yet-due puzzle due, cannot pull in a puzzle the caller
    did not offer, and cannot change a session nobody asked to focus.
    """

    def _stats(self, db, pid, *, due_days_ago=None, due_in_days=None):
        from datetime import timedelta

        from services.api.models import PuzzleStats

        now = datetime.now(timezone.utc)
        if due_days_ago is not None:
            due = now - timedelta(days=due_days_ago)
        elif due_in_days is not None:
            due = now + timedelta(days=due_in_days)
        else:
            due = None
        db.add(
            PuzzleStats(
                puzzle_id=pid,
                username="u",
                attempts=1,
                pass_count=1,
                ease_factor=2.0,
                interval_days=1,
                next_due_at=due.replace(tzinfo=None) if due else None,
            )
        )

    def test_cannot_promote_a_not_yet_due_puzzle_above_a_due_one(self, db_session):
        # The whole D4 guarantee in one assertion. If the focus sat before the
        # due/new/future tier in the sort key, "future" would come first here.
        self._stats(db_session, "due", due_days_ago=1)
        self._stats(db_session, "future", due_in_days=30)
        db_session.commit()

        ordered, _ = get_adaptive_puzzles(
            db_session, "u", ["due", "future"], n=2, focus_puzzle_ids={"future"}
        )
        assert ordered == ["due", "future"]

    def test_cannot_add_a_puzzle_the_caller_did_not_offer(self, db_session):
        # The focus is a membership test over the candidates, not a query. A
        # focus id absent from the candidate list stays absent — that is what
        # keeps the trainable narrowing upstream authoritative.
        self._stats(db_session, "a", due_days_ago=1)
        db_session.commit()

        ordered, _ = get_adaptive_puzzles(
            db_session, "u", ["a"], n=5, focus_puzzle_ids={"a", "not-offered"}
        )
        assert ordered == ["a"]

    def test_reorders_within_a_tier(self, db_session):
        # Both due, so serving the focus one first re-anchors no interval.
        self._stats(db_session, "older", due_days_ago=10)
        self._stats(db_session, "newer", due_days_ago=1)
        db_session.commit()

        unfocused, _ = get_adaptive_puzzles(db_session, "u", ["older", "newer"], n=2)
        assert unfocused == ["older", "newer"]

        focused, _ = get_adaptive_puzzles(
            db_session, "u", ["older", "newer"], n=2, focus_puzzle_ids={"newer"}
        )
        assert focused == ["newer", "older"]

    def test_changes_nothing_when_no_focus_is_asked_for(self, db_session):
        # Every user who never requests a focused session must get exactly the
        # queue they got before this parameter existed.
        self._stats(db_session, "a", due_days_ago=5)
        self._stats(db_session, "b", due_days_ago=2)
        self._stats(db_session, "c", due_in_days=3)
        db_session.commit()

        ids = ["c", "a", "b"]
        assert (
            get_adaptive_puzzles(db_session, "u", ids, n=5)[0]
            == get_adaptive_puzzles(db_session, "u", ids, n=5, focus_puzzle_ids=set())[
                0
            ]
        )
        assert (
            get_adaptive_puzzles(db_session, "u", ids, n=5)[0]
            == get_adaptive_puzzles(db_session, "u", ids, n=5, focus_puzzle_ids=None)[0]
        )

    def test_a_focus_with_nothing_trainable_yields_an_ordinary_session(
        self, db_session
    ):
        # Degrading to a normal session is the reason this is a bias and not a
        # filter: a user should never be told "no puzzles" for asking to work
        # on a pattern that happens to have nothing due today.
        self._stats(db_session, "a", due_days_ago=1)
        self._stats(db_session, "b", due_days_ago=2)
        db_session.commit()

        focused, _ = get_adaptive_puzzles(
            db_session, "u", ["a", "b"], n=5, focus_puzzle_ids={"nothing-due-today"}
        )
        unfocused, _ = get_adaptive_puzzles(db_session, "u", ["a", "b"], n=5)
        assert focused == unfocused
        assert focused != []

    def test_still_respects_the_session_size(self, db_session):
        # A focus must not be a way to smuggle extra puzzles into a session.
        for i in range(6):
            self._stats(db_session, f"p{i}", due_days_ago=i + 1)
        db_session.commit()

        ordered, _ = get_adaptive_puzzles(
            db_session,
            "u",
            [f"p{i}" for i in range(6)],
            n=3,
            focus_puzzle_ids={f"p{i}" for i in range(6)},
        )
        assert len(ordered) == 3


def test_a_never_reviewed_puzzle_counts_as_due(db_session):
    """A NULL next_due_at is "New", and New is trainable.

    Stats rows are created eagerly on save, so a user who has generated puzzles
    but reviewed none has a table full of NULL due dates. Excluding them makes
    the badge say "0 due" while a session happily serves them — the count and
    the queue must not disagree about what is trainable.

    This was lost once already: it lives on main and was dropped when dev's
    version of this file won a merge, so it is pinned here rather than left to
    the next reconciliation.
    """
    from services.api.models import PuzzleStats
    from services.api.storage.spaced_repetition import get_due_puzzle_count

    db_session.add(
        PuzzleStats(
            puzzle_id="never-reviewed",
            username="u",
            attempts=0,
            pass_count=0,
            ease_factor=2.0,
            next_due_at=None,
        )
    )
    db_session.commit()

    assert get_due_puzzle_count(db_session, "u") == 1


def test_mixed_case_username_finds_lowercase_stats(db_session):
    """Mixed-case caller finds lowercase PuzzleStats and respects due ordering.

    get_adaptive_puzzles folds the username before querying PuzzleStats.
    Live/API traffic is unaffected (Username canonicalises at the boundary);
    direct/internal callers with mixed-case are repaired.
    Candidates are listed new-first so a broken fold yields equal priorities
    and stable-sort keeps the wrong order — making both asserts fail.

    NB this covers CASE ONLY. It passes against a plain ``.lower()``, which is a
    different fold from ``canonical_username`` and silently wrong on whitespace
    and compatibility forms. ``test_noncanonical_username_reaches_canonical_rows``
    below is the one that pins the actual fold; keep both.
    """
    now = datetime.now(timezone.utc)

    # Stats stored lowercase, as the API route writes them.
    db_session.add(
        PuzzleStats(
            puzzle_id="due-mc",
            username="alice",
            attempts=1,
            pass_count=1,
            ease_factor=2.0,
            interval_days=1,
            next_due_at=(now - timedelta(days=1)).replace(tzinfo=None),
        )
    )
    # "new-mc" has no stats row — it lands in the never-seen tier.
    db_session.commit()

    # Pass mixed-case and new-puzzle first so order matters.
    ordered, all_stats = get_adaptive_puzzles(
        db_session, "Alice", ["new-mc", "due-mc"], n=2
    )

    assert (
        "due-mc" in all_stats
    ), "fold failed: mixed-case caller missed lowercase stats"
    assert ordered[0] == "due-mc", "due puzzle must rank before never-seen"


# One handle, spelled every way a non-canonical caller might spell it. Each
# folds to "alice" under canonical_username but NOT under a bare .lower():
#   " Alice "  -> .lower() = " alice "   (leading/trailing space survives)
#   "Ａｌｉｃｅ" -> .lower() = "ａｌｉｃｅ" (fullwidth survives; NFKC folds it)
#   "ALICE\xa0" -> .lower() = "alice\xa0" (NBSP is not stripped by .strip()
#                                          until NFKC turns it into a space)
# The last one is the sharpest: it needs NFKC *before* strip, which is exactly
# the ordering canonical_username documents and a hand-rolled fold gets wrong.
# The last entry combines all three defects at once, which is the case the task
# actually cares about: mixed case AND surrounding whitespace AND a fullwidth
# character in one handle.
NONCANONICAL_SPELLINGS = [
    " Alice ",
    "Ａｌｉｃｅ",
    "ALICE\xa0",
    " Ａlice\xa0",
]


@pytest.mark.parametrize("handle", NONCANONICAL_SPELLINGS)
def test_noncanonical_username_reaches_canonical_rows(db_session, handle):
    """A non-canonical handle must reach the SAME rows as its canonical form.

    Covers mixed case AND surrounding whitespace AND a fullwidth (NFKC
    compatibility) form in one parametrised sweep, because the three fail
    independently: a fold that lowercases but does not strip passes the
    case-only test and still returns nothing for " Alice ".

    Asserts the scheduling TIER, not a row count. The named failure mode is not
    "fewer rows" — it is that ``spaced_repetition`` reads missing stats as
    "never seen", so a due puzzle is served as new and ``update_puzzle_stats``
    then re-anchors its interval off the wrong date. Checking that "due-nc"
    still sorts ahead of a never-seen puzzle is the property that matters; a
    count would pass on a fold that returned the right number of wrong rows.
    """
    now = datetime.now(timezone.utc)

    # Stored canonically, as every live writer stores it.
    db_session.add(
        PuzzleStats(
            puzzle_id="due-nc",
            username="alice",
            attempts=1,
            pass_count=1,
            ease_factor=2.0,
            interval_days=1,
            next_due_at=(now - timedelta(days=1)).replace(tzinfo=None),
        )
    )
    db_session.commit()

    # "new-nc" has no stats row, and is listed FIRST so a broken fold leaves
    # both puzzles in the never-seen tier with equal keys — stable sort then
    # preserves this (wrong) order and the tier assert fails.
    ordered, all_stats = get_adaptive_puzzles(
        db_session, handle, ["new-nc", "due-nc"], n=2
    )

    assert "due-nc" in all_stats, (
        f"fold failed for {handle!r}: canonical stats not found. "
        "A .lower()-only fold produces a key that matches no row."
    )
    assert ordered[0] == "due-nc", (
        f"fold failed for {handle!r}: an overdue puzzle was demoted below a "
        "never-seen one, i.e. a due puzzle would be served as new."
    )


@pytest.mark.parametrize("handle", NONCANONICAL_SPELLINGS)
def test_noncanonical_username_write_and_read_agree(db_session, handle):
    """The write path and the read path must fold identically.

    This is the regression for the asymmetry #345 introduced: it folded the
    ``get_adaptive_puzzles`` READ but left ``update_puzzle_stats``' write
    unfolded, so a direct caller passing "Alice" stored ``('p','Alice')`` and
    then read with ``.lower()`` — ``all_stats`` came back empty and every
    puzzle collapsed to the never-seen tier. A one-sided fold is strictly worse
    than no fold, because no fold at least round-trips.

    So this asserts the round trip through the real functions rather than
    against a hand-inserted row: whatever ``update_puzzle_stats`` writes,
    ``get_puzzle_stats`` and ``get_adaptive_puzzles`` must find.
    """
    reviewed = datetime.now(timezone.utc) - timedelta(days=30)

    # Write through the public API with the ugly handle.
    insert_puzzle_review(db_session, "rt-nc", handle, "pass", reviewed_at=reviewed)
    update_puzzle_stats(db_session, "rt-nc", handle, "pass", reviewed_at=reviewed)
    db_session.commit()

    # The row must exist under the canonical key, not the caller's spelling.
    stored = db_session.query(PuzzleStats).filter_by(puzzle_id="rt-nc").one()
    assert stored.username == "alice", (
        f"{handle!r} was stored under {stored.username!r}; a write that does "
        "not fold forks the user's data away from every canonical read"
    )

    # And every read path must find it — the caller's spelling, the canonical
    # spelling, and the scheduler alike.
    assert get_puzzle_stats(db_session, "rt-nc", handle) is not None
    assert get_puzzle_stats(db_session, "rt-nc", "alice") is not None

    ordered, all_stats = get_adaptive_puzzles(
        db_session, handle, ["new-rt", "rt-nc"], n=2
    )
    assert "rt-nc" in all_stats, "write/read fold asymmetry: stats unreachable"
    assert ordered[0] == "rt-nc", "a puzzle 29 days overdue was served as new"


class TestVarietyCap:
    """One motif must not monopolise a session.

    Five forks in a row is a worse session than four forks and a pin, even when
    the fifth fork is the next most overdue — the point of a mixed session is
    that you cannot pattern-match your way through it.
    """

    def _stats(self, db, pid, motif, days_overdue):
        from datetime import timedelta

        from services.api.models import PuzzleStats

        db.add(
            PuzzleStats(
                puzzle_id=pid,
                username="u",
                primary_motif=motif,
                attempts=1,
                pass_count=1,
                ease_factor=2.0,
                interval_days=1,
                next_due_at=(
                    datetime.now(timezone.utc) - timedelta(days=days_overdue)
                ).replace(tzinfo=None),
            )
        )

    def test_caps_one_motif_below_the_whole_session(self, db_session):
        # Six forks and one pin, the pin least overdue. Without the cap the
        # session is six forks; with it the pin earns a place.
        for i in range(6):
            self._stats(db_session, f"fork{i}", "Fork", 30 - i)
        self._stats(db_session, "pin0", "Pin", 1)
        db_session.commit()

        ids = [f"fork{i}" for i in range(6)] + ["pin0"]
        ordered, _ = get_adaptive_puzzles(db_session, "u", ids, n=5)
        assert "pin0" in ordered
        assert sum(1 for p in ordered if p.startswith("fork")) <= 4

    def test_never_shortens_a_session(self, db_session):
        # The cap reorders; it must never make a session smaller than the
        # corpus could fill. All one motif here, so it cannot add variety.
        for i in range(6):
            self._stats(db_session, f"fork{i}", "Fork", 30 - i)
        db_session.commit()

        ids = [f"fork{i}" for i in range(6)]
        ordered, _ = get_adaptive_puzzles(db_session, "u", ids, n=5)
        assert len(ordered) == 5

    def test_never_changes_the_set(self, db_session):
        for i in range(4):
            self._stats(db_session, f"fork{i}", "Fork", 30 - i)
        db_session.commit()

        ids = [f"fork{i}" for i in range(4)]
        ordered, _ = get_adaptive_puzzles(db_session, "u", ids, n=10)
        assert sorted(ordered) == sorted(ids)

    def test_leaves_a_focused_session_concentrated(self, db_session):
        # A focus is an explicit request for concentration; capping it would
        # fight the user's own choice.
        for i in range(6):
            self._stats(db_session, f"fork{i}", "Fork", 30 - i)
        self._stats(db_session, "pin0", "Pin", 1)
        db_session.commit()

        ids = [f"fork{i}" for i in range(6)] + ["pin0"]
        ordered, _ = get_adaptive_puzzles(
            db_session, "u", ids, n=5, focus_puzzle_ids={f"fork{i}" for i in range(6)}
        )
        assert all(p.startswith("fork") for p in ordered)

    def test_puzzles_without_a_motif_are_exempt(self, db_session):
        # "Unknown" is not a motif; capping it would penalise exactly the
        # puzzles the user has seen least.
        for i in range(6):
            self._stats(db_session, f"none{i}", None, 30 - i)
        db_session.commit()

        ids = [f"none{i}" for i in range(6)]
        ordered, _ = get_adaptive_puzzles(db_session, "u", ids, n=5)
        assert len(ordered) == 5

    def test_keeps_the_most_overdue_first_within_the_cap(self, db_session):
        # Variety reorders across motifs; it must not scramble the scheduling
        # priority inside one.
        for i in range(3):
            self._stats(db_session, f"fork{i}", "Fork", 30 - i)
        self._stats(db_session, "pin0", "Pin", 1)
        db_session.commit()

        ids = ["pin0"] + [f"fork{i}" for i in range(3)]
        ordered, _ = get_adaptive_puzzles(db_session, "u", ids, n=4)
        forks = [p for p in ordered if p.startswith("fork")]
        assert forks == ["fork0", "fork1", "fork2"]


class TestGameDiversity:
    """Two positions from one game are not two problems.

    Same opening, same opponent, same sitting, often a few moves apart. On the
    live corpus the candidates average 3.19 puzzles per game, so without a cap a
    default five-puzzle session is drawn from one or two games.
    """

    def _puzzle(self, db, pid, game, motif="Fork", days_overdue=None, ply=None):
        from services.api.models import Puzzle, PuzzleStats

        db.add(
            Puzzle(
                id=pid,
                username="u",
                source_game_id=game,
                ply=ply if ply is not None else abs(hash(pid)) % 10_000,
                fen="8/8/8/8/8/8/8/8 w - - 0 1",
                side_to_move="white",
                played_move_uci="e2e4",
                best_move_uci="d2d4",
                eval_before=0.5,
                eval_after=-1.5,
                swing=2.0,
            )
        )
        due = None
        if days_overdue is not None:
            due = (datetime.now(timezone.utc) - timedelta(days=days_overdue)).replace(
                tzinfo=None
            )
        db.add(
            PuzzleStats(
                puzzle_id=pid,
                username="u",
                primary_motif=motif,
                attempts=1 if due else 0,
                pass_count=1 if due else 0,
                ease_factor=2.0,
                interval_days=1 if due else None,
                next_due_at=due,
            )
        )

    def test_a_session_spreads_across_games_when_it_can(self, db_session):
        # gameA holds the six most overdue puzzles; four other games hold one
        # each, all less overdue. Without the cap the session is five from
        # gameA and nothing else is ever seen.
        # motif="blunder" is exempt from the motif cap, so this isolates the
        # game constraint. With a real motif the two caps compose and only
        # three of one motif fit in a five-puzzle session.
        for i in range(6):
            self._puzzle(
                db_session,
                f"a{i}",
                "gameA",
                motif="blunder",
                days_overdue=30 - i,
                ply=i,
            )
        for j, g in enumerate("bcde"):
            self._puzzle(
                db_session,
                f"{g}0",
                f"game{g.upper()}",
                motif="blunder",
                days_overdue=5 - j,
                ply=50 + j,
            )
        db_session.commit()

        ids = [f"a{i}" for i in range(6)] + [f"{g}0" for g in "bcde"]
        ordered, _ = get_adaptive_puzzles(db_session, "u", ids, n=5)

        session = ordered[:5]
        assert len({p[0] for p in session}) == 5, session
        # The most overdue puzzle still leads: the cap reorders, it does not
        # demote priority.
        assert session[0] == "a0"

    def test_the_cap_cannot_invent_variety_that_is_not_there(self, db_session):
        # Only two games exist, so a five-puzzle session must repeat one. What
        # the cap guarantees is that the *distinct* game comes early rather
        # than being buried behind every repeat.
        for i in range(6):
            self._puzzle(db_session, f"a{i}", "gameA", days_overdue=30 - i, ply=i)
        self._puzzle(db_session, "b0", "gameB", days_overdue=1, ply=99)
        db_session.commit()

        ids = [f"a{i}" for i in range(6)] + ["b0"]
        ordered, _ = get_adaptive_puzzles(db_session, "u", ids, n=5)

        assert ordered[:2] == ["a0", "b0"]
        assert len(ordered[:5]) == 5

    def test_the_game_cap_holds_in_a_focused_session_too(self, db_session):
        # A focus asks for a kind of mistake, never for five positions out of
        # the same game — so unlike the motif cap this one stays on.
        # motif="blunder" is exempt from the motif cap, so this isolates the
        # game constraint. With a real motif the two caps compose and only
        # three of one motif fit in a five-puzzle session.
        for i in range(6):
            self._puzzle(
                db_session,
                f"a{i}",
                "gameA",
                motif="blunder",
                days_overdue=30 - i,
                ply=i,
            )
        for j, g in enumerate("bcde"):
            self._puzzle(
                db_session,
                f"{g}0",
                f"game{g.upper()}",
                motif="blunder",
                days_overdue=5 - j,
                ply=50 + j,
            )
        db_session.commit()

        ids = [f"a{i}" for i in range(6)] + [f"{g}0" for g in "bcde"]
        ordered, _ = get_adaptive_puzzles(
            db_session, "u", ids, n=5, focus_puzzle_ids={"a0", "a1"}
        )

        assert len({p[0] for p in ordered[:5]}) == 5, ordered[:5]

    def test_never_shortens_or_changes_the_set(self, db_session):
        # Everything from one game: the cap cannot invent variety that is not
        # there, and must not drop puzzles trying.
        for i in range(6):
            self._puzzle(db_session, f"a{i}", "gameA", days_overdue=30 - i, ply=i)
        db_session.commit()

        ids = [f"a{i}" for i in range(6)]
        ordered, _ = get_adaptive_puzzles(db_session, "u", ids, n=5)
        assert len(ordered) == 5

        everything, _ = get_adaptive_puzzles(db_session, "u", ids, n=10)
        assert sorted(everything) == sorted(ids)

    def test_blunder_is_exempt_from_the_motif_cap(self, db_session):
        """The exemption for an unrecorded motif never fired before.

        assign_primary_motif returns "blunder" when no tactic is identified, so
        it is the unknown sentinel — 65% of puzzle_stats. The cap exempted NULL,
        but no row is NULL, so the population the exemption was written to
        protect was the one being capped.
        """
        for i in range(6):
            self._puzzle(
                db_session,
                f"g{i}",
                f"game{i}",
                motif="blunder",
                days_overdue=30 - i,
                ply=i,
            )
        db_session.commit()

        ids = [f"g{i}" for i in range(6)]
        ordered, _ = get_adaptive_puzzles(db_session, "u", ids, n=5)

        # All six are "blunder" and each is from its own game, so nothing should
        # be deferred: the session is the five most overdue, in order.
        assert ordered[:5] == ["g0", "g1", "g2", "g3", "g4"]


class TestDiversityNeverDisplacesDuePuzzles:
    """The production shape: a small due set in few games, a large new pool.

    Every other diversity test builds a pool that is all-due or all-new, so none
    of them can observe a due puzzle being pushed behind a new one — which is
    exactly the regression a global variety pass introduces. On the live corpus
    the due tier is 10 puzzles across only 4 games.
    """

    def _p(self, db, pid, game, due_days=None, ply=None, motif="blunder"):
        from services.api.models import Puzzle, PuzzleStats

        db.add(
            Puzzle(
                id=pid,
                username="u",
                source_game_id=game,
                ply=ply if ply is not None else abs(hash(pid)) % 10_000,
                fen="8/8/8/8/8/8/8/8 w - - 0 1",
                side_to_move="white",
                played_move_uci="e2e4",
                best_move_uci="d2d4",
                eval_before=0.5,
                eval_after=-1.5,
                swing=2.0,
            )
        )
        due = None
        if due_days is not None:
            due = (datetime.now(timezone.utc) - timedelta(days=due_days)).replace(
                tzinfo=None
            )
        db.add(
            PuzzleStats(
                puzzle_id=pid,
                username="u",
                primary_motif=motif,
                attempts=1 if due else 0,
                pass_count=1 if due else 0,
                ease_factor=2.0,
                interval_days=1 if due else None,
                next_due_at=due,
            )
        )

    def _corpus(self, db):
        # 10 due puzzles across 4 games, mirroring production.
        # Clustered, NOT round-robined: the three most overdue all come from
        # dueGame0. With `i % 4` the top of the tier was already four distinct
        # games before any cap ran, so no assertion about spreading could fail.
        due_ids = []
        for i in range(10):
            pid = f"due{i}"
            self._p(db, pid, f"dueGame{i // 3}", due_days=30 - i, ply=i)
            due_ids.append(pid)
        # A large new pool, each from its own game.
        new_ids = []
        for i in range(40):
            pid = f"new{i}"
            self._p(db, pid, f"newGame{i}", ply=100 + i)
            new_ids.append(pid)
        db.commit()
        return due_ids, new_ids

    def test_a_large_session_still_serves_every_due_puzzle(self, db_session):
        # The regression: a one-per-game cap applied across the whole queue let
        # only 4 of the 10 due puzzles in, no matter how many were asked for.
        due_ids, new_ids = self._corpus(db_session)

        ordered, _ = get_adaptive_puzzles(db_session, "u", due_ids + new_ids, n=20)
        session = ordered[:20]

        assert sum(1 for p in session if p.startswith("due")) == 10, session

    def test_tiers_are_emitted_in_scheduling_order(self, db_session):
        """Guards tier ORDER, not displacement.

        Note this inspects the already-sliced session, so the pre-fix code also
        passed it — by serving fewer due puzzles rather than out-of-order ones.
        test_a_large_session_still_serves_every_due_puzzle is the displacement
        guard.
        """
        due_ids, new_ids = self._corpus(db_session)

        ordered, _ = get_adaptive_puzzles(db_session, "u", due_ids + new_ids, n=20)

        first_new = next(i for i, p in enumerate(ordered) if p.startswith("new"))
        last_due = max(i for i, p in enumerate(ordered) if p.startswith("due"))
        assert last_due < first_new, ordered[:22]

    def test_a_small_session_still_prefers_due_over_new(self, db_session):
        due_ids, new_ids = self._corpus(db_session)

        ordered, _ = get_adaptive_puzzles(db_session, "u", due_ids + new_ids, n=5)

        assert all(p.startswith("due") for p in ordered[:5]), ordered[:5]

    def test_variety_still_applies_inside_the_due_tier(self, db_session):
        """Tier-safety must not mean "no variety" within the tier.

        Reads the real source_game_id rather than deriving it from the pid —
        an earlier version did pid.replace("due", ""), which yields the loop
        index, so four distinct pids always looked like four distinct games and
        the assertion could not fail.
        """
        from services.api.models import Puzzle

        due_ids, new_ids = self._corpus(db_session)

        ordered, _ = get_adaptive_puzzles(db_session, "u", due_ids + new_ids, n=4)

        games = [db_session.get(Puzzle, pid).source_game_id for pid in ordered[:4]]
        # Without the cap this is dueGame0 three times, because the three most
        # overdue puzzles all come from it.
        assert len(set(games)) == 4, games


def test_one_game_can_reach_a_session_once_per_tier(db_session):
    """Pins the real per-game bound: three through this helper, two in production.

    Counters reset per tier, so one game may supply a due puzzle, a never-seen
    one, and a scheduled-later one. `/puzzles/due` narrows the scheduled-later
    tier away first, so the shipped bound is two; the third is reachable only
    via get_due_puzzles, which has no production caller today.

    Asserted rather than described, because the comment that said "up to two"
    read as verified and was not.
    """
    from services.api.models import Puzzle, PuzzleStats
    from services.api.storage.spaced_repetition import (
        get_due_puzzles,
        get_trainable_puzzle_ids,
    )

    now = datetime.now(timezone.utc)

    def add(pid, game, due_days, ply):
        db_session.add(
            Puzzle(
                id=pid,
                username="u",
                source_game_id=game,
                ply=ply,
                fen="8/8/8/8/8/8/8/8 w - - 0 1",
                side_to_move="white",
                played_move_uci="e2e4",
                best_move_uci="d2d4",
                eval_before=0.5,
                eval_after=-1.5,
                swing=2.0,
            )
        )
        due = (
            None
            if due_days is None
            else (now - timedelta(days=due_days)).replace(tzinfo=None)
        )
        db_session.add(
            PuzzleStats(
                puzzle_id=pid,
                username="u",
                primary_motif="blunder",
                attempts=1 if due else 0,
                pass_count=0,
                ease_factor=2.0,
                interval_days=1 if due else None,
                next_due_at=due,
            )
        )

    add("x_due", "gameX", 10, 1)
    add("x_new", "gameX", None, 2)
    add("x_future", "gameX", -30, 3)
    for i in range(4):
        add(f"filler{i}", f"gameF{i}", None, 10 + i)
    db_session.commit()

    ids = [p.id for p in db_session.query(Puzzle).all()]

    session, _ = get_due_puzzles(db_session, "u", ids, n=7)
    from_one_game = [
        pid for pid in session if db_session.get(Puzzle, pid).source_game_id == "gameX"
    ]
    assert len(from_one_game) == 3, from_one_game

    # The endpoint path never sees the third: futures are dropped upstream.
    assert "x_future" not in get_trainable_puzzle_ids(db_session, "u", ids)
