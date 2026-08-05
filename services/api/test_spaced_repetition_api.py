from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.api.db import get_db
from services.api.main import app
from services.api.models import (
    Game,
    PuzzleStats,
)
from services.api.models import (
    Puzzle as PuzzleModel,
)


@pytest.fixture
def client(db_session):

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    del app.dependency_overrides[get_db]


@pytest.fixture
def seed_puzzles(db_session):
    """Insert test puzzles directly into the DB."""
    # Create a parent game
    db_session.add(
        Game(
            game_id="g1",
            url="https://chess.com/game/g1",
            username="testuser",
            white_username="testuser",
            black_username="opponent",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=1704067200,
            rated=True,
        )
    )
    db_session.flush()

    for i, pid in enumerate(["p1", "p2", "p3"]):
        db_session.add(
            PuzzleModel(
                id=pid,
                username="testuser",
                source_game_id="g1",
                ply=i + 1,
                fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                side_to_move="white",
                played_move_uci="e2e3",
                best_move_uci="e2e4",
                eval_before=0.5,
                eval_after=-0.5,
                swing=1.0,
                created_at=datetime.now(timezone.utc),
            )
        )
    db_session.commit()


def test_due_puzzles_priority_and_merge(client, db_session, seed_puzzles):
    """Due first, then new — and never a puzzle scheduled for the future.

    /puzzles/due used to top the response up to `n` with not-yet-due puzzles,
    which made the UI's "N puzzles due" a lie and corrupted the intervals (an
    early review re-anchors next_due_at on today). Future puzzles are now
    excluded, so a short session is short.
    """
    # Setup stats: p1 is due, p2 is new, p3 is future
    now = datetime.now(timezone.utc)

    # p1: Due (yesterday)
    s1 = PuzzleStats(
        puzzle_id="p1",
        username="testuser",
        attempts=1,
        pass_count=1,
        last_result="pass",
        interval_days=1,
        ease_factor=2.0,
        next_due_at=now - timedelta(days=1),
    )
    # p3: Future (tomorrow)
    s3 = PuzzleStats(
        puzzle_id="p3",
        username="testuser",
        attempts=1,
        pass_count=1,
        last_result="pass",
        interval_days=1,
        ease_factor=2.0,
        next_due_at=now + timedelta(days=1),
    )
    db_session.add(s1)
    db_session.add(s3)
    db_session.commit()

    # Request 3 puzzles
    response = client.get("/puzzles/due?username=testuser&n=3")
    assert response.status_code == 200
    data = response.json()

    # p1 (due) + p2 (new) are trainable; p3 (due tomorrow) is not.
    assert data["due_count"] == 2
    assert data["returned_count"] == 2

    puzzles = data["puzzles"]
    # Order should be p1 (due), then p2 (new). p3 is withheld until it is due.
    assert [p["id"] for p in puzzles] == ["p1", "p2"]

    # Check merge
    assert puzzles[0]["attempts"] == 1
    assert puzzles[1]["attempts"] == 0  # New puzzle defaults
    assert puzzles[1]["ease_factor"] == 2.0


def test_due_puzzles_never_serves_a_future_puzzle_to_fill_the_session(
    client, db_session, seed_puzzles
):
    """A user with 1 due puzzle gets a 1-puzzle session, not a padded 3.

    Regression: `get_adaptive_puzzles` sorts future puzzles last but never
    dropped them, so `sorted_pids[:n]` happily returned puzzles due weeks out.
    Passing one of those inflated its interval; failing one reset a
    well-learned puzzle to interval 1.
    """
    now = datetime.now(timezone.utc)
    for pid, offset in (("p1", -timedelta(days=1)), ("p2", timedelta(days=30))):
        db_session.add(
            PuzzleStats(
                puzzle_id=pid,
                username="testuser",
                attempts=3,
                pass_count=3,
                last_result="pass",
                interval_days=30,
                ease_factor=2.5,
                next_due_at=now + offset,
            )
        )
    # p3 stays new (no stats) and so remains trainable.
    db_session.add(
        PuzzleStats(
            puzzle_id="p3",
            username="testuser",
            attempts=1,
            pass_count=1,
            last_result="pass",
            interval_days=30,
            ease_factor=2.5,
            next_due_at=now + timedelta(days=30),
        )
    )
    db_session.commit()

    data = client.get("/puzzles/due?username=testuser&n=5").json()
    assert [p["id"] for p in data["puzzles"]] == ["p1"]
    assert data["due_count"] == 1

    # ...and the status endpoint agrees, so the hero card can't promise 3.
    assert client.get("/users/testuser/status").json()["due_count"] == 1


