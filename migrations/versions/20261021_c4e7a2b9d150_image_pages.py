"""image pages: jobs.image_pages and pending_uploads.image_pages

Additive, each new column with a server default, so a release from before this one keeps
working on the migrated schema (a traffic rollback between `migrate` and `service`):

- `jobs.image_pages`: the upload form's "Convert scanned or handwritten pages" (ADR 0007). Every
  existing row becomes false through the default: those jobs kept such pages as images.
- `pending_uploads.image_pages`: the same choice for a large file waiting for its pages to be
  chosen, applied to every part.

The downgrade drops the columns. `tests/test_web_migrations.py` and `tests/test_web_postgres.py`
check that the migrated schema matches web/db.py.

Revision ID: c4e7a2b9d150
Revises: a6c2e9f4b713
Create Date: 2026-10-21 13:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e7a2b9d150"
down_revision: str | Sequence[str] | None = "a6c2e9f4b713"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("jobs", "pending_uploads"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("image_pages", sa.Boolean(), server_default=sa.false(), nullable=False)
            )


def downgrade() -> None:
    for table in ("pending_uploads", "jobs"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("image_pages")
