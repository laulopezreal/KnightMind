"""add focus-practice review telemetry and scheduling policy

Revision ID: e3f4a5b6c7d8
Revises: d1e2f3a4b5c6
Create Date: 2026-08-24 10:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill safe ordinary-review defaults before making columns required."""
    op.add_column(
        "puzzle_reviews",
        sa.Column(
            "review_context",
            sa.String(),
            nullable=False,
            server_default="standard",
        ),
    )
    op.add_column(
        "puzzle_reviews",
        sa.Column(
            "affects_scheduling",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_index(
        "ix_puzzle_reviews_username_context_reviewed_at",
        "puzzle_reviews",
        ["username", "review_context", "reviewed_at"],
    )


def downgrade() -> None:
    """Remove only the telemetry columns and their query index."""
    op.drop_index(
        "ix_puzzle_reviews_username_context_reviewed_at",
        table_name="puzzle_reviews",
    )
    op.drop_column("puzzle_reviews", "affects_scheduling")
    op.drop_column("puzzle_reviews", "review_context")