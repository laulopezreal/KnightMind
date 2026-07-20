"""Add server-verification fields to puzzle_reviews

Audit gate 7 (server-verified training integrity). Records whether a review's
outcome was independently verified by the server (from the attempted move) or
merely self-reported by the client, so analytics can distinguish verified skill
from a client-reported pass.

Adds to puzzle_reviews:
  - attempted_move: UCI the user actually played (NULL for no-move flows)
  - client_result: the raw pass/fail the client claimed (preserved on override)
  - verified: True only when the server checked the move (default False)
  - source: "server_verified" | "client_reported" (NULL for legacy rows)

Revision ID: a8b9c0d1e2f3
Revises: e6f7a8b9c0d1
Create Date: 2026-07-20 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "puzzle_reviews",
        sa.Column("attempted_move", sa.String(), nullable=True),
    )
    op.add_column(
        "puzzle_reviews",
        sa.Column("client_result", sa.String(), nullable=True),
    )
    # server_default so existing rows backfill to False; NOT NULL is safe under
    # SQLite add-column because a default is supplied.
    op.add_column(
        "puzzle_reviews",
        sa.Column(
            "verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "puzzle_reviews",
        sa.Column("source", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("puzzle_reviews", "source")
    op.drop_column("puzzle_reviews", "verified")
    op.drop_column("puzzle_reviews", "client_result")
    op.drop_column("puzzle_reviews", "attempted_move")
