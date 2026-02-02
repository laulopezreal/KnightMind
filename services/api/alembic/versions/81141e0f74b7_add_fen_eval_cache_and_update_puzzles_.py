"""add fen_eval_cache and update puzzles index

Revision ID: 81141e0f74b7
Revises: b7a2c7d2b7c9
Create Date: 2026-02-02 15:49:18.621274

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '81141e0f74b7'
down_revision: Union[str, Sequence[str], None] = 'b7a2c7d2b7c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # fen_eval_cache table already exists from earlier migration, skip creation

    # Use batch mode for SQLite constraint changes
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Only update puzzles table if the unique constraint exists
    if inspector.has_table('puzzles'):
        with op.batch_alter_table('puzzles', schema=None) as batch_op:
            # Check if the old constraint exists before trying to drop it
            constraints = {c['name'] for c in inspector.get_unique_constraints('puzzles')}
            if 'uq_puzzles_username_source_game_id_ply' in constraints:
                batch_op.drop_constraint('uq_puzzles_username_source_game_id_ply', type_='unique')

            # Check if the index already exists
            indexes = {idx['name'] for idx in inspector.get_indexes('puzzles')}
            if 'ix_puzzles_username_source_game_id_ply' not in indexes:
                batch_op.create_index('ix_puzzles_username_source_game_id_ply', ['username', 'source_game_id', 'ply'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    # Don't drop fen_eval_cache as it was created in an earlier migration

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Use batch mode for SQLite constraint changes
    if inspector.has_table('puzzles'):
        with op.batch_alter_table('puzzles', schema=None) as batch_op:
            indexes = {idx['name'] for idx in inspector.get_indexes('puzzles')}
            if 'ix_puzzles_username_source_game_id_ply' in indexes:
                batch_op.drop_index('ix_puzzles_username_source_game_id_ply')

            constraints = {c['name'] for c in inspector.get_unique_constraints('puzzles')}
            if 'uq_puzzles_username_source_game_id_ply' not in constraints:
                batch_op.create_unique_constraint('uq_puzzles_username_source_game_id_ply', ['username', 'source_game_id', 'ply'])
