"""sign-in fallback: linked Google accounts and email sign-in links

Two new tables, both additive:

- `linked_emails`: another Google address an admin allowed for one instructor.
- `login_links`: one-time email sign-in links (only a hash of the token). Unused while
  EMAIL_LOGIN_ENABLED is false.

The downgrade drops both. `tests/test_web_migrations.py` and `tests/test_web_postgres.py`
check that the migrated schema matches web/db.py.

Revision ID: 5e2b8d70a3c1
Revises: c4a7e9b05d18
Create Date: 2026-10-16 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e2b8d70a3c1"
down_revision: str | Sequence[str] | None = "c4a7e9b05d18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "linked_emails",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("linked_emails", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_linked_emails_email"), ["email"], unique=True)
        batch_op.create_index(batch_op.f("ix_linked_emails_user_id"), ["user_id"], unique=False)
    op.create_table(
        "login_links",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    with op.batch_alter_table("login_links", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_login_links_expires_at"), ["expires_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_login_links_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("login_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_login_links_user_id"))
        batch_op.drop_index(batch_op.f("ix_login_links_expires_at"))
    op.drop_table("login_links")
    with op.batch_alter_table("linked_emails", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_linked_emails_user_id"))
        batch_op.drop_index(batch_op.f("ix_linked_emails_email"))
    op.drop_table("linked_emails")
