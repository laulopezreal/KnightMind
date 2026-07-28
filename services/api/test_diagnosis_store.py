"""Tests for diagnosis persistence, the background job, and the API surface."""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from services.api.diagnosis import job as services_job  # noqa: E402
from services.api.diagnosis.causes import RULE_VERSION  # noqa: E402
from services.api.diagnosis.evidence import EXTRACTION_VERSION  # noqa: E402
from services.api.diagnosis.job import run_diagnosis  # noqa: E402
from services.api.main import app, get_db  # noqa: E402
from services.api.models import (  # noqa: E402
    Base,
    DiagnosisStatus,
    Game,
    Job,
    JobStatus,
    JobType,
    PuzzleDiagnosis,
    PuzzleStats,
)
from services.api.models import Puzzle as PuzzleModel  # noqa: E402
from services.api.storage.diagnosis_repository import (  # noqa: E402
    DiagnosisRepository,
    DiagnosisWrite,
)

USER = "diaguser"

# The user played Qd2 and missed Qxd5, which simply wins an undefended queen.
HANGING_QUEEN = "6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1"

PGN = """[Event "Live Chess"]
[White "diaguser"]
[Black "opponent"]
[TimeControl "600+5"]

1. e4 {[%clk 0:09:58]} e5 {[%clk 0:09:57]} 2. Nf3 {[%clk 0:09:50]} Nc6 {[%clk 0:09:45]} *
"""


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session, monkeypatch):
    monkeypatch.setenv("KNIGHTMIND_WORKER_DISABLED", "true")
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


class FakeContext:
    """Stands in for the worker's JobContext."""

    def __init__(self, username=USER, params=None, cancel_after=None):
        self.username = username
        self.job_id = "job-1"
        self.params = params or {}
        self._calls = 0
        self._cancel_after = cancel_after
        self.progress_calls = []

    def heartbeat(self):
        self._calls += 1
        return self._cancel_after is not None and self._calls > self._cancel_after

    def progress(self, done, total):
        self.progress_calls.append((done, total))


def _game(db, game_id="g1", username=USER, pgn=PGN):
    if db.get(Game, (game_id, username)):
        return
    db.add(
        Game(
            game_id=game_id,
            url=f"https://chess.com/game/{game_id}",
            username=username,
            white_username=username,
            black_username="opponent",
            white_result="resigned",
            black_result="win",
            time_control="600+5",
            end_time=int(datetime.now(timezone.utc).timestamp()),
            rated=True,
            pgn_blob=pgn,
        )
    )
    db.commit()


