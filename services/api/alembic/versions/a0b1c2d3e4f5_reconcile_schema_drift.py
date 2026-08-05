"""reconcile schema drift found by alembic check

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-08-05

`alembic check` could not run before this point: alembic/env.py imported the
declarative Base but never the models, so target_metadata was empty and every
table read as "removed". With that fixed the first real comparison against a
migrated Postgres found the drift this migration closes.

Two DB-side changes:

1. Drop `uq_accounts_email` and `uq_account_chess_usernames_username`. Both
   columns are declared `unique=True, index=True` on the model, which already
   produces a UNIQUE index (`ix_accounts_email`,
   `ix_account_chess_usernames_username`). The extra UNIQUE CONSTRAINT from
   e6f7a8b9c0d1 is a *second* btree enforcing the identical rule -- pure write
   cost and storage for no added guarantee. The uniqueness itself is preserved
   by the surviving unique index, so this is not a relaxation.

2. Make `fen_eval_cache.is_terminal` and `created_at` NOT NULL, matching the
   model. `is_terminal` carries `default=False` and `created_at` a default
   timestamp, both applied client-side by the ORM, so the write path has always
   supplied them; the columns were simply created nullable. The backfill below
   is expected to touch zero rows and exists so the ALTER cannot fail on a row
   predating the column (added in c0d1e2f3a4b5 / ab235ce26ed5).

Not addressed here, because they are model-side and carry no DDL: the
`uq_puzzles_username_source_game_id_ply` constraint and the partial index
`ix_puzzle_diagnoses_username_opening_name` are now declared in models.py to
match what the deployed schema already has.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a0b1c2d3e4f5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- 1. Drop the redundant unique constraints -----------------------------
    # SQLite cannot drop a named constraint in place; batch mode rebuilds the
    # table. On Postgres this emits a plain ALTER TABLE ... DROP CONSTRAINT.
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_constraint("uq_accounts_email", type_="unique")

    with op.batch_alter_table("account_chess_usernames", schema=None) as batch_op:
        batch_op.drop_constraint("uq_account_chess_usernames_username", type_="unique")

    # --- 2. Tighten fen_eval_cache to match the model ------------------------
    # Backfill first: SET NOT NULL fails outright if any row still holds a NULL.
    # Both defaults mirror the ORM-side defaults on the model.
    op.execute(
        "UPDATE fen_eval_cache SET is_terminal = false WHERE is_terminal IS NULL"
    )
    op.execute(
        "UPDATE fen_eval_cache SET created_at = CURRENT_TIMESTAMP "
        "WHERE created_at IS NULL"
    )

    with op.batch_alter_table("fen_eval_cache", schema=None) as batch_op:
        batch_op.alter_column("is_terminal", existing_type=sa.Boolean(), nullable=False)
        batch_op.alter_column("created_at", existing_type=sa.DateTime(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("fen_eval_cache", schema=None) as batch_op:
        batch_op.alter_column("created_at", existing_type=sa.DateTime(), nullable=True)
        batch_op.alter_column("is_terminal", existing_type=sa.Boolean(), nullable=True)

    with op.batch_alter_table("account_chess_usernames", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_account_chess_usernames_username", ["username"]
        )

    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_accounts_email", ["email"])
