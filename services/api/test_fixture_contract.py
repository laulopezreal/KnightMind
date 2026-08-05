"""What the shared DB fixture promises, asserted rather than assumed.

Two properties carry this suite's credibility, and both fail silently:

* Foreign keys are enforced. SQLite ignores them unless PRAGMA foreign_keys is
  set on every connection. When it was off, 38 tests were inserting puzzles
  with no game and reviews with no puzzle -- passing while asserting against
  rows the production schema forbids. If the PRAGMA listener in conftest is
  ever dropped or moved, nothing else in the suite goes red; it just quietly
  stops catching that class again.

* Tests start from an empty database. On SQLite that is structural (a new
  in-memory database per test). On Postgres it depends on a TRUNCATE actually
  running between tests, and a TRUNCATE that silently stopped would leave tests
  passing on each other's leftovers.

Both are asserted here against whichever backend is configured, so the checks
hold on the fast SQLite run and the Postgres one alike.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from services.api.models import Game, Puzzle

_ORPHAN_FEN = "6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1"


def _orphan_puzzle(puzzle_id="contract-orphan"):
    """A puzzle whose (source_game_id, username) has no row in games."""
    return Puzzle(
        id=puzzle_id,
        username="contractuser",
        source_game_id="no-such-game",
        ply=1,
        fen=_ORPHAN_FEN,
        side_to_move="white",
        played_move_uci="d1d2",
        best_move_uci="d1d5",
        eval_before=0.5,
        eval_after=-3.0,
        swing=3.0,
    )


def test_foreign_keys_are_enforced(db_session):
    """A child row with no parent must be rejected by the database.

    Backend-agnostic on purpose: Postgres rejects this natively, SQLite only
    once conftest turns the PRAGMA on. A single assertion covers both, so the
    default run is what catches a regression rather than a later Postgres run.
    """
    db_session.add(_orphan_puzzle())
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --- isolation -------------------------------------------------------------
# These two run in file order, which pytest guarantees within a module. The
# first deliberately commits; the second fails if that row survived.
#
# Load-bearing on Postgres only, and deliberately kept anyway. There, isolation
# is a TRUNCATE that can stop running; disabling it was confirmed to fail the
# second test. On SQLite isolation is structural -- the fixture builds a new
# in-memory database per test -- so the pair cannot fail there short of the
# fixture being rewritten. Worth stating, because a guard that only bites on one
# backend is easy to mistake for one that bites on both.


def test_isolation_first_commits_a_row(db_session):
    db_session.add(
        Game(
            game_id="contract-isolation",
            url="",
            username="contractuser",
            white_username="contractuser",
            black_username="",
            white_result="",
            black_result="",
            time_control="",
            end_time=0,
        )
    )
    db_session.commit()
    assert db_session.get(Game, ("contract-isolation", "contractuser")) is not None


def test_isolation_second_starts_empty(db_session):
    """The committed row from the previous test must not be visible here."""
    remaining = db_session.execute(select(func.count()).select_from(Game)).scalar()
    assert remaining == 0, (
        f"{remaining} games row(s) survived into the next test. On Postgres this "
        "means the TRUNCATE in the db_engine fixture is not running; on SQLite it "
        "means the per-test engine is being reused. Either way tests are now "
        "sharing state and passing on each other's leftovers."
    )
