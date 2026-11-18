"""the language a person chose when they uploaded a document

One nullable column on `jobs` and on `pending_uploads` (the large file kept while its pages
are chosen): `language`, a tag from the upload form's "Language of the document" list, NULL
for "Detect it". The worker hands it to the conversion (`ConversionOptions.language`), and the
text pass then takes it as the document's language, stated (`lang_source` "user"), without
detection. Additive; existing rows and an older release that inserts a row without the column
read as "Detect it"; the downgrade drops the columns.

Revision ID: c5d9e1a3b7f4
Revises: a9c3e5b7d1f2
Create Date: 2026-11-18 20:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d9e1a3b7f4"
down_revision: str | Sequence[str] | None = "a9c3e5b7d1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("jobs", "pending_uploads"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("language", sa.String(length=16), nullable=True))


def downgrade() -> None:
    for table in ("pending_uploads", "jobs"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("language")
