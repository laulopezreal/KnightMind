"""per-user game ownership: composite games primary key

Make the ``games`` table owned per (game_id, username) so both participants of
a physical chess game can import and own their own copy. Previously ``game_id``
(a sha256 of the game url) was a global primary key with a single ``username``
column, so the second importer never gained ownership.

Existing rows already carry a unique (game_id, username) pair, so this is a
metadata-only key swap that preserves all stored games. The puzzles -> games
foreign key becomes composite to keep pointing at the owning user's game copy.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PUZZLES_FK = "fk_puzzles_source_game"


def _find_fk(inspector, table, referred_table, constrained_columns):
    """Return the name of a FK on ``table`` matching the given shape, if any."""
    for fk in inspector.get_foreign_keys(table):
        if fk["referred_table"] == referred_table and set(
            fk["constrained_columns"]
        ) == set(constrained_columns):
            return fk["name"]
    return None


def upgrade() -> None:
    """Upgrade schema: games PK -> (game_id, username)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("games"):
        return

    pk = inspector.get_pk_constraint("games")
    if set(pk.get("constrained_columns") or []) == {"game_id", "username"}:
        return  # Already migrated.

    if bind.dialect.name == "postgresql":
        # Drop the old single-column puzzles -> games FK before the key swap.
        if inspector.has_table("puzzles"):
            old_fk = _find_fk(inspector, "puzzles", "games", ["source_game_id"])
            if old_fk:
                op.drop_constraint(old_fk, "puzzles", type_="foreignkey")

        pk_name = pk.get("name") or "games_pkey"
        op.drop_constraint(pk_name, "games", type_="primary")
        op.create_primary_key("games_pkey", "games", ["game_id", "username"])

        if inspector.has_table("puzzles"):
            op.create_foreign_key(
                PUZZLES_FK,
                "puzzles",
                "games",
                ["source_game_id", "username"],
                ["game_id", "username"],
            )
    else:
        # SQLite (and other dialects without ALTER PRIMARY KEY): recreate the
        # table with the composite PK. Batch mode copies the data across.
        with op.batch_alter_table("games", recreate="always") as batch_op:
            batch_op.create_primary_key("games_pkey", ["game_id", "username"])


def downgrade() -> None:
    """Downgrade schema: games PK -> (game_id,)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("games"):
        return

    pk = inspector.get_pk_constraint("games")
    if set(pk.get("constrained_columns") or []) == {"game_id"}:
        return  # Already at the single-column key.

    if bind.dialect.name == "postgresql":
        if inspector.has_table("puzzles"):
            new_fk = _find_fk(
                inspector, "puzzles", "games", ["source_game_id", "username"]
            )
            if new_fk:
                op.drop_constraint(new_fk, "puzzles", type_="foreignkey")

        pk_name = pk.get("name") or "games_pkey"
        op.drop_constraint(pk_name, "games", type_="primary")
        op.create_primary_key("games_pkey", "games", ["game_id"])

        if inspector.has_table("puzzles"):
            op.create_foreign_key(
                "puzzles_source_game_id_fkey",
                "puzzles",
                "games",
                ["source_game_id"],
                ["game_id"],
            )
    else:
        with op.batch_alter_table("games", recreate="always") as batch_op:
            batch_op.create_primary_key("games_pkey", ["game_id"])
