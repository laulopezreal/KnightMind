"""add job client-observability columns (client_id, client_last_seen_at, stall_reported_at)

Closes the observability gap documented in the 2026-08-24 P0 incident report
(section 13): no server-side record of client polling gaps or stall verdicts.

Three additive, nullable columns on the ``jobs`` table:
- ``client_id``            — UUID of the browser tab currently observing the job
- ``client_last_seen_at``  — when that tab last sent X-Client-Id on a GET /jobs/{id}
- ``stall_reported_at``    — when the tab's stall detector fired (POST /jobs/{id}/stall-report)

All nullable, no backfill required: pre-existing rows are represented as NULL.
The migration is fully reversible (downgrade drops the three columns).

Revision ID: a2b3c4d5e6f7
Revises: e3f4a5b6c7d8
Create Date: 2026-08-25 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("client_id", sa.String(), nullable=True))
    op.add_column("jobs", sa.Column("client_last_seen_at", sa.DateTime(), nullable=True))
    op.add_column("jobs", sa.Column("stall_reported_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "stall_reported_at")
    op.drop_column("jobs", "client_last_seen_at")
    op.drop_column("jobs", "client_id")
