"""fen_eval_cache: persist mate_in and is_terminal

Scorecard dim 6. Adds two columns so a cached, mate-scored (non-terminal)
position keeps its distance-to-mate across a cache read:
- ``mate_in``     (signed distance-to-mate; NULL for ordinary centipawn evals)
- ``is_terminal`` (True when the stored position is game-over)

Before this, ``get_or_compute_eval`` rebuilt ``EvalResult`` on a cache HIT
without ``mate_in``/``is_terminal`` (both defaulting to None/False), so a cached
mate lost "mate in N" on read. The write path now stores both and the read path
restores them.

Note: terminal positions are intentionally NOT cached (best_move_uci is NOT
NULL), so ``is_terminal`` is False for every stored row today; the column exists
for shape parity so a cache read reconstructs the full EvalResult.

Additive and nullable, so existing rows remain valid.

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-07-20 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = "b9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS = (
    ("mate_in", sa.Integer()),
    ("is_terminal", sa.Boolean()),
)


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "fen_eval_cache" not in inspector.get_table_names():
        # Nothing to alter (table is created by an earlier migration / models);
        # bail out rather than fail on a partially built schema.
        return

    existing = {c["name"] for c in inspector.get_columns("fen_eval_cache")}
    with op.batch_alter_table("fen_eval_cache", schema=None) as batch_op:
        for name, type_ in _NEW_COLUMNS:
            if name not in existing:
                batch_op.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "fen_eval_cache" not in inspector.get_table_names():
        return

    existing = {c["name"] for c in inspector.get_columns("fen_eval_cache")}
    with op.batch_alter_table("fen_eval_cache", schema=None) as batch_op:
        for name, _type in reversed(_NEW_COLUMNS):
            if name in existing:
                batch_op.drop_column(name)
