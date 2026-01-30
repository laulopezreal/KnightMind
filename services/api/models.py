import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Text, JSON, DateTime, Index, text, Float
from sqlalchemy.orm import Mapped, mapped_column
from enum import Enum
from services.api.db import Base

class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"

class PuzzleResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"

class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            'ix_jobs_active_username',
            'username',
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')")
        ),
        {"extend_existing": True},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    type: Mapped[str] = mapped_column(String, default="puzzle_generation")
    username: Mapped[str] = mapped_column(String, index=True)
    params: Mapped[dict] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String, default=JobStatus.QUEUED)  # Using Enum default
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, 
        default=lambda: datetime.now(timezone.utc), 
        onupdate=lambda: datetime.now(timezone.utc)
    )


class FenEvalCache(Base):
    __tablename__ = "fen_eval_cache"
    __table_args__ = {"extend_existing": True}

    key: Mapped[str] = mapped_column(String, primary_key=True)
    fen: Mapped[str] = mapped_column(Text, nullable=False)
    best_move_uci: Mapped[str] = mapped_column(Text, nullable=False)
    eval_pawns: Mapped[float] = mapped_column(Float, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=True)
    movetime_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    engine_name: Mapped[str] = mapped_column(Text, nullable=True)
    engine_version: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class PuzzleStats(Base):
    __tablename__ = "puzzle_stats"
    __table_args__ = {"extend_existing": True}

    puzzle_id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String, nullable=True)
    primary_motif: Mapped[str] = mapped_column(String, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    pass_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reviewed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_result: Mapped[str] = mapped_column(String, nullable=True)
    next_due_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=True)
    ease_factor: Mapped[float] = mapped_column(Float, default=2.0)


class PuzzleReview(Base):
    __tablename__ = "puzzle_reviews"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    puzzle_id: Mapped[str] = mapped_column(String, index=True)
    username: Mapped[str] = mapped_column(String, index=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    result: Mapped[PuzzleResult] = mapped_column(String)
    time_spent_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    session_id: Mapped[str] = mapped_column(String, nullable=True, index=True)


class TrainingSession(Base):
    __tablename__ = "training_sessions"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    requested_n: Mapped[int] = mapped_column(Integer, nullable=False)
    pass_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    total_time_ms: Mapped[int] = mapped_column(Integer, default=0)
