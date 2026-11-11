"""step facts: what model work and how much time each job took, and what stopped early

- `job_calls`: one row for each request a job sent to a model, with its step, page, model,
  tokens, seconds, dollars and how it ended (`core.types.CallFact`).
- `job_stages`: the wall time of each stage of a job's last conversion, and the time it
  waited before a worker took it (`core.types.StageFact`).
- `job_not_finished`: each step of a job's last conversion that stopped before it had done
  all of its work, with the page, the reason code and how much was done
  (`core.types.NotFinished`).

All three hold codes, counts, seconds and dollars only, are written by the worker when a job
is stored (`web.step_facts`) and read by `readablestem ops step-report` and the admin page.
Document-derived: deleted with the job (`ondelete=CASCADE`, and `ops.retention`).

Additive: an older release neither reads nor writes them. The downgrade drops all three.

Revision ID: 8b1d5f3a9e04
Revises: c2f4a8d61e57
Create Date: 2026-11-11 16:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b1d5f3a9e04"
down_revision: str | Sequence[str] | None = "c2f4a8d61e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_calls",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("step", sa.String(length=32), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("counted", sa.Boolean(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("is_fallback", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("job_calls", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_job_calls_job_id"), ["job_id"], unique=False)
    op.create_table(
        "job_stages",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("job_stages", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_job_stages_job_id"), ["job_id"], unique=False)
    op.create_table(
        "job_not_finished",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("step", sa.String(length=32), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("job_not_finished", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_job_not_finished_job_id"), ["job_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("job_not_finished", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_not_finished_job_id"))
    op.drop_table("job_not_finished")
    with op.batch_alter_table("job_stages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_stages_job_id"))
    op.drop_table("job_stages")
    with op.batch_alter_table("job_calls", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_calls_job_id"))
    op.drop_table("job_calls")
