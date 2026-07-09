"""add fen_eval_cache

Revision ID: ab235ce26ed5
Revises: 35523bdeba3e
Create Date: 2026-01-31 11:48:13.234686

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ab235ce26ed5"
down_revision: Union[str, Sequence[str], None] = "35523bdeba3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "fen_eval_cache" not in inspector.get_table_names():
        # Fresh database (e.g. Postgres/Supabase) — create the table from scratch.
        op.create_table(
            "fen_eval_cache",
            sa.Column("key", sa.String(), primary_key=True, nullable=False),
            sa.Column("fen", sa.Text(), nullable=False),
            sa.Column("best_move_uci", sa.Text(), nullable=False),
            sa.Column("eval_pawns", sa.Float(), nullable=False),
            sa.Column("depth", sa.Integer(), nullable=True),
            sa.Column("movetime_ms", sa.Integer(), nullable=True),
            sa.Column("engine_name", sa.Text(), nullable=True),
            sa.Column("engine_version", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    else:
        # Existing SQLite database — alter the columns that were created
        # outside of Alembic with slightly different types.
        with op.batch_alter_table("fen_eval_cache", schema=None) as batch_op:
            batch_op.alter_column(
                "key", existing_type=sa.TEXT(), type_=sa.String(), nullable=False
            )
            batch_op.alter_column(
                "eval_pawns",
                existing_type=sa.REAL(),
                type_=sa.Float(),
                existing_nullable=False,
            )
            batch_op.alter_column(
                "created_at",
                existing_type=sa.TIMESTAMP(),
                type_=sa.DateTime(),
                existing_nullable=False,
                existing_server_default=sa.text("(CURRENT_TIMESTAMP)"),
            )
            batch_op.drop_index("idx_fen_eval_cache_created_at")
            batch_op.drop_index("idx_fen_eval_cache_fen")


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "fen_eval_cache" in inspector.get_table_names():
        # If the table was created fresh by upgrade(), just drop it.
        # Otherwise restore the old indexes/types.
        indexes = [i["name"] for i in inspector.get_indexes("fen_eval_cache")]

        if "idx_fen_eval_cache_fen" not in indexes:
            # Table was created fresh — drop it entirely.
            op.drop_table("fen_eval_cache")
        else:
            with op.batch_alter_table("fen_eval_cache", schema=None) as batch_op:
                batch_op.create_index("idx_fen_eval_cache_fen", ["fen"], unique=False)
                batch_op.create_index(
                    "idx_fen_eval_cache_created_at", ["created_at"], unique=False
                )
                batch_op.alter_column(
                    "created_at",
                    existing_type=sa.DateTime(),
                    type_=sa.TIMESTAMP(),
                    existing_nullable=False,
                    existing_server_default=sa.text("(CURRENT_TIMESTAMP)"),
                )
                batch_op.alter_column(
                    "eval_pawns",
                    existing_type=sa.Float(),
                    type_=sa.REAL(),
                    existing_nullable=False,
                )
                batch_op.alter_column(
                    "key", existing_type=sa.String(), type_=sa.TEXT(), nullable=True
                )
