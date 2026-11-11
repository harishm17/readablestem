"""job error detail: the names that stopped a LaTeX source

One nullable JSON column, `jobs.error_detail`. A LaTeX source that would lose content outside a
formula (a missing file, an unclosed brace, commands that are not defined) stops with a code
(`jobs.error_code`, a category, never text); this column holds the file and command names from
LaTeX's own report so the job page can name them. It is document content: it is never logged,
it is cleared when the job is tried again, and it goes with the job row. Additive; existing rows
get NULL; the downgrade drops the column.

Revision ID: 3c9a4e7d1b62
Revises: 5d8e1f2a7c39
Create Date: 2026-11-11 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3c9a4e7d1b62"
down_revision: str | Sequence[str] | None = "5d8e1f2a7c39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("error_detail", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("error_detail")
