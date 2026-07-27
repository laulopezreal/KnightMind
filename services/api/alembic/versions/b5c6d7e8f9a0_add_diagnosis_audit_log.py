"""add diagnosis_audit_log and AI columns on puzzle_diagnoses

Additive only: one new table plus two nullable columns. Nothing existing is
rewritten, so the upgrade cannot fail on existing data and the downgrade is a
clean drop.

``diagnosis_audit_log`` is deliberately a separate table rather than columns on
``puzzle_diagnoses``: that table is read on every puzzle-detail page load, and
prompt/response blobs have no business on a hot read path. The table also serves
as the spend ledger — counting today's rows is how the daily AI caps are
enforced, so the budget survives a restart without a separate counter that could
drift from what was actually called.

``model_confidence`` is kept distinct from the existing ``primary_strength``.
That one is a hand-assigned rule ordering prior; this one is what the model
reported. Merging them would let a rule ordering masquerade as a calibrated
probability.

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-07-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "puzzle_diagnoses", sa.Column("model_confidence", sa.Float(), nullable=True)
    )
    op.add_column(
        "puzzle_diagnoses", sa.Column("agreed_with_rules", sa.Boolean(), nullable=True)
    )

    op.create_table(
        "diagnosis_audit_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("puzzle_id", sa.String(), nullable=True),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("agreed_with_rules", sa.Boolean(), nullable=True),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column("extraction_version", sa.Integer(), nullable=True),
        sa.Column("prompt_hash", sa.String(), nullable=True),
        sa.Column("evidence_hash", sa.String(), nullable=True),
        sa.Column("response_json", sa.Text(), nullable=True),
        sa.Column(
            "response_truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_diagnosis_audit_log_username", "diagnosis_audit_log", ["username"]
    )
    # The retention sweep and the daily spend count both scan on time.
    op.create_index(
        "ix_diagnosis_audit_created_at", "diagnosis_audit_log", ["created_at"]
    )
    # Per-user daily spend.
    op.create_index(
        "ix_diagnosis_audit_username_created",
        "diagnosis_audit_log",
        ["username", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_diagnosis_audit_username_created", table_name="diagnosis_audit_log"
    )
    op.drop_index("ix_diagnosis_audit_created_at", table_name="diagnosis_audit_log")
    op.drop_index("ix_diagnosis_audit_log_username", table_name="diagnosis_audit_log")
    op.drop_table("diagnosis_audit_log")
    op.drop_column("puzzle_diagnoses", "agreed_with_rules")
    op.drop_column("puzzle_diagnoses", "model_confidence")
