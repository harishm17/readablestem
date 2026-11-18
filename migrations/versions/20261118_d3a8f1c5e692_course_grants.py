"""course grants: a person's right to read every document of a course

`course_grants` (`web.shares`, WS27.1): one row per person the owner of a course gave the right
to view or review its documents. The row holds the address the person must sign in with
(lower-cased) and the right ("view" or "review", checked by the database too). It names no
document: the documents a grant covers are the ones whose `jobs.course_id` is the course at the
time of each request. Rows go with the course (ON DELETE CASCADE) and with the person it names.
Additive; no row has any effect until `FEATURE_COURSE_GRANTS` is turned on. The downgrade drops
the table.

Revision ID: d3a8f1c5e692
Revises: c5d9e1a3b7f4
Create Date: 2026-11-18 20:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3a8f1c5e692"
down_revision: str | Sequence[str] | None = "c5d9e1a3b7f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "course_grants",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("course_id", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('view', 'review')", name="ck_course_grants_role"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "email", name="uq_course_grant_person"),
    )
    with op.batch_alter_table("course_grants", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_course_grants_course_id"), ["course_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_course_grants_email"), ["email"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("course_grants", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_course_grants_email"))
        batch_op.drop_index(batch_op.f("ix_course_grants_course_id"))
    op.drop_table("course_grants")
