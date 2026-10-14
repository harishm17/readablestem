"""large files converted in parts

- `pending_uploads`: a PDF over the page or size limit, kept while the person chooses which
  of its pages to convert (T3-3, `web/pending.py`). Document content: deleted at
  `retain_until`, when its last page was sent, by the person, and with the person. The
  course is a foreign key that sets itself to NULL when the course is deleted.
- `jobs.part_group`, `jobs.part_label`: the document is one part of a larger file, and the
  pages it holds ("31-59"). `part_group` is the id of the pending upload it was cut from,
  not a foreign key (that row is deleted when its last page has been sent).

Additive and nullable (the table is new), so rows that an older release inserts after a
traffic rollback are valid. The downgrade drops all of it; a part keeps its result either
way, and a file still waiting for its pages is lost (it is a copy of what the person
uploaded, kept for at most a day).

Revision ID: a8c4e0b61d97
Revises: f7a3c9d20b15
Create Date: 2026-10-17 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8c4e0b61d97"
down_revision: str | Sequence[str] | None = "f7a3c9d20b15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_uploads",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("course_id", sa.String(length=32), nullable=True),
        sa.Column("exam_mode", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("retention_mode", sa.String(length=8), server_default="day", nullable=False),
        sa.Column("done_pages", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_pending_uploads_course_id_courses",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("pending_uploads", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_pending_uploads_user_id"), ["user_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_pending_uploads_retain_until"), ["retain_until"], unique=False
        )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("part_group", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("part_label", sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f("ix_jobs_part_group"), ["part_group"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_part_group"))
        batch_op.drop_column("part_label")
        batch_op.drop_column("part_group")
    with op.batch_alter_table("pending_uploads", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_pending_uploads_retain_until"))
        batch_op.drop_index(batch_op.f("ix_pending_uploads_user_id"))
    op.drop_table("pending_uploads")