def test_status_due_count_includes_freshly_generated_puzzles(
    client, db_session, seed_puzzles
):
    """Never-reviewed puzzles are trainable even when older ones are not.

    Regression: due_count only counted scheduled-and-arrived puzzles, with an
    all-or-nothing "if there are no stats rows at all, everything is due"
    fallback. A returning user who generated new puzzles while their existing
    ones were scheduled ahead saw due_count == 0 and a disabled "Start Session"
    button sitting on top of a pile of untouched puzzles.
    """
    now = datetime.now(timezone.utc)
    db_session.add(
        PuzzleStats(
            puzzle_id="p1",
            username="testuser",
            attempts=1,
            pass_count=1,
            last_result="pass",
            interval_days=30,
            ease_factor=2.5,
            next_due_at=now + timedelta(days=30),
        )
    )
    db_session.commit()

    # p2 and p3 have never been reviewed.
    assert client.get("/users/testuser/status").json()["due_count"] == 2
    data = client.get("/puzzles/due?username=testuser&n=5").json()
    assert sorted(p["id"] for p in data["puzzles"]) == ["p2", "p3"]


def test_due_puzzles_returns_empty_when_nothing_is_trainable(
    client, db_session, seed_puzzles
):
    """All caught up is an empty 200, not a padded session."""
    now = datetime.now(timezone.utc)
    for pid in ("p1", "p2", "p3"):
        db_session.add(
            PuzzleStats(
                puzzle_id=pid,
                username="testuser",
                attempts=1,
                pass_count=1,
                last_result="pass",
                interval_days=30,
                ease_factor=2.5,
                next_due_at=now + timedelta(days=30),
            )
        )
    db_session.commit()

    response = client.get("/puzzles/due?username=testuser&n=5")
    assert response.status_code == 200
    data = response.json()
    assert data["puzzles"] == []
    assert data["due_count"] == 0
    assert client.get("/users/testuser/status").json()["due_count"] == 0


def test_review_endpoint(client, db_session, seed_puzzles):
    response = client.post(
        "/puzzles/p1/review",
        json={"username": "testuser", "result": "pass", "time_spent_ms": 3000},
    )
    assert response.status_code == 200
    data = response.json()

    assert data["interval_days"] == 1  # First review pass = 1
    assert data["ease_factor"] == pytest.approx(2.05)
    assert data["stats"]["attempts"] == 1

    # Second review pass
    response = client.post(
        "/puzzles/p1/review",
        json={"username": "testuser", "result": "pass", "time_spent_ms": 2000},
    )
    data = response.json()
    assert data["interval_days"] == 3  # pass after 1 = 3
    assert data["ease_factor"] == pytest.approx(2.1)
    assert data["stats"]["attempts"] == 2


def test_due_puzzles_no_puzzles_returns_404(client):
    response = client.get("/puzzles/due?username=missinguser&n=2")
    assert response.status_code == 404
    assert "no puzzles found" in response.json()["detail"].lower()


