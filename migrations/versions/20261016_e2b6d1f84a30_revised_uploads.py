"""a revised upload carries the earlier version's decisions

Two columns on `jobs`:

- `revises_job_id`: the id of the earlier version of the document this job revises (T3-2,
  `web/carry.py`). Not a foreign key: the earlier version may be deleted before this one.
- `carried_json`: what happened to the earlier decisions, as element ids and counts
  (`carry.CarryReport.to_json`) and the earlier file's name; document-derived, so it is
  deleted with the job.

Additive and nullable, so rows that an older release inserts after a traffic rollback are
valid. The downgrade drops both columns; a job keeps its decisions either way, since the
carried decisions are written into the job's own result.

`tests/test_web_migrations.py` and `tests/test_web_postgres.py` check that the migrated
schema matches web/db.py and that the downgrade removes all of it.

Revision ID: e2b6d1f84a30
Revises: c4a7e9b05d18
Create Date: 2026-10-16 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b6d1f84a30"
down_revision: str | Sequence[str] | None = "c4a7e9b05d18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("revises_job_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("carried_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("carried_json")
        batch_op.drop_column("revises_job_id")
