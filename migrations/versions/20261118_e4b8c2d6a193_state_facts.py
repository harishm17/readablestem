"""what a finished job's state reads of its result, kept beside it

`jobs.state_facts`: what the summary says of a finished result (ready to share, what remains),
the version that made it, and whether it has any gap note or notice to word, so the documents
list, the batch page and the counts name a finished job's state without loading its result
(`web.states.finished_facts`, set whenever a result is stored). Additive and nullable: a row
from before it is filled the first time a list reads it, as `open_decisions` was; the
downgrade drops the column.

Revision ID: e4b8c2d6a193
Revises: 9d3e5a7c1f24
Create Date: 2026-11-18 15:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b8c2d6a193"
down_revision: str | Sequence[str] | None = "9d3e5a7c1f24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("state_facts", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("state_facts")
