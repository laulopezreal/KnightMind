"""Audit trail and spend ledger for AI diagnosis.

One table serves both jobs deliberately. Counting today's audit rows *is* the
budget check, so the daily cap survives a process restart and can never drift
from what was actually called — which a separate in-memory counter would.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.api.ai import config
from services.api.models import DiagnosisAuditLog


def _utcnow_naive() -> datetime:
    """Naive UTC, matching the DateTime columns elsewhere in the schema.

    An aware value would compare wrongly on Postgres under a non-UTC session
    TimeZone — the same trap documented in ``storage/spaced_repetition.py``.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class AuditWrite:
    username: str
    status: str
    puzzle_id: str | None = None
    reason: str | None = None
    agreed_with_rules: bool | None = None
    model_version: str | None = None
    rule_version: int | None = None
    extraction_version: int | None = None
    prompt_hash: str | None = None
    evidence_hash: str | None = None
    response_json: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Budget:
    """A day's remaining AI allowance."""

    user_used: int
    global_used: int

    @property
    def user_remaining(self) -> int:
        return max(0, config.DAILY_CAP_PER_USER - self.user_used)

    @property
    def global_remaining(self) -> int:
        return max(0, config.DAILY_CAP_GLOBAL - self.global_used)

    @property
    def remaining(self) -> int:
        return min(self.user_remaining, self.global_remaining)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def spend(self, n: int = 1) -> "Budget":
        """Account for a call locally between database re-reads."""
        return Budget(self.user_used + n, self.global_used + n)


class AIAuditRepository:
    def __init__(self, db: Session):
        self.db = db

    def record(self, write: AuditWrite) -> DiagnosisAuditLog:
        """Persist one attempt — accepted, rejected, skipped, or errored.

        Rejections are the rows that matter most: they are the debugging corpus
        for a prompt or model regression, and the only place a hallucinated
        cause is preserved after being refused.
        """
        response = write.response_json
        truncated = False
        if response and len(response) > config.AUDIT_RESPONSE_MAX_CHARS:
            response = response[: config.AUDIT_RESPONSE_MAX_CHARS]
            truncated = True

        row = DiagnosisAuditLog(
            username=write.username,
            puzzle_id=write.puzzle_id,
            status=write.status,
            reason=write.reason,
            agreed_with_rules=write.agreed_with_rules,
            model_version=write.model_version,
            rule_version=write.rule_version,
            extraction_version=write.extraction_version,
            prompt_hash=write.prompt_hash,
            evidence_hash=write.evidence_hash,
            response_json=response,
            response_truncated=truncated,
            input_tokens=write.input_tokens,
            output_tokens=write.output_tokens,
        )
        self.db.add(row)
        return row

    def budget_today(self, username: str) -> Budget:
        """Calls already billed today, per-user and globally.

        Only ``accepted`` and ``rejected`` rows count: both made a model call
        and cost money. ``skipped`` never called, and ``error`` covers failures
        that were not billed (network, auth) — counting either would let a
        provider outage consume the day's allowance.
        """
        since = _utcnow_naive() - timedelta(days=1)
        billable = DiagnosisAuditLog.status.in_(("accepted", "rejected"))

        user_used = (
            self.db.scalar(
                select(func.count())
                .select_from(DiagnosisAuditLog)
                .where(
                    DiagnosisAuditLog.username == username,
                    DiagnosisAuditLog.created_at >= since,
                    billable,
                )
            )
            or 0
        )
        global_used = (
            self.db.scalar(
                select(func.count())
                .select_from(DiagnosisAuditLog)
                .where(DiagnosisAuditLog.created_at >= since, billable)
            )
            or 0
        )
        return Budget(user_used=user_used, global_used=global_used)

    def purge_older_than(self, days: int | None = None) -> int:
        """Delete audit rows past the retention window. Returns rows removed."""
        cutoff = _utcnow_naive() - timedelta(
            days=days if days is not None else config.AUDIT_RETENTION_DAYS
        )
        rows = self.db.query(DiagnosisAuditLog).filter(
            DiagnosisAuditLog.created_at < cutoff
        )
        removed = rows.delete(synchronize_session=False)
        return removed or 0

    def agreement_stats(self, days: int = 7) -> dict:
        """Rolling rule/model agreement, for /ops/status.

        This metric is why the feature can ship with the flag ON: without a
        dark-launch period it is the earliest signal that a prompt or model
        change regressed. A falling agreement rate, or a rising rejection rate,
        means look at the audit rows.
        """
        since = _utcnow_naive() - timedelta(days=days)
        rows = self.db.execute(
            select(DiagnosisAuditLog.status, DiagnosisAuditLog.agreed_with_rules).where(
                DiagnosisAuditLog.created_at >= since
            )
        ).all()

        accepted = [r for r in rows if r.status == "accepted"]
        rejected = [r for r in rows if r.status == "rejected"]
        agreed = [r for r in accepted if r.agreed_with_rules]
        attempts = len(accepted) + len(rejected)

        return {
            "window_days": days,
            "attempts": attempts,
            "accepted": len(accepted),
            "rejected": len(rejected),
            # None rather than 0.0 when nothing has run: an absent rate and a
            # zero rate mean very different things on a dashboard.
            "agreement_rate": (
                round(len(agreed) / len(accepted), 3) if accepted else None
            ),
            "rejection_rate": (
                round(len(rejected) / attempts, 3) if attempts else None
            ),
        }


def prompt_hash(text: str) -> str:
    """Hash of the rendered prompt.

    The prompt is reproducible from the packet plus the version columns, so the
    hash is enough to tell whether two attempts used the same input — storing
    the full text would multiply the table for no diagnostic gain.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
