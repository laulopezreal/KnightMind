"""create_puzzle_reviews_and_stats

Revision ID: 9a8d0c56273f
Revises: ee3cd9312dc0
Create Date: 2026-01-30 15:33:31.768334

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a8d0c56273f'
down_revision: Union[str, Sequence[str], None] = 'ee3cd9312dc0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create puzzle_stats table
    op.create_table(
        'puzzle_stats',
        sa.Column('puzzle_id', sa.String(), primary_key=True),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('pass_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('fail_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('last_result', sa.String(), nullable=True),
        sa.Column('next_due_at', sa.DateTime(), nullable=True),
        sa.Column('interval_days', sa.Integer(), nullable=True),
        sa.Column('ease_factor', sa.Float(), server_default='2.0', nullable=False),
    )
    op.create_index('ix_puzzle_stats_username', 'puzzle_stats', ['username'])

    # Create puzzle_reviews table
    op.create_table(
        'puzzle_reviews',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('puzzle_id', sa.String(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('result', sa.String(), nullable=False),
        sa.Column('time_spent_ms', sa.Integer(), nullable=True),
    )
    op.create_index('ix_puzzle_reviews_puzzle_id', 'puzzle_reviews', ['puzzle_id'])
    op.create_index('ix_puzzle_reviews_username', 'puzzle_reviews', ['username'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_puzzle_reviews_username', table_name='puzzle_reviews')
    op.drop_index('ix_puzzle_reviews_puzzle_id', table_name='puzzle_reviews')
    op.drop_table('puzzle_reviews')
    op.drop_index('ix_puzzle_stats_username', table_name='puzzle_stats')
    op.drop_table('puzzle_stats')
