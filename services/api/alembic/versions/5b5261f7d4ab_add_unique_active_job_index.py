"""add_unique_active_job_index

Revision ID: 5b5261f7d4ab
Revises: 611ff7c9eef8
Create Date: 2026-01-30 10:23:47.028896

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b5261f7d4ab'
down_revision: Union[str, Sequence[str], None] = '611ff7c9eef8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create partial unique index for active jobs
    op.create_index(
        'ix_jobs_active_username',
        'jobs',
        ['username'],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')")
    )


def downgrade() -> None:
    op.drop_index('ix_jobs_active_username', table_name='jobs')
