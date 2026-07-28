"""scope the active-job index by job type

Widens the partial unique index ``ix_jobs_active_username`` from ``(username)``
to ``(username, type)``.

Why
---
The index has always encoded "a user may have only one job in flight". In
practice it encoded something stronger: only one job *of any kind*. With more
than one job type (analysis alongside puzzle generation) that blocks unrelated
work — a queued analysis job for a user who is mid-generation cannot even be
inserted.

Scoping by type keeps the invariant that actually matters — no two concurrent
jobs of the *same* kind for one user — while letting different kinds coexist.

Safety
------
Widening a unique index is strictly permissive: every row set that satisfied
``UNIQUE(username)`` also satisfies ``UNIQUE(username, type)``. The upgrade
therefore cannot fail on existing data.

``jobs.type`` is NOT NULL (since the table was created in 611ff7c9eef8), so the
composite index cannot be defeated by NULLs comparing distinct — no COALESCE
wrapper is needed here, unlike ``uq_puzzle_reviews_client_key``.

The downgrade is the narrowing direction and CAN fail: if any user holds two
active jobs of different types at that moment, recreating ``UNIQUE(username)``
raises. That is correct — silently dropping rows to fit an older constraint
would lose queued work.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-27 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE = "status IN ('queued', 'running')"


def upgrade() -> None:
    op.drop_index("ix_jobs_active_username", table_name="jobs")
    op.create_index(
        "ix_jobs_active_username",
        "jobs",
        ["username", "type"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
        sqlite_where=sa.text(_ACTIVE),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_active_username", table_name="jobs")
    op.create_index(
        "ix_jobs_active_username",
        "jobs",
        ["username"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
        sqlite_where=sa.text(_ACTIVE),
    )
