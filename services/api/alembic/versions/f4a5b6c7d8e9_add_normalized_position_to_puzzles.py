"""add normalized_position to puzzles + unique manual-position index

Revision ID: f4a5b6c7d8e9
Revises: f3a4b5c6d7e8
Create Date: 2026-07-27 00:00:00.000000

Adds ``puzzles.normalized_position`` (the first four FEN fields: piece placement
+ side to move + castling + en passant, dropping the halfmove/fullmove counters)
and a partial UNIQUE index over (username, source_game_id, normalized_position)
scoped to manual (analysis-save) puzzles.

Manual puzzles were deduped by RAW FEN, so the same board reached via a different
move order (a transposition) had different counters, a different raw FEN, and
inserted a permanent duplicate. Keying on the position fixes that at the app
layer; this partial unique index closes the concurrent TOCTOU window at the DB so
a staggered same-position save cannot slip past the precheck.

Scoped to source_game_id = '__manual__' because only the synthetic manual
sequence keys off position; generation-path rows key off (game, ply) and
legitimately repeat a position across games. ``normalized_position IS NOT NULL``
leaves any pre-migration manual row (NULL key) exempt (NULLs are distinct in a
unique index), so the migration is safe on a populated table. Manual puzzles are
new in this release, so in practice the backfill touches zero rows and no
pre-existing duplicate positions can exist to block the unique index.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "puzzles",
        sa.Column("normalized_position", sa.Text(), nullable=True),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Backfill existing manual rows so they participate in dedup and are
        # covered by the unique index. WHERE ... IS NULL keeps this re-runnable.
        op.execute("""
            UPDATE puzzles
            SET normalized_position =
                array_to_string((string_to_array(fen, ' '))[1:4], ' ')
            WHERE source_game_id = '__manual__' AND normalized_position IS NULL
            """)
        # IF NOT EXISTS makes the index creation idempotent. Partial + scoped to
        # manual rows with a non-NULL key.
        op.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_puzzles_manual_position
            ON puzzles (username, source_game_id, normalized_position)
            WHERE source_game_id = '__manual__' AND normalized_position IS NOT NULL
            """)
    else:
        # Non-Postgres (e.g. a SQLite dev DB). CI runs migrations only on
        # Postgres; this keeps the migration usable elsewhere.
        op.create_index(
            "uq_puzzles_manual_position",
            "puzzles",
            ["username", "source_game_id", "normalized_position"],
            unique=True,
            sqlite_where=sa.text(
                "source_game_id = '__manual__' AND normalized_position IS NOT NULL"
            ),
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS uq_puzzles_manual_position")
    else:
        op.drop_index("uq_puzzles_manual_position", table_name="puzzles")
    op.drop_column("puzzles", "normalized_position")
