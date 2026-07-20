"""Add solution_pv to puzzles

Full principal-variation puzzles (SCORECARD dim 12 -> 9). A puzzle used to store
only its first solution move (best_move_uci + the accept_moves_uci equivalence
set). This persists the whole forcing line — the engine's principal variation
from the puzzle FEN, computed at the confirmation depth and bounded in length —
so the puzzle can be trained move-by-move instead of only move 1.

Adds to puzzles:
  - solution_pv: space-separated UCI line starting with the solution move
    (NULL for legacy rows, which keep training as a single move).

Reversible, single head off c0d1e2f3a4b5. Safe under SQLite add-column (the new
column is nullable, no server default required).

Revision ID: e2f3a4b5c6d7
Revises: c0d1e2f3a4b5
Create Date: 2026-07-20 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "puzzles",
        sa.Column("solution_pv", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("puzzles", "solution_pv")
