"""every output has a version number and the time of its last change

Two columns on `jobs` (`web.output.output_changed`): `output_version`, how many changes have
reached the document's output, from 1, and `output_changed_at`, when the last one did. The
version id a person reads is the first six characters of the job id and this number
(`b9487b-12`). Existing jobs get version 1 and the time of their last decision, or of their
finish when none was made, or of their upload when neither exists. Additive; an older release
that inserts a job without the columns gets version 1 and no time (read as its finish time);
the downgrade drops the columns.

Revision ID: a9c3e5b7d1f2
Revises: e4b8c2d6a193
Create Date: 2026-11-18 16:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9c3e5b7d1f2"
down_revision: str | Sequence[str] | None = "e4b8c2d6a193"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("output_version", sa.Integer(), server_default="1", nullable=False)
        )
        batch_op.add_column(sa.Column("output_changed_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE jobs SET output_changed_at = COALESCE("
        "(SELECT MAX(review_edits.created_at) FROM review_edits "
        "WHERE review_edits.job_id = jobs.id), jobs.finished_at, jobs.created_at)"
    )


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("output_changed_at")
        batch_op.drop_column("output_version")
