"""add_games_and_puzzles_tables

Revision ID: b7a2c7d2b7c9
Revises: 794d3af3a02c
Create Date: 2026-02-01 17:42:10.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7a2c7d2b7c9"
down_revision: Union[str, Sequence[str], None] = "794d3af3a02c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("games"):
        op.create_table(
            "games",
            sa.Column("game_id", sa.String(), primary_key=True),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("username", sa.String(), nullable=False),
            sa.Column("white_username", sa.String(), nullable=False),
            sa.Column("black_username", sa.String(), nullable=False),
            sa.Column("white_result", sa.String(), nullable=False),
            sa.Column("black_result", sa.String(), nullable=False),
            sa.Column("time_control", sa.String(), nullable=False),
            sa.Column("end_time", sa.Integer(), nullable=False),
            sa.Column("rated", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("imported_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("source_path", sa.Text(), nullable=True),
            sa.Column("pgn_blob", sa.Text(), nullable=True),
        )

    games_indexes = {index["name"] for index in inspector.get_indexes("games")}
    indexes_to_create = {
        "ix_games_username_end_time": ["username", "end_time"],
        "ix_games_game_id": ["game_id"],
        "ix_games_username": ["username"],
    }
    for name, columns in indexes_to_create.items():
        if name not in games_indexes:
            op.create_index(name, "games", columns)

    if not inspector.has_table("puzzles"):
        op.create_table(
            "puzzles",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("username", sa.String(), nullable=False),
            sa.Column("source_game_id", sa.String(), nullable=False),
            sa.Column("ply", sa.Integer(), nullable=False),
            sa.Column("fen", sa.Text(), nullable=False),
            sa.Column("side_to_move", sa.String(), nullable=False),
            sa.Column("played_move_uci", sa.String(), nullable=False),
            sa.Column("best_move_uci", sa.String(), nullable=False),
            sa.Column("eval_before", sa.Float(), nullable=False),
            sa.Column("eval_after", sa.Float(), nullable=False),
            sa.Column("swing", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("used_on", sa.Date(), nullable=True),
            sa.Column("imported_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("source_path", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["source_game_id"], ["games.game_id"]),
            sa.UniqueConstraint("username", "source_game_id", "ply", name="uq_puzzles_username_source_game_id_ply"),
        )

    puzzles_indexes = {index["name"] for index in inspector.get_indexes("puzzles")}
    if "ix_puzzles_username_created_at" not in puzzles_indexes:
        op.create_index("ix_puzzles_username_created_at", "puzzles", ["username", "created_at"])
    if "ix_puzzles_username" not in puzzles_indexes:
        op.create_index("ix_puzzles_username", "puzzles", ["username"])

    if inspector.has_table("puzzle_stats"):
        puzzle_stats_fks = {fk["name"] for fk in inspector.get_foreign_keys("puzzle_stats")}
        if "fk_puzzle_stats_puzzle_id" not in puzzle_stats_fks:
            with op.batch_alter_table("puzzle_stats") as batch_op:
                batch_op.create_foreign_key(
                    "fk_puzzle_stats_puzzle_id",
                    "puzzles",
                    ["puzzle_id"],
                    ["id"],
                )

    if inspector.has_table("puzzle_reviews"):
        puzzle_reviews_fks = {fk["name"] for fk in inspector.get_foreign_keys("puzzle_reviews")}
        if "fk_puzzle_reviews_puzzle_id" not in puzzle_reviews_fks:
            with op.batch_alter_table("puzzle_reviews") as batch_op:
                batch_op.create_foreign_key(
                    "fk_puzzle_reviews_puzzle_id",
                    "puzzles",
                    ["puzzle_id"],
                    ["id"],
                )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Drop foreign keys from puzzle_reviews if they exist
    if inspector.has_table("puzzle_reviews"):
        puzzle_reviews_fks = {fk["name"] for fk in inspector.get_foreign_keys("puzzle_reviews")}
        if "fk_puzzle_reviews_puzzle_id" in puzzle_reviews_fks:
            with op.batch_alter_table("puzzle_reviews") as batch_op:
                batch_op.drop_constraint("fk_puzzle_reviews_puzzle_id", type_="foreignkey")

    # Drop foreign keys from puzzle_stats if they exist
    if inspector.has_table("puzzle_stats"):
        puzzle_stats_fks = {fk["name"] for fk in inspector.get_foreign_keys("puzzle_stats")}
        if "fk_puzzle_stats_puzzle_id" in puzzle_stats_fks:
            with op.batch_alter_table("puzzle_stats") as batch_op:
                batch_op.drop_constraint("fk_puzzle_stats_puzzle_id", type_="foreignkey")

    # Drop puzzles table and its indexes if they exist
    if inspector.has_table("puzzles"):
        puzzles_indexes = {index["name"] for index in inspector.get_indexes("puzzles")}
        indexes_to_drop = ["ix_puzzles_username", "ix_puzzles_username_created_at"]
        for index_name in indexes_to_drop:
            if index_name in puzzles_indexes:
                op.drop_index(index_name, table_name="puzzles")
        op.drop_table("puzzles")

    # Drop games table and its indexes if they exist
    if inspector.has_table("games"):
        games_indexes = {index["name"] for index in inspector.get_indexes("games")}
        if "ix_games_username" in games_indexes:
            op.drop_index("ix_games_username", table_name="games")
        if "ix_games_game_id" in games_indexes:
            op.drop_index("ix_games_game_id", table_name="games")
        if "ix_games_username_end_time" in games_indexes:
            op.drop_index("ix_games_username_end_time", table_name="games")
        op.drop_table("games")
