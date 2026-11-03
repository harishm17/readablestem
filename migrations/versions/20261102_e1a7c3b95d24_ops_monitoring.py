"""ops monitoring: heartbeats, erasure ledger, per-user daily spend, sign-up cap warning

All additive, each new column with a server default, so a release from before this one keeps
working on the migrated schema:

- `ops_heartbeat`: when the daily backup and the hourly cron last finished well
  (`/healthz/ops`). Empty at the start, so the check stays unhealthy until both have run once.
- `erasure_ledger`: a hash of each erased user id and when, so a restored backup can have the
  erasure repeated (`readablestem ops replay-erasures`). Empty at the start.
- `quota_days.spent_usd`: what a person's jobs cost that day, added when a model call settles.
- `spend_holds.user_id`: the owner of the job a hold belongs to, so the settlement knows whose
  row to add to. Holds are short-lived; existing ones have none and count nowhere per person.
- `signup_days.cap_warned`: whether today's "sign-up cap reached" warning was logged.

The downgrade drops them again.

Revision ID: e1a7c3b95d24
Revises: c4e7a2b9d150
Create Date: 2026-11-02 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1a7c3b95d24"
down_revision: str | Sequence[str] | None = "c4e7a2b9d150"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ops_heartbeat",
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "erasure_ledger",
        sa.Column("user_hash", sa.String(length=64), nullable=False),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_hash"),
    )
    with op.batch_alter_table("quota_days", schema=None) as batch_op:
        batch_op.add_column(sa.Column("spent_usd", sa.Float(), server_default="0", nullable=False))
    with op.batch_alter_table("spend_holds", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.String(length=32), nullable=True))
    with op.batch_alter_table("signup_days", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("cap_warned", sa.Boolean(), server_default=sa.false(), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("signup_days", schema=None) as batch_op:
        batch_op.drop_column("cap_warned")
    with op.batch_alter_table("spend_holds", schema=None) as batch_op:
        batch_op.drop_column("user_id")
    with op.batch_alter_table("quota_days", schema=None) as batch_op:
        batch_op.drop_column("spent_usd")
    op.drop_table("erasure_ledger")
    op.drop_table("ops_heartbeat")