def _puzzle(
    db,
    puzzle_id="p1",
    username=USER,
    fen=HANGING_QUEEN,
    played="d1d2",
    best="d1d5",
    game_id="g1",
    ply=41,
    motif="hanging_queen",
    created_at=None,
):
    _game(db, game_id, username)
    db.add(
        PuzzleModel(
            id=puzzle_id,
            username=username,
            source_game_id=game_id,
            ply=ply,
            created_at=created_at or datetime.now(timezone.utc).replace(tzinfo=None),
            fen=fen,
            side_to_move="white",
            played_move_uci=played,
            best_move_uci=best,
            accept_moves_uci=best,
            solution_pv=best,
            eval_before=1.5,
            eval_after=-7.5,
            swing=9.0,
            confirmed_depth=18,
        )
    )
    db.add(
        PuzzleStats(
            puzzle_id=puzzle_id,
            username=username,
            title="The Hanging Queen",
            primary_motif=motif,
            attempts=3,
            pass_count=1,
            fail_count=2,
        )
    )
    db.commit()
    return puzzle_id


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class TestRepository:
    def test_a_puzzle_with_no_diagnosis_is_pending(self, db_session):
        _puzzle(db_session)
        repo = DiagnosisRepository(db_session)
        assert repo.pending_puzzle_ids(USER) == ["p1"]
        assert repo.pending_count(USER) == 1

    def test_a_current_diagnosis_is_not_pending(self, db_session):
        _puzzle(db_session)
        repo = DiagnosisRepository(db_session)
        repo.upsert(DiagnosisWrite(puzzle_id="p1", username=USER, evidence_hash="abc"))
        db_session.commit()
        assert repo.pending_puzzle_ids(USER) == []

    def test_a_version_bump_makes_stored_rows_pending_again(self, db_session):
        """Staleness is a predicate over versions, which is what makes a rule
        change re-diagnose the corpus with no migration and no purge."""
        _puzzle(db_session)
        repo = DiagnosisRepository(db_session)
        repo.upsert(DiagnosisWrite(puzzle_id="p1", username=USER))
        db_session.commit()
        assert repo.pending_puzzle_ids(USER) == []

        db_session.get(PuzzleDiagnosis, ("p1", USER)).rule_version = RULE_VERSION - 1
        db_session.commit()
        assert repo.pending_puzzle_ids(USER) == ["p1"]

    def test_upsert_reports_whether_the_diagnosis_actually_changed(self, db_session):
        """``changed`` drives both the run counters and whether updated_at
        moves, so it must track the outcome rather than merely the write."""
        _puzzle(db_session)
        repo = DiagnosisRepository(db_session)
        write = DiagnosisWrite(
            puzzle_id="p1",
            username=USER,
            primary_cause="forcing_move_blindness",
            evidence_hash="abc",
        )
        _, changed = repo.upsert(write)
        assert changed  # first insert
        db_session.commit()

        _, changed = repo.upsert(write)
        assert not changed  # identical outcome
        db_session.commit()

        _, changed = repo.upsert(
            DiagnosisWrite(
                puzzle_id="p1",
                username=USER,
                primary_cause="quiet_move_blindness",
                evidence_hash="abc",
            )
        )
        assert changed  # same evidence, different cause

    def test_same_evidence_but_a_new_cause_still_counts_as_changed(self, db_session):
        """A rule-version bump is exactly when identical facts can yield a
        different cause. Keying "unchanged" on the evidence hash alone would
        report no change on precisely the runs that produce one."""
        _puzzle(db_session)
        repo = DiagnosisRepository(db_session)
        repo.upsert(
            DiagnosisWrite(
                puzzle_id="p1", username=USER, primary_cause="a", evidence_hash="same"
            )
        )
        db_session.commit()
        _, changed = repo.upsert(
            DiagnosisWrite(
                puzzle_id="p1", username=USER, primary_cause="b", evidence_hash="same"
            )
        )
        assert changed

    def test_a_version_bump_always_clears_pending_even_when_nothing_changed(
        self, db_session
    ):
        """Otherwise a stale-by-version row whose diagnosis is unchanged would
        stay pending forever and every run would redo it."""
        _puzzle(db_session)
        repo = DiagnosisRepository(db_session)
        write = DiagnosisWrite(
            puzzle_id="p1", username=USER, primary_cause="a", evidence_hash="same"
        )
        repo.upsert(write)
        db_session.commit()
        db_session.get(PuzzleDiagnosis, ("p1", USER)).rule_version = RULE_VERSION - 1
        db_session.commit()
        assert repo.pending_puzzle_ids(USER) == ["p1"]

        _, changed = repo.upsert(write)
        db_session.commit()
        assert not changed
        assert repo.pending_puzzle_ids(USER) == []

    def test_another_users_puzzles_are_never_pending(self, db_session):
        _puzzle(db_session, puzzle_id="p1", username=USER)
        _puzzle(db_session, puzzle_id="p2", username="someone-else", game_id="g2")
        repo = DiagnosisRepository(db_session)
        assert repo.pending_puzzle_ids(USER) == ["p1"]

    def test_upsert_preserves_a_manual_correction(self, db_session):
        """A re-run of the rules must not silently discard a label the user
        fixed by hand — that correction is the only ground truth available."""
        _puzzle(db_session)
        repo = DiagnosisRepository(db_session)
        repo.upsert(
            DiagnosisWrite(
                puzzle_id="p1", username=USER, primary_cause="forcing_move_blindness"
            )
        )
        db_session.commit()
        repo.confirm_cause(USER, "p1", "king_safety_blindness")
        db_session.commit()

        repo.upsert(
            DiagnosisWrite(
                puzzle_id="p1", username=USER, primary_cause="quiet_move_blindness"
            )
        )
        db_session.commit()
        row = repo.get(USER, "p1")
        assert row.primary_cause == "quiet_move_blindness"
        assert row.user_confirmed_cause == "king_safety_blindness"

    def test_cause_counts_prefer_the_users_correction(self, db_session):
        _puzzle(db_session, puzzle_id="p1")
        _puzzle(db_session, puzzle_id="p2", ply=43, game_id="g1")
        repo = DiagnosisRepository(db_session)
        repo.upsert(
            DiagnosisWrite(
                puzzle_id="p1", username=USER, primary_cause="forcing_move_blindness"
            )
        )
        repo.upsert(
            DiagnosisWrite(
                puzzle_id="p2", username=USER, primary_cause="forcing_move_blindness"
            )
        )
        db_session.commit()
        repo.confirm_cause(USER, "p2", "king_safety_blindness")
        db_session.commit()
        assert dict(repo.cause_counts(USER)) == {
            "forcing_move_blindness": 1,
            "king_safety_blindness": 1,
        }

    def test_unavailable_rows_carry_no_cause_to_count(self, db_session):
        _puzzle(db_session)
        repo = DiagnosisRepository(db_session)
        repo.upsert(
            DiagnosisWrite(
                puzzle_id="p1",
                username=USER,
                status=DiagnosisStatus.UNAVAILABLE,
                error="illegal move",
            )
        )
        db_session.commit()
        assert repo.cause_counts(USER) == []


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


