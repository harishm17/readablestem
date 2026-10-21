"""open sign-up: users.access_tier, the open spend pool, re-read counts, daily sign-ups

All additive, each new column with a server default, so a release from before this one keeps
working on the migrated schema (a traffic rollback between `migrate` and `service`):

- `users.access_tier` ("invited" or "open"). Every existing row becomes "invited" through the
  default: everyone who is on the allowlist today was added by an admin or the command line.
- `spend_days.open_spent_usd` and `open_held_usd`: the part of the day's spend that belongs
  to jobs of open-tier accounts (the pool `OPEN_TIER_DAILY_SPEND_CAP_USD` limits).
- `quota_days.rereads`: model reads from review started per person per day.
- `signup_days`: accounts made by open sign-up per UTC day (`MAX_NEW_ACCOUNTS_PER_DAY`).
- `spend_holds`: one row per open reservation of the spend guard, so a hold cannot outlive
  the process that made it (`web.spend`, released by job and by age). Empty at the start.
- `deleted_accounts`: what an account deleted today had used, under a hash of its address,
  for the rest of that day (`web.tombstones`). Empty at the start.

Nothing changes while `SIGNUP_MODE` is "allowlist" (the default). The downgrade drops the
columns and the table. `tests/test_web_migrations.py` and `tests/test_web_postgres.py` check
that the migrated schema matches web/db.py.

Revision ID: a6c2e9f4b713
Revises: d3f8a1c5b920
Create Date: 2026-10-21 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6c2e9f4b713"
down_revision: str | Sequence[str] | None = "d3f8a1c5b920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("access_tier", sa.String(length=8), server_default="invited", nullable=False)
        )
    # The default already gives every existing row "invited"; said again so the backfill
    # does not depend on how a database applies a default to old rows.
    op.execute("UPDATE users SET access_tier = 'invited' WHERE access_tier IS NULL")
    with op.batch_alter_table("spend_days", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("open_spent_usd", sa.Float(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("open_held_usd", sa.Float(), server_default="0", nullable=False)
        )
    with op.batch_alter_table("quota_days", schema=None) as batch_op:
        batch_op.add_column(sa.Column("rereads", sa.Integer(), server_default="0", nullable=False))
    op.create_table(
        "signup_days",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("accounts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("day"),
    )
    op.create_table(
        "spend_holds",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("in_pool", sa.Boolean(), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("spend_holds", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_spend_holds_day"), ["day"], unique=False)
        batch_op.create_index(batch_op.f("ix_spend_holds_job_id"), ["job_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_spend_holds_created_at"), ["created_at"], unique=False)
    op.create_table(
        "deleted_accounts",
        sa.Column("email_hash", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("rereads", sa.Integer(), nullable=False),
        sa.Column("slot_taken", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("email_hash", "day"),
    )
    with op.batch_alter_table("deleted_accounts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_deleted_accounts_day"), ["day"], unique=False)


def downgrade() -> None:
    op.drop_table("deleted_accounts")
    op.drop_table("spend_holds")
    op.drop_table("signup_days")
    with op.batch_alter_table("quota_days", schema=None) as batch_op:
        batch_op.drop_column("rereads")
    with op.batch_alter_table("spend_days", schema=None) as batch_op:
        batch_op.drop_column("open_held_usd")
        batch_op.drop_column("open_spent_usd")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("access_tier")
