"""decision outcomes: what a reviewer did, as content-free columns on `events`

Eight nullable short columns on `events` (`kind`, `reason`, `outcome`, `origin`, `source`,
`mode`, `write_mode`, `release`), filled only on a `decision_outcome` row, and an index on
(name, reason, release) for the counts grouped by release (`web.events.outcome_cells`).
Existing rows keep NULL in all of them. Additive; the downgrade drops the index and the
columns.

Revision ID: c2f4a8d61e57
Revises: 5d8e1f2a7c39
Create Date: 2026-11-11 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2f4a8d61e57"
down_revision: str | Sequence[str] | None = "5d8e1f2a7c39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("kind", 16),
    ("reason", 64),
    ("outcome", 16),
    ("origin", 16),
    ("source", 16),
    ("mode", 16),
    ("write_mode", 16),
    ("release", 40),
)


def upgrade() -> None:
    with op.batch_alter_table("events", schema=None) as batch_op:
        for name, length in COLUMNS:
            batch_op.add_column(sa.Column(name, sa.String(length=length), nullable=True))
        batch_op.create_index(
            "ix_events_name_reason_release", ["name", "reason", "release"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index("ix_events_name_reason_release")
        for name, _ in reversed(COLUMNS):
            batch_op.drop_column(name)
