"""add puzzle_diagnoses

Stores the evidence-backed reason a mistake was probably made, one row per
(puzzle, user).

Additive only: a new table plus its indexes. No existing table is touched, so
the upgrade cannot fail on existing data and the downgrade is a clean drop.

Two column choices worth knowing when reading the schema:

* ``primary_strength`` is deliberately not called ``confidence``. It is the
  winning rule's hand-assigned ordering prior, not a calibrated probability,
  and must never surface to a user as a percentage. A model confidence arrives
  as its own column with the AI stage.
* ``status`` exists so an un-analysable puzzle gets a row recording that. Without
  it, every backfill run would re-attempt the same broken puzzle forever.

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-07-27 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "puzzle_diagnoses",
        sa.Column("puzzle_id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("primary_motif", sa.String(), nullable=True),
        sa.Column("primary_cause", sa.String(), nullable=True),
        sa.Column("secondary_causes", sa.JSON(), nullable=True),
        sa.Column("primary_strength", sa.Float(), nullable=True),
        sa.Column(
            "insufficient_evidence",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("phase", sa.String(), nullable=True),
        sa.Column("opening_family", sa.String(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("evidence_hash", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="rules"),
        sa.Column("extraction_version", sa.Integer(), nullable=True),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("training_recommendation", sa.Text(), nullable=True),
        sa.Column("user_confirmed_cause", sa.String(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.ForeignKeyConstraint(["puzzle_id"], ["puzzles.id"]),
        sa.PrimaryKeyConstraint("puzzle_id", "username"),
    )
    op.create_index("ix_puzzle_diagnoses_username", "puzzle_diagnoses", ["username"])
    op.create_index(
        "ix_puzzle_diagnoses_primary_cause", "puzzle_diagnoses", ["primary_cause"]
    )
    # Insights "top mistake causes" and the Library cause filter.
    op.create_index(
        "ix_puzzle_diagnoses_username_cause",
        "puzzle_diagnoses",
        ["username", "primary_cause"],
    )
    # The backfill's "what still needs work" scan.
    op.create_index(
        "ix_puzzle_diagnoses_username_versions",
        "puzzle_diagnoses",
        ["username", "extraction_version", "rule_version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_puzzle_diagnoses_username_versions", table_name="puzzle_diagnoses"
    )
    op.drop_index("ix_puzzle_diagnoses_username_cause", table_name="puzzle_diagnoses")
    op.drop_index("ix_puzzle_diagnoses_primary_cause", table_name="puzzle_diagnoses")
    op.drop_index("ix_puzzle_diagnoses_username", table_name="puzzle_diagnoses")
    op.drop_table("puzzle_diagnoses")
