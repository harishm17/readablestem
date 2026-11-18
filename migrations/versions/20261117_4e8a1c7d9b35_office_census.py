"""office census: what the PDF route did with a Word or PowerPoint upload

One nullable JSON column on `jobs` and one on `pending_uploads`: `office_json`, the census the
upload's preflight counted (`web.office_convert.Census.to_json`: counts only, no text), kept
with the job so the job page can say how the file was read and name a Word file's open tracked
changes. A large file kept for its parts carries it to every part. Additive; existing rows and
every PDF upload get NULL; the downgrade drops the columns.

Revision ID: 4e8a1c7d9b35
Revises: 8b1d5f3a9e04
Create Date: 2026-11-17 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4e8a1c7d9b35"
down_revision: str | Sequence[str] | None = "8b1d5f3a9e04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("office_json", sa.JSON(), nullable=True))
    with op.batch_alter_table("pending_uploads", schema=None) as batch_op:
        batch_op.add_column(sa.Column("office_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pending_uploads", schema=None) as batch_op:
        batch_op.drop_column("office_json")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("office_json")
