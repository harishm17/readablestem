"""problem reports to the operator

`reports`: one row per report written from "Report this item" and from a stopped
document's page (`web.reports`). Ids, codes and what the sender typed; no picture and no
document text. `job_ref` is not a foreign key, so a report outlives its document. Deleted
with the sender's account, 30 days after it is closed and 180 days after it was made.
Additive; the downgrade drops the table.

Revision ID: e4b7a1c9d352
Revises: 9d3e5a7c1f24
Create Date: 2026-11-18 15:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7a1c9d352"
down_revision: str | Sequence[str] | None = "9d3e5a7c1f24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="tool", nullable=False),
        sa.Column("from_user_id", sa.String(length=32), nullable=False),
        sa.Column("job_ref", sa.String(length=32), nullable=True),
        sa.Column("item_ref", sa.String(length=64), nullable=True),
        sa.Column("item_kind", sa.String(length=16), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("pipeline_version", sa.String(length=40), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=8), server_default="open", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["from_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_reports_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_reports_from_user_id"), ["from_user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_reports_from_user_id"))
        batch_op.drop_index(batch_op.f("ix_reports_created_at"))
    op.drop_table("reports")
