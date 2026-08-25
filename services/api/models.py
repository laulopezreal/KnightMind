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
    UniqueConstraint,
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


class JobType(str, Enum):
    """What a background job actually does.

    Lives here rather than in ``worker`` so the model, the API layer and the
    worker all name the same constant; ``worker`` imports models, so the
    dependency can only point this way.

    Every value must have a handler registered in ``worker.JOB_HANDLERS`` — a
    job whose type has no handler is failed explicitly rather than silently
    running someone else's work.
    """

    PUZZLE_GENERATION = "puzzle_generation"
    DIAGNOSIS = "diagnosis"


class PuzzleResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # One active job per (username, TYPE). Scoping by type is what lets an
        # analysis job run alongside a puzzle generation for the same user
        # while still guaranteeing a user can never have two concurrent jobs of
        # the *same* kind — the invariant this index has always been for.
        # Widening a unique index is strictly permissive, so no existing row
        # can conflict with it.
        Index(
            "ix_jobs_active_username",
            "username",
            "type",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        {"extend_existing": True},
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # NOT NULL since the table was created, so the composite index above can
    # never be defeated by a NULL (NULLs compare distinct in a unique index).
    type: Mapped[str] = mapped_column(
        String, default=JobType.PUZZLE_GENERATION, nullable=False
    )
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
    # Client-observability columns: record which browser tab last polled this
    # job and when, and whether the tab's stall detector ever fired. These are
    # purely additive observability markers — they do not affect job lifecycle.
    # All three nullable so pre-migration rows and jobs that were never observed
    # by a tab-aware client are represented without a backfill.
    client_id: Mapped[str | None] = mapped_column(String, nullable=True)
    client_last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    stall_reported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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


# The per-user title uniqueness index, named once so the model, the code that
# recognises its IntegrityError (services.api.puzzles.title_registry) and the
# tests all mean the same object. The migration that creates it spells the name
# out literally, as migrations must.
PUZZLE_TITLE_UNIQUE_INDEX = "uq_puzzle_stats_username_title"


class PuzzleStats(Base):
    __tablename__ = "puzzle_stats"
    __table_args__ = (
        Index(
            "ix_puzzle_stats_tricky_puzzles",
            "username",
            "fail_count",
            "last_reviewed_at",
        ),
        # A title is a display name, and a library that shows the same name
        # twice cannot be navigated: "The Missed Win" appeared 103 times for one
        # user. Application code deduplicated within a single naming pass, which
        # says nothing about the pass before it — so uniqueness has to be a
        # property of the table, not of a run.
        #
        # Scoped to the user, not global. Two users independently reaching the
        # same fork on f7 SHOULD both get "The f7 Knight Fork"; making that
        # collide would let one tenant's corpus rename another's.
        #
        # Not partial, and that matters: Postgres treats NULLs as distinct in a
        # unique index, so the untitled rows (a stats row written before its
        # name was computed) stay unconstrained for free.
        Index(
            PUZZLE_TITLE_UNIQUE_INDEX,
            "username",
            "title",
            unique=True,
        ),
        {"extend_existing": True},
    )

    puzzle_id: Mapped[str] = mapped_column(
        String, ForeignKey("puzzles.id"), primary_key=True
    )
    username: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String, nullable=True)
    # Where ``title`` came from: motif | position | ai | user.
    #
    # Without this, "has a title" was the only signal available, and every
    # puzzle has one — the creation path always writes a generated title. Code
    # that wanted to mean "the user named this, leave it alone" could only test
    # for non-NULL, which is true for all of them. This column is what makes
    # "never overwrite a name the user chose" expressible.
    title_source: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_motif: Mapped[str] = mapped_column(String, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    pass_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reviewed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_result: Mapped[str] = mapped_column(String, nullable=True)
    next_due_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=True)
    ease_factor: Mapped[float] = mapped_column(Float, default=2.0)


