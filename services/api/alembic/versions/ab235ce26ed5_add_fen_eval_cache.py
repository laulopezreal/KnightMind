"""add fen_eval_cache

Revision ID: ab235ce26ed5
Revises: 35523bdeba3e
Create Date: 2026-01-31 11:48:13.234686

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ab235ce26ed5'
down_revision: Union[str, Sequence[str], None] = '35523bdeba3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('fen_eval_cache',
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('fen', sa.Text(), nullable=False),
        sa.Column('best_move_uci', sa.Text(), nullable=False),
        sa.Column('eval_pawns', sa.Float(), nullable=False),
        sa.Column('depth', sa.Integer(), nullable=True),
        sa.Column('movetime_ms', sa.Integer(), nullable=True),
        sa.Column('engine_name', sa.Text(), nullable=True),
        sa.Column('engine_version', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('key')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('fen_eval_cache')
