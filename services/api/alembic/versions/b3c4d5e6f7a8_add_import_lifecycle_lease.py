"""add durable import lifecycle lease

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-09-11

The import endpoint is synchronous, but its request outlives the Home route that
started it. Persisting the current operation and lease lets a remounted client
reattach truthfully and prevents a second write while the first is active.
"""

import sqlalchemy as sa
from alembic import op

revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("import_summaries", schema=None) as batch_op:
        batch_op.alter_column(
            "last_imported_at", existing_type=sa.DateTime(), nullable=True
        )
        batch_op.alter_column(
            "last_new_games",
            existing_type=sa.Integer(),
            server_default=None,
            nullable=True,
        )
        batch_op.add_column(
            sa.Column("status", sa.String(), server_default="succeeded", nullable=False)
        )
        batch_op.add_column(sa.Column("operation_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("completed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("error", sa.Text(), nullable=True))

    op.execute(
        "UPDATE import_summaries SET "
        "started_at = last_imported_at, updated_at = last_imported_at, "
        "completed_at = last_imported_at"
    )
    with op.batch_alter_table("import_summaries", schema=None) as batch_op:
        batch_op.alter_column(
            "status", existing_type=sa.String(), server_default=None, nullable=False
        )


def downgrade() -> None:
    # An active row has no completed summary and cannot satisfy the old NOT NULL
    # contract. It carries no successful import result, so remove only those rows.
    op.execute("DELETE FROM import_summaries WHERE last_imported_at IS NULL")
    with op.batch_alter_table("import_summaries", schema=None) as batch_op:
        batch_op.drop_column("error")
        batch_op.drop_column("completed_at")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("operation_id")
        batch_op.drop_column("status")
        batch_op.alter_column(
            "last_new_games",
            existing_type=sa.Integer(),
            server_default="0",
            nullable=False,
        )
        batch_op.alter_column(
            "last_imported_at", existing_type=sa.DateTime(), nullable=False
        )