class TestFocusCauseParameter:
    """`focus_cause` on /puzzles/due biases a session; it must not filter it.

    The distinction matters at the endpoint boundary because `motif` — the
    parameter sitting next to it — *does* filter, and 404s when nothing
    matches. A user clicking "train this pattern" on a day with nothing of that
    pattern due should get their normal session, not an error.
    """

    def _diagnose(self, db, puzzle_id, cause):
        from services.api.models import DiagnosisStatus, PuzzleDiagnosis

        db.add(
            PuzzleDiagnosis(
                puzzle_id=puzzle_id,
                username="testuser",
                status=DiagnosisStatus.OK,
                primary_cause=cause,
                source="rules",
                evidence_json=[],
            )
        )
        db.commit()

    def _due(self, db, pid, days_ago):
        db.add(
            PuzzleStats(
                puzzle_id=pid,
                username="testuser",
                attempts=1,
                pass_count=1,
                last_result="pass",
                interval_days=1,
                ease_factor=2.0,
                next_due_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            )
        )
        db.commit()

    def test_serves_the_focused_puzzle_first(self, client, db_session, seed_puzzles):
        self._due(db_session, "p1", 10)
        self._due(db_session, "p2", 1)
        self._diagnose(db_session, "p2", "king_safety_blindness")

        plain = client.get("/puzzles/due?username=testuser&n=5").json()
        assert plain["puzzles"][0]["id"] == "p1"

        focused = client.get(
            "/puzzles/due?username=testuser&n=5&focus_cause=king_safety_blindness"
        ).json()
        assert focused["puzzles"][0]["id"] == "p2"

    def test_returns_a_normal_session_when_the_focus_has_nothing_due(
        self, client, db_session, seed_puzzles
    ):
        self._due(db_session, "p1", 3)
        self._diagnose(db_session, "p1", "loose_piece_awareness")

        focused = client.get(
            "/puzzles/due?username=testuser&n=5&focus_cause=endgame_technique_gap"
        )
        assert focused.status_code == 200
        plain = client.get("/puzzles/due?username=testuser&n=5").json()
        assert [p["id"] for p in focused.json()["puzzles"]] == [
            p["id"] for p in plain["puzzles"]
        ]

    def test_an_unknown_cause_is_not_an_error(self, client, db_session, seed_puzzles):
        # No allow-list check on purpose: an unknown cause simply matches no
        # puzzles, which the bias already handles. Rejecting it would turn a
        # stale bookmark into a broken session.
        res = client.get("/puzzles/due?username=testuser&n=5&focus_cause=not_a_cause")
        assert res.status_code == 200
        assert len(res.json()["puzzles"]) > 0

    def test_never_serves_more_than_the_session_size(
        self, client, db_session, seed_puzzles
    ):
        for pid in ("p1", "p2", "p3"):
            self._due(db_session, pid, 2)
            self._diagnose(db_session, pid, "loose_piece_awareness")

        body = client.get(
            "/puzzles/due?username=testuser&n=2&focus_cause=loose_piece_awareness"
        ).json()
        assert len(body["puzzles"]) == 2

    def test_does_not_reach_into_another_users_diagnoses(
        self, client, db_session, seed_puzzles
    ):
        from services.api.models import DiagnosisStatus, PuzzleDiagnosis

        self._due(db_session, "p1", 10)
        self._due(db_session, "p2", 1)
        db_session.add(
            PuzzleDiagnosis(
                puzzle_id="p2",
                username="someone_else",
                status=DiagnosisStatus.OK,
                primary_cause="king_safety_blindness",
                source="rules",
                evidence_json=[],
            )
        )
        db_session.commit()

        focused = client.get(
            "/puzzles/due?username=testuser&n=5&focus_cause=king_safety_blindness"
        ).json()
        assert focused["puzzles"][0]["id"] == "p1"