class TestJob:
    def test_diagnoses_a_pending_puzzle_end_to_end(self, db_session, monkeypatch):
        _puzzle(db_session)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        result = run_diagnosis(FakeContext())

        assert result["diagnosed"] == 1
        assert result["remaining"] == 0
        row = DiagnosisRepository(db_session).get(USER, "p1")
        # An undefended queen the solution simply takes.
        assert row.primary_cause == "loose_piece_awareness"
        assert row.status == DiagnosisStatus.OK
        assert row.source == "rules"
        assert row.extraction_version == EXTRACTION_VERSION
        assert row.rule_version == RULE_VERSION
        assert row.primary_motif == "hanging_queen"
        assert row.phase == "middlegame"
        assert row.evidence_hash

    def test_stores_the_citable_evidence(self, db_session, monkeypatch):
        _puzzle(db_session)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())
        row = DiagnosisRepository(db_session).get(USER, "p1")
        ids = {item["id"] for item in row.evidence_json}
        assert {"position.phase", "best.move", "eval.swing"} <= ids
        assert all({"id", "label", "value"} == set(i) for i in row.evidence_json)

    def test_a_second_run_changes_nothing(self, db_session, monkeypatch):
        """Identical facts under identical versions must not rewrite the row —
        otherwise updated_at would lie about when the diagnosis last changed."""
        _puzzle(db_session)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())
        before = DiagnosisRepository(db_session).get(USER, "p1").updated_at

        second = run_diagnosis(FakeContext())
        assert second["diagnosed"] == 0
        # Nothing was pending, so nothing was even re-examined.
        assert second["unchanged"] == 0
        assert DiagnosisRepository(db_session).get(USER, "p1").updated_at == before

    def test_an_unanalysable_puzzle_is_recorded_not_retried_forever(
        self, db_session, monkeypatch
    ):
        """Without a row for the failure, every backfill run would re-attempt
        the same broken puzzle for the life of the corpus."""
        _puzzle(db_session, played="a1a8", best="d1d5")  # a1a8 is illegal here
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        result = run_diagnosis(FakeContext())

        assert result["unavailable"] == 1
        assert result["remaining"] == 0
        row = DiagnosisRepository(db_session).get(USER, "p1")
        assert row.status == DiagnosisStatus.UNAVAILABLE
        assert row.insufficient_evidence
        assert "illegal" in row.error

    def test_a_missing_game_degrades_rather_than_failing(self, db_session, monkeypatch):
        """A manual puzzle has no source game; the FEN still carries the
        position, so a diagnosis is still possible without PGN context."""
        _puzzle(db_session)
        db_session.query(Game).delete()
        db_session.commit()
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        result = run_diagnosis(FakeContext())
        assert result["diagnosed"] == 1
        assert DiagnosisRepository(db_session).get(USER, "p1").primary_cause

    def test_respects_the_limit_and_reports_what_is_left(self, db_session, monkeypatch):
        for i in range(3):
            _puzzle(db_session, puzzle_id=f"p{i}", ply=41 + i * 2)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        result = run_diagnosis(FakeContext(params={"limit": 2}))
        assert result["diagnosed"] == 2
        assert result["remaining"] == 1

    def test_cancellation_stops_the_run(self, db_session, monkeypatch):
        for i in range(3):
            _puzzle(db_session, puzzle_id=f"p{i}", ply=41 + i * 2)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        monkeypatch.setattr("services.api.diagnosis.job.HEARTBEAT_INTERVAL", 1)
        result = run_diagnosis(FakeContext(cancel_after=1))
        assert result["canceled"]
        assert result["remaining"] >= 1

    def test_an_empty_corpus_is_not_an_error(self, db_session, monkeypatch):
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        result = run_diagnosis(FakeContext())
        assert result == {
            "username": USER,
            "diagnosed": 0,
            "unchanged": 0,
            "unavailable": 0,
            "enriched": 0,
            "remaining": 0,
            "canceled": False,
        }


