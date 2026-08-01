"""extend diagnosis records for SOTA generation

Additive, nullable persistence hooks for the revised SOTA puzzle generation
sequence. PuzzleDiagnosis remains the canonical row for cause/motif/prose plus
training-value metadata; DiagnosisAuditLog remains the canonical ledger for AI
and deterministic generation decisions. No PuzzleIntelligence table is created.

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-07-29 13:40:00.000000

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


def downgrade() -> None:
    op.drop_index("ix_diagnosis_audit_generation", table_name="diagnosis_audit_log")
    op.drop_index("ix_diagnosis_audit_event_created", table_name="diagnosis_audit_log")
    op.drop_column("diagnosis_audit_log", "context_json")
    op.drop_column("diagnosis_audit_log", "review_context")
    op.drop_column("diagnosis_audit_log", "generation_id")
    op.drop_column("diagnosis_audit_log", "event_type")

    op.drop_index(
        "ix_puzzle_diagnoses_username_training_value", table_name="puzzle_diagnoses"
    )
    op.drop_column("puzzle_diagnoses", "hint_ladder_version")
    op.drop_column("puzzle_diagnoses", "scoring_version")
    op.drop_column("puzzle_diagnoses", "hint_ladder")
    op.drop_column("puzzle_diagnoses", "priority_reason")
    op.drop_column("puzzle_diagnoses", "training_value_score")
    op.drop_column("puzzle_diagnoses", "difficulty_score")
