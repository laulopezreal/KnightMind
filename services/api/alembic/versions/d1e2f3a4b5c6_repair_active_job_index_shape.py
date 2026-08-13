"""repair ix_jobs_active_username when it is still the pre-f3a4b5c6d7e8 shape

Revision ID: d1e2f3a4b5c6
Revises: 399a35540403
Create Date: 2026-08-13 00:00:00.000000

Production's ``ix_jobs_active_username`` is UNIQUE on ``(username)`` alone,
while ``f3a4b5c6d7e8_scope_active_job_index_by_type`` widened it to
``(username, type)`` and is an applied ancestor of the recorded revision. The
schema is in the downgrade shape while Alembic believes otherwise — so nothing
would ever correct it, because the migration that should have will never run
again.

Why it matters, rather than being a tidiness fix. The index is what enforces
"at most one active job per user per type", and ``puzzles_routes`` relies on
exactly that wording. In the narrow shape, ANY active job blocks every other
kind: a user with a diagnosis job queued cannot start a puzzle generation. The
endpoint catches the IntegrityError, looks for an active job *of the type it
was asked for*, does not find one, and returns **HTTP 200 "Job completed
recently"** with a stale job id. The user is told it worked and nothing runs.

Combined with a job orphaned in RUNNING — which until this release was only
reclaimed when the worker process restarted — that state can persist
indefinitely, while /ops/health stays green.

Conditional rather than unconditional. On a database built from migrations the
index is already correct, and this must be a no-op there rather than a needless
drop and rebuild of a uniqueness guarantee. The DO block inspects the live
definition and acts only on the narrow shape, which also makes the migration
re-runnable.

Safe on populated data: widening a unique index is strictly PERMISSIVE — every
row pair that satisfies the new constraint already satisfied the old one — so
the CREATE cannot fail on existing rows. Verified against production before
writing this: 0 active jobs, 0 (username, type) duplicates. Postgres DDL is
transactional, so the drop and the create land together and no concurrent
writer sees a window without the guarantee.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "399a35540403"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PARTIAL_WHERE = "status IN ('queued', 'running')"


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # The drift is a Postgres artefact and pg_indexes is Postgres-only.
        # Non-Postgres databases build from the migration chain, where the
        # index is already correct.
        return

    op.execute(f"""
        DO $$
        DECLARE
            definition text;
        BEGIN
            SELECT indexdef INTO definition
              FROM pg_indexes
             WHERE schemaname = current_schema()
               AND indexname = 'ix_jobs_active_username';

            IF definition IS NULL THEN
                -- Absent entirely: create it in the correct shape.
                CREATE UNIQUE INDEX ix_jobs_active_username
                    ON jobs (username, type)
                    WHERE {_PARTIAL_WHERE};

            ELSIF position('type' in definition) = 0 THEN
                -- The narrow shape. Widening is permissive, so no existing row
                -- can conflict with the replacement.
                DROP INDEX ix_jobs_active_username;
                CREATE UNIQUE INDEX ix_jobs_active_username
                    ON jobs (username, type)
                    WHERE {_PARTIAL_WHERE};
                RAISE NOTICE 'ix_jobs_active_username repaired to (username, type)';
            END IF;
            -- Already correct: do nothing.
        END $$;
        """)


def downgrade() -> None:
    """Downgrade schema.

    Deliberately a no-op. This migration exists to bring a database into the
    shape the chain already says it should have, so "undoing" it would mean
    reintroducing drift — and there is no state to restore, because the
    corrected index is what every other revision assumes.
    """
    pass
