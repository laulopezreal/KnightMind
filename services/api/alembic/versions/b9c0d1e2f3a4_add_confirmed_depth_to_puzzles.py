"""Add confirmed_depth to puzzles

Audit gate 10 (high-quality, stable puzzles). Records the search depth at which
a candidate mistake+solution was confirmed stable by the generator's deeper
confirmation pass. NULL for pre-confirmation rows so a puzzle's provenance is
auditable and reproducible: a puzzle vetted at depth 18 is distinguishable from
a legacy single-shallow-pass one.

Adds to puzzles:
  - confirmed_depth: engine depth used for the confirmation pass (NULL legacy)

Reversible, single head off a8b9c0d1e2f3. Safe under SQLite add-column (the new
column is nullable, no server default required).

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-07-20 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "puzzles",
        sa.Column("confirmed_depth", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("puzzles", "confirmed_depth")
