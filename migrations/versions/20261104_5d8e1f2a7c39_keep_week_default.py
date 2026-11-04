"""keep week default: an account's "Keep my documents for 7 days by default"

One boolean column, `users.keep_week_default`, false for every existing account. When it is on,
the upload form's "Keep it for 7 days" box starts ticked; each upload still decides. Additive;
the downgrade drops the column.

Revision ID: 5d8e1f2a7c39
Revises: ecb0b22515e0
Create Date: 2026-11-04 02:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d8e1f2a7c39"
down_revision: str | Sequence[str] | None = "ecb0b22515e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("keep_week_default", sa.Boolean(), server_default=sa.false(), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("keep_week_default")
