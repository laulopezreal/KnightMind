"""Tests for AI enrichment as wired into the diagnosis job.

Covers the parts the client tests can't: the budget ledger, the audit trail,
retention, and what actually lands on the diagnosis row. No network — the model
call is replaced at the job's seam.
"""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from services.api.ai import client as ai_client  # noqa: E402
from services.api.ai.schema import AIDiagnosis  # noqa: E402
from services.api.diagnosis import job as diagnosis_job  # noqa: E402
from services.api.diagnosis.job import run_diagnosis  # noqa: E402
from services.api.jobs.cleanup_sessions import purge_expired_ai_audit  # noqa: E402
from services.api.models import Base, DiagnosisAuditLog  # noqa: E402
from services.api.storage.ai_audit_repository import (  # noqa: E402
    AIAuditRepository,
    AuditWrite,
    Budget,
)
from services.api.storage.diagnosis_repository import DiagnosisRepository  # noqa: E402
from services.api.test_diagnosis_store import (  # noqa: E402
    USER,
    FakeContext,
    _NoClose,
    _puzzle,
)


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


@pytest.fixture(autouse=True)
def _ai_on(monkeypatch):
    monkeypatch.delenv("KNIGHTMIND_AI_DIAGNOSIS", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    yield


def accepted(cause="loose_piece_awareness", agreed=True):
    return ai_client.EnrichmentOutcome(
        status=ai_client.ACCEPTED,
        diagnosis=AIDiagnosis(
            primary_cause=cause,
            secondary_causes=[],
            evidence_ids=["position.phase"],
            confidence=0.82,
            explanation="You left two pieces undefended at once.",
            training_recommendation="Scan for loose pieces before calculating.",
        ),
        raw_response='{"primary_cause": "loose_piece_awareness"}',
        model_version="claude-opus-5",
        agreed_with_rules=agreed,
        input_tokens=1500,
        output_tokens=400,
    )


def patch_ai(monkeypatch, outcome):
    monkeypatch.setattr(diagnosis_job.ai_client, "enrich", lambda *a, **k: outcome)


def patch_session(monkeypatch, db):
    monkeypatch.setattr(diagnosis_job, "SessionLocal", lambda: _NoClose(db))


class TestEnrichmentReachesTheRow:
    def test_an_accepted_diagnosis_is_stored_with_its_prose(
        self, db_session, monkeypatch
    ):
        _puzzle(db_session)
        patch_session(monkeypatch, db_session)
        patch_ai(monkeypatch, accepted())

        result = run_diagnosis(FakeContext())
        assert result["enriched"] == 1

        row = DiagnosisRepository(db_session).get(USER, "p1")
        assert row.source == "llm"
        assert row.model_version == "claude-opus-5"
        assert row.model_confidence == 0.82
        assert row.agreed_with_rules is True
        assert row.explanation.startswith("You left two pieces")
        assert row.training_recommendation

    def test_a_model_re_rank_wins_over_the_rules_ordering(
        self, db_session, monkeypatch
    ):
        """Re-ranking inside the candidate set is the model's remit — the
        stored cause must reflect it, or the call was pointless."""
        _puzzle(db_session)
        patch_session(monkeypatch, db_session)
        patch_ai(monkeypatch, accepted(cause="forcing_move_blindness", agreed=False))

        run_diagnosis(FakeContext())
        row = DiagnosisRepository(db_session).get(USER, "p1")
        assert row.primary_cause == "forcing_move_blindness"
        assert row.agreed_with_rules is False

    @pytest.mark.parametrize(
        "outcome",
        [
            ai_client.EnrichmentOutcome(
                ai_client.REJECTED, reason="cause_not_supported:x"
            ),
            ai_client.EnrichmentOutcome(ai_client.ERROR, reason="ConnectionError"),
            ai_client.EnrichmentOutcome(ai_client.SKIPPED, reason="no_api_key"),
        ],
        ids=["rejected", "error", "skipped"],
    )
    def test_any_non_accepted_outcome_leaves_the_rules_diagnosis_intact(
        self, db_session, monkeypatch, outcome
    ):
        _puzzle(db_session)
        patch_session(monkeypatch, db_session)
        patch_ai(monkeypatch, outcome)

        run_diagnosis(FakeContext())
        row = DiagnosisRepository(db_session).get(USER, "p1")
        assert row.source == "rules"
        assert row.primary_cause == "loose_piece_awareness"  # from the rules
        assert row.explanation is None
        assert row.model_confidence is None


class TestKillSwitch:
    def test_disabling_leaves_no_trace_at_all(self, db_session, monkeypatch):
        """A disabled feature should write nothing — not even skip records.
        An audit table full of skips is noise that hides the real rows."""
        monkeypatch.setenv("KNIGHTMIND_AI_DIAGNOSIS", "0")
        _puzzle(db_session)
        patch_session(monkeypatch, db_session)

        def explode(*a, **k):
            raise AssertionError("the model must not be called")

        monkeypatch.setattr(diagnosis_job.ai_client, "enrich", explode)

        result = run_diagnosis(FakeContext())
        assert result["diagnosed"] == 1
        assert result["enriched"] == 0
        assert db_session.query(DiagnosisAuditLog).count() == 0
        assert DiagnosisRepository(db_session).get(USER, "p1").source == "rules"


class TestBudget:
    def test_only_billable_attempts_count_against_the_cap(self, db_session):
        """A provider outage must not consume the day's allowance — an errored
        call was never billed."""
        repo = AIAuditRepository(db_session)
        for status in ("accepted", "rejected", "skipped", "error"):
            repo.record(AuditWrite(username=USER, status=status))
        db_session.commit()

        budget = repo.budget_today(USER)
        assert budget.user_used == 2  # accepted + rejected only

    def test_another_users_spend_counts_globally_but_not_per_user(self, db_session):
        repo = AIAuditRepository(db_session)
        repo.record(AuditWrite(username=USER, status="accepted"))
        repo.record(AuditWrite(username="someone-else", status="accepted"))
        db_session.commit()

        budget = repo.budget_today(USER)
        assert budget.user_used == 1
        assert budget.global_used == 2

    def test_yesterdays_spend_does_not_count(self, db_session):
        repo = AIAuditRepository(db_session)
        row = repo.record(AuditWrite(username=USER, status="accepted"))
        db_session.commit()
        row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=2
        )
        db_session.commit()
        assert repo.budget_today(USER).user_used == 0

    def test_the_lower_of_the_two_caps_binds(self):
        from services.api.ai import config

        nearly_global = Budget(user_used=0, global_used=config.DAILY_CAP_GLOBAL - 1)
        assert nearly_global.remaining == 1
        assert not nearly_global.exhausted
        assert nearly_global.spend(1).exhausted

    def test_exhaustion_stops_calls_and_records_why(self, db_session, monkeypatch):
        """A spent budget is a normal end-state for a big backfill, not an
        error — but it must be visible, or bare cards look like a bug."""
        monkeypatch.setattr(
            "services.api.storage.ai_audit_repository.config.DAILY_CAP_PER_USER", 0
        )
        _puzzle(db_session)
        patch_session(monkeypatch, db_session)

        def explode(*a, **k):
            raise AssertionError("the model must not be called")

        monkeypatch.setattr(diagnosis_job.ai_client, "enrich", explode)

        result = run_diagnosis(FakeContext())
        assert result["diagnosed"] == 1
        assert result["enriched"] == 0

        rows = db_session.query(DiagnosisAuditLog).all()
        assert [r.reason for r in rows] == ["budget_exhausted"]
        assert DiagnosisRepository(db_session).get(USER, "p1").source == "rules"


