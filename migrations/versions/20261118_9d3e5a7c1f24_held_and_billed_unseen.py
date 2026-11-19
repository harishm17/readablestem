"""what a job's calls in flight hold, and the mark on a call charged at its reservation

`jobs.held_usd`: the part of `jobs.cost_usd` that is a reservation not yet settled, so a page
can show what a job has spent apart from what its calls in flight hold.
`job_calls.billed_unseen_usd`: the part of a call's cost that was charged at its reservation
because no usage came back; the step report and the admin page total it apart from settled
spend. Additive; existing rows get 0; the downgrade drops the columns.

Revision ID: 9d3e5a7c1f24
Revises: c5d2a8e6f1b7
Create Date: 2026-11-18 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d3e5a7c1f24"
down_revision: str | Sequence[str] | None = "c5d2a8e6f1b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("held_usd", sa.Float(), nullable=False, server_default="0"))
    with op.batch_alter_table("job_calls", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("billed_unseen_usd", sa.Float(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("job_calls", schema=None) as batch_op:
        batch_op.drop_column("billed_unseen_usd")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("held_usd")
