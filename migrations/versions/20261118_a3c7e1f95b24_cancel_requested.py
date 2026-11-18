"""cancel request: when a person asked for a converting document to stop

One nullable column on `jobs`: `cancel_requested_at`. A queued document is stopped by the
request at once; a running one is stopped by the worker at its next beat (`web.worker`), which
ends it with the error code `canceled`. Additive; existing rows get NULL; the downgrade drops
the column.

Revision ID: a3c7e1f95b24
Revises: 4e8a1c7d9b35
Create Date: 2026-11-18 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c7e1f95b24"
down_revision: str | Sequence[str] | None = "4e8a1c7d9b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("cancel_requested_at")
