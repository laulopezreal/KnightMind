"""add import_summaries table

Revision ID: a1b2c3d4e5f6
Revises: 02cb658e598b
Create Date: 2026-02-03 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "02cb658e598b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "import_summaries",
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("last_imported_at", sa.DateTime(), nullable=False),
        sa.Column("last_new_games", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("username"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("import_summaries")
