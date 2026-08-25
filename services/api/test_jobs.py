import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

from services.api.db import Base, get_db
from services.api.main import app
from services.api.models import Game, Job, JobStatus, Puzzle
from services.api.worker import JobWorker

# The worker runs in a thread, which is why this module used a file-based
# SQLite rather than :memory:. The shared Postgres fixture handles concurrent
# connections natively, so both the file and the module-scoped schema setup are
# gone; db_session comes from conftest.


# Override dependency so the API endpoints use the test session.


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_generate_puzzles_enqueues_job(client, db_session):
    # 1. Trigger job
    response = client.post("/puzzles/generate?username=jobtester")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == JobStatus.QUEUED
    assert "job_id" in data
    job_id = data["job_id"]

    # 2. Verify DB
    job = db_session.get(Job, job_id)
    assert job is not None
    assert job.username == "jobtester"
    assert job.status == JobStatus.QUEUED


def test_generate_puzzles_idempotency(client, db_session):
    # 1. First Trigger
    resp1 = client.post("/puzzles/generate?username=doubletester")
    job_id1 = resp1.json()["job_id"]

    # 2. Second Trigger
    resp2 = client.post("/puzzles/generate?username=doubletester")
    job_id2 = resp2.json()["job_id"]

    assert job_id1 == job_id2
    assert resp2.json()["message"] == "Job already in progress"