class _NoClose:
    """Hands the test session to code that owns its own session lifecycle."""

    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestDiagnosisEndpoint:
    def test_an_undiagnosed_puzzle_reports_pending_not_an_error(
        self, client, db_session
    ):
        _puzzle(db_session)
        body = client.get(f"/puzzles/p1/diagnosis?username={USER}").json()
        assert body["state"] == "pending"
        assert body["primary_cause"] is None

    def test_a_diagnosed_puzzle_returns_cause_label_and_evidence(
        self, client, db_session, monkeypatch
    ):
        _puzzle(db_session)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())

        body = client.get(f"/puzzles/p1/diagnosis?username={USER}").json()
        assert body["state"] == "ready"
        assert body["primary_cause"] == "loose_piece_awareness"
        assert body["primary_cause_label"] == "Loose piece awareness"
        assert body["evidence"]
        assert body["source"] == "rules"

    def test_no_numeric_confidence_is_exposed(self, client, db_session, monkeypatch):
        """Rule strength is an ordering prior, not a calibrated probability.
        Shipping it as a number invites rendering it as a percentage."""
        _puzzle(db_session)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())
        body = client.get(f"/puzzles/p1/diagnosis?username={USER}").json()
        assert "confidence" not in body
        assert "primary_strength" not in body

    def test_an_unclassifiable_puzzle_reports_unclear(
        self, client, db_session, monkeypatch
    ):
        """An honest 'we can't tell' rather than the least-bad guess."""
        _puzzle(
            db_session,
            fen="4k3/pp4pp/8/8/8/8/PP4PP/4K3 w - - 0 20",
            played="e1d2",
            best="e1f2",
            motif="blunder",
        )
        db_session.query(PuzzleModel).filter_by(id="p1").update(
            {"eval_before": 0.3, "eval_after": -0.4, "swing": 0.7}
        )
        db_session.commit()
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())

        body = client.get(f"/puzzles/p1/diagnosis?username={USER}").json()
        assert body["state"] == "unclear"

    def test_an_unavailable_puzzle_does_not_leak_the_internal_error(
        self, client, db_session, monkeypatch
    ):
        _puzzle(db_session, played="a1a8")
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())

        body = client.get(f"/puzzles/p1/diagnosis?username={USER}").json()
        assert body["state"] == "unavailable"
        assert "illegal" not in str(body).lower()

    def test_unknown_puzzle_is_404(self, client, db_session):
        _puzzle(db_session)
        assert client.get(f"/puzzles/nope/diagnosis?username={USER}").status_code == 404

    def test_another_users_puzzle_is_404(self, client, db_session):
        _puzzle(db_session, puzzle_id="p1", username="other", game_id="g9")
        assert client.get(f"/puzzles/p1/diagnosis?username={USER}").status_code == 404

    def test_pending_count_endpoint(self, client, db_session):
        _puzzle(db_session)
        body = client.get(f"/users/{USER}/diagnosis/pending").json()
        assert body == {"username": USER, "pending": 1}


class TestDiagnoseEnqueue:
    def test_queues_a_diagnosis_typed_job(self, client, db_session):
        response = client.post(f"/users/{USER}/diagnose")
        assert response.status_code == 200
        job = db_session.get(Job, response.json()["job_id"])
        assert job.type == JobType.DIAGNOSIS
        assert job.status == JobStatus.QUEUED

    def test_a_duplicate_request_returns_the_running_job(self, client, db_session):
        first = client.post(f"/users/{USER}/diagnose").json()
        second = client.post(f"/users/{USER}/diagnose").json()
        assert second["job_id"] == first["job_id"]
        assert second["message"] == "Diagnosis already in progress"

    def test_can_be_queued_alongside_a_generation_job(self, client, db_session):
        """The point of scoping the active-job index by type: diagnosing a
        corpus must not be blocked by an in-flight generation."""
        db_session.add(
            Job(
                username=USER,
                type=JobType.PUZZLE_GENERATION,
                status=JobStatus.RUNNING,
            )
        )
        db_session.commit()
        response = client.post(f"/users/{USER}/diagnose")
        assert response.status_code == 200
        assert db_session.get(Job, response.json()["job_id"]).type == JobType.DIAGNOSIS


