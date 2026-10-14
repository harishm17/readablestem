"""trying a stopped conversion again without paying for the finished steps

- `step_records`: the answer of each finished model step of a job (a `Fix` as JSON, under
  the key of the question it answered), written while the job converts and read when the
  person asks to try it again (T3-1, `web/step_cache.py`). Document-derived: deleted with
  the job (`ondelete=CASCADE`, and `ops.retention.delete_jobs`).
- `jobs.retries`: how many times the person asked for a job to be tried again (capped).
- `jobs.attempts_at_retry`: `attempts` when the last try-again began, so that the limit on
  interrupted attempts (`worker.MAX_ATTEMPTS`) counts from there.

Additive with server defaults of 0, so rows that an older release inserts after a traffic
rollback are valid. The downgrade drops all three; an older release converts a job
from the start whatever was recorded.

Revision ID: f7a3c9d20b15
Revises: e2b6d1f84a30
Create Date: 2026-10-16 11:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a3c9d20b15"
down_revision: str | Sequence[str] | None = "e2b6d1f84a30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "step_records",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("element_id", sa.String(length=64), nullable=False),
        sa.Column("input_key", sa.String(length=64), nullable=False),
        sa.Column("fix_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "element_id", name="uq_step_record_slot"),
    )
    with op.batch_alter_table("step_records", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_step_records_job_id"), ["job_id"], unique=False)
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("retries", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(
            sa.Column("attempts_at_retry", sa.Integer(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("attempts_at_retry")
        batch_op.drop_column("retries")
    with op.batch_alter_table("step_records", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_step_records_job_id"))
    op.drop_table("step_records")
