"""the documents list's facts on the job

Two columns on `jobs`, what My documents shows of a stored result:

- `open_decisions`: decisions still open in the stored result (a heading group is one).
- `result_title`: the title in the stored result.

With them on the job the list, polled every few seconds while anything converts, no longer
loads and validates the fixes of every finished document. Both are set whenever a result is
stored (`web/db.py`); a finished job from before this release has them NULL, and the list
fills them in the first time it sees the job.

Additive and nullable, so rows that an older release inserts after a traffic rollback are
valid (the list fills those in too). An older release that keeps writing `result_json`
leaves the two facts as they were; they are set again by the next write of this release.
The downgrade drops the columns. `tests/test_web_migrations.py` and
`tests/test_web_postgres.py` check that the migrated schema matches web/db.py and that the
downgrade removes all of it.

Revision ID: 9d2f6a41c7e3
Revises: 3b8e5f1a9d27
Create Date: 2026-10-15 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d2f6a41c7e3"
down_revision: str | Sequence[str] | None = "3b8e5f1a9d27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("open_decisions", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("result_title", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("result_title")
        batch_op.drop_column("open_decisions")