class TestAuditTrail:
    def test_every_attempt_is_recorded_with_its_versions(self, db_session, monkeypatch):
        _puzzle(db_session)
        patch_session(monkeypatch, db_session)
        patch_ai(monkeypatch, accepted())
        run_diagnosis(FakeContext())

        row = db_session.query(DiagnosisAuditLog).one()
        assert row.status == "accepted"
        assert row.puzzle_id == "p1"
        assert row.rule_version and row.extraction_version
        assert row.evidence_hash and row.prompt_hash
        assert row.input_tokens == 1500 and row.output_tokens == 400

    def test_a_rejection_keeps_the_raw_response_for_debugging(
        self, db_session, monkeypatch
    ):
        """Rejections are the debugging corpus — the only place a refused
        answer survives."""
        _puzzle(db_session)
        patch_session(monkeypatch, db_session)
        patch_ai(
            monkeypatch,
            ai_client.EnrichmentOutcome(
                ai_client.REJECTED,
                reason="cause_not_supported:astrology",
                raw_response='{"primary_cause": "astrology"}',
            ),
        )
        run_diagnosis(FakeContext())

        row = db_session.query(DiagnosisAuditLog).one()
        assert row.status == "rejected"
        assert "astrology" in row.response_json

    def test_an_oversized_response_is_truncated_and_flagged(self, db_session):
        """Silently clipping a payload would mislead whoever reads it later."""
        from services.api.ai import config

        repo = AIAuditRepository(db_session)
        repo.record(
            AuditWrite(username=USER, status="rejected", response_json="x" * 50_000)
        )
        db_session.commit()

        row = db_session.query(DiagnosisAuditLog).one()
        assert len(row.response_json) == config.AUDIT_RESPONSE_MAX_CHARS
        assert row.response_truncated is True

    def test_a_normal_response_is_not_flagged_as_truncated(self, db_session):
        repo = AIAuditRepository(db_session)
        repo.record(AuditWrite(username=USER, status="accepted", response_json="{}"))
        db_session.commit()
        assert db_session.query(DiagnosisAuditLog).one().response_truncated is False