class TestQueueReasons:
    """Why each puzzle is in today's queue.

    The point is that the queue is inspectable rather than a black box. A user
    who sees an unexpected puzzle should be able to find out why it was served.
    """

    def _due(self, db, pid, days_ago, fail_count=0):
        db.add(
            PuzzleStats(
                puzzle_id=pid,
                username="testuser",
                attempts=1 + fail_count,
                pass_count=1,
                fail_count=fail_count,
                last_result="pass",
                interval_days=1,
                ease_factor=2.0,
                next_due_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            )
        )
        db.commit()

    def _diagnose(self, db, pid, cause):
        from services.api.models import DiagnosisStatus, PuzzleDiagnosis

        db.add(
            PuzzleDiagnosis(
                puzzle_id=pid,
                username="testuser",
                status=DiagnosisStatus.OK,
                primary_cause=cause,
                source="rules",
                evidence_json=[],
            )
        )
        db.commit()

    def test_a_never_trained_puzzle_says_so(self, client, db_session, seed_puzzles):
        body = client.get("/puzzles/due?username=testuser&n=5").json()
        reasons = {p["id"]: p["queue_reason"] for p in body["puzzles"]}
        assert reasons["p1"]["reason"] == "new"
        assert "not trained this position yet" in reasons["p1"]["explanation"]

    def test_a_due_puzzle_says_how_overdue_it_is(
        self, client, db_session, seed_puzzles
    ):
        self._due(db_session, "p1", 3)
        body = client.get("/puzzles/due?username=testuser&n=5").json()
        reason = next(p["queue_reason"] for p in body["puzzles"] if p["id"] == "p1")
        assert reason["reason"] == "due"
        assert "3 days ago" in reason["explanation"]

    def test_a_puzzle_due_today_is_not_reported_as_overdue(
        self, client, db_session, seed_puzzles
    ):
        self._due(db_session, "p1", 0)
        body = client.get("/puzzles/due?username=testuser&n=5").json()
        reason = next(p["queue_reason"] for p in body["puzzles"] if p["id"] == "p1")
        assert "ago" not in reason["explanation"]

    def test_a_focus_match_names_the_pattern(self, client, db_session, seed_puzzles):
        self._due(db_session, "p1", 2)
        self._diagnose(db_session, "p1", "loose_piece_awareness")
        body = client.get(
            "/puzzles/due?username=testuser&n=5&focus_cause=loose_piece_awareness"
        ).json()
        reason = next(p["queue_reason"] for p in body["puzzles"] if p["id"] == "p1")
        assert reason["pattern"] == "Loose Piece Syndrome"
        assert "Loose Piece Syndrome" in reason["explanation"]

    def test_a_focus_explains_the_order_not_the_presence(
        self, client, db_session, seed_puzzles
    ):
        # The scheduling reason survives. Saying "matches your focus" about a
        # puzzle that is here because it came due would misrepresent why it was
        # served — the focus only decided where in the queue it sits.
        self._due(db_session, "p1", 4)
        self._diagnose(db_session, "p1", "loose_piece_awareness")
        body = client.get(
            "/puzzles/due?username=testuser&n=5&focus_cause=loose_piece_awareness"
        ).json()
        reason = next(p["queue_reason"] for p in body["puzzles"] if p["id"] == "p1")
        assert reason["reason"] == "due"
        assert "4 days ago" in reason["explanation"]

    def test_puzzles_outside_the_focus_carry_no_pattern(
        self, client, db_session, seed_puzzles
    ):
        self._due(db_session, "p1", 2)
        self._due(db_session, "p2", 1)
        self._diagnose(db_session, "p1", "loose_piece_awareness")
        body = client.get(
            "/puzzles/due?username=testuser&n=5&focus_cause=loose_piece_awareness"
        ).json()
        p2 = next(p["queue_reason"] for p in body["puzzles"] if p["id"] == "p2")
        assert "pattern" not in p2

    def test_repeat_failures_are_surfaced(self, client, db_session, seed_puzzles):
        # A repeat failure is the strongest signal in the corpus, and a user
        # re-seeing a puzzle deserves to know it is a repeat.
        self._due(db_session, "p1", 1, fail_count=3)
        body = client.get("/puzzles/due?username=testuser&n=5").json()
        reason = next(p["queue_reason"] for p in body["puzzles"] if p["id"] == "p1")
        assert reason["previous_failures"] == 3

    def test_a_never_failed_puzzle_omits_the_count(
        self, client, db_session, seed_puzzles
    ):
        self._due(db_session, "p1", 1)
        body = client.get("/puzzles/due?username=testuser&n=5").json()
        reason = next(p["queue_reason"] for p in body["puzzles"] if p["id"] == "p1")
        assert "previous_failures" not in reason

    def test_the_reason_never_carries_the_solution(
        self, client, db_session, seed_puzzles
    ):
        # The queue reason is served before the attempt, so it sits on the same
        # side of the solution gate as everything else in this payload.
        self._due(db_session, "p1", 1)
        body = client.get("/puzzles/due?username=testuser&n=5").json()
        for p in body["puzzles"]:
            blob = str(p["queue_reason"]).lower()
            assert "best" not in blob and "solution" not in blob

    def test_every_served_puzzle_has_one(self, client, db_session, seed_puzzles):
        body = client.get("/puzzles/due?username=testuser&n=5").json()
        assert body["puzzles"]
        assert all("queue_reason" in p for p in body["puzzles"])


