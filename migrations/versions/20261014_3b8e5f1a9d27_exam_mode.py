"""job option: exam or quiz

One column on `jobs`, set from the upload form's "Exam or quiz" box:

- `exam_mode`: the document is an exam or graded assessment. The conversion then describes
  figures by what is shown and never gives the result (`ConversionOptions.exam_mode`,
  `web.notation.conversion_options`).

Additive, with a server default of false, so rows that an older release inserts after a
traffic rollback are valid. The downgrade drops the column.
`tests/test_web_migrations.py` and `tests/test_web_postgres.py` check that the migrated
schema matches web/db.py and that the downgrade removes all of it.

Revision ID: 3b8e5f1a9d27
Revises: 7c1d9a4e2b60
Create Date: 2026-10-14 15:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3b8e5f1a9d27"
down_revision: str | Sequence[str] | None = "7c1d9a4e2b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("exam_mode", sa.Boolean(), server_default=sa.false(), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("exam_mode")
