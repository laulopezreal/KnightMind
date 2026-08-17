"""The resolution gate, as an invariant over (route x puzzle state).

Design §4 and §4.1, rollout step 3.

The central claim is "a revealing field is never served for an unresolved
puzzle". That is a property of a *set* of routes, so testing it per endpoint is
the shape that lets a sixth endpoint be added later without failing anything --
which is exactly how the five leak sites accumulated in the first place. These
tests parametrise over the routes instead.

The state axis matters as much as the route axis, and an earlier revision of
the design had only one point on it. Three states:

  never attempted        -- gate shut
  resolved and current   -- gate open, the user is reading their own outcome
  re-due after an attempt -- gate SHUT again

The third is §4.1. Under a lifetime `attempts > 0` rule it is indistinguishable
from the second, and every spaced-repetition repeat attempt arrives pre-spoiled
by the nickname earned on the previous one. A test that omits it passes against
the broken rule.

Every revealing field is seeded NON-NULL before absence is asserted. A fixture
that leaves `primary_motif` unset makes "the motif is not in the response" pass
with the gate deleted, and this repo has shipped six tests that could not fail.
"""

import os
from datetime import datetime, timedelta, timezone

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from services.api.main import app, get_db  # noqa: E402
from services.api.models import (  # noqa: E402
    DiagnosisStatus,
    Game,
    PuzzleDiagnosis,
    PuzzleStats,
)
from services.api.models import Puzzle as PuzzleModel  # noqa: E402

USER = "gateuser"
FEN = "6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1"

# Everything the gate is supposed to withhold, seeded so its absence means the
# gate acted rather than that the fixture never set it.
NICKNAME = "Bishop Had Bigger Plans"
MOTIF = "hanging_queen"
OPENING = "Sicilian Defense B27"


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    """Every test here runs with the rollout flag ON.

    Off is the default and is covered by the whole rest of the suite, which
    would fail loudly if the gate withheld anything while disabled.
    """
    monkeypatch.setenv("KNIGHTMIND_RESOLUTION_GATE", "true")


def _seed(db, puzzle_id: str, *, attempts: int, next_due_at: datetime | None):
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
            created_at=_naive(datetime.now(timezone.utc)),
            fen=FEN,
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
    db.add(
        PuzzleStats(
            puzzle_id=puzzle_id,
            username=USER,
            attempts=attempts,
            pass_count=0,
            fail_count=2 if attempts else 0,
            ease_factor=2.0,
            interval_days=1,
            title=NICKNAME,
            title_source="ai",
            primary_motif=MOTIF,
            next_due_at=_naive(next_due_at) if next_due_at else None,
            last_reviewed_at=_naive(datetime.now(timezone.utc)) if attempts else None,
            last_result="fail" if attempts else None,
        )
    )
    db.add(
        PuzzleDiagnosis(
            puzzle_id=puzzle_id,
            username=USER,
            status=DiagnosisStatus.OK.value,
            primary_motif=MOTIF,
            primary_cause="loose_piece_awareness",
            opening_name=OPENING,
            explanation="The solution is Qxd5.",
            training_recommendation="Look for the queen move Qxd5.",
            source="rules",
            evidence_json=[{"id": "best.move", "label": "Best move", "value": "Qxd5"}],
        )
    )
    db.commit()


NOW = datetime.now(timezone.utc)

STATES: dict[str, dict] = {
    # never attempted -> shut
    "never_attempted": dict(attempts=0, next_due_at=None, resolved=False),
    # attempted, not yet due again -> open, the user is reading their outcome
    "resolved_and_current": dict(
        attempts=1, next_due_at=NOW + timedelta(days=3), resolved=True
    ),
    # attempted, and it has come due again -> SHUT. This is §4.1.
    "re_due_after_attempt": dict(
        attempts=1, next_due_at=NOW - timedelta(minutes=1), resolved=False
    ),
}


def _payloads(client, puzzle_id: str) -> dict[str, dict]:
    """Every route that returns this puzzle, keyed by route name."""
    detail = client.get(f"/puzzles/{puzzle_id}?username={USER}").json()
    listed = client.get(f"/puzzles/list?username={USER}").json()["puzzles"]
    from_list: dict = next((p for p in listed if p["id"] == puzzle_id), {})
    due = client.get(f"/puzzles/due?username={USER}&limit=10").json()["puzzles"]
    from_due: dict = next((p for p in due if p.get("id") == puzzle_id), {})
    return {
        "detail": detail,
        "list": from_list,
        "due": from_due,
    }


