"""job shares: a document shared with named, signed-in people

One new table, `job_shares`: the address the person must sign in with (lower-cased), their
right ("view" or "review"), and the job it is for. Additive: no existing table changes, so a
release from before this one keeps working on the migrated schema, and the table starts empty.
The downgrade drops it (every share is lost; the documents are not touched).

Revision ID: b7112167eb0d
Revises: e1a7c3b95d24
Create Date: 2026-11-03 22:25:59.594537+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7112167eb0d"
down_revision: str | Sequence[str] | None = "e1a7c3b95d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_shares",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "email", name="uq_job_share_person"),
    )
    with op.batch_alter_table("job_shares", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_job_shares_email"), ["email"], unique=False)
        batch_op.create_index(batch_op.f("ix_job_shares_job_id"), ["job_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("job_shares", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_shares_job_id"))
        batch_op.drop_index(batch_op.f("ix_job_shares_email"))

    op.drop_table("job_shares")