class DiagnosisStatus(str, Enum):
    """Whether a diagnosis could be produced at all.

    ``UNAVAILABLE`` rows exist on purpose: a puzzle whose stored move is
    illegal for its FEN can never be analysed, and without a row recording
    that, every backfill run would re-attempt it forever. The row is the
    negative result, not the absence of one.
    """

    OK = "ok"
    UNAVAILABLE = "unavailable"


class PuzzleDiagnosis(Base):
    """Why the user probably made this mistake, and the facts behind that.

    Keyed on (puzzle_id, username). This is deliberately *stronger* than
    :class:`PuzzleStats`, which is keyed on puzzle_id alone: a puzzle id already
    belongs to exactly one user by construction, so the username adds no rows,
    but putting it in the key makes a cross-tenant row unrepresentable rather
    than merely unlikely.

    Staleness is a predicate over the version columns rather than a flag: a row
    is stale when ``extraction_version`` or ``rule_version`` no longer match the
    code, or when re-extraction yields a different ``evidence_hash``. That is
    also the backfill's resume cursor — a crashed or budget-exhausted run
    re-queries and continues, with no separate progress state to drift out of
    sync with reality.
    """

    __tablename__ = "puzzle_diagnoses"
    __table_args__ = (
        # Drives the Insights "top mistake causes" aggregate and the Library
        # cause filter.
        Index("ix_puzzle_diagnoses_username_cause", "username", "primary_cause"),
        # Drives the backfill's "what still needs work" scan.
        Index(
            "ix_puzzle_diagnoses_username_versions",
            "username",
            "extraction_version",
            "rule_version",
        ),
        # Drives the Library opening/opening_line filters. Partial because most
        # rows have no opening attributed and only the non-NULL ones are ever
        # searched. Created in e8f9a0b1c2d3 but never declared here, so tests'
        # create_all() built a schema without it and planned differently to prod.
        Index(
            "ix_puzzle_diagnoses_username_opening_name",
            "username",
            "opening_name",
            postgresql_where=text("opening_name IS NOT NULL"),
        ),
        {"extend_existing": True},
    )

    puzzle_id: Mapped[str] = mapped_column(
        String, ForeignKey("puzzles.id"), primary_key=True
    )
    username: Mapped[str] = mapped_column(String, primary_key=True, index=True)

    status: Mapped[str] = mapped_column(
        String, nullable=False, default=DiagnosisStatus.OK
    )
    # Why extraction failed, for UNAVAILABLE rows. Never shown to the user.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Denormalised from PuzzleStats so the detail read is a single row lookup.
    primary_motif: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_cause: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    secondary_causes: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    # The winning rule's hand-assigned strength. Deliberately NOT named
    # "confidence": it is an ordering prior, not a calibrated probability, and
    # must never reach the user as a percentage (see diagnosis/causes.py).
    # A model confidence is a separate column added with the AI stage.
    primary_strength: Mapped[float | None] = mapped_column(Float, nullable=True)
    insufficient_evidence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    phase: Mapped[str | None] = mapped_column(String, nullable=True)
    # Needs an ECO mapping that does not exist yet; NULL until then rather than
    # guessed at.
    opening_family: Mapped[str | None] = mapped_column(String, nullable=True)
    # The full line and its ECO code, beside the family. All three are derived
    # from one classification pass, so they cannot disagree about which opening
    # the game played.
    opening_name: Mapped[str | None] = mapped_column(String, nullable=True)
    opening_eco: Mapped[str | None] = mapped_column(String, nullable=True)

    # The citable facts (id/label/value), i.e. exactly what the UI renders and
    # what an AI citation is validated against. The full packet is not stored:
    # evidence_hash already pins its identity, and the packet is reproducible
    # from the puzzle at a given extraction_version.
    evidence_json: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    evidence_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    # "rules" today; "llm" once the AI stage ranks and writes prose. Never
    # blended — a row says which produced it.
    source: Mapped[str] = mapped_column(String, nullable=False, default="rules")
    extraction_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)

    # Populated by the AI stage; NULL for a rules-only diagnosis.
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    training_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The model's own confidence, 0-1. Deliberately separate from
    # primary_strength: that is a hand-assigned rule prior, this is what the
    # model reported. Conflating them would let a rule ordering masquerade as a
    # calibrated probability. NULL on a rules-only row.
    model_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # True when the model's primary cause matched the rules' top pick. Rolled up
    # on /ops/status — the earliest signal that a prompt or model change
    # regressed. NULL when no model call was accepted.
    agreed_with_rules: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Manual correction. Kept alongside the computed cause rather than
    # overwriting it, so rule accuracy stays measurable against user feedback.
    user_confirmed_cause: Mapped[str | None] = mapped_column(String, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class DiagnosisAuditLog(Base):
    """One row per AI diagnosis attempt — accepted, rejected, or failed.

    Kept out of ``puzzle_diagnoses`` on purpose: that table is read on every
    puzzle-detail page load, and prompt/response blobs have no business on a hot
    read path.

    The table does double duty as the **spend ledger**. Counting today's rows is
    how the daily caps are enforced, which means the budget survives a process
    restart without a separate counter that could drift from what was actually
    called.

    Swept after ``AUDIT_RETENTION_DAYS`` by the existing session-cleanup loop.
    """

    __tablename__ = "diagnosis_audit_log"
    __table_args__ = (
        # The retention sweep and the daily spend count both scan on time.
        Index("ix_diagnosis_audit_created_at", "created_at"),
        # Per-user daily spend.
        Index("ix_diagnosis_audit_username_created", "username", "created_at"),
        # The per-type spend count filters on call_type as well, which the two
        # indexes above do not cover.
        Index("ix_diagnosis_audit_call_type_created", "call_type", "created_at"),
        {"extend_existing": True},
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    puzzle_id: Mapped[str | None] = mapped_column(String, nullable=True)
    username: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Which kind of model call this row records: diagnosis | naming.
    #
    # The table doubles as the spend ledger, so without a discriminator a
    # naming backfill would silently consume the diagnosis budget and land in
    # agreement_stats, where agreed_with_rules is meaningless for a name.
    call_type: Mapped[str] = mapped_column(
        String, nullable=False, default="diagnosis", server_default="diagnosis"
    )

    # accepted | rejected | skipped | error
    status: Mapped[str] = mapped_column(String, nullable=False)
    # Why a response was rejected, or why a call failed. The interesting rows:
    # this is the debugging corpus for prompt and model regressions.
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    agreed_with_rules: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extraction_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The prompt is reproducible from the packet plus these versions, so only
    # its hash is kept — the full text would multiply the table for no gain.
    prompt_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    # Truncated at AUDIT_RESPONSE_MAX_CHARS; the flag records that it happened
    # so a debugger is never misled by a silently clipped payload.
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )


class PuzzleReview(Base):
    __tablename__ = "puzzle_reviews"
    __table_args__ = (
        Index(
            "ix_puzzle_reviews_username_context_reviewed_at",
            "username",
            "review_context",
            "reviewed_at",
        ),
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
    # The server-owned training mode that recorded this event.  It is telemetry,
    # not a client claim, and preserves the distinction between an ordinary
    # spaced-repetition review and extra practice against a future-scheduled
    # position.
    review_context: Mapped[str] = mapped_column(
        String, nullable=False, default="standard", server_default="standard"
    )
    # Whether this event was allowed to change the ordinary scheduling state.
    # Focus-practice rows can be verified and useful without re-anchoring a
    # future puzzle's interval.
    affects_scheduling: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


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
        # Declared as the named UNIQUE CONSTRAINT the deployed schema actually
        # has (created in b7a2c7d2b7c9), not an equivalent unique Index. Both
        # enforce the same rule, but the mismatch made `alembic check` report
        # perpetual drift, and renaming it in the DB would mean dropping and
        # rebuilding a uniqueness guarantee that the review path's
        # IntegrityError-replay backstop depends on.
        UniqueConstraint(
            "username",
            "source_game_id",
            "ply",
            name="uq_puzzles_username_source_game_id_ply",
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


class OpeningExplorerCache(Base):
    """Opening-explorer aggregates for one position and rating band.

    Shared across users and not derived from anyone's games: the row is a fact
    about a position in a public database, so one user's lookup answers
    everyone else's. That is the point — it keeps a page that shows a baseline
    on every selection from making an outbound call per selection.

    ``key`` folds in the scheme version, speeds and rating band (see
    ``explorer.cache_key``), so a change to what we ask for cannot read back
    rows that answered a different question.
    """

    __tablename__ = "opening_explorer_cache"
    __table_args__ = {"extend_existing": True}

    key: Mapped[str] = mapped_column(String, primary_key=True)
    # Stored beside the key so a row is self-describing when read by hand.
    epd: Mapped[str] = mapped_column(Text, nullable=False)
    white: Mapped[int] = mapped_column(Integer, nullable=False)
    draws: Mapped[int] = mapped_column(Integer, nullable=False)
    black: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )


class WorkerHeartbeat(Base):
    """Liveness for a job worker that no longer runs inside the API.

    Before the worker was extracted, ``/ops/health`` answered "is the worker
    up?" from an in-process flag. Once it runs in its own container the API
    cannot see it at all -- and the naive version of that move leaves the API
    reporting ``disabled`` (because KNIGHTMIND_WORKER_DISABLED is set on the API)
    while the worker is running fine next door. The deploy gate probes that same
    endpoint, so worker health would have gone dark exactly where it is checked.

    One row per worker, rewritten on its own timer (not per job-loop
    iteration -- a long job would otherwise look like death). Staleness, not
    presence, is what makes it meaningful: a crashed worker stops updating and
    its row ages out, which is the same mechanism the per-job ``heartbeat_at``
    lease already uses for crash recovery.
    """

    __tablename__ = "worker_heartbeats"
    __table_args__ = {"extend_existing": True}

    # Identifies the process. Defaults to the container hostname, so a second
    # worker replica gets its own row rather than fighting over one.
    worker_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Naive UTC defaults, matching the column. An AWARE default on a naive
    # column is the same anti-pattern the heartbeat write had: Postgres casts it
    # through the session TimeZone and stores local time. Production supplies
    # both explicitly, but any ORM-constructed row would reproduce it.
    started_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
    )
    beat_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
    )


class RateLimitHit(Base):
    """One recorded request against a rate-limited route, shared across processes.

    The in-process limiter is correct for a single uvicorn worker and wrong the
    moment there are two: each process keeps its own window, so the effective
    limit multiplies by the worker count. That is the constraint that kept the
    API pinned to ``--workers 1``.

    A row per hit rather than a counter per window: it preserves the in-process
    limiter's sliding-window-log semantics exactly, so both stores answer
    identically. A fixed-window counter would be one cheap upsert instead of
    three statements, but it lets a caller spend the whole limit at the end of
    one window and again at the start of the next -- double the intended rate,
    precisely at the burst these routes are protected from.

    Volume is negligible: every limited route is an expensive one, capped
    between 5 and 60 requests a minute.
    """

    __tablename__ = "rate_limit_hits"
    __table_args__ = (
        # The only query shape: hits for one key inside a window, and the sweep
        # of everything already aged out.
        Index("ix_rate_limit_hits_key_hit_at", "key", "hit_at"),
        {"extend_existing": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # "<limiter name>:<principal>" -- the account when auth is on, else client IP.
    key: Mapped[str] = mapped_column(String, nullable=False)
    hit_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