@pytest.mark.parametrize("state", list(STATES))
@pytest.mark.parametrize("route", ["detail", "list", "due"])
def test_revealing_fields_follow_the_gate(client, db_session, state, route):
    """One assertion, every (route x state) pair.

    A sixth route added later fails this the moment it is added to `_payloads`
    without going through the serializer.
    """
    spec = STATES[state]
    _seed(
        db_session,
        "p-gate",
        attempts=spec["attempts"],
        next_due_at=spec["next_due_at"],
    )

    payload = _payloads(client, "p-gate")[route]
    if not payload:
        pytest.skip(f"{route} does not serve this puzzle in state {state}")

    if spec["resolved"]:
        assert payload.get("title") == NICKNAME
        assert payload.get("primary_motif") == MOTIF
        assert payload.get("display_name") == NICKNAME
    else:
        assert payload.get("title") is None
        assert payload.get("primary_motif") is None
        # Provenance takes its place -- never blank, and never the nickname.
        assert payload.get("display_name")
        assert NICKNAME not in payload["display_name"]
        assert "move 18" in payload["display_name"]


class TestTheDiagnosisProse:
    """The most revealing payload in the app: it names the cause and the
    solution in sentences."""

    def test_it_is_withheld_before_an_attempt(self, client, db_session):
        _seed(db_session, "p-diag", attempts=0, next_due_at=None)

        body = client.get(f"/puzzles/p-diag/diagnosis?username={USER}").json()

        assert body["state"] == "withheld"
        assert body["primary_cause"] is None
        assert body.get("explanation") is None
        assert body["primary_motif"] is None

    def test_it_is_withheld_again_once_the_puzzle_is_re_due(self, client, db_session):
        """§4.1 on the surface that matters most."""
        _seed(
            db_session,
            "p-diag2",
            attempts=1,
            next_due_at=NOW - timedelta(minutes=1),
        )

        body = client.get(f"/puzzles/p-diag2/diagnosis?username={USER}").json()

        assert body["state"] == "withheld"

    def test_it_is_served_while_the_user_is_reading_their_outcome(
        self, client, db_session
    ):
        _seed(db_session, "p-diag3", attempts=1, next_due_at=NOW + timedelta(days=3))

        body = client.get(f"/puzzles/p-diag3/diagnosis?username={USER}").json()

        assert body["state"] != "withheld"
        assert body["primary_cause"] == "loose_piece_awareness"


class TestSearchCannotConfirmAWithheldName:
    """Review 6, finding 7. Hiding a nickname from the payload while the WHERE
    clause still answers questions about it is a slower way of showing it."""

    def test_an_unresolved_nickname_is_not_matchable(self, client, db_session):
        _seed(db_session, "pzl-x1", attempts=0, next_due_at=None)

        body = client.get(f"/puzzles/list?username={USER}&q=bishop").json()

        assert body["puzzles"] == []

    def test_a_resolved_nickname_is_still_matchable(self, client, db_session):
        _seed(db_session, "pzl-x2", attempts=1, next_due_at=NOW + timedelta(days=3))

        body = client.get(f"/puzzles/list?username={USER}&q=bishop").json()

        assert [p["id"] for p in body["puzzles"]] == ["pzl-x2"]

    def test_the_opening_stays_searchable_either_way(self, client, db_session):
        """The opening is provenance, which is never withheld."""
        _seed(db_session, "pzl-x3", attempts=0, next_due_at=None)

        body = client.get(f"/puzzles/list?username={USER}&q=sicilian").json()

        assert [p["id"] for p in body["puzzles"]] == ["pzl-x3"]


class TestTheFlagIsOffByDefault:
    def test_nothing_is_withheld_without_the_flag(
        self, client, db_session, monkeypatch
    ):
        """The whole point of shipping behind a flag: `dev` behaves exactly as
        it did before this module existed."""
        monkeypatch.delenv("KNIGHTMIND_RESOLUTION_GATE", raising=False)
        _seed(db_session, "p-off", attempts=0, next_due_at=None)

        detail = client.get(f"/puzzles/p-off?username={USER}").json()

        assert detail["title"] == NICKNAME
        assert detail["primary_motif"] == MOTIF
