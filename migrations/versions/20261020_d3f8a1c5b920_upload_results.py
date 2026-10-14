"""upload results: the answer to an upload of several files, kept for an hour

One new table, additive: `upload_results` holds what became of each file of one upload
(queued, kept for choosing its pages, or refused and why), so the answer is a page opened by
a redirect and a refresh does not upload again. Rows go after an hour and with the person.

The downgrade drops the table. `tests/test_web_migrations.py` and `tests/test_web_postgres.py`
check that the migrated schema matches web/db.py.

Revision ID: d3f8a1c5b920
Revises: 5e2b8d70a3c1
Create Date: 2026-10-20 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3f8a1c5b920"
down_revision: str | Sequence[str] | None = "5e2b8d70a3c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "upload_results",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("upload_results", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_upload_results_user_id"), ["user_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_upload_results_retain_until"), ["retain_until"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("upload_results", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_upload_results_retain_until"))
        batch_op.drop_index(batch_op.f("ix_upload_results_user_id"))
    op.drop_table("upload_results")