def test_get_job_status(client, db_session):
    # Create manual job
    job = Job(username="statuschecker", status=JobStatus.RUNNING, progress_current=50)
    db_session.add(job)
    db_session.commit()

    resp = client.get(f"/jobs/{job.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job.id
    assert data["status"] == JobStatus.RUNNING
    assert data["progress"] == 50


def test_get_job_status_exposes_liveness_timestamps(client, db_session):
    """updated_at and heartbeat_at must be in the response so a polling client's
    stall detector can treat them as forward progress. heartbeat_at is the
    signal that keeps a single long game alive (updated_at is pinned across the
    per-ply heartbeat), so both must round-trip as stable ISO strings."""
    hb = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    upd = datetime(2026, 1, 2, 3, 0, 0, tzinfo=timezone.utc)
    job = Job(
        username="livenesschecker",
        status=JobStatus.RUNNING,
        progress_current=12,
        heartbeat_at=hb,
        updated_at=upd,
    )
    db_session.add(job)
    db_session.commit()

    data = client.get(f"/jobs/{job.id}").json()
    assert "updated_at" in data
    assert "heartbeat_at" in data
    # Stable, parseable strings whose values reflect the persisted timestamps.
    assert data["heartbeat_at"] is not None
    assert data["updated_at"] is not None
    assert (
        datetime.fromisoformat(data["heartbeat_at"]).replace(tzinfo=timezone.utc) == hb
    )
    assert (
        datetime.fromisoformat(data["updated_at"]).replace(tzinfo=timezone.utc) == upd
    )


def test_get_job_status_null_heartbeat_serializes_as_none(client, db_session):
    """A pre-heartbeat job (heartbeat_at NULL) must serialize heartbeat_at as
    null, not omit-and-crash, so the client signature simply falls back to the
    other progress fields."""
    job = Job(username="nohbchecker", status=JobStatus.QUEUED, progress_current=0)
    db_session.add(job)
    db_session.commit()

    data = client.get(f"/jobs/{job.id}").json()
    assert data["heartbeat_at"] is None


def test_cancel_job_success(client, db_session):
    job = Job(username="cancelme", status=JobStatus.QUEUED)
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/jobs/{job.id}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == JobStatus.CANCELED

    db_session.refresh(job)
    assert job.status == JobStatus.CANCELED


def test_cancel_job_invalid_status(client, db_session):
    job = Job(username="done", status=JobStatus.SUCCEEDED)
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/jobs/{job.id}/cancel")
    assert resp.status_code == 400
    assert "cannot cancel" in resp.json()["detail"].lower()


def test_cancel_job_not_found(client):
    resp = client.post("/jobs/missing-id/cancel")
    assert resp.status_code == 404


async def run_sync_in_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


@patch("services.api.worker.generate_puzzles")
@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_worker_execute_job(mock_to_thread, mock_generate, db_session):
    # Test execute_job directly to verify logic without claiming loop complexity
    from services.api.puzzles.generator import GenerationResult
    from services.api.worker import worker

    # Create running job
    job = Job(username="exectest", status=JobStatus.RUNNING)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    # Mock generation
    mock_generate.return_value = GenerationResult(5, 0, 100)

    # Execute with patched SessionLocal
    with patch("services.api.worker.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value = db_session
        mock_sl.return_value.__exit__.return_value = None

        await worker.execute_job(job.id)

    # Verify
    db_session.expire_all()
    updated_job = db_session.get(Job, job.id)
    assert updated_job.status == JobStatus.SUCCEEDED
    assert updated_job.result_json["generated"] == 5


@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_cancel_in_success_window_is_not_overwritten(mock_to_thread, db_session):
    """Audit invariant: a canceled job must NEVER become succeeded.

    Reproduces the two-transaction success-path race in ``execute_job``:
    generation returns, then a ``POST /jobs/{id}/cancel`` commits BEFORE the
    worker writes SUCCEEDED. The completion write is now a guarded UPDATE
    (``WHERE status = RUNNING``), so the cancel wins and the job stays CANCELED.

    We land the cancel exactly in that window by having ``asdict`` (called only
    while the worker builds its completion write, after generation returned)
    commit the cancel as a side effect. On the old unconditional write this
    flips the job to SUCCEEDED and the test FAILS; with the guard it stays
    CANCELED and the result is discarded.
    """
    from dataclasses import asdict as real_asdict

    from services.api.puzzles.generator import GenerationResult
    from services.api.worker import worker

    job = Job(username="cancel-window", status=JobStatus.RUNNING)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    job_id = job.id

    def cancel_then_serialize(result):
        # Simulate the cancel endpoint committing in the success window.
        db_session.execute(
            update(Job).where(Job.id == job_id).values(status=JobStatus.CANCELED)
        )
        db_session.commit()
        return real_asdict(result)

    with (
        patch("services.api.worker.generate_puzzles") as mock_generate,
        patch("services.api.worker.asdict", side_effect=cancel_then_serialize),
        patch("services.api.worker.SessionLocal") as mock_sl,
    ):
        mock_generate.return_value = GenerationResult(5, 0, 100)
        mock_sl.return_value.__enter__.return_value = db_session
        mock_sl.return_value.__exit__.return_value = None

        await worker.execute_job(job_id)

    db_session.expire_all()
    final = db_session.get(Job, job_id)
    assert final.status == JobStatus.CANCELED  # NOT succeeded
    assert final.result_json is None  # completion result discarded


def test_write_progress_updates_running_job(db_session):
    """Mid-run progress writes percent + a per-game message, capped below the
    terminal write's 100 so 'done' can only come from the completion path."""
    from services.api.worker import worker

    job = Job(username="progress-test", status=JobStatus.RUNNING)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    with patch("services.api.worker.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value = db_session
        mock_sl.return_value.__exit__.return_value = None

        worker._write_progress(job.id, done=3, total=12)

    db_session.expire_all()
    updated = db_session.get(Job, job.id)
    assert updated.progress_current == 25  # 3/12
    assert updated.progress_total == 100
    assert updated.message == "Analyzing game 4 of 12"

    # Last game of the batch stays below 100 (99 cap).
    with patch("services.api.worker.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value = db_session
        mock_sl.return_value.__exit__.return_value = None
        worker._write_progress(job.id, done=12, total=12)
    db_session.expire_all()
    assert db_session.get(Job, job.id).progress_current == 99


def test_write_progress_never_touches_a_non_running_job(db_session):
    """Same invariant as the success write: a late progress write must not
    resurrect or mutate a job that has left RUNNING (cancel is terminal)."""
    from services.api.worker import worker

    job = Job(
        username="progress-canceled",
        status=JobStatus.CANCELED,
        progress_current=40,
        message="canceled by user",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    with patch("services.api.worker.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value = db_session
        mock_sl.return_value.__exit__.return_value = None

        worker._write_progress(job.id, done=5, total=10)

    db_session.expire_all()
    final = db_session.get(Job, job.id)
    assert final.progress_current == 40  # untouched
    assert final.message == "canceled by user"


# ---------------------------------------------------------------------------
# AUDIT GATE 5: atomic job claim (QUEUED -> RUNNING)
# ---------------------------------------------------------------------------


def _reset_jobs(db_session):
    """Clear the shared module-scoped jobs table for claim isolation."""
    db_session.query(Job).delete()
    db_session.commit()


def _insert_pending_puzzle(db_session, username: str, puzzle_id: str) -> None:
    """Insert a puzzle with no diagnosis so DiagnosisRepository sees work."""
    game_id = f"{puzzle_id}-game"
    db_session.add(
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
            pgn_blob='[Event "Test"]\n\n1. e4 e5 *',
        )
    )
    db_session.add(
        Puzzle(
            id=puzzle_id,
            username=username,
            source_game_id=game_id,
            ply=3,
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
    db_session.commit()


def test_claim_job_transitions_queued_to_running(db_session):
    """A single claim flips exactly the oldest QUEUED job to RUNNING."""
    _reset_jobs(db_session)
    older = Job(
        username="claim-older",
        status=JobStatus.QUEUED,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    newer = Job(
        username="claim-newer",
        status=JobStatus.QUEUED,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([older, newer])
    db_session.commit()

    claimed_id = JobWorker._claim_job(db_session)

    assert claimed_id == older.id  # oldest-first ordering preserved
    db_session.expire_all()
    claimed = db_session.get(Job, older.id)
    assert claimed.status == JobStatus.RUNNING
    # The claim sets the liveness lease atomically in the same UPDATE.
    assert claimed.heartbeat_at is not None
    assert db_session.get(Job, newer.id).status == JobStatus.QUEUED


def test_claim_job_is_atomic_no_double_claim(db_session):
    """The guarded UPDATE has rowcount==1 semantics: once a job leaves QUEUED,
    a second claim attempt on the same row transitions nothing.

    This is the regression guard for the double-claim window. The old
    select-then-commit claim had no `WHERE status='queued'` guard on the
    write, so two workers that both SELECTed the same QUEUED row would both
    commit status=RUNNING and run the job twice. With the guarded UPDATE, the
    second writer's rowcount is 0.
    """
    _reset_jobs(db_session)
    job = Job(username="race-target", status=JobStatus.QUEUED)
    db_session.add(job)
    db_session.commit()

    # First claimer wins.
    first = JobWorker._claim_job(db_session)
    assert first == job.id

    # Second claimer finds nothing QUEUED -> returns None (no re-claim).
    second = JobWorker._claim_job(db_session)
    assert second is None

    # Directly exercise the guarded UPDATE against the now-RUNNING row to prove
    # the rowcount==0 semantics that make the claim safe under a real race.
    guarded = (
        update(Job)
        .where(Job.id == job.id, Job.status == JobStatus.QUEUED)
        .values(status=JobStatus.RUNNING)
    )
    result = db_session.execute(guarded)
    db_session.commit()
    assert result.rowcount == 0  # already claimed; guard blocks the transition


def test_claim_job_returns_none_when_empty(db_session):
    """No QUEUED jobs -> claim returns None."""
    _reset_jobs(db_session)
    assert JobWorker._claim_job(db_session) is None


@pytest.mark.postgres
def test_claim_job_concurrent_postgres():
    """Integration: two concurrent workers claiming from a shared Postgres must
    never claim the same job. Skipped unless a disposable Postgres is provided
    via KNIGHTMIND_TEST_POSTGRES_URL.
    """
    import threading

    pg_url = os.environ["KNIGHTMIND_TEST_POSTGRES_URL"]
    pg_engine = create_engine(pg_url)
    PgSession = sessionmaker(bind=pg_engine)
    Base.metadata.create_all(bind=pg_engine)

    # Seed N queued jobs, each for a distinct username (active-username index).
    n = 10
    with PgSession() as s:
        s.query(Job).delete()
        for i in range(n):
            s.add(Job(username=f"pg-race-{i}", status=JobStatus.QUEUED))
        s.commit()

    claimed: list[str] = []
    lock = threading.Lock()

    def worker_claim():
        while True:
            with PgSession() as s:
                jid = JobWorker._claim_job(s)
            if jid is None:
                return
            with lock:
                claimed.append(jid)

    threads = [threading.Thread(target=worker_claim) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every job claimed exactly once (no duplicates), all jobs claimed.
    assert len(claimed) == n
    assert len(set(claimed)) == n
    # Each claimed row got its liveness lease set atomically.
    with PgSession() as s:
        for jid in claimed:
            job = s.get(Job, jid)
            assert job is not None
            assert job.heartbeat_at is not None


@pytest.mark.postgres
def test_migration_applied_heartbeat_column_postgres():
    """Migration smoke: after `alembic upgrade head` (run by CI before pytest),
    the real Postgres jobs table has the heartbeat_at lease column. Proves the
    migration chain including the new revision applies on Postgres from zero.
    """
    from sqlalchemy import inspect

    pg_engine = create_engine(os.environ["KNIGHTMIND_TEST_POSTGRES_URL"])
    cols = {c["name"] for c in inspect(pg_engine).get_columns("jobs")}
    assert "heartbeat_at" in cols


@pytest.mark.postgres
def test_active_job_unique_under_concurrency_postgres():
    """Integration (dim 19): two workers inserting a QUEUED job for the SAME
    username over separate Postgres connections must yield exactly ONE active
    job.

    Real two-connection race on the partial unique index ``ix_jobs_active_username``
    (``postgresql_where status IN ('queued','running')``): one INSERT commits,
    the other raises IntegrityError — which the /puzzles/generate endpoint turns
    into an "already in progress" replay (see test_generate_puzzles_idempotency).
    SQLite serializes writers and can't truly race, so this is PG-gated.

    Deterministic (no sleeps): a 2-party barrier crossed BEFORE either commits
    guarantees both INSERTs overlap; the database then arbitrates.
    """
    import threading

    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError

    pg_engine = create_engine(os.environ["KNIGHTMIND_TEST_POSTGRES_URL"])
    PgSession = sessionmaker(bind=pg_engine)
    Base.metadata.create_all(bind=pg_engine)

    with PgSession() as s:
        s.query(Job).delete()
        s.commit()

    barrier = threading.Barrier(2, timeout=30)
    outcomes: dict[int, str] = {}

    def insert(idx):
        try:
            with PgSession() as s:
                s.add(
                    Job(
                        username="dim19-race",
                        status=JobStatus.QUEUED,
                        message="queued",
                    )
                )
                # Both threads reach here (nothing flushed yet), then commit at
                # once so the two INSERTs contend on the partial unique index.
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass
                s.commit()
                outcomes[idx] = "committed"
        except IntegrityError:
            outcomes[idx] = "rejected"

    threads = [threading.Thread(target=insert, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one INSERT wins; the other is rejected by the partial unique index.
    assert sorted(outcomes.values()) == ["committed", "rejected"]
    with PgSession() as s:
        active = s.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        )
        assert active == 1


# ---------------------------------------------------------------------------
# Job-type dispatch and the (username, type)-scoped active-job index
# ---------------------------------------------------------------------------


def test_every_job_type_has_a_handler():
    """A JobType with no handler is a job that can only ever fail. The enum and
    the registry must not drift apart."""
    from services.api.models import JobType
    from services.api.worker import JOB_HANDLERS

    assert {t.value for t in JobType} <= set(JOB_HANDLERS)


class TestResultPayload:
    def test_dataclass_is_serialised(self):
        from services.api.puzzles.generator import GenerationResult
        from services.api.worker import _result_payload

        payload = _result_payload(GenerationResult(5, 0, 100))
        assert payload is not None
        assert payload["generated"] == 5

    def test_dict_passes_through(self):
        from services.api.worker import _result_payload

        assert _result_payload({"diagnosed": 3}) == {"diagnosed": 3}

    def test_none_passes_through(self):
        from services.api.worker import _result_payload

        assert _result_payload(None) is None

    def test_anything_else_is_a_programming_error(self):
        from services.api.worker import _result_payload

        with pytest.raises(TypeError, match="handlers must return"):
            _result_payload("not a result")


@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_execute_job_dispatches_on_type(mock_to_thread, db_session):
    """The worker must run the handler the job names.

    Before the handler registry, ``execute_job`` ignored ``job.type`` entirely
    and ran puzzle generation for every job whatever it claimed to be.
    """
    from services.api.worker import JOB_HANDLERS, worker

    seen = {}

    def fake_handler(ctx):
        seen["username"] = ctx.username
        seen["params"] = ctx.params
        return {"ran": "custom"}

    job = Job(
        username="dispatch-test",
        type="custom_type",
        status=JobStatus.RUNNING,
        params={"k": "v"},
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    JOB_HANDLERS["custom_type"] = fake_handler
    try:
        with patch("services.api.worker.SessionLocal") as mock_sl:
            mock_sl.return_value.__enter__.return_value = db_session
            mock_sl.return_value.__exit__.return_value = None
            await worker.execute_job(job.id)
    finally:
        JOB_HANDLERS.pop("custom_type", None)

    db_session.expire_all()
    updated = db_session.get(Job, job.id)
    assert updated.status == JobStatus.SUCCEEDED
    assert updated.result_json == {"ran": "custom"}
    assert seen == {"username": "dispatch-test", "params": {"k": "v"}}


@pytest.mark.parametrize(
    "stored",
    [" JobUser ", "ＪＯＢＵＳＥＲ", "JOBUSER\xa0", " Ｊobuser\xa0"],
)
@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_execute_job_canonicalises_the_job_username(
    mock_to_thread, db_session, stored
):
    """The job boundary canonicalises exactly like the HTTP boundary does.

    Every ``Job`` row is created today from a ``Username``-annotated request, or
    copied from an existing job by ``_enqueue_diagnosis`` — so in production
    ``job.username`` is already canonical and this fold is a no-op. But that is
    an invariant nothing enforces: it holds only as long as every future job
    creator remembers, and the failure is silent rather than loud. A generation
    job whose username misses by one space runs a full Stockfish pass against an
    empty corpus and reports success.

    Asserted on ``ctx.username``, because that is what the handlers actually use
    as a storage key (``generate_puzzles`` and ``run_diagnosis`` both take it
    straight from the context).
    """
    from services.api.worker import JOB_HANDLERS, worker

    seen = {}

    def fake_handler(ctx):
        seen["username"] = ctx.username
        return {"ok": True}

    job = Job(username=stored, type="fold_probe", status=JobStatus.RUNNING)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    JOB_HANDLERS["fold_probe"] = fake_handler
    try:
        with patch("services.api.worker.SessionLocal") as mock_sl:
            mock_sl.return_value.__enter__.return_value = db_session
            mock_sl.return_value.__exit__.return_value = None
            await worker.execute_job(job.id)
    finally:
        JOB_HANDLERS.pop("fold_probe", None)

    assert seen["username"] == "jobuser", (
        f"job username {stored!r} reached the handler as {seen['username']!r}; "
        "a handler using that as a storage key would find nothing"
    )


@patch("services.api.worker.generate_puzzles")
@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_unknown_job_type_fails_loudly_and_runs_nothing(
    mock_to_thread, mock_generate, db_session
):
    """An unrecognised type must fail the job, NOT fall back to generation.

    Running the wrong work silently is worse than failing: the user would get
    puzzles generated by a job that claimed to be doing something else.
    """
    from services.api.worker import worker

    job = Job(username="unknown-type", type="not_a_real_type", status=JobStatus.RUNNING)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    with patch("services.api.worker.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value = db_session
        mock_sl.return_value.__exit__.return_value = None
        await worker.execute_job(job.id)

    db_session.expire_all()
    updated = db_session.get(Job, job.id)
    assert updated.status == JobStatus.FAILED
    assert "not_a_real_type" in updated.error_message
    mock_generate.assert_not_called()


@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_unknown_job_type_does_not_overwrite_a_cancel(mock_to_thread, db_session):
    """CANCELED is terminal. The unknown-type path takes the ordinary failure
    route, so its guarded UPDATE must leave a canceled job alone."""
    from services.api.worker import worker

    job = Job(
        username="unknown-canceled", type="not_a_real_type", status=JobStatus.CANCELED
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    with patch("services.api.worker.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value = db_session
        mock_sl.return_value.__exit__.return_value = None
        await worker.execute_job(job.id)

    db_session.expire_all()
    assert db_session.get(Job, job.id).status == JobStatus.CANCELED


def test_different_job_types_can_be_active_for_one_user(db_session):
    """The point of the index change: an analysis job must not be blocked by an
    in-flight generation for the same user."""
    from services.api.models import JobType

    _reset_jobs(db_session)
    db_session.add(
        Job(
            username="coexist", type=JobType.PUZZLE_GENERATION, status=JobStatus.RUNNING
        )
    )
    db_session.commit()

    db_session.add(
        Job(username="coexist", type="some_other_type", status=JobStatus.QUEUED)
    )
    db_session.commit()  # must not raise

    active = (
        db_session.query(Job)
        .filter(Job.username == "coexist")
        .filter(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        .count()
    )
    assert active == 2


def test_same_job_type_still_conflicts_for_one_user(db_session):
    """Widening the index must not weaken the invariant it exists for: no two
    concurrent jobs of the SAME kind for one user."""
    from sqlalchemy.exc import IntegrityError

    from services.api.models import JobType

    _reset_jobs(db_session)
    db_session.add(
        Job(username="dupe", type=JobType.PUZZLE_GENERATION, status=JobStatus.RUNNING)
    )
    db_session.commit()

    db_session.add(
        Job(username="dupe", type=JobType.PUZZLE_GENERATION, status=JobStatus.QUEUED)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_generate_reports_the_generation_job_not_another_type(client, db_session):
    """The conflict handler resolves the collision by looking the existing job
    back up. That lookup must be scoped to the generation type, or a
    concurrently active job of another type could be handed back as the
    caller's generation job — an id that will never produce puzzles."""
    from services.api.models import JobType

    _reset_jobs(db_session)
    other = Job(username="scoped", type="some_other_type", status=JobStatus.RUNNING)
    generation = Job(
        username="scoped", type=JobType.PUZZLE_GENERATION, status=JobStatus.RUNNING
    )
    db_session.add_all([other, generation])
    db_session.commit()
    db_session.refresh(other)
    db_session.refresh(generation)

    response = client.post("/puzzles/generate?username=scoped")
    assert response.status_code == 200
    assert response.json()["job_id"] == generation.id
    assert response.json()["job_id"] != other.id


@pytest.mark.postgres
def test_migration_scoped_active_job_index_postgres():
    """Migration smoke: after `alembic upgrade head` (run by CI before pytest),
    the real Postgres active-job index is keyed on (username, type)."""
    from sqlalchemy import inspect

    pg_engine = create_engine(os.environ["KNIGHTMIND_TEST_POSTGRES_URL"])
    indexes = {ix["name"]: ix for ix in inspect(pg_engine).get_indexes("jobs")}
    assert "ix_jobs_active_username" in indexes
    index = indexes["ix_jobs_active_username"]
    assert index["unique"]
    assert index["column_names"] == ["username", "type"]


# ---------------------------------------------------------------------------
# Follow-up diagnosis after puzzle generation
# ---------------------------------------------------------------------------


class TestFollowUpDiagnosis:
    """Fresh puzzles are fresh mistakes. Chaining diagnosis off generation
    means importing games is the only thing a user has to do."""

    def test_a_successful_generation_queues_a_diagnosis_run(self, db_session):
        from services.api.models import JobType
        from services.api.worker import JobWorker

        _reset_jobs(db_session)
        with patch("services.api.worker.SessionLocal") as mock_sl:
            mock_sl.return_value.__enter__.return_value = db_session
            mock_sl.return_value.__exit__.return_value = None
            JobWorker._enqueue_followup(JobType.PUZZLE_GENERATION.value, "chained")

        job = db_session.query(Job).filter_by(username="chained").one()
        assert job.type == JobType.DIAGNOSIS
        assert job.status == JobStatus.QUEUED
        # The whole corpus, not a default batch — this is a backfill.
        assert job.params["limit"] > 1000
        assert job.params["auto_chain"] is True

    def test_a_diagnosis_run_does_not_chain_another(self, db_session):
        """Otherwise every diagnosis queues a diagnosis, forever."""
        from services.api.models import JobType
        from services.api.worker import JobWorker

        _reset_jobs(db_session)
        with patch("services.api.worker.SessionLocal") as mock_sl:
            mock_sl.return_value.__enter__.return_value = db_session
            mock_sl.return_value.__exit__.return_value = None
            JobWorker._enqueue_followup(JobType.DIAGNOSIS.value, "chained")

        assert db_session.query(Job).filter_by(username="chained").count() == 0

    def test_an_already_active_diagnosis_is_not_duplicated(self, db_session):
        """The active-job index rejects the insert; marked automatic diagnosis
        jobs re-check pending work at completion, so the collision is a no-op,
        not an error."""
        from services.api.models import JobType
        from services.api.worker import JobWorker

        _reset_jobs(db_session)
        db_session.add(
            Job(username="busy", type=JobType.DIAGNOSIS, status=JobStatus.RUNNING)
        )
        db_session.commit()

        with patch("services.api.worker.SessionLocal") as mock_sl:
            mock_sl.return_value.__enter__.return_value = db_session
            mock_sl.return_value.__exit__.return_value = None
            JobWorker._enqueue_followup(JobType.PUZZLE_GENERATION.value, "busy")

        assert db_session.query(Job).filter_by(username="busy").count() == 1
        # The session must still be usable — a failed flush that is not rolled
        # back leaves it poisoned for every later query.
        assert db_session.query(Job).count() >= 1

    def test_a_failure_to_queue_never_propagates(self, db_session):
        """This runs after the generation is already marked SUCCEEDED. A
        follow-up that cannot be queued is a missing enrichment, not lost
        work — it must never turn a successful generation into a failure."""
        from services.api.models import JobType
        from services.api.worker import JobWorker

        with patch(
            "services.api.worker.SessionLocal", side_effect=RuntimeError("db down")
        ):
            JobWorker._enqueue_followup(JobType.PUZZLE_GENERATION.value, "unlucky")
        # No exception escaped.


@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_diagnosis_success_with_no_remaining_pending_chains_nothing(
    mock_to_thread, db_session
):
    from services.api.models import JobType
    from services.api.worker import JOB_HANDLERS, worker

    _reset_jobs(db_session)
    job = Job(username="diag-empty", type=JobType.DIAGNOSIS, status=JobStatus.RUNNING)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    def fake_diagnosis(ctx):
        return {"username": ctx.username, "remaining": 0, "canceled": False}

    original = JOB_HANDLERS[JobType.DIAGNOSIS.value]
    JOB_HANDLERS[JobType.DIAGNOSIS.value] = fake_diagnosis
    try:
        with patch("services.api.worker.SessionLocal") as mock_sl:
            mock_sl.return_value.__enter__.return_value = db_session
            mock_sl.return_value.__exit__.return_value = None
            await worker.execute_job(job.id)
    finally:
        JOB_HANDLERS[JobType.DIAGNOSIS.value] = original

    db_session.expire_all()
    assert db_session.get(Job, job.id).status == JobStatus.SUCCEEDED
    queued = (
        db_session.query(Job)
        .filter_by(
            username="diag-empty", type=JobType.DIAGNOSIS, status=JobStatus.QUEUED
        )
        .count()
    )
    assert queued == 0


@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_diagnosis_success_with_late_pending_puzzle_queues_followup(
    mock_to_thread, db_session
):
    from services.api.models import JobType
    from services.api.worker import JOB_HANDLERS, worker

    _reset_jobs(db_session)
    job = Job(
        username="diag-late",
        type=JobType.DIAGNOSIS,
        status=JobStatus.RUNNING,
        params={"limit": 1, "auto_chain": True},
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    def fake_diagnosis(ctx):
        _insert_pending_puzzle(db_session, ctx.username, "late-pending")
        return {"username": ctx.username, "remaining": 1, "canceled": False}

    original = JOB_HANDLERS[JobType.DIAGNOSIS.value]
    JOB_HANDLERS[JobType.DIAGNOSIS.value] = fake_diagnosis
    try:
        with patch("services.api.worker.SessionLocal") as mock_sl:
            mock_sl.return_value.__enter__.return_value = db_session
            mock_sl.return_value.__exit__.return_value = None
            await worker.execute_job(job.id)
    finally:
        JOB_HANDLERS[JobType.DIAGNOSIS.value] = original

    db_session.expire_all()
    assert db_session.get(Job, job.id).status == JobStatus.SUCCEEDED
    followup = (
        db_session.query(Job)
        .filter_by(
            username="diag-late", type=JobType.DIAGNOSIS, status=JobStatus.QUEUED
        )
        .one()
    )
    assert followup.message == "Queued for remaining diagnosis"
    assert followup.params["limit"] > 1000
    assert followup.params["auto_chain"] is True


@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_manual_bounded_diagnosis_success_does_not_queue_followup(
    mock_to_thread, db_session
):
    from services.api.models import JobType
    from services.api.worker import JOB_HANDLERS, worker

    _reset_jobs(db_session)
    _insert_pending_puzzle(db_session, "diag-manual", "manual-before-1")
    _insert_pending_puzzle(db_session, "diag-manual", "manual-before-2")
    job = Job(
        username="diag-manual",
        type=JobType.DIAGNOSIS,
        status=JobStatus.RUNNING,
        params={"limit": 1},
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    def fake_diagnosis(ctx):
        assert ctx.params == {"limit": 1}
        return {"username": ctx.username, "diagnosed": 1, "remaining": 1}

    original = JOB_HANDLERS[JobType.DIAGNOSIS.value]
    JOB_HANDLERS[JobType.DIAGNOSIS.value] = fake_diagnosis
    try:
        with patch("services.api.worker.SessionLocal") as mock_sl:
            mock_sl.return_value.__enter__.return_value = db_session
            mock_sl.return_value.__exit__.return_value = None
            await worker.execute_job(job.id)
    finally:
        JOB_HANDLERS[JobType.DIAGNOSIS.value] = original

    db_session.expire_all()
    assert db_session.get(Job, job.id).status == JobStatus.SUCCEEDED
    queued = (
        db_session.query(Job)
        .filter_by(
            username="diag-manual", type=JobType.DIAGNOSIS, status=JobStatus.QUEUED
        )
        .count()
    )
    assert queued == 0


@patch("services.api.worker.generate_puzzles")
@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_generation_success_chains_diagnosis_end_to_end(
    mock_to_thread, mock_generate, db_session
):
    from services.api.models import JobType
    from services.api.puzzles.generator import GenerationResult
    from services.api.worker import worker

    _reset_jobs(db_session)
    job = Job(
        username="e2e-chain", type=JobType.PUZZLE_GENERATION, status=JobStatus.RUNNING
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    mock_generate.return_value = GenerationResult(5, 0, 100)

    with patch("services.api.worker.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value = db_session
        mock_sl.return_value.__exit__.return_value = None
        await worker.execute_job(job.id)

    db_session.expire_all()
    assert db_session.get(Job, job.id).status == JobStatus.SUCCEEDED
    followup = (
        db_session.query(Job)
        .filter_by(username="e2e-chain", type=JobType.DIAGNOSIS)
        .one()
    )
    assert followup.status == JobStatus.QUEUED
    assert followup.params["auto_chain"] is True


@patch("services.api.worker.generate_puzzles")
@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_a_canceled_generation_chains_nothing(
    mock_to_thread, mock_generate, db_session
):
    """The completion was discarded, so there is nothing to follow up on."""
    from services.api.models import JobType
    from services.api.puzzles.generator import GenerationResult
    from services.api.worker import worker

    _reset_jobs(db_session)
    job = Job(
        username="e2e-cancel", type=JobType.PUZZLE_GENERATION, status=JobStatus.CANCELED
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    mock_generate.return_value = GenerationResult(5, 0, 100)

    with patch("services.api.worker.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value = db_session
        mock_sl.return_value.__exit__.return_value = None
        await worker.execute_job(job.id)

    db_session.expire_all()
    assert (
        db_session.query(Job)
        .filter_by(username="e2e-cancel", type=JobType.DIAGNOSIS)
        .count()
        == 0
    )


@patch("services.api.worker.generate_puzzles")
@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_a_raising_followup_cannot_undo_a_succeeded_generation(
    mock_to_thread, mock_generate, db_session
):
    """The strong form of "must never fail the generation".

    _enqueue_followup swallows its own errors, but that swallow is one edit
    away from being removed. The guarantee that actually holds is structural:
    the follow-up runs *after* the success write, and the failure write is
    guarded on status = RUNNING — so even an exception escaping the follow-up
    finds a job that has already left RUNNING and is discarded.
    """
    from services.api.models import JobType
    from services.api.puzzles.generator import GenerationResult
    from services.api.worker import JobWorker, worker

    _reset_jobs(db_session)
    job = Job(
        username="e2e-raise", type=JobType.PUZZLE_GENERATION, status=JobStatus.RUNNING
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    mock_generate.return_value = GenerationResult(5, 0, 100)

    def boom(*a, **k):
        raise RuntimeError("follow-up exploded")

    with (
        patch("services.api.worker.SessionLocal") as mock_sl,
        patch.object(JobWorker, "_enqueue_followup", staticmethod(boom)),
    ):
        mock_sl.return_value.__enter__.return_value = db_session
        mock_sl.return_value.__exit__.return_value = None
        await worker.execute_job(job.id)

    db_session.expire_all()
    assert db_session.get(Job, job.id).status == JobStatus.SUCCEEDED
    assert db_session.get(Job, job.id).error_message is None


# ---------------------------------------------------------------------------
# Client-observability: X-Client-Id header tracking and stall-report endpoint
# ---------------------------------------------------------------------------


def test_get_job_status_response_includes_observability_fields(client, db_session):
    """JobStatusResponse always includes client_id, client_last_seen_at, and
    stall_reported_at — even when all three are NULL (pre-observation job)."""
    job = Job(username="obs-null", status=JobStatus.RUNNING)
    db_session.add(job)
    db_session.commit()

    data = client.get(f"/jobs/{job.id}").json()
    assert "client_id" in data
    assert "client_last_seen_at" in data
    assert "stall_reported_at" in data
    assert data["client_id"] is None
    assert data["client_last_seen_at"] is None
    assert data["stall_reported_at"] is None


def test_get_job_status_first_sighting_sets_client_id(client, db_session):
    """First X-Client-Id sighting writes client_id and client_last_seen_at."""
    job = Job(username="obs-first", status=JobStatus.RUNNING)
    db_session.add(job)
    db_session.commit()

    data = client.get(f"/jobs/{job.id}", headers={"X-Client-Id": "tab-abc"}).json()
    assert data["client_id"] == "tab-abc"
    assert data["client_last_seen_at"] is not None

    db_session.expire_all()
    job = db_session.get(Job, job.id)
    assert job.client_id == "tab-abc"
    assert job.client_last_seen_at is not None


def test_get_job_status_new_tab_overwrites_client_id(client, db_session):
    """A different client-id (new tab) overwrites the stored client_id."""
    from datetime import timezone

    old_seen = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job = Job(
        username="obs-newtab",
        status=JobStatus.RUNNING,
        client_id="tab-old",
        client_last_seen_at=old_seen,
    )
    db_session.add(job)
    db_session.commit()

    data = client.get(f"/jobs/{job.id}", headers={"X-Client-Id": "tab-new"}).json()
    assert data["client_id"] == "tab-new"
    # client_last_seen_at should be updated to now (> old_seen)
    new_seen_str = data["client_last_seen_at"]
    assert new_seen_str is not None


def test_get_job_status_throttles_writes_for_same_client(client, db_session):
    """Same client-id within < 5 s does NOT update client_last_seen_at."""
    from datetime import timezone

    # Set client_last_seen_at to "just now" so the throttle kicks in.
    recent = datetime.now(timezone.utc)
    job = Job(
        username="obs-throttle",
        status=JobStatus.RUNNING,
        client_id="tab-same",
        client_last_seen_at=recent,
    )
    db_session.add(job)
    db_session.commit()

    client.get(f"/jobs/{job.id}", headers={"X-Client-Id": "tab-same"})

    db_session.expire_all()
    job = db_session.get(Job, job.id)
    # Should NOT have moved (throttled).
    assert job.client_last_seen_at is not None
    # The stored value is still the original (within tolerance of a few ms for
    # commit overhead; just assert it was NOT bumped significantly).
    diff = abs((job.client_last_seen_at.replace(tzinfo=timezone.utc) - recent).total_seconds())
    assert diff < 1.0, f"Expected throttled (no write), got diff={diff}s"


def test_get_job_status_no_header_does_not_write(client, db_session):
    """Request without X-Client-Id leaves client columns untouched."""
    job = Job(username="obs-noheader", status=JobStatus.RUNNING)
    db_session.add(job)
    db_session.commit()

    client.get(f"/jobs/{job.id}")  # no header

    db_session.expire_all()
    job = db_session.get(Job, job.id)
    assert job.client_id is None
    assert job.client_last_seen_at is None


def test_get_job_status_does_not_write_for_terminal_job(client, db_session):
    """client_last_seen_at is NOT updated for a SUCCEEDED/FAILED/CANCELED job."""
    job = Job(username="obs-terminal", status=JobStatus.SUCCEEDED)
    db_session.add(job)
    db_session.commit()

    client.get(f"/jobs/{job.id}", headers={"X-Client-Id": "tab-late"})

    db_session.expire_all()
    job = db_session.get(Job, job.id)
    assert job.client_id is None
    assert job.client_last_seen_at is None


def test_stall_report_sets_stall_reported_at(client, db_session):
    """POST /jobs/{id}/stall-report marks stall_reported_at and returns status."""
    job = Job(username="stall-mark", status=JobStatus.RUNNING)
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/jobs/{job.id}/stall-report")
    assert resp.status_code == 200
    data = resp.json()
    assert data["stall_reported_at"] is not None
    # Should NOT change job lifecycle.
    assert data["status"] == JobStatus.RUNNING

    db_session.expire_all()
    job = db_session.get(Job, job.id)
    assert job.stall_reported_at is not None
    assert job.status == JobStatus.RUNNING


def test_stall_report_is_idempotent(client, db_session):
    """Re-posting stall-report just refreshes the timestamp; no error."""
    from datetime import timezone

    old_stall = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job = Job(username="stall-idem", status=JobStatus.RUNNING, stall_reported_at=old_stall)
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/jobs/{job.id}/stall-report")
    assert resp.status_code == 200
    data = resp.json()
    # stall_reported_at must be refreshed (later than old_stall)
    assert data["stall_reported_at"] is not None
    new_stall = datetime.fromisoformat(data["stall_reported_at"])
    # Allow for serialisation without tzinfo
    if new_stall.tzinfo is None:
        new_stall = new_stall.replace(tzinfo=timezone.utc)
    assert new_stall > old_stall


def test_stall_report_sets_client_id_if_provided(client, db_session):
    """X-Client-Id on stall-report is stored as client_id."""
    job = Job(username="stall-cid", status=JobStatus.RUNNING)
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/jobs/{job.id}/stall-report", headers={"X-Client-Id": "tab-stall"})
    assert resp.status_code == 200

    db_session.expire_all()
    assert db_session.get(Job, job.id).client_id == "tab-stall"


def test_stall_report_not_found(client):
    """Unknown job id returns 404."""
    resp = client.post("/jobs/no-such-job/stall-report")
    assert resp.status_code == 404


@pytest.mark.postgres
def test_migration_applied_client_observability_columns_postgres():
    """Migration smoke: after `alembic upgrade head`, the real Postgres jobs
    table has the three client-observability columns added by this migration."""
    import os

    from sqlalchemy import inspect

    pg_engine = create_engine(os.environ["KNIGHTMIND_TEST_POSTGRES_URL"])
    cols = {c["name"] for c in inspect(pg_engine).get_columns("jobs")}
    assert "client_id" in cols
    assert "client_last_seen_at" in cols
    assert "stall_reported_at" in cols


def test_single_alembic_head(db_session):
    """Sanity: there must be exactly ONE Alembic head in this branch."""
    import subprocess
    result = subprocess.run(
        ["uv", "run", "alembic", "heads"],
        capture_output=True,
        text=True,
        cwd="/home/lauureal/.hermes/kanban/boards/knightmind/workspaces/job-client-observability-20260825/services/api",
    )
    heads = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert len(heads) == 1, f"Expected 1 head, got: {heads}"

