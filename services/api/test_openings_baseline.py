"""Endpoint tests for the opening baseline.

Upstream is always stubbed. What is under test is the part we own: which band
is asked for, what is cached, what happens when lichess is down, and whether
the answer is honest about how much it rests on.
"""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from services.api.db import get_db
from services.api.main import app
from services.api.models import OpeningExplorerCache, RatingSnapshot
from services.api.openings.explorer import ExplorerStats, ExplorerUnavailable

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
SICILIAN_FEN = "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"


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


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Nothing in this file may reach lichess.

    Not a convenience: a test that forgets to stub upstream would otherwise
    pass by making a real call, and would then fail in CI, offline, or the
    moment the explorer rate-limits us. Default it to unavailable so forgetting
    is loud and local.
    """
    monkeypatch.setattr(
        "services.api.main.fetch_explorer_stats",
        AsyncMock(side_effect=ExplorerUnavailable("network disabled in tests")),
    )


@pytest.fixture
def upstream(monkeypatch, _no_network):
    """Stub the lichess call with data. Returns the mock so calls can be counted."""
    mock = AsyncMock(return_value=ExplorerStats(white=600, draws=200, black=200))
    monkeypatch.setattr("services.api.main.fetch_explorer_stats", mock)
    return mock


def rate(db_session, username: str, rating: int, time_control: str = "rapid") -> None:
    db_session.add(
        RatingSnapshot(
            username=username,
            time_control=time_control,
            rating=rating,
            recorded_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()


def ask(client, *, fen=START_FEN, color="white", username="alice"):
    return client.get(
        "/openings/baseline",
        params={"username": username, "fen": fen, "color": color},
    )


class TestTheAnswer:
    def test_reports_the_expected_score_for_the_side_asked_about(
        self, client, upstream
    ):
        response = ask(client, color="white")

        assert response.status_code == 200
        # (600 + 0.5*200) / 1000 — a chess score, matching how the tree states
        # the user's own figure, so the two are comparable at all.
        assert response.json()["expected_score"] == 70.0

    def test_the_other_side_gets_the_other_answer(self, client, upstream):
        assert ask(client, color="black").json()["expected_score"] == 30.0

    def test_reports_how_many_games_it_rests_on(self, client, upstream):
        assert ask(client).json()["games"] == 1000

    def test_refuses_a_colour_it_cannot_answer_for(self, client, upstream):
        # Under a "both" filter the user's own figure already mixes games from
        # either side of the board, so there is no single expectation to
        # compare against. Answering anyway would be a fabrication.
        assert ask(client, color="both").status_code == 422

    def test_says_nothing_rather_than_something_thin(self, client, monkeypatch):
        monkeypatch.setattr(
            "services.api.main.fetch_explorer_stats",
            AsyncMock(return_value=ExplorerStats(white=3, draws=0, black=1)),
        )

        body = ask(client).json()

        assert body["expected_score"] is None
        # Still honest about what was found, so the client can say why.
        assert body["games"] == 4

    def test_rejects_a_position_that_is_not_one(self, client, upstream):
        response = ask(client, fen="not a fen")

        assert response.status_code == 422
        upstream.assert_not_called()


class TestTheBand:
    def test_uses_the_band_the_player_is_in(self, client, db_session, upstream):
        rate(db_session, "alice", 1650)

        band = ask(client).json()["band"]

        assert band["low"] == 1600
        assert band["label"] == "1600–1800"
        assert upstream.await_args.args[1].low == 1600

    def test_prefers_rapid_over_blitz(self, client, db_session, upstream):
        # The explorer query excludes bullet and the two pools are scaled
        # differently, so the band must come from a pool the comparison is
        # actually drawn from.
        rate(db_session, "alice", 1100, time_control="blitz")
        rate(db_session, "alice", 1900, time_control="rapid")

        assert ask(client).json()["band"]["low"] == 1800

    def test_admits_when_it_does_not_know_the_rating(self, client, upstream):
        body = ask(client).json()

        # The figures are then over all ratings, and the client has to say so
        # rather than implying a peer group the user may not be in.
        assert body["band"] is None
        assert upstream.await_args.args[1] is None


class TestTheCache:
    def test_a_second_look_does_not_leave_the_box(self, client, upstream):
        ask(client)
        ask(client)

        # A baseline on every selection would otherwise be an outbound call on
        # every selection.
        assert upstream.await_count == 1

    def test_the_cache_is_shared_between_users(self, client, db_session, upstream):
        # The row is a fact about a position in a public database, not about
        # anyone's games, so one user's lookup answers everyone else's.
        ask(client, username="alice")
        ask(client, username="bob")

        assert upstream.await_count == 1

    def test_a_transposition_reuses_the_same_row(self, client, upstream):
        # Same position, different move counters — the counters are dropped
        # before the key is built, so the two share one lookup.
        ask(client, fen=SICILIAN_FEN)
        ask(client, fen=SICILIAN_FEN.replace(" 0 2", " 4 9"))

        assert upstream.await_count == 1

    def test_different_positions_do_not_share_a_row(self, client, upstream):
        ask(client, fen=START_FEN)
        ask(client, fen=SICILIAN_FEN)

        assert upstream.await_count == 2

    def test_different_bands_do_not_share_a_row(self, client, db_session, upstream):
        rate(db_session, "alice", 1050)
        ask(client, username="alice")
        rate(db_session, "bob", 2300)
        ask(client, username="bob")

        assert upstream.await_count == 2

    def test_stores_what_it_fetched(self, client, db_session, upstream):
        ask(client)

        row = db_session.query(OpeningExplorerCache).one()
        assert (row.white, row.draws, row.black) == (600, 200, 200)


class TestHoldingResources:
    def test_lets_go_of_the_connection_while_waiting_on_lichess(
        self, client, db_session, monkeypatch
    ):
        """The pool is 15 deep and this route fires on every line selected.

        SQLAlchemy holds a pooled connection from the first query until the
        transaction ends, so an in-flight miss that kept one would starve every
        other endpoint whenever the explorer got slow.
        """
        observed = {}

        async def watching(epd, band):
            observed["in_transaction"] = db_session.in_transaction()
            return ExplorerStats(white=600, draws=200, black=200)

        monkeypatch.setattr("services.api.main.fetch_explorer_stats", watching)

        assert ask(client).status_code == 200
        assert observed["in_transaction"] is False

    def test_still_writes_what_it_fetched_afterwards(
        self, client, db_session, upstream
    ):
        # Releasing the connection must not cost us the write that follows.
        ask(client)

        assert db_session.query(OpeningExplorerCache).count() == 1


class TestWhenLichessIsDown:
    def test_serves_a_stale_row_rather_than_nothing(
        self, client, db_session, monkeypatch, upstream
    ):
        ask(client, username="alice")  # warms the cache
        row = db_session.query(OpeningExplorerCache).one()
        row.fetched_at = datetime.now(timezone.utc) - timedelta(days=400)
        db_session.commit()
        monkeypatch.setattr(
            "services.api.main.fetch_explorer_stats",
            AsyncMock(side_effect=ExplorerUnavailable("down")),
        )

        response = ask(client)

        # These aggregates move at the speed of millions of games; a year-old
        # number is still true enough to judge a line by, and the alternative
        # is the baseline blinking out whenever lichess has a bad minute.
        assert response.status_code == 200
        assert response.json()["expected_score"] == 70.0

    def test_says_so_when_there_is_nothing_to_fall_back_on(self, client):
        # `_no_network` already has upstream failing.
        response = ask(client)

        # A distinct status, so the client can stay quiet instead of showing a
        # baseline it does not have.
        assert response.status_code == 503

    def test_a_stale_row_is_refreshed_when_upstream_recovers(
        self, client, db_session, upstream
    ):
        ask(client)
        row = db_session.query(OpeningExplorerCache).one()
        row.fetched_at = datetime.now(timezone.utc) - timedelta(days=400)
        db_session.commit()

        ask(client)

        assert upstream.await_count == 2
        assert db_session.query(OpeningExplorerCache).one().fetched_at > (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).replace(tzinfo=None)
