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
