"""Table step: a detected data table -> a rebuilt grid (owned by the tables-step builder).

The contract is the other steps' (`pipeline.run` calls it the same way, on a pool
thread, with the table's crop): `rebuild(element, png, *, client, settings,
context_text) -> Fix` with `kind=Kind.TABLE`, the proposal on `Fix.table`, and every
model call on `Fix.calls`. The options in force (`pipeline.options.current()`) say
whether rebuilding is on (`ConversionOptions.tables`).

Until the step is built, and whenever rebuilding is off, the table is kept as an image of
the table with a required decision: FAILED with reason "table_not_rebuilt", decided
before any model work (`assemble_tables` writes the flagged image).
"""

from __future__ import annotations

from a11y_stem.config import Settings
from a11y_stem.core.types import Element, Fix, Kind, Status
from a11y_stem.models.base import VisionClient

# Declared prompt version (None until the step sends prompts; see run._prompt_versions).
PROMPT_VERSION: str | None = None


def rebuild(
    element: Element,
    png: bytes,
    *,
    client: VisionClient,
    settings: Settings,
    context_text: str | None = None,
) -> Fix:
    """The table's fix. For now: kept as an image, a required decision."""
    return Fix(
        element_id=element.id, kind=Kind.TABLE, status=Status.FAILED, reason="table_not_rebuilt"
    )
