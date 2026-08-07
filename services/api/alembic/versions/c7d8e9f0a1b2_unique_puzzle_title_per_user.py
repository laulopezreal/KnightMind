"""make puzzle titles unique per user

Revision ID: c7d8e9f0a1b2
Revises: b1c2d3e4f5a6
Create Date: 2026-08-07 00:00:00.000000

A title is a display name, and a library that shows the same name twice cannot
be navigated. Uniqueness was enforced only inside a single naming run — a set
built from the puzzles that run happened to touch — which says nothing about the
run before it. On the live corpus that left 260 stats rows carrying 91 distinct
names: 169 rows are duplicates, and one of them, "The Missed Win", is repeated
103 times for a single user.

Scoped to the user. Two users independently reaching the same fork on f7 SHOULD
both get "The f7 Knight Fork"; a global constraint would let one tenant's corpus
rename another's.

Safe on populated data
----------------------
The index cannot be created while duplicates exist, so this migration resolves
them first, in SQL, without needing a chess engine or the application's naming
code: the earliest row (by puzzle_id) keeps the name, every later one gets the
first free ``" (n)"`` suffix. That mirrors ``position_names.disambiguate``'s last
resort, including its 48-character cap and its trailing-punctuation trim, so the
names it produces are indistinguishable from ones the app would have written.

They are also temporary in practice: these rows are ``title_source='motif'`` or
``'position'``, so the next AI naming pass replaces them outright. The suffix is
here to make the constraint applicable, not to be the final answer.

Idempotent and re-runnable. The dedup is a no-op once no duplicates remain, and
the index is created IF NOT EXISTS, so running ``upgrade`` twice against the same
database does nothing the second time.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_puzzle_stats_username_title"

# Matches position_names.MAX_NAME_CHARS. Duplicated as a literal rather than
# imported: a migration describes the schema at a point in time, and must keep
# doing what it did even after the application's constant moves.
MAX_NAME_CHARS = 48

# Resolve duplicate titles, oldest row wins.
#
# The outer FOR opens a cursor whose snapshot predates the loop, so ``dup.title``
# is always the ORIGINAL name even after earlier iterations have written new
# ones. The inner EXISTS is a fresh statement in the same transaction, so it DOES
# see those writes — which is what stops two duplicates being handed the same
# suffix. Both halves of that are load-bearing.
#
# The suffix search starts at 2 and climbs, so it also steps over a name a
# previous partial run already created; that is what makes this re-runnable.
DEDUP_TITLES = f"""
DO $$
DECLARE
    dup RECORD;
    candidate TEXT;
    n INT;
BEGIN
    FOR dup IN
        SELECT puzzle_id, username, title
        FROM (
            SELECT
                puzzle_id,
                username,
                title,
                row_number() OVER (
                    PARTITION BY username, title ORDER BY puzzle_id
                ) AS rn
            FROM puzzle_stats
            WHERE title IS NOT NULL
        ) ranked
        WHERE rn > 1
    LOOP
        n := 2;
        LOOP
            -- Trim the BASE, not the suffix: the suffix is the part that makes
            -- the name unique, so it is the part that must survive the cap.
            candidate := rtrim(
                left(dup.title, {MAX_NAME_CHARS} - length(' (' || n || ')')),
                ' ,'
            ) || ' (' || n || ')';
            EXIT WHEN NOT EXISTS (
                SELECT 1
                FROM puzzle_stats
                WHERE username = dup.username
                  AND title = candidate
            );
            n := n + 1;
        END LOOP;
        -- puzzle_id is the primary key, so this touches exactly one row.
        UPDATE puzzle_stats SET title = candidate WHERE puzzle_id = dup.puzzle_id;
    END LOOP;
END
$$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(DEDUP_TITLES)
    # No separate "did the dedup work?" check: CREATE UNIQUE INDEX is that
    # check, and it fails the migration rather than half-applying it.
    op.create_index(
        INDEX_NAME,
        "puzzle_stats",
        ["username", "title"],
        unique=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Only the constraint comes off. The renamed titles stay: they are valid
    # names, the original duplicates carried no information the suffix removed,
    # and reversing them would need a record of what they were that this
    # migration deliberately does not keep.
    op.drop_index(INDEX_NAME, table_name="puzzle_stats", if_exists=True)
