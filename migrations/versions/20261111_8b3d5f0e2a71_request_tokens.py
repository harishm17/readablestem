"""request tokens: one row per submitted form, so a repeat gets the first answer (SW43)

A new table, `request_tokens` (user, token, form key, outcome), unique on (user, token).
Content-free: a random token, the form's key and the answer (status, redirect target and
the one-time notices). Additive; the downgrade drops the table.

Revision ID: 8b3d5f0e2a71
Revises: c2f4a8d61e57
Create Date: 2026-11-11 18:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b3d5f0e2a71"
down_revision: str | Sequence[str] | None = "c2f4a8d61e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "request_tokens",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("route", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "token", name="uq_request_token_user_token"),
    )
    with op.batch_alter_table("request_tokens", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_request_tokens_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_request_tokens_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("request_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_request_tokens_user_id"))
        batch_op.drop_index(batch_op.f("ix_request_tokens_created_at"))
    op.drop_table("request_tokens")
