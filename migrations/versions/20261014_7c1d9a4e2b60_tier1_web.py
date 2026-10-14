"""job page choices: retention mode, title for students, contact line

Three columns on `jobs`, all set from the job page or the upload form:

- `retention_mode`: "day" (deleted `RETENTION_HOURS` after the last activity), "week" (kept
  7 days, only the owner sees it) or "dev" (kept 30 days, the developer may look). Rows made
  before this release get "dev" when they were uploaded with `opt_in_retention`, else "day".
- `title_override`: the title the instructor chose for students, used instead of the one
  read from the PDF.
- `contact_line`: the contact line (an email or web address) for the student file's footer.

Additive. `retention_mode` has a server default of "day", so rows that an older release
inserts after a traffic rollback are valid; the two text columns are nullable. The
downgrade drops the columns (the older release only knows `opt_in_retention`, which stays
in step with "dev"). `tests/test_web_migrations.py` and `tests/test_web_postgres.py` check
that the migrated schema matches web/db.py and that the downgrade removes all of it.

Revision ID: 7c1d9a4e2b60
Revises: fa3662c01396
Create Date: 2026-10-14 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c1d9a4e2b60"
down_revision: str | Sequence[str] | None = "fa3662c01396"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("retention_mode", sa.String(length=8), server_default="day", nullable=False)
        )
        batch_op.add_column(sa.Column("title_override", sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column("contact_line", sa.String(length=320), nullable=True))
    op.execute("UPDATE jobs SET retention_mode = 'dev' WHERE opt_in_retention")


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("contact_line")
        batch_op.drop_column("title_override")
        batch_op.drop_column("retention_mode")