class TestConfirm:
    def _diagnose(self, db_session, monkeypatch):
        _puzzle(db_session)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())

    def test_records_the_correction_beside_the_computed_cause(
        self, client, db_session, monkeypatch
    ):
        self._diagnose(db_session, monkeypatch)
        body = client.post(
            f"/puzzles/p1/diagnosis/confirm?username={USER}",
            json={"cause": "king_safety_blindness"},
        ).json()
        assert body["user_confirmed_cause"] == "king_safety_blindness"
        assert body["primary_cause"] == "king_safety_blindness"
        # The computed cause survives underneath for accuracy measurement.
        assert (
            DiagnosisRepository(db_session).get(USER, "p1").primary_cause
            == "loose_piece_awareness"
        )

    def test_an_unknown_cause_is_rejected(self, client, db_session, monkeypatch):
        self._diagnose(db_session, monkeypatch)
        response = client.post(
            f"/puzzles/p1/diagnosis/confirm?username={USER}",
            json={"cause": "vibes"},
        )
        assert response.status_code == 422

    def test_confirming_an_undiagnosed_puzzle_is_404(self, client, db_session):
        _puzzle(db_session)
        response = client.post(
            f"/puzzles/p1/diagnosis/confirm?username={USER}",
            json={"cause": "king_safety_blindness"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Guards for the four properties this feature is documented as having.
# Each was asserted in prose before it was enforced anywhere.
# ---------------------------------------------------------------------------


class TestNoScoreEscapesToTheClient:
    """Rule strength is an ordering prior, not a calibrated probability.

    The risk is not that today's payload leaks it — it is that someone later
    adds it in good faith and a UI renders it as "82% confident". These guards
    are on the schema, so that addition fails here rather than in a design
    review nobody runs.
    """

    FORBIDDEN = {"confidence", "primary_strength", "score", "strength", "certainty"}

    def test_no_scoring_field_is_declared(self):
        from services.api.main import DiagnosisResponse

        assert not self.FORBIDDEN & set(DiagnosisResponse.model_fields)

    def test_no_numeric_field_is_declared_at_all(self):
        """A float on this model has no honest meaning today, so the absence of
        one is the invariant worth pinning rather than a denylist of names."""
        from services.api.main import DiagnosisResponse

        for name, field in DiagnosisResponse.model_fields.items():
            annotation = str(field.annotation)
            assert "float" not in annotation, f"{name} exposes a numeric score"

    def test_a_stored_strength_still_never_reaches_the_client(
        self, client, db_session, monkeypatch
    ):
        _puzzle(db_session)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())
        assert DiagnosisRepository(db_session).get(USER, "p1").primary_strength

        body = client.get(f"/puzzles/p1/diagnosis?username={USER}").json()
        assert not self.FORBIDDEN & set(body)


class TestUnanalysablePuzzlesSettle:
    def test_a_failure_is_not_re_examined_on_the_next_run(
        self, db_session, monkeypatch
    ):
        """The whole point of storing the negative result: without it every
        backfill run would re-attempt the same broken puzzle forever."""
        _puzzle(db_session, played="a1a8")  # illegal in this position
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        first = run_diagnosis(FakeContext())
        assert first["unavailable"] == 1

        second = run_diagnosis(FakeContext())
        assert second["unavailable"] == 0
        assert second["diagnosed"] == 0
        assert second["remaining"] == 0

    def test_the_stored_error_is_never_echoed_to_the_client(
        self, client, db_session, monkeypatch
    ):
        """Checked against the actual stored text, not a guessed substring."""
        _puzzle(db_session, played="a1a8")
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())

        stored = DiagnosisRepository(db_session).get(USER, "p1").error
        assert stored  # kept for debugging
        body = client.get(f"/puzzles/p1/diagnosis?username={USER}").json()
        assert body["state"] == "unavailable"
        assert stored not in str(body)


