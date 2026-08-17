"""Rung 0 of the hint ladder: ask for the motif (design §5.1).

Without this the gate is a wall rather than a gate. §4 strips the motif from
every pre-attempt payload, and the existing ladder starts at "name the piece to
move" -- which reveals strictly MORE than the motif does. So a user who wants
the smallest possible nudge has to take a bigger one, and the cheapest rung is
the one the gate removed.

The existing hint endpoint could not serve this: `POST /sessions/{id}/hint`
takes a session and a username and has no `puzzle_id`, so it cannot say which
puzzle was hinted, and after §4 the client has nowhere else to read the motif.
"""

import os
from datetime import datetime, timezone

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from services.api.main import app, get_db  # noqa: E402
from services.api.models import (  # noqa: E402
    Game,
    PuzzleStats,
    TrainingSession,
)
from services.api.models import Puzzle as PuzzleModel  # noqa: E402

USER = "hintuser"
MOTIF = "hanging_queen"


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    """The rung only matters when the gate is on -- otherwise the motif is
    already in the payload and nobody needs to ask."""
    monkeypatch.setenv("KNIGHTMIND_RESOLUTION_GATE", "true")


def _seed(db, puzzle_id="p1", *, motif: str | None = MOTIF, stats: bool = True):
    game_id = f"g-{puzzle_id}"
    if not db.get(Game, (game_id, USER)):
        db.add(
            Game(
                game_id=game_id,
                url=f"https://chess.com/game/{game_id}",
                username=USER,
                white_username=USER,
                black_username="opponent",
                white_result="resigned",
                black_result="win",
                time_control="600+5",
                end_time=int(datetime(2026, 3, 12, tzinfo=timezone.utc).timestamp()),
                rated=True,
                pgn_blob="1. e4 e5 *",
            )
        )
    db.add(
        PuzzleModel(
            id=puzzle_id,
            username=USER,
            source_game_id=game_id,
            ply=35,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            fen="6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1",
            side_to_move="white",
            played_move_uci="d1d2",
            best_move_uci="d1d5",
            accept_moves_uci="d1d5",
            solution_pv="d1d5",
            eval_before=1.5,
            eval_after=-7.5,
            swing=9.0,
            confirmed_depth=18,
        )
    )
    if stats:
        db.add(
            PuzzleStats(
                puzzle_id=puzzle_id,
                username=USER,
                attempts=0,
                pass_count=0,
                fail_count=0,
                ease_factor=2.0,
                primary_motif=motif,
            )
        )
    db.commit()


def _session(db, session_id="s1", *, completed=False):
    db.add(
        TrainingSession(
            id=session_id,
            username=USER,
            requested_n=5,
            hints_used=0,
            completed_at=(
                datetime.now(timezone.utc).replace(tzinfo=None) if completed else None
            ),
        )
    )
    db.commit()


class TestItIsTheGatesExit:
    def test_the_motif_is_returned_even_though_the_payload_withholds_it(
        self, client, db_session
    ):
        """Both halves in one test, because either alone is misleading: the
        payload must withhold it AND this endpoint must serve it."""
        _seed(db_session)

        detail = client.get(f"/puzzles/p1?username={USER}").json()
        assert detail["primary_motif"] is None  # the gate is doing its job

        hinted = client.post("/puzzles/p1/hint/motif", json={"username": USER}).json()
        assert hinted["primary_motif"] == MOTIF

    def test_it_works_without_a_session(self, client, db_session):
        """The Library board has no session. Refusing there would make the gate
        inescapable on that surface."""
        _seed(db_session)

        body = client.post("/puzzles/p1/hint/motif", json={"username": USER}).json()

        assert body["primary_motif"] == MOTIF
        assert body["hints_used"] is None


class TestTheAskIsRecorded:
    def test_it_increments_the_session_counter(self, client, db_session):
        """The reason this is an endpoint rather than a relaxed serializer: a
        motif that arrives this way is a hint the user spent."""
        _seed(db_session)
        _session(db_session)

        body = client.post(
            "/puzzles/p1/hint/motif",
            json={"username": USER, "session_id": "s1"},
        ).json()

        assert body["hints_used"] == 1
        assert db_session.get(TrainingSession, "s1").hints_used == 1

    def test_a_completed_session_does_not_cost_a_hint_but_still_answers(
        self, client, db_session
    ):
        _seed(db_session)
        _session(db_session, completed=True)

        body = client.post(
            "/puzzles/p1/hint/motif",
            json={"username": USER, "session_id": "s1"},
        ).json()

        assert body["primary_motif"] == MOTIF
        assert body["hints_used"] is None
        assert db_session.get(TrainingSession, "s1").hints_used == 0

    def test_an_unknown_session_still_answers(self, client, db_session):
        """A bad session id must not cost the user the hint they asked for."""
        _seed(db_session)

        body = client.post(
            "/puzzles/p1/hint/motif",
            json={"username": USER, "session_id": "nope"},
        ).json()

        assert body["primary_motif"] == MOTIF
        assert body["hints_used"] is None


class TestItSpendsNothingOnNothing:
    def test_blunder_is_reported_as_no_motif(self, client, db_session):
        """`blunder` means no motif was identified. Spending a hint to be told
        that nothing was identified is worse than being told there is nothing
        to tell -- and §11 step 7 stops rendering it everywhere else."""
        _seed(db_session, motif="blunder")

        body = client.post("/puzzles/p1/hint/motif", json={"username": USER}).json()

        assert body["primary_motif"] is None

    def test_a_puzzle_with_no_stats_row_answers_honestly(self, client, db_session):
        _seed(db_session, stats=False)

        body = client.post("/puzzles/p1/hint/motif", json={"username": USER}).json()

        assert body["primary_motif"] is None


class TestOwnership:
    def test_another_users_puzzle_is_a_404(self, client, db_session):
        _seed(db_session)

        response = client.post(
            "/puzzles/p1/hint/motif", json={"username": "someone_else"}
        )

        assert response.status_code == 404

    def test_a_missing_puzzle_is_a_404(self, client, db_session):
        response = client.post("/puzzles/nope/hint/motif", json={"username": USER})

        assert response.status_code == 404
