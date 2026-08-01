"""The recency window on /openings.

A repertoire is a moving target. Pooling every game ever played means a line
fixed in April still reads as a weakness, because two years of losses in it sit
beside last week's wins. These cover the window itself and — just as much — the
difference between "you have played nothing lately" and "you have imported
nothing", which want different things said to them.
"""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.db import get_db
from services.api.main import app
from services.api.models import Base, Game
from services.api.openings import make_key

PGN_E4 = '[White "alice"]\n[Black "bob"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 1-0'
PGN_D4 = '[White "alice"]\n[Black "bob"]\n[Result "0-1"]\n\n1. d4 d5 2. c4 0-1'


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session, monkeypatch):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        "services.api.main.SessionLocal", sessionmaker(bind=db_session.get_bind())
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def store(db, game_id: str, pgn: str, days_ago: int, username: str = "alice") -> None:
    end = datetime.now(timezone.utc) - timedelta(days=days_ago)
    db.add(
        Game(
            game_id=game_id,
            url=f"https://example.test/{game_id}",
            username=username,
            white_username=username,
            black_username="bob",
            white_result="win",
            black_result="checkmated",
            time_control="600",
            end_time=int(end.timestamp()),
            rated=True,
            pgn_blob=pgn,
        )
    )
    db.commit()


def tree(client, **params):
    query = {"username": "alice", "color": "white", **params}
    response = client.get("/openings", params=query)
    assert response.status_code == 200, response.text
    return response.json()


def first_moves(body) -> set[str]:
    return {child["move_san"] for child in body.get("children", [])}


class TestTheWindow:
    def test_keeps_only_games_inside_it(self, client, db_session):
        store(db_session, "recent", PGN_E4, days_ago=5)
        store(db_session, "old", PGN_D4, days_ago=400)

        assert first_moves(tree(client, since_days=90)) == {"e4"}

    def test_the_whole_archive_when_no_window_is_asked_for(self, client, db_session):
        store(db_session, "recent", PGN_E4, days_ago=5)
        store(db_session, "old", PGN_D4, days_ago=400)

        assert first_moves(tree(client)) == {"e4", "d4"}

    def test_a_game_on_the_near_side_of_the_boundary_counts(self, client, db_session):
        store(db_session, "just-inside", PGN_E4, days_ago=89)

        assert first_moves(tree(client, since_days=90)) == {"e4"}

    def test_a_game_past_the_boundary_does_not(self, client, db_session):
        store(db_session, "just-outside", PGN_E4, days_ago=91)
        store(db_session, "recent", PGN_D4, days_ago=1)

        assert first_moves(tree(client, since_days=90)) == {"d4"}

    def test_reports_what_the_window_left_out(self, client, db_session):
        store(db_session, "recent", PGN_E4, days_ago=5)
        store(db_session, "old-1", PGN_D4, days_ago=400)
        store(db_session, "old-2", PGN_D4, days_ago=500)

        analysis = tree(client, since_days=90)["analysis"]

        # Beside excluded_by_color, and for the same reason: the user asked for
        # this, so it is a fact to state rather than data loss to warn about.
        assert analysis["excluded_by_date"] == 2
        assert analysis["games_skipped"] == 0

    def test_reports_nothing_excluded_without_a_window(self, client, db_session):
        store(db_session, "old", PGN_D4, days_ago=400)

        assert tree(client)["analysis"]["excluded_by_date"] == 0

    def test_reports_the_window_it_applied(self, client, db_session):
        store(db_session, "recent", PGN_E4, days_ago=5)

        assert tree(client, since_days=30)["analysis"]["since_days"] == 30
        assert tree(client)["analysis"]["since_days"] is None

    def test_still_counts_the_whole_archive_as_stored(self, client, db_session):
        store(db_session, "recent", PGN_E4, days_ago=5)
        store(db_session, "old", PGN_D4, days_ago=400)

        # `games_stored` is what the account holds, not what this view shows —
        # the partial-archive warning is judged against it.
        assert tree(client, since_days=90)["analysis"]["games_stored"] == 2

    def test_rejects_a_window_the_endpoint_does_not_accept(self, client, db_session):
        store(db_session, "recent", PGN_E4, days_ago=5)

        assert (
            client.get(
                "/openings", params={"username": "alice", "since_days": 0}
            ).status_code
            == 422
        )


class TestNothingLately:
    def test_an_empty_window_is_not_an_empty_account(self, client, db_session):
        store(db_session, "old", PGN_E4, days_ago=400)

        response = client.get(
            "/openings", params={"username": "alice", "since_days": 30}
        )

        # A 404 here would send someone with 400 imported games to the import
        # screen. They have played nothing lately, which is a different
        # sentence with a different next step.
        assert response.status_code == 200
        body = response.json()
        assert body["games_count"] == 0
        assert body["analysis"]["excluded_by_date"] == 1

    def test_an_empty_account_is_still_a_404(self, client):
        response = client.get(
            "/openings", params={"username": "nobody", "since_days": 30}
        )

        assert response.status_code == 404


class TestCaching:
    def test_two_windows_do_not_share_an_entry(self):
        common = dict(
            username="alice",
            color="white",
            max_ply=12,
            game_count=10,
            latest_game_time=None,
        )

        assert make_key(**common, since="2026-08-01") != make_key(
            **common, since="2026-05-03"
        )

    def test_a_window_retires_when_the_day_turns(self):
        """The boundary moves even though the stored games do not.

        This is the one invalidation signal the rest of the key cannot supply:
        `game_count` and the latest game's timestamp are both unchanged
        overnight, so without the resolved date, yesterday's "last 90 days"
        would be served today.
        """
        common = dict(
            username="alice",
            color="white",
            max_ply=12,
            game_count=10,
            latest_game_time=None,
        )

        assert make_key(**common, since="2026-08-01") != make_key(
            **common, since="2026-07-31"
        )

    def test_the_same_window_on_the_same_day_hits(self):
        common = dict(
            username="alice",
            color="white",
            max_ply=12,
            game_count=10,
            latest_game_time=None,
        )

        assert make_key(**common, since="2026-08-01") == make_key(
            **common, since="2026-08-01"
        )

    def test_serves_the_right_tree_for_each_window_in_turn(self, client, db_session):
        # Same user, same colour, two windows back to back: the second must not
        # be answered out of the first's entry.
        store(db_session, "recent", PGN_E4, days_ago=5)
        store(db_session, "old", PGN_D4, days_ago=400)

        narrow = first_moves(tree(client, since_days=90))
        wide = first_moves(tree(client))
        narrow_again = first_moves(tree(client, since_days=90))

        assert narrow == {"e4"}
        assert wide == {"e4", "d4"}
        assert narrow_again == {"e4"}
