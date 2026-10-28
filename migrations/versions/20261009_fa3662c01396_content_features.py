"""content features: courses, notation, events, sign-in identities, job progress

The schema for the content features:

- `courses` and `notation_entries`: an instructor's courses and each course's notation
  memory (one entry per canonical symbol). User content, deleted with the user.
- `events`: content-free usage events (a name, a code, a number), deleted with the user.
  `job_ref` is deliberately not a foreign key: a job's events outlive the job.
- `auth_identities`: sign-in identities (Google, Microsoft) linked to a user.
- `jobs.course_id` (ON DELETE SET NULL: deleting a course leaves its jobs),
  `jobs.progress_json` (content-free stage progress) and `jobs.result_version` (bumped
  on every review write, for the stale-tab guard).

Additive. `result_version` has a server default of 0, so rows that an older release
inserts after a traffic rollback are valid; the new tables are unknown to that release
and left alone. Defaults otherwise live in the models (Python side), as in the initial
schema. `tests/test_web_migrations.py` and `tests/test_web_postgres.py` check that the
migrated schema matches web/db.py and that the downgrade removes all of it.

Revision ID: fa3662c01396
Revises: eb74844c4a9d
Create Date: 2026-10-09 06:35:58.500165+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fa3662c01396"
down_revision: str | Sequence[str] | None = "eb74844c4a9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOBS_COURSE_FK = "fk_jobs_course_id_courses"  # named in web/db.py too


def upgrade() -> None:
    op.create_table(
        "auth_identities",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("tenant", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "issuer", "subject", name="uq_auth_identity"),
    )
    op.create_index(
        op.f("ix_auth_identities_user_id"), "auth_identities", ["user_id"], unique=False
    )

    op.create_table(
        "courses",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_courses_user_id"), "courses", ["user_id"], unique=False)

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("job_ref", sa.String(length=32), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_events_created_at"), "events", ["created_at"], unique=False)
    op.create_index(op.f("ix_events_user_id"), "events", ["user_id"], unique=False)

    op.create_table(
        "notation_entries",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("course_id", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("canonical", sa.String(length=64), nullable=False),
        sa.Column("meaning", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "canonical", name="uq_notation_course_canonical"),
    )

    # batch mode: SQLite cannot add a foreign key (or drop a column, in the downgrade) in
    # place, so it rebuilds the table; on Postgres this is a plain ALTER TABLE.
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("course_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("progress_json", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("result_version", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.create_index(batch_op.f("ix_jobs_course_id"), ["course_id"], unique=False)
        batch_op.create_foreign_key(
            JOBS_COURSE_FK, "courses", ["course_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_constraint(JOBS_COURSE_FK, type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_jobs_course_id"))
        batch_op.drop_column("result_version")
        batch_op.drop_column("progress_json")
        batch_op.drop_column("course_id")

    op.drop_table("notation_entries")
    op.drop_index(op.f("ix_events_user_id"), table_name="events")
    op.drop_index(op.f("ix_events_created_at"), table_name="events")
    op.drop_table("events")
    op.drop_index(op.f("ix_courses_user_id"), table_name="courses")
    op.drop_table("courses")
    op.drop_index(op.f("ix_auth_identities_user_id"), table_name="auth_identities")
    op.drop_table("auth_identities")
