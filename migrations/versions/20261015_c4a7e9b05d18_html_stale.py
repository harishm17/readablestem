"""a flag for an HTML file that is out of date

One column on `jobs`:

- `html_stale`: a review write changed `result_json` and the HTML file (and the static
  checks stored with the result) have not been made again yet. They are made when somebody
  asks for the file or for the checks of it: a download, the view, the Canvas version, the
  job page. A run of review clicks therefore costs one render instead of one each.

Additive, with a server default of false, so rows that an older release inserts after a
traffic rollback are valid (that release makes the file at every write, as before). The
downgrade drops the column; a file marked stale would then be served out of date by the
older release, so the downgrade first empties `html` for those rows (the older release
answers "not ready" for them rather than hand out a file from before the last decision),
and the upgrade marks a finished job without a file stale, so it is made again.

`tests/test_web_migrations.py` and `tests/test_web_postgres.py` check that the migrated
schema matches web/db.py and that the downgrade removes all of it.

Revision ID: c4a7e9b05d18
Revises: 9d2f6a41c7e3
Create Date: 2026-10-15 10:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a7e9b05d18"
down_revision: str | Sequence[str] | None = "9d2f6a41c7e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("html_stale", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
    op.execute(
        sa.text(
            "UPDATE jobs SET html_stale = :yes "
            "WHERE html IS NULL AND status = 'done' AND result_json IS NOT NULL"
        ).bindparams(yes=True)
    )


def downgrade() -> None:
    op.execute(sa.text("UPDATE jobs SET html = NULL WHERE html_stale = :yes").bindparams(yes=True))
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("html_stale")
