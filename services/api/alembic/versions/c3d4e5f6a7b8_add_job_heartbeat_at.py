"""add jobs.heartbeat_at liveness lease

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-07-19 00:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable heartbeat_at lease column to jobs.

    Nullable with no server_default: existing rows stay NULL, and crash
    recovery falls back to updated_at/created_at for them (COALESCE), so no
    in-flight job is stranded by the migration.
    """
    op.add_column("jobs", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Remove the heartbeat_at column."""
    op.drop_column("jobs", "heartbeat_at")