class TestResumability:
    def _many(self, db_session, n=4):
        base = datetime(2026, 1, 1)
        for i in range(n):
            _puzzle(
                db_session,
                puzzle_id=f"p{i}",
                ply=41 + i * 2,
                created_at=base + timedelta(days=i),
            )

    def test_pending_work_is_ordered_most_recent_first(self, db_session):
        """A run that only gets partway through should have done the mistakes
        that say most about how the user plays now."""
        self._many(db_session, 3)
        assert DiagnosisRepository(db_session).pending_puzzle_ids(USER) == [
            "p2",
            "p1",
            "p0",
        ]

    def test_a_re_failed_puzzle_outranks_a_newer_untouched_one(self, db_session):
        """Recency alone would bury a puzzle failed four times behind a fresh
        one never failed. Surviving a second exposure is the strongest signal
        in the corpus, so it leads."""
        base = datetime(2026, 1, 1)
        _puzzle(db_session, puzzle_id="old-but-failed", ply=41, created_at=base)
        _puzzle(
            db_session,
            puzzle_id="new-but-clean",
            ply=43,
            created_at=base + timedelta(days=30),
        )
        db_session.query(PuzzleStats).filter_by(puzzle_id="old-but-failed").update(
            {"fail_count": 4}
        )
        db_session.query(PuzzleStats).filter_by(puzzle_id="new-but-clean").update(
            {"fail_count": 0}
        )
        db_session.commit()

        assert DiagnosisRepository(db_session).pending_puzzle_ids(USER) == [
            "old-but-failed",
            "new-but-clean",
        ]

    def test_recency_still_breaks_ties_between_equal_failures(self, db_session):
        self._many(db_session, 3)
        db_session.query(PuzzleStats).update({"fail_count": 1})
        db_session.commit()
        assert DiagnosisRepository(db_session).pending_puzzle_ids(USER) == [
            "p2",
            "p1",
            "p0",
        ]

    def test_a_puzzle_never_attempted_sorts_as_zero_failures_not_last(self, db_session):
        """A missing stats row must COALESCE to zero, not sort as NULL — on
        Postgres a NULL would otherwise land at the wrong end entirely."""
        base = datetime(2026, 1, 1)
        _puzzle(db_session, puzzle_id="never-tried", ply=41, created_at=base)
        db_session.query(PuzzleStats).filter_by(puzzle_id="never-tried").delete()
        _puzzle(
            db_session,
            puzzle_id="failed-once",
            ply=43,
            created_at=base - timedelta(days=5),
        )
        db_session.query(PuzzleStats).filter_by(puzzle_id="failed-once").update(
            {"fail_count": 1}
        )
        db_session.commit()

        ids = DiagnosisRepository(db_session).pending_puzzle_ids(USER)
        assert ids == ["failed-once", "never-tried"]

    def test_a_limited_run_takes_the_most_recent_first(self, db_session):
        self._many(db_session, 3)
        assert DiagnosisRepository(db_session).pending_puzzle_ids(USER, 1) == ["p2"]

    def test_a_canceled_run_commits_its_work_and_the_next_run_finishes_it(
        self, db_session, monkeypatch
    ):
        """The claim that the work query IS the resume cursor. A canceled run
        must keep what it finished — otherwise cancelling would be a rollback
        and a long backfill could never make progress under repeated cancels."""
        self._many(db_session, 4)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        monkeypatch.setattr("services.api.diagnosis.job.HEARTBEAT_INTERVAL", 1)

        first = run_diagnosis(FakeContext(cancel_after=2))
        assert first["canceled"]
        done_after_cancel = db_session.query(PuzzleDiagnosis).count()
        assert 0 < done_after_cancel < 4  # kept partial work
        assert first["remaining"] == 4 - done_after_cancel

        second = run_diagnosis(FakeContext())
        assert not second["canceled"]
        assert second["remaining"] == 0
        # Every puzzle diagnosed exactly once — none skipped, none redone.
        assert db_session.query(PuzzleDiagnosis).count() == 4

    def test_no_progress_state_is_stored_anywhere(self, db_session, monkeypatch):
        """Resumption is derived from the diagnosis rows themselves. Deleting
        them must restore the work exactly, with no cursor left behind to
        disagree."""
        self._many(db_session, 3)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())
        repo = DiagnosisRepository(db_session)
        assert repo.pending_count(USER) == 0

        db_session.query(PuzzleDiagnosis).delete()
        db_session.commit()
        assert repo.pending_count(USER) == 3


