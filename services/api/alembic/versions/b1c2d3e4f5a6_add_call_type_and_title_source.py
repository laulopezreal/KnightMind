"""add diagnosis_audit_log.call_type and puzzle_stats.title_source

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-08-06 00:00:00.000000

Two columns, both making an existing implicit meaning explicit.

``diagnosis_audit_log.call_type`` — the table doubles as the AI spend ledger,
and it is about to carry a second kind of call (puzzle naming). Without a
discriminator a naming backfill would consume the diagnosis budget and land in
``agreement_stats``, where ``agreed_with_rules`` is meaningless for a name.
Existing rows are all diagnosis calls, so the backfill value is exact rather
than a guess.

``puzzle_stats.title_source`` — every puzzle has a title, because the creation
path always writes one. That made "has a title" useless as a stand-in for "the
user named this": code wanting to preserve a user's chosen name could only test
for non-NULL, which is true for every row. Existing titles were all produced by
``generate_puzzle_title`` from the motif — verified against the live corpus,
where 230 of 230 titles are byte-identical to the generator's output for their
stored motif — so backfilling ``'motif'`` states what is already true.

Both columns are additive and nullable/defaulted, so this is safe to run
against a populated table and safe to roll back.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "a0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NOT NULL with a server default: existing rows are backfilled by the
    # default in one pass, and the ORM never has to remember to set it.
    op.add_column(
        "diagnosis_audit_log",
        sa.Column(
            "call_type",
            sa.String(),
            nullable=False,
            server_default="diagnosis",
        ),
    )
    # The per-type budget count filters on (username, created_at, call_type);
    # the existing username/created_at index no longer covers it alone.
    op.create_index(
        "ix_diagnosis_audit_call_type_created",
        "diagnosis_audit_log",
        ["call_type", "created_at"],
    )

    # Nullable rather than defaulted: a NULL here means "written before this
    # column existed and not yet reclassified", which is distinguishable from
    # an explicit 'motif'. The UPDATE below then makes every current row
    # explicit, leaving NULL to mean only what it says.
    op.add_column(
        "puzzle_stats",
        sa.Column("title_source", sa.String(), nullable=True),
    )
    op.execute("""
        UPDATE puzzle_stats
        SET title_source = 'motif'
        WHERE title IS NOT NULL AND title_source IS NULL
        """)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("puzzle_stats", "title_source")
    op.drop_index(
        "ix_diagnosis_audit_call_type_created", table_name="diagnosis_audit_log"
    )
    op.drop_column("diagnosis_audit_log", "call_type")
