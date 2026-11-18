"""class links to a document's published student version

`job_links` (`web.links`, WS27.2): one row per link the owner of a finished document makes.
Only a SHA-256 of the token is stored. The row holds the label the owner typed, when the link
opens and closes, whether it is paused or removed, and how many times it was opened and when
first: no person and no address. Rows go with the document, and 30 days after the link closes
or is removed. The published copy it serves is a `blobs` row (kind `published`), so no column
of `jobs` changes. Additive; nothing exists until `FEATURE_CLASS_LINK` is turned on. The
downgrade drops the table.

Revision ID: b7d2f4a8c613
Revises: a9c3e5b7d1f2
Create Date: 2026-11-18 18:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2f4a8c613"
down_revision: str | Sequence[str] | None = "a9c3e5b7d1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_links",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=8), server_default="class", nullable=False),
        sa.Column("label", sa.String(length=80), server_default="", nullable=False),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("open_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    with op.batch_alter_table("job_links", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_job_links_closes_at"), ["closes_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_job_links_job_id"), ["job_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("job_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_links_job_id"))
        batch_op.drop_index(batch_op.f("ix_job_links_closes_at"))
    op.drop_table("job_links")
