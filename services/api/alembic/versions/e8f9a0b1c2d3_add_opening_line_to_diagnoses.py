"""add opening_name and opening_eco to puzzle_diagnoses

Line-level opening identity beside the family that is already stored.
``opening_family`` answers "Sicilian Defense"; these answer "Sicilian Defense:
Najdorf Variation" and "B90". All three come from one classification pass in
``pgn_context.extract_game_context`` — the family is derived from the name, so
they cannot disagree.

Additive and nullable: existing rows keep NULL until the next extraction. No
backfill is scheduled here because none is needed — EXTRACTION_VERSION already
moved to 2, so every stored diagnosis is stale and the ordinary backfill
re-extracts it, filling all three columns in the same pass.

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-08-01 22:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "puzzle_diagnoses", sa.Column("opening_name", sa.String(), nullable=True)
    )
    op.add_column(
        "puzzle_diagnoses", sa.Column("opening_eco", sa.String(), nullable=True)
    )
    # The line filter's access pattern: one user, one line. Partial on NOT NULL
    # because most rows are NULL until re-extraction and an index over those
    # would be mostly dead weight.
    op.create_index(
        "ix_puzzle_diagnoses_username_opening_name",
        "puzzle_diagnoses",
        ["username", "opening_name"],
        postgresql_where=sa.text("opening_name IS NOT NULL"),
        sqlite_where=sa.text("opening_name IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_puzzle_diagnoses_username_opening_name", table_name="puzzle_diagnoses"
    )
    op.drop_column("puzzle_diagnoses", "opening_eco")
    op.drop_column("puzzle_diagnoses", "opening_name")
