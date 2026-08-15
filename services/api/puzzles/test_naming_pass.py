"""The naming pass as the background job uses it.

The CLI path is exercised by running it; these cover the properties that make
it safe to hang off a job nobody is watching: it must not call the model when
naming is off, must not overwrite a human's name, must be idempotent, must
report honestly how much work is left, and must not spin when the provider is
down.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from services.api.ai import config
from services.api.diagnosis.causes import RULE_VERSION
from services.api.diagnosis.evidence import EXTRACTION_VERSION
from services.api.models import (
    DiagnosisAuditLog,
    Game,
    Job,
    JobStatus,
    JobType,
    Puzzle,
    PuzzleDiagnosis,
    PuzzleStats,
)
from services.api.puzzles import ai_naming, naming_pass
from services.api.storage.ai_audit_repository import AIAuditRepository, AuditWrite

FORK_FEN = "3q3k/8/8/4N3/8/8/8/6K1 w - - 0 1"
FORK_BEST = "e5f7"
USER = "lauureal"


def _seed(
    db,
    puzzle_id,
    *,
    username=USER,
    title=None,
    source=None,
    stats=True,
    diagnosed=False,
):
    if db.get(Game, (f"game-{puzzle_id}", username)) is None:
        db.add(
            Game(
                game_id=f"game-{puzzle_id}",
                url="",
                username=username,
                white_username=username,
                black_username="",
                white_result="win",
                black_result="loss",
                time_control="600",
                end_time=0,
            )
        )
        db.flush()
    db.add(
        Puzzle(
            id=puzzle_id,
            username=username,
            source_game_id=f"game-{puzzle_id}",
            ply=10,
            fen=FORK_FEN,
            side_to_move="white",
            played_move_uci="g1g2",
            best_move_uci=FORK_BEST,
            eval_before=0.0,
            eval_after=-3.0,
            swing=3.0,
        )
    )
    if stats:
        db.add(
            PuzzleStats(
                puzzle_id=puzzle_id,
                username=username,
                title=title,
                title_source=source,
                primary_motif="fork",
            )
        )
    if diagnosed:
        # A current-version diagnosis row, so DiagnosisRepository.pending_count
        # reports zero for this puzzle. Only the re-queue tests need it: they
        # assert on what the NAMING term does to the chain, and a puzzle that is
        # also undiagnosed would keep the chain alive on the diagnosis term
        # alone and prove nothing.
        db.add(
            PuzzleDiagnosis(
                puzzle_id=puzzle_id,
                username=username,
                extraction_version=EXTRACTION_VERSION,
                rule_version=RULE_VERSION,
            )
        )
    db.commit()


@pytest.fixture
def naming_on(monkeypatch):
    monkeypatch.setenv("KNIGHTMIND_AI_NAMING", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def _model_returns(monkeypatch, name):
    monkeypatch.setattr(
        ai_naming,
        "name_puzzle",
        lambda facts, avoid=None: ai_naming.NameOutcome(
            ai_naming.ACCEPTED, name=name, model_version="test"
        ),
    )


class TestDisabled:
    def test_no_model_call_when_naming_is_off(self, db_session, monkeypatch):
        """Naming ships off. The job runs on every deployment, so the disabled
        path must cost nothing and call nothing."""
        monkeypatch.delenv("KNIGHTMIND_AI_NAMING", raising=False)
        _seed(db_session, "p1", source="position", title="The f7 Knight Fork")
        monkeypatch.setattr(
            ai_naming,
            "name_puzzle",
            lambda *a, **k: pytest.fail("the model must not be called"),
        )

        summary = naming_pass.name_puzzles(db_session, username=USER)
        assert summary["named"] == 1  # it still got its deterministic name
        assert db_session.get(PuzzleStats, "p1").title_source == "position"

    def test_pending_count_is_zero_when_disabled(self, db_session, monkeypatch):
        """So a deployment that never enables naming cannot queue work that
        will never be done."""
        monkeypatch.delenv("KNIGHTMIND_AI_NAMING", raising=False)
        _seed(db_session, "p1", source="position")
        assert naming_pass.pending_count(db_session, USER) == 0


class TestNaming:
    def test_an_accepted_name_is_stored_as_ai(self, db_session, monkeypatch, naming_on):
        _seed(db_session, "p1", source="position", title="The f7 Knight Fork")
        _model_returns(monkeypatch, "Knight Went Wandering")

        naming_pass.name_puzzles(db_session, username=USER)

        stats = db_session.get(PuzzleStats, "p1")
        assert stats.title == "Knight Went Wandering"
        assert stats.title_source == "ai"

    def test_a_second_pass_is_free(self, db_session, monkeypatch, naming_on):
        """The stored name IS the cache — this is what makes it safe to hang
        off a job that runs repeatedly."""
        _seed(db_session, "p1", source="ai", title="Knight Went Wandering")
        monkeypatch.setattr(
            ai_naming,
            "name_puzzle",
            lambda *a, **k: pytest.fail("already-named puzzles must not re-call"),
        )

        summary = naming_pass.name_puzzles(db_session, username=USER)
        assert summary["outcomes"]["already_named"] == 1

    def test_a_user_title_is_never_touched(self, db_session, monkeypatch, naming_on):
        _seed(db_session, "p1", source="user", title="My Nemesis")
        _model_returns(monkeypatch, "Knight Went Wandering")

        naming_pass.name_puzzles(db_session, username=USER, force=True)

        assert db_session.get(PuzzleStats, "p1").title == "My Nemesis"

    def test_a_rejected_name_falls_back_to_the_position(
        self, db_session, monkeypatch, naming_on
    ):
        _seed(db_session, "p1", source="position")
        monkeypatch.setattr(
            ai_naming,
            "name_puzzle",
            lambda facts, avoid=None: ai_naming.NameOutcome(
                ai_naming.REJECTED, reason="name_reveals_tactic:fork"
            ),
        )

        naming_pass.name_puzzles(db_session, username=USER)

        stats = db_session.get(PuzzleStats, "p1")
        assert stats.title == "King to g2"
        assert stats.title_source == "position"

    def test_a_puzzle_with_no_stats_row_gets_one(
        self, db_session, monkeypatch, naming_on
    ):
        """The stats row is created on demand, not at puzzle creation — 88 live
        puzzles were in that state before this pass first ran. Without this they
        would be permanently unnameable: there is nowhere to put the title."""
        _seed(db_session, "p1", stats=False)
        _model_returns(monkeypatch, "Knight Went Wandering")

        naming_pass.name_puzzles(db_session, username=USER)

        stats = db_session.get(PuzzleStats, "p1")
        assert stats is not None
        assert stats.title_source == "ai"

    def test_names_are_deduplicated_even_when_the_model_repeats_itself(
        self, db_session, monkeypatch, naming_on
    ):
        for pid in ("p1", "p2", "p3"):
            _seed(db_session, pid, source="position")
        _model_returns(monkeypatch, "Knight Went Wandering")

        naming_pass.name_puzzles(db_session, username=USER)

        titles = {db_session.get(PuzzleStats, p).title for p in ("p1", "p2", "p3")}
        assert len(titles) == 3


class TestPendingCount:
    def test_counts_only_what_still_needs_a_name(self, db_session, naming_on):
        _seed(db_session, "p1", source="position")
        _seed(db_session, "p2", source=None)
        _seed(db_session, "p3", stats=False)
        _seed(db_session, "p4", source="ai", title="Knight Went Wandering")
        _seed(db_session, "p5", source="user", title="My Nemesis")

        # position + NULL + no-stats-row; ai and user are done.
        assert naming_pass.pending_count(db_session, USER) == 3

    def test_another_users_puzzles_are_not_counted(self, db_session, naming_on):
        _seed(db_session, "p1", source="position")
        _seed(db_session, "p2", username="someone-else", source="position")

        assert naming_pass.pending_count(db_session, USER) == 1

    def test_a_persistently_rejected_puzzle_stops_counting(
        self, db_session, monkeypatch, naming_on
    ):
        """The worker re-queues while this is > 0, so it MUST reach zero.

        A rejected name leaves the puzzle on its deterministic title, which by
        itself still looks like pending work — the job would be re-queued,
        reject it again, and loop forever. The audit row is the record that the
        model already answered for this puzzle. Same guarantee the diagnosis
        job gets from recording UNAVAILABLE rows.
        """
        _seed(db_session, "p1", source="position")
        monkeypatch.setattr(
            ai_naming,
            "name_puzzle",
            lambda facts, avoid=None: ai_naming.NameOutcome(
                ai_naming.REJECTED, reason="name_reveals_tactic:fork"
            ),
        )

        assert naming_pass.pending_count(db_session, USER) == 1
        naming_pass.name_puzzles(db_session, username=USER)
        # Still on its deterministic name — but no longer pending work.
        assert db_session.get(PuzzleStats, "p1").title_source == "position"
        assert naming_pass.pending_count(db_session, USER) == 0

    def test_an_outage_stays_eligible_for_retry(
        self, db_session, monkeypatch, naming_on
    ):
        """An error is not an answer. A provider outage must not permanently
        give up on naming a puzzle."""
        _seed(db_session, "p1", source="position")
        monkeypatch.setattr(
            ai_naming,
            "name_puzzle",
            lambda facts, avoid=None: ai_naming.NameOutcome(
                ai_naming.ERROR, reason="ConnectionError"
            ),
        )

        naming_pass.name_puzzles(db_session, username=USER)
        assert naming_pass.pending_count(db_session, USER) == 1

    def test_the_count_falls_as_puzzles_are_named(
        self, db_session, monkeypatch, naming_on
    ):
        """The worker re-queues on this number, so it has to actually move or
        the chain either stops early or loops forever."""
        for pid in ("p1", "p2", "p3"):
            _seed(db_session, pid, source="position")
        _model_returns(monkeypatch, "Knight Went Wandering")

        assert naming_pass.pending_count(db_session, USER) == 3
        naming_pass.name_puzzles(db_session, username=USER, limit=2)
        assert naming_pass.pending_count(db_session, USER) == 1
        naming_pass.name_puzzles(db_session, username=USER)
        assert naming_pass.pending_count(db_session, USER) == 0


def _provider_unreachable(*args, **kwargs):
    """What a network failure looks like from inside ai_naming.

    Patched over ``_call`` rather than over ``name_puzzle`` so the real
    try/except runs and produces a genuine ERROR outcome — the loop being
    tested starts with that except block, so stubbing past it would test the
    fixture instead of the code.
    """
    raise ConnectionError("provider unreachable")


def _chain_once(db) -> int:
    """One turn of the worker's re-queue chain. Returns jobs it queued.

    Stands in for a diagnosis job completing: the worker marks it SUCCEEDED and
    then asks whether to queue another. The queued job is removed afterwards
    because the active-job unique index allows only one per (user, type), so
    leaving it would silently suppress the next turn and make a loop look
    bounded.
    """
    with patch("services.api.worker.SessionLocal") as mock_sl:
        from services.api.worker import JobWorker

        mock_sl.return_value.__enter__.return_value = db
        mock_sl.return_value.__exit__.return_value = None
        JobWorker._enqueue_remaining_diagnosis_if_pending(
            JobType.DIAGNOSIS.value, USER, {"auto_chain": True}
        )

    queued = (
        db.query(Job)
        .filter_by(username=USER, type=JobType.DIAGNOSIS, status=JobStatus.QUEUED)
        .all()
    )
    for job in queued:
        db.delete(job)
    db.commit()
    return len(queued)


class TestRequeueChain:
    """What the worker DOES with pending_count, not just what it returns.

    The re-queue predicate had no test of its own for the naming term, and the
    two halves are only wrong together: an ERROR deliberately leaves a puzzle
    pending, and the worker re-queues while anything is pending. A provider
    outage therefore had the worker claiming an identical job every two seconds
    — writing an audit row per failed call and a job row per turn — with no
    brake, because an error is not billed and so never reaches the daily cap
    either.
    """

    def test_an_outage_stops_the_chain_instead_of_re_queuing_forever(
        self, db_session, monkeypatch, naming_on
    ):
        for pid in ("p1", "p2", "p3", "p4"):
            _seed(db_session, pid, source="position", diagnosed=True)
        monkeypatch.setattr(ai_naming, "_call", _provider_unreachable)

        queued = 0
        for _ in range(5):
            naming_pass.name_puzzles(
                db_session, username=USER, limit=naming_pass.NAMING_BATCH_MAX
            )
            queued += _chain_once(db_session)

        assert queued == 0
        # And the puzzles are still pending: the chain paused, it did not give
        # up on naming them. That distinction is the whole design.
        assert naming_pass.pending_count(db_session, USER) == 4

    def test_one_isolated_failure_still_chains(
        self, db_session, monkeypatch, naming_on
    ):
        """The brake is for an outage, not for a single unlucky call.

        Tripping on one error would stall naming every time a request happened
        to be the one that timed out, which is a far more common event than a
        provider being down.
        """
        for pid in ("p1", "p2"):
            _seed(db_session, pid, source="position", diagnosed=True)
        db_session.add(
            DiagnosisAuditLog(
                username=USER,
                call_type=ai_naming.CALL_TYPE,
                puzzle_id="p1",
                status=ai_naming.ERROR,
                reason="ConnectionError",
            )
        )
        db_session.commit()

        assert naming_pass.retry_is_backed_off(db_session, USER) is False
        assert _chain_once(db_session) == 1

    def test_a_missing_api_key_backs_off_too(self, db_session, monkeypatch, naming_on):
        """`skipped` used to be invisible to both the breaker and pending_count.

        A missing key writes SKIPPED("no_api_key"): it never tripped the
        streak, and it never reduced pending — so the worker re-queued, the
        pass skipped again, and the cycle repeated every ~2s indefinitely. And
        because a skip is not billed, the daily cap could not bound it either.
        Unlike an outage this never self-heals on its own, so it would have run
        until someone noticed the container was busy doing nothing.
        """
        for pid in ("p1", "p2", "p3", "p4"):
            _seed(db_session, pid, source="position", diagnosed=True)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        naming_pass.name_puzzles(db_session, username=USER)

        assert naming_pass.retry_is_backed_off(db_session, USER) is True
        assert _chain_once(db_session) == 0

    def test_a_puzzle_with_no_position_backs_off_too(
        self, db_session, monkeypatch, naming_on
    ):
        """The skip that reads like a per-puzzle defect is still a systemic one.

        A puzzle with no FEN is never sent, so ``pending_count`` never sees an
        answer for it and it stays pending for good. That makes it the third
        spin: the worker re-queues, the pass skips it again, and nothing bills.
        Excluding it from the streak — on the reasonable-sounding grounds that
        one broken row should not pause the good ones behind it — removes the
        only bound there is. The pause is the price; the loop is not.
        """
        for pid in ("p1", "p2", "p3", "p4"):
            _seed(db_session, pid, source="position", diagnosed=True)
            db_session.query(Puzzle).filter(Puzzle.id == pid).update({"fen": ""})
        db_session.commit()
        monkeypatch.setattr(
            ai_naming,
            "_call",
            lambda *a, **k: pytest.fail("a puzzle with no position must not be sent"),
        )

        naming_pass.name_puzzles(db_session, username=USER)

        assert naming_pass.pending_count(db_session, USER) == 4
        assert naming_pass.retry_is_backed_off(db_session, USER) is True
        assert _chain_once(db_session) == 0

    def test_an_exhausted_budget_backs_off_too(
        self, db_session, monkeypatch, naming_on
    ):
        """Same shape, and reachable in ordinary operation rather than only on
        a misconfiguration: the corpus is larger than a day's allowance."""
        for pid in ("p1", "p2", "p3", "p4"):
            _seed(db_session, pid, source="position", diagnosed=True)
        monkeypatch.setattr(
            ai_naming,
            "name_puzzle",
            lambda facts, avoid=None: ai_naming.NameOutcome(
                ai_naming.SKIPPED, reason="budget_exhausted"
            ),
        )

        naming_pass.name_puzzles(db_session, username=USER)

        assert naming_pass.retry_is_backed_off(db_session, USER) is True
        assert _chain_once(db_session) == 0

    def test_an_answered_call_clears_the_streak(
        self, db_session, monkeypatch, naming_on
    ):
        """Recovery is automatic. Nothing has to notice the provider is back —
        the first call it answers resets the streak by itself."""
        for pid in ("p1", "p2", "p3", "p4"):
            _seed(db_session, pid, source="position", diagnosed=True)
        monkeypatch.setattr(ai_naming, "_call", _provider_unreachable)
        naming_pass.name_puzzles(db_session, username=USER)
        assert naming_pass.retry_is_backed_off(db_session, USER) is True

        _model_returns(monkeypatch, "Knight Went Wandering")
        # One puzzle only, so work remains and the chain has something to say.
        naming_pass.name_puzzles(db_session, username=USER, limit=1)

        assert naming_pass.retry_is_backed_off(db_session, USER) is False
        assert _chain_once(db_session) == 1

    def test_a_stale_streak_expires(self, db_session, monkeypatch, naming_on):
        """Otherwise the breaker latches shut: the only thing that clears a
        streak is a successful call, and while it is open there are none."""
        for pid in ("p1", "p2", "p3", "p4"):
            _seed(db_session, pid, source="position", diagnosed=True)
        monkeypatch.setattr(ai_naming, "_call", _provider_unreachable)
        naming_pass.name_puzzles(db_session, username=USER)
        assert naming_pass.retry_is_backed_off(db_session, USER) is True

        # Age the failures past the cooldown. Naive UTC, matching how the
        # column is written and read everywhere else.
        stale = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - naming_pass.ERROR_STREAK_COOLDOWN
            - timedelta(minutes=1)
        )
        for row in db_session.query(DiagnosisAuditLog).all():
            row.created_at = stale
        db_session.commit()

        assert naming_pass.retry_is_backed_off(db_session, USER) is False
        assert _chain_once(db_session) == 1

    def test_another_users_outage_does_not_pause_this_one(
        self, db_session, monkeypatch, naming_on
    ):
        """The streak is per user for the same reason the budget is: one
        tenant's failures must not stop everyone else's naming."""
        for pid in ("p1", "p2", "p3", "p4"):
            _seed(db_session, pid, username="someone-else", source="position")
        monkeypatch.setattr(ai_naming, "_call", _provider_unreachable)
        naming_pass.name_puzzles(db_session, username="someone-else")

        assert naming_pass.retry_is_backed_off(db_session, "someone-else") is True
        assert naming_pass.retry_is_backed_off(db_session, USER) is False

    def test_the_backoff_costs_nothing_when_naming_is_off(
        self, db_session, monkeypatch
    ):
        """The branch every deployment takes today: no query, no work."""
        monkeypatch.delenv("KNIGHTMIND_AI_NAMING", raising=False)
        assert naming_pass.retry_is_backed_off(db_session, USER) is False

    def test_a_diagnosis_run_does_not_retry_naming_while_backed_off(
        self, db_session, monkeypatch, naming_on
    ):
        """Diagnosis converges on its own and keeps running during an outage.

        Each of those runs would otherwise spend a batch of connection timeouts
        rediscovering what the ledger already records — real wall-clock, since a
        dead provider fails slowly.
        """
        from services.api.diagnosis import job

        for pid in ("p1", "p2", "p3", "p4"):
            _seed(db_session, pid, source="position")
        calls = 0

        def _count_then_fail(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise ConnectionError("provider unreachable")

        monkeypatch.setattr(ai_naming, "_call", _count_then_fail)

        # Four calls, four failures, four deterministic fallback names.
        assert job._name_new_puzzles(db_session, USER) == 4
        assert calls == 4
        # Second run: the streak is open, so it does not call at all.
        assert job._name_new_puzzles(db_session, USER) == 0
        assert calls == 4


OTHER_USER = "alfi3sr"


_TEST_CAP = 3


def _spend_the_days_allowance(db, monkeypatch, username):
    """Exhaust one user's naming budget, leaving every other user's intact.

    The cap is lowered and real billable rows are recorded, rather than patching
    ``Budget.exhausted`` or the cap to zero. The pass reads its budget through
    ``budget_last_24h``, so a test that stubbed past it would never prove the
    loop enters the exhausted branch at all — which is exactly how the existing
    ``test_an_exhausted_budget_backs_off_too`` misses it. A cap of zero is also
    not a state the environment can reach: ``env_int`` enforces ``min_value=1``.

    The cap is per user, so lowering it applies to everyone; the rows are what
    single out this one. That leaves an untouched user ``_TEST_CAP`` calls of
    room, which is what makes "was the other tenant reached?" answerable.
    """
    monkeypatch.setattr(config, "NAMING_DAILY_CAP_PER_USER", _TEST_CAP)
    audit = AIAuditRepository(db)
    for _ in range(_TEST_CAP):
        audit.record(
            AuditWrite(
                username=username,
                call_type=ai_naming.CALL_TYPE,
                status=ai_naming.ACCEPTED,
                model_version="test",
            )
        )
    db.commit()


class TestBudgetExhausted:
    """The capped pass, which is the ordinary end of a large backfill.

    A corpus bigger than a day's allowance reaches this branch on every run
    until the backfill finishes, so what it does with the puzzles it cannot
    name is not an edge case — it is most of the work.
    """

    def test_a_puzzle_that_hits_the_cap_still_gets_its_fallback_name(
        self, db_session, monkeypatch, naming_on
    ):
        """The deterministic name is free and does not need the model.

        Withholding it strands the puzzle: the pass reads the same
        ``created_at`` order every run, so the same row hits the cap at the same
        position and is passed over again. ``p3`` has no stats row at all — the
        state every puzzle starts in, since the row is written on demand — and
        for those, skipping the write leaves them not merely unnamed but
        unnameable.
        """
        _spend_the_days_allowance(db_session, monkeypatch, USER)
        _seed(db_session, "p1", source="position")
        _seed(db_session, "p2", source="position")
        _seed(db_session, "p3", stats=False)
        _seed(db_session, "p4", source="position")
        monkeypatch.setattr(
            ai_naming,
            "name_puzzle",
            lambda *a, **k: pytest.fail("the budget is spent; nothing may be called"),
        )

        summary = naming_pass.name_puzzles(db_session, username=USER)

        assert summary["outcomes"]["budget_exhausted"] == 4
        for pid in ("p1", "p2", "p3", "p4"):
            stats = db_session.get(PuzzleStats, pid)
            assert stats is not None, f"{pid} was left with no stats row"
            assert stats.title, f"{pid} was left unnamed"
            assert stats.title_source == "position"

    def test_the_rows_written_are_capped_at_the_streak_threshold(
        self, db_session, monkeypatch, naming_on
    ):
        """Bounded, but not below the threshold.

        One row per remaining puzzle turns a capped pass over a large corpus
        into hundreds of identical rows. Writing only the first is worse: the
        streak stays under the limit, the breaker never opens, and the worker
        re-queues at once — cheaper rows, same loop.
        """
        _spend_the_days_allowance(db_session, monkeypatch, USER)
        for pid in ("p1", "p2", "p3", "p4", "p5", "p6"):
            _seed(db_session, pid, source="position", diagnosed=True)

        naming_pass.name_puzzles(db_session, username=USER)

        skips = (
            db_session.query(DiagnosisAuditLog)
            .filter(
                DiagnosisAuditLog.call_type == ai_naming.CALL_TYPE,
                DiagnosisAuditLog.status == ai_naming.SKIPPED,
            )
            .all()
        )
        assert len(skips) == naming_pass.ERROR_STREAK_LIMIT
        assert naming_pass.retry_is_backed_off(db_session, USER) is True
        assert _chain_once(db_session) == 0

    def test_an_exhausted_user_does_not_starve_the_others(
        self, db_session, monkeypatch, naming_on
    ):
        """Budgets are per user, so stopping the pass on one is over-broad.

        ``scripts/ai_name_puzzles.py`` defaults ``--username`` to None, and the
        live corpus spans two handles — so the pass that ends here is the one
        that runs over everybody.
        """
        _spend_the_days_allowance(db_session, monkeypatch, USER)
        for pid in ("p1", "p2", "p3", "p4"):
            _seed(db_session, pid, source="position")
        _seed(db_session, "q1", username=OTHER_USER, source="position")
        _seed(db_session, "q2", username=OTHER_USER, source="position")
        _model_returns(monkeypatch, "Knight Went Wandering")

        naming_pass.name_puzzles(db_session)

        for pid in ("q1", "q2"):
            stats = db_session.get(PuzzleStats, pid)
            assert stats.title_source == "ai", f"{pid} was never reached"

    def test_a_streak_split_across_users_still_opens_both_breakers(
        self, db_session, monkeypatch, naming_on
    ):
        """Counting globally is not a smaller version of counting per user.

        With both users capped, a global threshold stops the pass after three
        rows spread over two ledgers — so neither streak reaches the limit,
        neither breaker opens, and the next run is byte-identical. The cheap
        rows were saved and the loop was kept.
        """
        _spend_the_days_allowance(db_session, monkeypatch, USER)
        _spend_the_days_allowance(db_session, monkeypatch, OTHER_USER)
        for i in range(4):
            _seed(db_session, f"p{i}", source="position", diagnosed=True)
            _seed(
                db_session,
                f"q{i}",
                username=OTHER_USER,
                source="position",
                diagnosed=True,
            )

        naming_pass.name_puzzles(db_session)

        assert naming_pass.retry_is_backed_off(db_session, USER) is True
        assert naming_pass.retry_is_backed_off(db_session, OTHER_USER) is True