class TestReadPathNeverComputes:
    def test_a_get_does_not_extract_or_classify(self, client, db_session, monkeypatch):
        """Diagnosis is background work so a page load stays one indexed row
        read — and stays that way when computing means a model call. This is a
        regression guard against a well-meaning compute-on-read shortcut."""
        _puzzle(db_session)

        def explode(*args, **kwargs):
            raise AssertionError("the read path must not compute a diagnosis")

        monkeypatch.setattr("services.api.diagnosis.evidence.extract_evidence", explode)
        monkeypatch.setattr("services.api.diagnosis.causes.classify_causes", explode)

        body = client.get(f"/puzzles/p1/diagnosis?username={USER}").json()
        assert body["state"] == "pending"

    def test_a_get_writes_nothing_and_queues_nothing(self, client, db_session):
        """ "Never computes" also means never quietly enqueuing work: a page
        view must not be able to schedule a job the user did not ask for."""
        _puzzle(db_session)
        client.get(f"/puzzles/p1/diagnosis?username={USER}")
        client.get(f"/users/{USER}/diagnosis/pending")

        assert db_session.query(PuzzleDiagnosis).count() == 0
        assert db_session.query(Job).count() == 0

    def test_a_get_issues_only_reads_and_a_bounded_number_of_them(
        self, client, db_session, monkeypatch
    ):
        """A structural guard, because patching a module only catches a compute
        path that imports at call time. Counting statements catches any shape:
        computing a diagnosis and caching it necessarily writes, and doing real
        work necessarily costs queries.
        """
        from sqlalchemy import event

        _puzzle(db_session)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())

        statements = []
        engine = db_session.get_bind()

        def record(conn, cursor, statement, *args):
            statements.append(statement.strip().split()[0].upper())

        event.listen(engine, "before_cursor_execute", record)
        try:
            body = client.get(f"/puzzles/p1/diagnosis?username={USER}").json()
        finally:
            event.remove(engine, "before_cursor_execute", record)

        assert body["state"] == "ready"
        assert not {"INSERT", "UPDATE", "DELETE"} & set(statements)
        # Existence check + the diagnosis row. A regression that starts
        # computing or fanning out would blow this budget immediately.
        assert len(statements) <= 3, statements


class TestEvidenceIsNotASolutionSideChannel:
    """The evidence names the solution — "Best move: Qxd5", the squares it
    attacks, the length of the winning line. Without the same gate as
    /puzzles/{id}, this endpoint would be a way to read the answer while the
    anti-cheat flag was on.
    """

    def _diagnose(self, db_session, monkeypatch):
        _puzzle(db_session)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())

    def test_the_evidence_does_name_the_solution(self, db_session, monkeypatch):
        """Establishes the premise the gate exists for; if this ever stops
        being true the gate can be revisited, but not before."""
        self._diagnose(db_session, monkeypatch)
        row = DiagnosisRepository(db_session).get(USER, "p1")
        values = " ".join(item["value"] for item in row.evidence_json)
        assert "Qxd5" in values

    def test_withheld_under_the_strict_flag(self, client, db_session, monkeypatch):
        self._diagnose(db_session, monkeypatch)
        monkeypatch.setenv("KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS", "true")

        body = client.get(f"/puzzles/p1/diagnosis?username={USER}").json()
        assert body["evidence"] == []
        assert body["evidence_withheld"] is True
        assert "Qxd5" not in str(body)
        # The coaching label is not the move, so it still comes through.
        assert body["primary_cause"] == "loose_piece_awareness"

    def test_revealed_on_request_under_the_strict_flag(
        self, client, db_session, monkeypatch
    ):
        self._diagnose(db_session, monkeypatch)
        monkeypatch.setenv("KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS", "true")

        body = client.get(f"/puzzles/p1/diagnosis?username={USER}&reveal=true").json()
        assert body["evidence"]
        assert body["evidence_withheld"] is False

    def test_confirm_honours_the_same_gate_as_the_read(
        self, client, db_session, monkeypatch
    ):
        """Regression: the gate was added to GET and missed on POST /confirm,
        which returns the same body. One un-gated call site is a whole hole —
        hence the fail-closed default on the response builder."""
        self._diagnose(db_session, monkeypatch)
        monkeypatch.setenv("KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS", "true")

        body = client.post(
            f"/puzzles/p1/diagnosis/confirm?username={USER}",
            json={"cause": "king_safety_blindness"},
        ).json()
        assert body["evidence"] == []
        assert body["evidence_withheld"] is True
        assert "Qxd5" not in str(body)

    def test_included_by_default_when_the_flag_is_off(
        self, client, db_session, monkeypatch
    ):
        """Matches /puzzles/{id}: flag OFF is the deploy-safe default, so this
        change cannot break the current frontend."""
        self._diagnose(db_session, monkeypatch)
        monkeypatch.delenv("KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS", raising=False)

        body = client.get(f"/puzzles/p1/diagnosis?username={USER}").json()
        assert body["evidence"]
        assert body["evidence_withheld"] is False


