"""add client_review_id idempotency key to puzzle_reviews

Revision ID: c4d5e6f7a8b9
Revises: a1b2c3d4e5f6
Create Date: 2026-07-19 00:00:00.000000

Adds an optional client-supplied idempotency key to puzzle_reviews and a unique
index over (puzzle_id, username, session_id, client_review_id) so a retried or
double-submitted review cannot be recorded (and re-scheduled/re-counted) twice.
Rows with a NULL client_review_id are exempt (NULLs are distinct in a unique
index on both SQLite and Postgres), preserving the legacy no-key behaviour.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "puzzle_reviews",
        sa.Column("client_review_id", sa.String(), nullable=True),
    )
    op.create_index(
        "uq_puzzle_reviews_client_key",
        "puzzle_reviews",
        ["puzzle_id", "username", "session_id", "client_review_id"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_puzzle_reviews_client_key", table_name="puzzle_reviews")
    op.drop_column("puzzle_reviews", "client_review_id")
