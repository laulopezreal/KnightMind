"""drop unused SOTA scoring and hint-ladder columns

``c6d7e8f9a0b1`` added persistence hooks ahead of the logic that was meant to
fill them: per-puzzle scoring (difficulty/training value/priority reason), a
stored hint ladder, and three extra audit dimensions. None of them were ever
mapped on the models, written, or read — they exist only as columns.

Two of those designs were subsequently rejected in code rather than merely left
undone:

* ``training_value_score`` — ``main._queue_reason`` declines a numeric score
  outright: the queue order is already determined by tier, due date and focus,
  all of which are in the payload, so a score would add an uncalibrated number
  that explains nothing the visible fields do not.
* ``hint_ladder`` — the shipped clue derives its stages from the solution
  fetched at reveal time, so there is no stored text to leak and nothing to
  keep in sync when a puzzle is re-analysed. Storing a ladder is the weaker
  design, not the missing one.

The remaining columns are speculative rather than rejected, but a column that
is never written is indistinguishable from one that is broken, and it reports
the schema as further along than the code. Dropping is reversible: the
downgrade re-adds every column and the index, and no data is lost because all
of them are NULL for every row.

If weakness ranking returns, it belongs at the cause/cluster grain — "which
weakness should I attack" — not per puzzle, which is the question the queue
already answers.

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-08-05 11:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DIAGNOSIS_COLUMNS = (
    "difficulty_score",
    "training_value_score",
    "priority_reason",
    "hint_ladder",
    "scoring_version",
    "hint_ladder_version",
)

_AUDIT_COLUMNS = ("event_type", "generation_id", "review_context", "context_json")


def upgrade() -> None:
    # Indexes first: each covers a column dropped below, and SQLite rejects a
    # DROP COLUMN that would leave an index referencing a missing column.
    op.drop_index("ix_diagnosis_audit_generation", table_name="diagnosis_audit_log")
    op.drop_index("ix_diagnosis_audit_event_created", table_name="diagnosis_audit_log")
    op.drop_index(
        "ix_puzzle_diagnoses_username_training_value",
        table_name="puzzle_diagnoses",
    )
    for name in _DIAGNOSIS_COLUMNS:
        op.drop_column("puzzle_diagnoses", name)
    for name in _AUDIT_COLUMNS:
        op.drop_column("diagnosis_audit_log", name)


def downgrade() -> None:
    op.add_column(
        "puzzle_diagnoses", sa.Column("difficulty_score", sa.Float(), nullable=True)
    )
    op.add_column(
        "puzzle_diagnoses", sa.Column("training_value_score", sa.Float(), nullable=True)
    )
    op.add_column(
        "puzzle_diagnoses", sa.Column("priority_reason", sa.Text(), nullable=True)
    )
    op.add_column(
        "puzzle_diagnoses", sa.Column("hint_ladder", sa.JSON(), nullable=True)
    )
    op.add_column(
        "puzzle_diagnoses", sa.Column("scoring_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "puzzle_diagnoses",
        sa.Column("hint_ladder_version", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_puzzle_diagnoses_username_training_value",
        "puzzle_diagnoses",
        ["username", "status", "training_value_score"],
    )

    op.add_column(
        "diagnosis_audit_log", sa.Column("event_type", sa.String(), nullable=True)
    )
    op.add_column(
        "diagnosis_audit_log", sa.Column("generation_id", sa.String(), nullable=True)
    )
    op.add_column(
        "diagnosis_audit_log", sa.Column("review_context", sa.String(), nullable=True)
    )
    op.add_column(
        "diagnosis_audit_log", sa.Column("context_json", sa.JSON(), nullable=True)
    )
    op.create_index(
        "ix_diagnosis_audit_event_created",
        "diagnosis_audit_log",
        ["event_type", "created_at"],
    )
    op.create_index(
        "ix_diagnosis_audit_generation",
        "diagnosis_audit_log",
        ["generation_id"],
    )
