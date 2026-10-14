"""a recorded sample document outside the quota

One column on `jobs`:

- `is_sample`: the document is the recorded sample (T3-9, `web/sample.py`): a finished
  conversion made once, ahead of time, put in a person's list so they can practise on the
  review screen before uploading a file. It uses no pages, no spend and no model call, takes
  no place from the documents a person may keep, and is left out of the usage numbers.

Additive, with a server default of false, so rows that an older release inserts after a
traffic rollback are valid. The downgrade deletes the sample documents first (an older
release would count them as the person's own, against their cap and the beta's numbers),
with the pictures cut from their pages and the review edits made on them, then drops the
column.

Revision ID: b1d5f7a29c46
Revises: a8c4e0b61d97
Create Date: 2026-10-18 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1d5f7a29c46"
down_revision: str | Sequence[str] | None = "a8c4e0b61d97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_sample", sa.Boolean(), server_default=sa.false(), nullable=False)
        )


def downgrade() -> None:
    samples = "SELECT id FROM jobs WHERE is_sample = :yes"
    for table in ("blobs", "review_edits", "step_records"):
        op.execute(sa.text(f"DELETE FROM {table} WHERE job_id IN ({samples})").bindparams(yes=True))
    op.execute(sa.text("DELETE FROM jobs WHERE is_sample = :yes").bindparams(yes=True))
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("is_sample")