class TestRetention:
    def _aged(self, db, days, status="accepted"):
        repo = AIAuditRepository(db)
        row = repo.record(AuditWrite(username=USER, status=status))
        db.commit()
        row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=days
        )
        db.commit()
        return row

    def test_rows_past_the_window_are_purged(self, db_session):
        self._aged(db_session, days=40)
        self._aged(db_session, days=5)

        removed = purge_expired_ai_audit(db_session)
        assert removed == 1
        assert db_session.query(DiagnosisAuditLog).count() == 1

    def test_the_sweep_is_a_no_op_when_nothing_has_expired(self, db_session):
        self._aged(db_session, days=1)
        assert purge_expired_ai_audit(db_session) == 0
        assert db_session.query(DiagnosisAuditLog).count() == 1

    def test_the_window_is_configurable(self, db_session, monkeypatch):
        self._aged(db_session, days=10)
        monkeypatch.setattr(
            "services.api.storage.ai_audit_repository.config.AUDIT_RETENTION_DAYS", 7
        )
        assert AIAuditRepository(db_session).purge_older_than() == 1


class TestAgreementMetric:
    def test_reports_agreement_and_rejection_rates(self, db_session):
        repo = AIAuditRepository(db_session)
        repo.record(
            AuditWrite(username=USER, status="accepted", agreed_with_rules=True)
        )
        repo.record(
            AuditWrite(username=USER, status="accepted", agreed_with_rules=True)
        )
        repo.record(
            AuditWrite(username=USER, status="accepted", agreed_with_rules=False)
        )
        repo.record(AuditWrite(username=USER, status="rejected", reason="x"))
        db_session.commit()

        stats = repo.agreement_stats(days=7)
        assert stats["attempts"] == 4
        assert stats["accepted"] == 3 and stats["rejected"] == 1
        assert stats["agreement_rate"] == round(2 / 3, 3)
        assert stats["rejection_rate"] == 0.25

    def test_rates_are_none_rather_than_zero_when_nothing_has_run(self, db_session):
        """An absent rate and a zero rate mean very different things on a
        dashboard — zero would read as total disagreement."""
        stats = AIAuditRepository(db_session).agreement_stats()
        assert stats["attempts"] == 0
        assert stats["agreement_rate"] is None
        assert stats["rejection_rate"] is None

    def test_skipped_attempts_are_excluded(self, db_session):
        """Skips never called the model, so they cannot agree or disagree."""
        repo = AIAuditRepository(db_session)
        repo.record(AuditWrite(username=USER, status="skipped", reason="no_api_key"))
        db_session.commit()
        assert AIAuditRepository(db_session).agreement_stats()["attempts"] == 0
