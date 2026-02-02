"""create_problem_reports_table

Revision ID: a1b2c3d4e5f6
Revises: 794d3af3a02c
Create Date: 2026-02-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '794d3af3a02c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('problem_reports',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('category', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('page', sa.String(), nullable=True),
    sa.Column('username', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_problem_reports_username'), 'problem_reports', ['username'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_problem_reports_username'), table_name='problem_reports')
    op.drop_table('problem_reports')
