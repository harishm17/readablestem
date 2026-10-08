"""users.session_version: signing out ends copied sessions too

The session cookie is signed, not stored, so clearing it in one browser left any copy
valid for its 7 days. `session_version` is bumped on sign-out and on removal from the
allowlist; a session signed in with an older value no longer signs anyone in
(web/deps.py `current_user`).

Additive, with a server default of 0: rows that an older release inserts after a
traffic rollback get a valid version, and existing sessions (which carry no version,
read as 0) stay signed in.

Revision ID: eb74844c4a9d
Revises: 47b87074b147
Create Date: 2026-10-08 18:00:04.801146+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "eb74844c4a9d"
down_revision: str | Sequence[str] | None = "47b87074b147"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch mode: SQLite before 3.35 cannot drop columns in place (downgrade), and the
    # same code works on Postgres as a plain ALTER TABLE.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("session_version", sa.Integer(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("session_version")
