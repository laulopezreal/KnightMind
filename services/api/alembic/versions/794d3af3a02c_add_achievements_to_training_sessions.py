"""add_achievements_to_training_sessions

Revision ID: 794d3af3a02c
Revises: e78ac1ff84d3
Create Date: 2026-02-01 17:00:22.752344

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "794d3af3a02c"
down_revision: Union[str, Sequence[str], None] = "e78ac1ff84d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "training_sessions",
        sa.Column("achievements", sa.JSON(), nullable=True, default=[]),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("training_sessions", "achievements")
