"""add opening_explorer_cache

Additive only: one new table, nothing existing touched, so the upgrade cannot
fail on existing data and the downgrade is a clean drop.

The table is a cache of public aggregates, not user data — rows are keyed by
position and rating band and are identical for every account, which is why it
carries no username column and needs no ownership check. Dropping it costs
nothing but a re-fetch.

``key`` folds in the scheme version, the speeds queried and the rating band, so
a change to what we ask the explorer for yields different keys and can never
read back rows that answered a different question. Same discipline as
``fen_eval_cache``.

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-08-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "opening_explorer_cache",
        sa.Column("key", sa.String(), primary_key=True, nullable=False),
        sa.Column("epd", sa.Text(), nullable=False),
        sa.Column("white", sa.Integer(), nullable=False),
        sa.Column("draws", sa.Integer(), nullable=False),
        sa.Column("black", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("opening_explorer_cache")
