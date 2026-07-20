"""fen_eval_cache: version-aware, self-describing columns

Audit gate 4 (cache reproducibility). Adds the engine-config columns that make
each ``fen_eval_cache`` row self-describing and auditable:
- ``threads``            (Stockfish 'Threads' option in effect)
- ``hash_mb``            (Stockfish 'Hash' option in effect)
- ``multipv``            (Stockfish 'MultiPV' option in effect)
- ``conversion_version`` (version of the raw-eval -> pawns/mate conversion)

These are *also* folded into the primary ``key`` by
engine.stockfish._compute_cache_key (together with the engine version, which
already had a column but was written as NULL), so a change to any of them yields
a different key and an old row can never be reused under a materially different
engine/config. This migration only makes the stored rows self-describing; the
key change alone provides the invalidation.

Additive and nullable, so existing rows remain valid.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-20 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS = (
    ("threads", sa.Integer()),
    ("hash_mb", sa.Integer()),
    ("multipv", sa.Integer()),
    ("conversion_version", sa.Integer()),
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
