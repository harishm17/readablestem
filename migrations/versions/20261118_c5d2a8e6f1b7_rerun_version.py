"""re-run after a new version: the version it was offered for and the spend before it

Three columns on `jobs`: `rerun_version` (the `PIPELINE_VERSION` a finished document was last
converted again for, so a re-run is offered at most once per version) and `cap_base_usd` and
`cap_base_calls` (what the job had spent when that run began, so the run's spend caps count
from there). Additive; existing rows get NULL and 0; the downgrade drops the columns.

Revision ID: c5d2a8e6f1b7
Revises: a3c7e1f95b24
Create Date: 2026-11-18 14:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d2a8e6f1b7"
down_revision: str | Sequence[str] | None = "a3c7e1f95b24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("rerun_version", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("cap_base_usd", sa.Float(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("cap_base_calls", sa.Integer(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("cap_base_calls")
        batch_op.drop_column("cap_base_usd")
        batch_op.drop_column("rerun_version")
