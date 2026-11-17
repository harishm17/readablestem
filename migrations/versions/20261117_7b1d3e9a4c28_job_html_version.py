"""job html version: the render version of a stored page, beside it

One nullable integer column, `jobs.html_version`: the render version written in the head of
`jobs.html` (`pipeline.assemble.render_version`), set whenever the page is stored. With it a
status poll or a download asks "is this page made by an older renderer" and "has a page been
made" without reading the page, which is megabytes for a long document (`jobs.html` and
`jobs.result_json` are now loaded only where a route reads them). Additive; existing rows get
NULL, which the app fills in the first time it looks at the row (`Job.has_html`); the
downgrade drops the column.

Revision ID: 7b1d3e9a4c28
Revises: c2f4a8d61e57
Create Date: 2026-11-17 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b1d3e9a4c28"
down_revision: str | Sequence[str] | None = "c2f4a8d61e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("html_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("html_version")