class TestCrashDoesNotDiscardTheRun:
    def test_work_is_committed_in_chunks_not_only_at_the_end(
        self, db_session, monkeypatch
    ):
        """Without chunked commits the whole run is one transaction, so a crash
        near the end discards every diagnosis it produced and "resumable" means
        "starts over". Simulates a crash by raising partway through."""
        for i in range(6):
            _puzzle(db_session, puzzle_id=f"p{i}", ply=41 + i * 2)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        monkeypatch.setattr("services.api.diagnosis.job.COMMIT_INTERVAL", 2)

        real = services_job._diagnose_one
        calls = {"n": 0}

        def crash_on_the_fifth(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 5:
                raise RuntimeError("simulated worker crash")
            return real(*args, **kwargs)

        monkeypatch.setattr(
            "services.api.diagnosis.job._diagnose_one", crash_on_the_fifth
        )
        with pytest.raises(RuntimeError):
            run_diagnosis(FakeContext())

        db_session.rollback()  # what a crashed worker's session would do
        # The first two committed chunks survived; the run is not back to zero.
        assert db_session.query(PuzzleDiagnosis).count() == 4


class TestUserColourComesFromThePosition:
    """A puzzle is by construction a position where it was the user's turn, so
    side_to_move is the authoritative answer. Deriving it from the game row
    instead was wrong for manual puzzles, which all record
    white_username = <the user> whatever the position actually is.
    """

    BLACK_TO_MOVE = "3q2k1/pp3ppp/8/8/8/8/PP3PPP/3Q2K1 b - - 0 1"

    def _manual_puzzle(self, db):
        from services.api.storage.game_repository import MANUAL_GAME_ID

        db.add(
            Game(
                game_id=MANUAL_GAME_ID,
                url=f"manual://{USER}",
                username=USER,
                white_username=USER,  # hardcoded by the manual-puzzle endpoint
                black_username="manual",
                white_result="manual",
                black_result="manual",
                time_control="manual",
                end_time=0,
                rated=False,
            )
        )
        db.add(
            PuzzleModel(
                id="p1",
                username=USER,
                source_game_id=MANUAL_GAME_ID,
                ply=41,
                fen=self.BLACK_TO_MOVE,
                side_to_move="black",
                played_move_uci="d8d2",
                best_move_uci="d8d1",
                accept_moves_uci="d8d1",
                eval_before=1.5,
                eval_after=-7.5,
                swing=9.0,
            )
        )
        db.commit()

    def test_a_black_to_move_manual_puzzle_is_not_labelled_white(
        self, db_session, monkeypatch
    ):
        self._manual_puzzle(db_session)
        monkeypatch.setattr(
            "services.api.diagnosis.job.SessionLocal", lambda: _NoClose(db_session)
        )
        run_diagnosis(FakeContext())

        row = DiagnosisRepository(db_session).get(USER, "p1")
        assert row.status == DiagnosisStatus.OK
        # The evidence must not contradict its own FEN.
        colours = {
            item["value"]
            for item in row.evidence_json
            if item["id"] == "position.phase"
        }
        assert colours  # sanity: the packet was built
        facts = services_job._game_facts(
            db_session.get(Game, ("__manual__", USER)),
            db_session.get(PuzzleModel, "p1"),
        )
        assert facts.user_is_white is False

    def test_a_manual_game_contributes_no_clock_or_result(
        self, db_session, monkeypatch
    ):
        """ "manual" is not a time control and not a game result; treating it as
        either would manufacture evidence out of a placeholder row."""
        self._manual_puzzle(db_session)
        from services.api.storage.game_repository import MANUAL_GAME_ID

        facts = services_job._game_facts(
            db_session.get(Game, (MANUAL_GAME_ID, USER)),
            db_session.get(PuzzleModel, "p1"),
        )
        assert facts.user_result is None
        assert facts.time_control.is_known is False
        assert facts.rated is False
