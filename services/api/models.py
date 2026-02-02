import uuid
from datetime import datetime, timezone, date
from sqlalchemy import String, Integer, Text, JSON, DateTime, Index, text, Float, Boolean, Date, ForeignKey
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
    __table_args__ = (
        Index("ix_puzzle_stats_tricky_puzzles", "username", "fail_count", "last_reviewed_at"),
        {"extend_existing": True},
    )

    puzzle_id: Mapped[str] = mapped_column(String, ForeignKey("puzzles.id"), primary_key=True)
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
    puzzle_id: Mapped[str] = mapped_column(String, ForeignKey("puzzles.id"), index=True)
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
    # Enhanced session fields
    session_type: Mapped[str] = mapped_column(String, nullable=True)  # "timed", "target_count", "accuracy_goal"
    target_accuracy: Mapped[float] = mapped_column(Float, nullable=True)  # Target accuracy percentage (0.0-100.0)
    target_time_minutes: Mapped[int] = mapped_column(Integer, nullable=True)  # Target session time in minutes
    current_streak: Mapped[int] = mapped_column(Integer, default=0)  # Current correct answer streak
    best_streak: Mapped[int] = mapped_column(Integer, default=0)  # Best streak in this session
    hints_used: Mapped[int] = mapped_column(Integer, default=0)  # Number of hints used in session
    session_data: Mapped[dict] = mapped_column(JSON, nullable=True)  # Flexible storage for session-specific data
    achievements: Mapped[list] = mapped_column(JSON, nullable=True, default=list)  # List of achievements earned in this session


class RatingSnapshot(Base):
    __tablename__ = "rating_snapshots"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, default="chesscom", nullable=False)
    time_control: Mapped[str] = mapped_column(String, nullable=False)  # "rapid", "blitz"
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=True, index=True)  # FK to training_sessions.id logical


class ProblemReport(Base):
    __tablename__ = "problem_reports"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    category: Mapped[str] = mapped_column(String, nullable=False)  # "bug", "feature", "feedback"
    description: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[str] = mapped_column(String, nullable=True)  # Route where report was filed
    username: Mapped[str] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        Index("ix_games_username_end_time", "username", "end_time"),
        Index("ix_games_game_id", "game_id"),
        {"extend_existing": True},
    )

    game_id: Mapped[str] = mapped_column(String, primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(String, index=True)
    white_username: Mapped[str] = mapped_column(String, nullable=False)
    black_username: Mapped[str] = mapped_column(String, nullable=False)
    white_result: Mapped[str] = mapped_column(String, nullable=False)
    black_result: Mapped[str] = mapped_column(String, nullable=False)
    time_control: Mapped[str] = mapped_column(String, nullable=False)
    end_time: Mapped[int] = mapped_column(Integer, nullable=False)
    rated: Mapped[bool] = mapped_column(Boolean, default=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    pgn_blob: Mapped[str | None] = mapped_column(Text, nullable=True)


class Puzzle(Base):
    __tablename__ = "puzzles"
    __table_args__ = (
        Index("ix_puzzles_username_created_at", "username", "created_at"),
        Index("ix_puzzles_username_source_game_id_ply", "username", "source_game_id", "ply", unique=True),
        {"extend_existing": True},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    source_game_id: Mapped[str] = mapped_column(String, ForeignKey("games.game_id"), nullable=False)
    ply: Mapped[int] = mapped_column(Integer, nullable=False)
    fen: Mapped[str] = mapped_column(Text, nullable=False)
    side_to_move: Mapped[str] = mapped_column(String, nullable=False)
    played_move_uci: Mapped[str] = mapped_column(String, nullable=False)
    best_move_uci: Mapped[str] = mapped_column(String, nullable=False)
    eval_before: Mapped[float] = mapped_column(Float, nullable=False)
    eval_after: Mapped[float] = mapped_column(Float, nullable=False)
    swing: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    used_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