class TestFocusNameAgreement:
    """The queue must name the pattern the way the focus card named it.

    Both resolve through cause_breakdown's dominant phase. Resolving the queue
    side with phase=None instead meant a user clicking through under "Back Rank
    Neglect" saw every puzzle attribute itself to "King Safety Blind Spot" —
    the naming drift the static table exists to prevent, inside one click.
    """

    def _seed(self, db, pid, phase, cause="king_safety_blindness"):
        from services.api.models import DiagnosisStatus, PuzzleDiagnosis

        db.add(
            PuzzleDiagnosis(
                puzzle_id=pid,
                username="testuser",
                status=DiagnosisStatus.OK,
                primary_cause=cause,
                phase=phase,
                source="rules",
                evidence_json=[],
            )
        )
        db.add(
            PuzzleStats(
                puzzle_id=pid,
                username="testuser",
                attempts=1,
                pass_count=1,
                interval_days=1,
                ease_factor=2.0,
                next_due_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )
        db.commit()

    def test_uses_the_phase_specific_name_the_focus_card_uses(
        self, client, db_session, seed_puzzles
    ):
        # An endgame-dominant king-safety cause is "Back Rank Neglect", not the
        # generic "King Safety Blind Spot".
        for pid in ("p1", "p2", "p3"):
            self._seed(db_session, pid, "endgame")

        body = client.get(
            "/puzzles/due?username=testuser&n=5&focus_cause=king_safety_blindness"
        ).json()
        names = {
            p["queue_reason"].get("pattern")
            for p in body["puzzles"]
            if "pattern" in p["queue_reason"]
        }
        assert names == {"Back Rank Neglect"}

    def test_falls_back_to_the_general_name_when_no_phase_dominates(
        self, client, db_session, seed_puzzles
    ):
        self._seed(db_session, "p1", "endgame")
        self._seed(db_session, "p2", "middlegame")
        self._seed(db_session, "p3", "opening")

        body = client.get(
            "/puzzles/due?username=testuser&n=5&focus_cause=king_safety_blindness"
        ).json()
        names = {
            p["queue_reason"].get("pattern")
            for p in body["puzzles"]
            if "pattern" in p["queue_reason"]
        }
        assert names == {"King Safety Blind Spot"}

    def _extra_puzzle(self, db, pid, ply):
        db.add(
            PuzzleModel(
                id=pid,
                username="testuser",
                source_game_id="g1",
                ply=ply,
                fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                side_to_move="white",
                played_move_uci="e2e3",
                best_move_uci="e2e4",
                eval_before=0.5,
                eval_after=-0.5,
                swing=1.0,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    def test_agrees_with_the_todays_focus_endpoint(
        self, client, db_session, seed_puzzles
    ):
        # The strongest form: whatever the focus card would call it, the queue
        # calls it too. Compared against the endpoint rather than a literal, so
        # the two cannot drift apart later. Four diagnoses, because below the
        # ranking threshold there is no focus to agree with.
        for pid in ("p1", "p2", "p3"):
            self._seed(db_session, pid, "endgame")
        self._extra_puzzle(db_session, "p4", 4)
        self._seed(db_session, "p4", "endgame")

        focus = client.get("/users/testuser/todays-focus").json()["focus"]
        assert focus is not None

        body = client.get(
            f"/puzzles/due?username=testuser&n=5&focus_cause={focus['cause']}"
        ).json()
        named = [
            p["queue_reason"]["pattern"]
            for p in body["puzzles"]
            if "pattern" in p["queue_reason"]
        ]
        assert named
        assert set(named) == {focus["name"]}


class TestFocusOpeningParameter:
    """`focus_opening` on /puzzles/due — the Openings → Train path.

    Same contract as focus_cause: a bias, never a filter. The explorer sends
    users here from a line they are losing, and a line with nothing due today
    must give an ordinary session rather than an error or an empty one.
    """

    def _diagnose(self, db, pid, name):
        from services.api.models import DiagnosisStatus, PuzzleDiagnosis

        db.add(
            PuzzleDiagnosis(
                puzzle_id=pid,
                username="testuser",
                status=DiagnosisStatus.OK,
                primary_cause="loose_piece_awareness",
                opening_name=name,
                opening_family=name.split(":", 1)[0].strip(),
                source="rules",
                evidence_json=[],
            )
        )
        db.commit()

    def _due(self, db, pid, days_ago):
        db.add(
            PuzzleStats(
                puzzle_id=pid,
                username="testuser",
                attempts=1,
                pass_count=1,
                last_result="pass",
                interval_days=1,
                ease_factor=2.0,
                next_due_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            )
        )
        db.commit()

    def test_serves_the_line_first(self, client, db_session, seed_puzzles):
        self._due(db_session, "p1", 10)
        self._due(db_session, "p2", 1)
        self._diagnose(db_session, "p2", "Sicilian Defense: Najdorf Variation")

        body = client.get(
            "/puzzles/due?username=testuser&n=5"
            "&focus_opening=Sicilian%20Defense:%20Najdorf%20Variation"
        ).json()
        assert body["puzzles"][0]["id"] == "p2"

    def test_family_scope_widens_beyond_the_exact_line(
        self, client, db_session, seed_puzzles
    ):
        self._due(db_session, "p1", 10)
        self._due(db_session, "p2", 1)
        self._diagnose(db_session, "p2", "Sicilian Defense: Dragon Variation")

        # The Najdorf line itself has nothing; the family does.
        line = client.get(
            "/puzzles/due?username=testuser&n=5"
            "&focus_opening=Sicilian%20Defense:%20Najdorf%20Variation"
        ).json()
        family = client.get(
            "/puzzles/due?username=testuser&n=5"
            "&focus_opening=Sicilian%20Defense&focus_opening_scope=family"
        ).json()
        assert line["puzzles"][0]["id"] == "p1"
        assert family["puzzles"][0]["id"] == "p2"

    def test_an_opening_with_nothing_due_gives_an_ordinary_session(
        self, client, db_session, seed_puzzles
    ):
        self._due(db_session, "p1", 3)
        plain = client.get("/puzzles/due?username=testuser&n=5").json()
        focused = client.get(
            "/puzzles/due?username=testuser&n=5"
            "&focus_opening=French%20Defense:%20Advance%20Variation"
        )
        assert focused.status_code == 200
        assert [p["id"] for p in focused.json()["puzzles"]] == [
            p["id"] for p in plain["puzzles"]
        ]

    def test_never_serves_more_than_the_session_size(
        self, client, db_session, seed_puzzles
    ):
        for pid in ("p1", "p2", "p3"):
            self._due(db_session, pid, 2)
            self._diagnose(db_session, pid, "Sicilian Defense: Najdorf Variation")

        body = client.get(
            "/puzzles/due?username=testuser&n=2"
            "&focus_opening=Sicilian%20Defense:%20Najdorf%20Variation"
        ).json()
        assert len(body["puzzles"]) == 2

    def test_is_scoped_to_the_requesting_user(self, client, db_session, seed_puzzles):
        from services.api.models import DiagnosisStatus, PuzzleDiagnosis

        self._due(db_session, "p1", 10)
        self._due(db_session, "p2", 1)
        db_session.add(
            PuzzleDiagnosis(
                puzzle_id="p2",
                username="someone_else",
                status=DiagnosisStatus.OK,
                primary_cause="loose_piece_awareness",
                opening_name="Sicilian Defense: Najdorf Variation",
                opening_family="Sicilian Defense",
                source="rules",
                evidence_json=[],
            )
        )
        db_session.commit()

        body = client.get(
            "/puzzles/due?username=testuser&n=5"
            "&focus_opening=Sicilian%20Defense:%20Najdorf%20Variation"
        ).json()
        assert body["puzzles"][0]["id"] == "p1"
