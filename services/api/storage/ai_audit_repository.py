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
from services.api.usernames import canonical_username

# Username convention: the ledger is read per user (``budget_last_24h``) and
# written per user (``record``). Both fold with ``canonical_username`` so a
# non-canonical handle cannot spend against an empty ledger and bypass the
# per-user daily cap. See ``services.api.usernames``.


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
    # diagnosis | naming. Defaulted so every existing caller keeps working and
    # keeps landing in the diagnosis ledger.
    call_type: str = "diagnosis"
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
    """A day's remaining allowance for one kind of AI call.

    The caps are carried on the instance rather than read from ``config`` in
    the properties, because diagnosis and naming have separate ceilings and a
    Budget must not be able to answer with the wrong one. They default to the
    diagnosis caps so existing construction sites are unchanged.
    """

    user_used: int
    global_used: int
    user_cap: int = config.DAILY_CAP_PER_USER
    global_cap: int = config.DAILY_CAP_GLOBAL

    @property
    def user_remaining(self) -> int:
        return max(0, self.user_cap - self.user_used)

    @property
    def global_remaining(self) -> int:
        return max(0, self.global_cap - self.global_used)

    @property
    def remaining(self) -> int:
        return min(self.user_remaining, self.global_remaining)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def spend(self, n: int = 1) -> "Budget":
        """Account for a call locally between database re-reads."""
        return Budget(
            self.user_used + n, self.global_used + n, self.user_cap, self.global_cap
        )


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
            # Folded so the row lands under the same key budget_last_24h counts.
            username=canonical_username(write.username),
            call_type=write.call_type,
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

    def budget_last_24h(self, username: str, call_type: str = "diagnosis") -> Budget:
        """Calls billed in the last rolling 24 hours, per-user and globally.

        Rolling rather than calendar-day on purpose: a calendar reset gives a
        midnight cliff where a capped backfill suddenly gets a full new
        allowance, which is exactly when nobody is watching.

        Only ``accepted`` and ``rejected`` rows count: both made a model call
        and cost money. ``skipped`` never called, and ``error`` covers failures
        that were not billed (network, auth) — counting either would let a
        provider outage consume the day's allowance.

        Scoped to one ``call_type``. Diagnosis and naming hold separate
        ledgers, so a bulk naming backfill cannot spend the allowance that
        keeps per-page diagnosis working.
        """
        since = _utcnow_naive() - timedelta(days=1)
        billable = DiagnosisAuditLog.status.in_(("accepted", "rejected"))
        of_type = DiagnosisAuditLog.call_type == call_type

        user_used = (
            self.db.scalar(
                select(func.count())
                .select_from(DiagnosisAuditLog)
                .where(
                    DiagnosisAuditLog.username == canonical_username(username),
                    DiagnosisAuditLog.created_at >= since,
                    billable,
                    of_type,
                )
            )
            or 0
        )
        global_used = (
            self.db.scalar(
                select(func.count())
                .select_from(DiagnosisAuditLog)
                .where(DiagnosisAuditLog.created_at >= since, billable, of_type)
            )
            or 0
        )
        caps = (
            (config.NAMING_DAILY_CAP_PER_USER, config.NAMING_DAILY_CAP_GLOBAL)
            if call_type == "naming"
            else (config.DAILY_CAP_PER_USER, config.DAILY_CAP_GLOBAL)
        )
        return Budget(
            user_used=user_used,
            global_used=global_used,
            user_cap=caps[0],
            global_cap=caps[1],
        )

    def failing_streak(self, username: str, call_type: str, within: timedelta) -> int:
        """Failed calls since the last one the model actually answered.

        The counterpart to :meth:`budget_last_24h`, and needed for the same
        reason that method exists: the daily cap is what stops a working
        provider being called too much, and this is what stops a *broken* one
        being called too often. An ``error`` row is not billed, so the budget
        never engages during an outage — the ledger has to answer "is this
        provider currently down?" some other way, and the streak is it.

        Measured since the last ``accepted``/``rejected`` row rather than over a
        fixed window, so a run that half-succeeded before the provider died
        reports the failures that followed rather than being excused by the
        successes that preceded them. Any answer at all resets it to zero, which
        is what makes recovery automatic: nothing has to remember to close a
        breaker that a working call closes by itself.

        ``skipped`` counts as a failure here, not just ``error``. Only ``error``
        did, and that left three ways to spin: a missing API key, an exhausted
        daily cap, and a puzzle at the head of the batch that is skipped every
        run. Each writes ``skipped``, which neither tripped this breaker nor
        reduced ``naming_pass.pending_count`` — so the worker re-queued, the
        pass skipped again, and the cycle repeated every ~2 seconds with no
        spend to bound it, because a skip is not billed. A skip means "this
        cannot proceed right now", which is exactly the condition a breaker is
        for; whether the model refused or was never asked does not change that.

        Failures older than ``within`` report 0. Without that a streak would
        latch — the only thing that clears it is a successful call, and the
        caller's whole purpose is to stop making calls.

        Returns 0 rather than raising for a user with no rows at all.
        """
        scope = (
            DiagnosisAuditLog.username == canonical_username(username),
            DiagnosisAuditLog.call_type == call_type,
        )

        last_answer = self.db.scalar(
            select(func.max(DiagnosisAuditLog.created_at)).where(
                *scope, DiagnosisAuditLog.status.in_(("accepted", "rejected"))
            )
        )

        # MAX rather than an ORDER BY over the rows: a pass writes its rows in
        # one transaction, and two of them can land on the same microsecond, so
        # "the last N rows" is not a well-defined set. An aggregate is.
        streak = select(func.count(), func.max(DiagnosisAuditLog.created_at)).where(
            *scope, DiagnosisAuditLog.status.in_(("error", "skipped"))
        )
        if last_answer is not None:
            streak = streak.where(DiagnosisAuditLog.created_at > last_answer)

        failures, newest = self.db.execute(streak).one()
        if not failures or newest is None:
            return 0
        if newest < _utcnow_naive() - within:
            return 0
        return int(failures)

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
                DiagnosisAuditLog.created_at >= since,
                # Diagnosis only. A name has no rules ranking to agree with, so
                # naming rows would dilute the agreement rate toward zero and
                # make the regression signal unreadable.
                DiagnosisAuditLog.call_type == "diagnosis",
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
