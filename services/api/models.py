import uuid
from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from services.api.db import Base


class Account(Base):
    """A KnightMind end-user identity (the authentication principal).

    Authenticates with email + password (argon2 hash) and receives a signed JWT
    bearer token. Distinct from a Chess.com username, which is the *data* tenancy
    key: an account claims one or more Chess.com usernames via
    ``account_chess_usernames``.
    """

    __tablename__ = "accounts"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )


class AccountChessUsername(Base):
    """Ownership link: which Chess.com usernames an account may access.

    ``UNIQUE(username)`` is the load-bearing invariant — it makes
    "first-importer-wins" a database guarantee: a Chess.com handle is owned by at
    most one account. An account may own several handles.
    """

    __tablename__ = "account_chess_usernames"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    account_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Lowercased, matches games.username / puzzles.username / etc.
    username: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, index=True
    )
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )


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
            "ix_jobs_active_username",
            "username",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
        {"extend_existing": True},
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    type: Mapped[str] = mapped_column(String, default="puzzle_generation")
    username: Mapped[str] = mapped_column(String, index=True)
    params: Mapped[dict] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String, default=JobStatus.QUEUED
    )  # Using Enum default
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    # Liveness lease, decoupled from status-write timestamps. Set at claim and
    # bumped by the worker's per-game heartbeat; crash recovery keys on this
    # (not updated_at) so an ordinary status write can never look like liveness.
    # Nullable so pre-migration rows exist as NULL; recovery falls back to
    # updated_at/created_at for those.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FenEvalCache(Base):
    __tablename__ = "fen_eval_cache"
    __table_args__ = {"extend_existing": True}

    key: Mapped[str] = mapped_column(String, primary_key=True)
    fen: Mapped[str] = mapped_column(Text, nullable=False)
    best_move_uci: Mapped[str] = mapped_column(Text, nullable=False)
    eval_pawns: Mapped[float] = mapped_column(Float, nullable=False)
    # Signed distance-to-mate from the side-to-move perspective (see
    # EvalResult.mate_in). NULL for ordinary centipawn evals. Persisted so a
    # cache HIT returns the same shape as a fresh compute -- previously a cached
    # mate lost its distance-to-mate on read (it defaulted back to None).
    mate_in: Mapped[int] = mapped_column(Integer, nullable=True)
    # True when the stored position is itself game-over. Terminal positions are
    # intentionally NOT cached today (best_move_uci is NOT NULL), so in practice
    # this is always False for stored rows; it is persisted for shape parity so
    # a cache read reconstructs the full EvalResult.
    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=True)
    movetime_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    engine_name: Mapped[str] = mapped_column(Text, nullable=True)
    engine_version: Mapped[str] = mapped_column(Text, nullable=True)
    # Result-changing engine config, stored so a cache row is self-describing
    # and auditable. These are *also* folded into the primary ``key`` (see
    # engine.stockfish._compute_cache_key), so a change in any of them yields a
    # different key and old rows can never be reused under a new config.
    threads: Mapped[int] = mapped_column(Integer, nullable=True)
    hash_mb: Mapped[int] = mapped_column(Integer, nullable=True)
    multipv: Mapped[int] = mapped_column(Integer, nullable=True)
    # Version of the raw-eval -> (eval_pawns, mate_in) conversion used to write
    # this row; bumped when that mapping changes so stale conversions invalidate.
    conversion_version: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class PuzzleStats(Base):
    __tablename__ = "puzzle_stats"
    __table_args__ = (
        Index(
            "ix_puzzle_stats_tricky_puzzles",
            "username",
            "fail_count",
            "last_reviewed_at",
        ),
        {"extend_existing": True},
    )

    puzzle_id: Mapped[str] = mapped_column(
        String, ForeignKey("puzzles.id"), primary_key=True
    )
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
    __table_args__ = (
        # Idempotency: a client-supplied review key must be unique per
        # (puzzle, user, session) so a retried/double-submitted POST cannot be
        # recorded (and re-scheduled/re-counted) twice. Rows with a NULL
        # client_review_id are exempt (NULLs are distinct in a unique index),
        # preserving the legacy no-key behaviour.
        #
        # session_id is wrapped in COALESCE(session_id, '') so a NULL session
        # collapses to a single key value: a plain multi-column unique index
        # treats each NULL as distinct (SQLite and Postgres), which would let
        # two concurrent session-less submits with the same client_review_id
        # both insert and double-count. The COALESCE functional index closes
        # that hole while leaving no-key (NULL client_review_id) rows exempt.
        Index(
            "uq_puzzle_reviews_client_key",
            "puzzle_id",
            "username",
            text("coalesce(session_id, '')"),
            "client_review_id",
            unique=True,
        ),
        {"extend_existing": True},
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    puzzle_id: Mapped[str] = mapped_column(String, ForeignKey("puzzles.id"), index=True)
    username: Mapped[str] = mapped_column(String, index=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    # Authoritative outcome that drives scheduling/stats. Equals the
    # server-verified result when an attempted move was verified; otherwise it
    # falls back to the client-reported result (legacy / no-move flows).
    result: Mapped[PuzzleResult] = mapped_column(String)
    time_spent_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    session_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    client_review_id: Mapped[str] = mapped_column(String, nullable=True)
    # --- Training-integrity fields (audit gate 7) ---
    # The move the user actually played, in UCI. NULL for legacy/no-move
    # submissions (e.g. timeouts, "mark failed", revealed solutions).
    attempted_move: Mapped[str | None] = mapped_column(String, nullable=True)
    # The raw pass/fail the client claimed, preserved even when the server
    # overrides it (e.g. client says "pass" but the move was wrong). NULL for
    # pre-migration rows.
    client_result: Mapped[str | None] = mapped_column(String, nullable=True)
    # True only when the server independently verified the attempted move
    # against the puzzle's accepted-solution set. Self-reported reviews stay
    # False and MUST NOT be presented as verified skill.
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # How the recorded result was determined:
    #   "server_verified" — server checked the attempted move (verified True)
    #   "client_reported" — trust the client's pass/fail (no move to verify)
    # NULL for pre-migration rows.
    source: Mapped[str | None] = mapped_column(String, nullable=True)


class TrainingSession(Base):
    __tablename__ = "training_sessions"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    username: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    requested_n: Mapped[int] = mapped_column(Integer, nullable=False)
    pass_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    total_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    # Enhanced session fields
    session_type: Mapped[str] = mapped_column(
        String, nullable=True
    )  # "timed", "target_count", "accuracy_goal"
    target_accuracy: Mapped[float] = mapped_column(
        Float, nullable=True
    )  # Target accuracy percentage (0.0-100.0)
    target_time_minutes: Mapped[int] = mapped_column(
        Integer, nullable=True
    )  # Target session time in minutes
    current_streak: Mapped[int] = mapped_column(
        Integer, default=0
    )  # Current correct answer streak
    best_streak: Mapped[int] = mapped_column(
        Integer, default=0
    )  # Best streak in this session
    hints_used: Mapped[int] = mapped_column(
        Integer, default=0
    )  # Number of hints used in session
    session_data: Mapped[dict] = mapped_column(
        JSON, nullable=True
    )  # Flexible storage for session-specific data
    achievements: Mapped[list] = mapped_column(
        JSON, nullable=True, default=list
    )  # List of achievements earned in this session


class RatingSnapshot(Base):
    __tablename__ = "rating_snapshots"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    username: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, default="chesscom", nullable=False)
    time_control: Mapped[str] = mapped_column(
        String, nullable=False
    )  # "rapid", "blitz"
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    session_id: Mapped[str] = mapped_column(
        String, nullable=True, index=True
    )  # FK to training_sessions.id logical


class ImportSummary(Base):
    __tablename__ = "import_summaries"
    __table_args__ = {"extend_existing": True}

    username: Mapped[str] = mapped_column(String, primary_key=True)
    last_imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_new_games: Mapped[int] = mapped_column(Integer, default=0)


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        Index("ix_games_username_end_time", "username", "end_time"),
        Index("ix_games_game_id", "game_id"),
        {"extend_existing": True},
    )

    # Composite primary key: the same canonical game (game_id derived from the
    # url) can be owned by every participant who imports it, one row per user.
    game_id: Mapped[str] = mapped_column(String, primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    white_username: Mapped[str] = mapped_column(String, nullable=False)
    black_username: Mapped[str] = mapped_column(String, nullable=False)
    white_result: Mapped[str] = mapped_column(String, nullable=False)
    black_result: Mapped[str] = mapped_column(String, nullable=False)
    time_control: Mapped[str] = mapped_column(String, nullable=False)
    end_time: Mapped[int] = mapped_column(Integer, nullable=False)
    rated: Mapped[bool] = mapped_column(Boolean, default=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    pgn_blob: Mapped[str | None] = mapped_column(Text, nullable=True)


class Puzzle(Base):
    __tablename__ = "puzzles"
    __table_args__ = (
        Index("ix_puzzles_username_created_at", "username", "created_at"),
        Index(
            "ix_puzzles_username_source_game_id_ply",
            "username",
            "source_game_id",
            "ply",
            unique=True,
        ),
        # Idempotency backstop for manual (analysis-save) puzzles: two saves of
        # the SAME board position (see normalized_position — first four FEN
        # fields, so transpositions collapse to one key) must not both persist.
        # Scoped to MANUAL_GAME_ID because only the manual sequence keys off
        # position; generation-path rows key off (game, ply) and legitimately
        # repeat a position across games. normalized_position IS NOT NULL keeps
        # pre-migration manual rows (NULL key) exempt; the app-level precheck in
        # create_manual_puzzle handles transpositions, this closes the concurrent
        # TOCTOU window at the DB.
        Index(
            "uq_puzzles_manual_position",
            "username",
            "source_game_id",
            "normalized_position",
            unique=True,
            postgresql_where=text(
                "source_game_id = '__manual__' AND normalized_position IS NOT NULL"
            ),
            sqlite_where=text(
                "source_game_id = '__manual__' AND normalized_position IS NOT NULL"
            ),
        ),
        # A puzzle references the owning user's copy of the source game, so the
        # FK is composite now that games is keyed by (game_id, username).
        ForeignKeyConstraint(
            ["source_game_id", "username"],
            ["games.game_id", "games.username"],
            name="fk_puzzles_source_game",
        ),
        {"extend_existing": True},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    source_game_id: Mapped[str] = mapped_column(String, nullable=False)
    ply: Mapped[int] = mapped_column(Integer, nullable=False)
    fen: Mapped[str] = mapped_column(Text, nullable=False)
    # Position-identity key: the first four fields of ``fen`` (piece placement +
    # side to move + castling + en passant), dropping the halfmove/fullmove
    # counters. Two FENs for the same board reached via different move orders
    # (a transposition) share this key. Used to dedup manual puzzles by position
    # rather than by raw FEN (see storage.puzzle_repository.normalized_position
    # and the uq_puzzles_manual_position partial unique index). NULL for rows
    # written before this column existed.
    normalized_position: Mapped[str | None] = mapped_column(Text, nullable=True)
    side_to_move: Mapped[str] = mapped_column(String, nullable=False)
    played_move_uci: Mapped[str] = mapped_column(String, nullable=False)
    best_move_uci: Mapped[str] = mapped_column(String, nullable=False)
    # Comma-separated UCI moves that are all accepted as correct solutions
    # (the multi-PV equivalence set). Nullable for pre-existing rows; solvers
    # should fall back to best_move_uci when absent.
    accept_moves_uci: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The full solution line (principal variation) as space-separated UCI moves,
    # starting with the solution move (== best_move_uci). Persisted so the puzzle
    # trains the whole forcing combination move-by-move, not just move 1. NULL for
    # legacy rows generated before full-PV persistence — those train as a single
    # move exactly as before. Bounded length (see generator PUZZLE_PV_MAX_PLIES);
    # stops at mate/terminal.
    solution_pv: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval_before: Mapped[float] = mapped_column(Float, nullable=False)
    eval_after: Mapped[float] = mapped_column(Float, nullable=False)
    swing: Mapped[float] = mapped_column(Float, nullable=False)
    # Search depth at which the mistake+solution were CONFIRMED stable (the
    # generator's deeper confirmation pass). NULL for pre-confirmation rows, so
    # a puzzle's provenance is auditable: a solver/analyst can tell a puzzle
    # vetted at depth 18 from a legacy single-shallow-pass one. eval_before /
    # eval_after / swing / accept_moves_uci on a confirmed row reflect this
    # depth, not the shallow scan that first flagged the candidate.
    confirmed_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    used_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
