from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.api.models import Base
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
