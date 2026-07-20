"""add accounts and account_chess_usernames

Introduces the multi-user identity layer:
- ``accounts``: KnightMind end-user (email + argon2 password_hash).
- ``account_chess_usernames``: ownership link binding a Chess.com username to at
  most one account (UNIQUE(username) = first-importer-wins as a DB guarantee).

Additive only; no existing table is touched, so this is safe to deploy with the
``KNIGHTMIND_REQUIRE_AUTH`` flag off (enforcement stays inert until flipped).

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "disabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_accounts_email"),
    )
    op.create_index("ix_accounts_email", "accounts", ["email"], unique=True)

    op.create_table(
        "account_chess_usernames",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_account_chess_usernames_username"),
    )
    op.create_index(
        "ix_account_chess_usernames_account_id",
        "account_chess_usernames",
        ["account_id"],
    )
    op.create_index(
        "ix_account_chess_usernames_username",
        "account_chess_usernames",
        ["username"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_account_chess_usernames_username", table_name="account_chess_usernames"
    )
    op.drop_index(
        "ix_account_chess_usernames_account_id", table_name="account_chess_usernames"
    )
    op.drop_table("account_chess_usernames")
    op.drop_index("ix_accounts_email", table_name="accounts")
    op.drop_table("accounts")
