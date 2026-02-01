"""enhance_training_sessions_with_goals_and_metrics

Revision ID: e78ac1ff84d3
Revises: 18d35c5a5d79
Create Date: 2026-02-01 16:52:55.422076

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e78ac1ff84d3'
down_revision: Union[str, Sequence[str], None] = '18d35c5a5d79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add new columns to training_sessions table
    op.add_column('training_sessions', sa.Column('session_type', sa.String(), nullable=True))
    op.add_column('training_sessions', sa.Column('target_accuracy', sa.Float(), nullable=True))
    op.add_column('training_sessions', sa.Column('target_time_minutes', sa.Integer(), nullable=True))
    op.add_column('training_sessions', sa.Column('current_streak', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('training_sessions', sa.Column('best_streak', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('training_sessions', sa.Column('hints_used', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('training_sessions', sa.Column('session_data', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove added columns
    op.drop_column('training_sessions', 'session_data')
    op.drop_column('training_sessions', 'hints_used')
    op.drop_column('training_sessions', 'best_streak')
    op.drop_column('training_sessions', 'current_streak')
    op.drop_column('training_sessions', 'target_time_minutes')
    op.drop_column('training_sessions', 'target_accuracy')
    op.drop_column('training_sessions', 'session_type')
