"""How `assemble` writes a TABLE element (owned by the tables-step builder).

`render_table_element` is the one place a table element becomes HTML, so table work
lands here and not in `assemble.py` (docs/design/content-features.md). It writes:

- a rebuilt table, when the fix has a usable one (`Fix.table`, its status not in
  `KEPT_ORIGINAL_STATUSES`, a data table): `table_html.render_table` inside a wrapper
  that scrolls sideways on a narrow screen (WCAG 1.4.10 lets a data table keep its two
  dimensions) and can take focus, so keyboard users can scroll it too;
- otherwise the image of the table, kept and flagged like a figure or an equation that
  was not converted: a fixed alt text, the caption, a border and a "Not converted."
  note (never colour alone). `data-reason` says why ("table_not_rebuilt" when rebuilding
  tables is off, a step's reason, or "no_fix" / "no_output" from assembly).

A table's caption (`Element.caption`, copied to `TableGrid.caption`) and its cells are
plain text: layout puts no inline-equation markers in them (maths in a cell is
`TableCell.latex`/`mathml`), so both are written escaped.

Rendering depends on the stored element and fix only, never on `ConversionOptions`: the
review screen re-renders a stored result without them (web/review.py `rerender_html`).
"""

from __future__ import annotations

import re
from typing import Protocol

from a11y_stem.core.types import KEPT_ORIGINAL_STATUSES, Element, Fix, TableGrid
from a11y_stem.pipeline.safe_html import escape_text
from a11y_stem.pipeline.table_html import render_table

_NOT_ID = re.compile(r"[^A-Za-z0-9_.:\-]")

TABLE_NOT_CONVERTED_ALT = "Table not converted"
TABLE_NOTE = (
    "This table is shown as an image of the original, so screen readers cannot read its "
    "rows and columns. Review it before publishing."
)
TABLE_FLAG_LABEL = "Not converted."
# Added to the stylesheet of documents that contain a rebuilt table (and only those).
TABLE_CSS = """\
.table-scroll { margin: 1.5em 0; overflow-x: auto; }
table { border-collapse: collapse; }
caption { caption-side: top; text-align: left; font-weight: 700; padding: 0 0 0.4em; }
th, td { border: 1px solid #5c5c5c; padding: 0.3em 0.6em; text-align: left;
  vertical-align: top; }
thead th { border-bottom-width: 3px; }
"""


class RenderContext(Protocol):
    """What `assemble`'s renderer offers a feature module (it implements all of it)."""

    def id_attr(self, element_id: str) -> str:
        """' id="..."' the first time an element id is used, else ""."""
        ...

    def original(self, element: Element, alt: str) -> str:
        """The element's crop as an <img alt> (or its text when there is no crop)."""
        ...

    def flag_block(self, element: Element, reason: str, body: str, label: str, note: str) -> str:
        """`body` outlined and labelled as not converted, with a note (role="note")."""
        ...

    def add_style(self, name: str, css: str) -> None:
        """Add CSS to this document's stylesheet (once per name)."""
        ...


def rebuilt_grid(fix: Fix | None) -> TableGrid | None:
    """The grid to write as a table, or None when the image is kept."""
    if fix is None or fix.status in KEPT_ORIGINAL_STATUSES or fix.table is None:
        return None
    grid = fix.table.grid
    return grid if grid.is_data and grid.cells else None


def id_prefix(element_id: str) -> str:
    """The element id as a prefix `render_table` accepts (ids are "p1-e3" already)."""
    prefix = _NOT_ID.sub("-", element_id)[:64]
    return prefix if prefix[:1].isalpha() else f"t{prefix}"[:64]


def render_table_element(element: Element, fix: Fix | None, ctx: RenderContext) -> str:
    grid = rebuilt_grid(fix)
    if grid is not None:
        ctx.add_style("table", TABLE_CSS)
        prefix = id_prefix(element.id)
        table = render_table(grid, id_prefix=prefix)
        name = (
            f' aria-labelledby="{prefix}-caption"'
            if grid.caption and grid.caption.strip()
            else ' aria-label="Table"'
        )
        attrs = ctx.id_attr(element.id)
        return f'<div class="table-scroll"{attrs} role="group"{name} tabindex="0">{table}</div>'
    if fix is None:
        reason = "no_fix"
    elif fix.status in KEPT_ORIGINAL_STATUSES:
        reason = fix.reason or ""
    else:
        reason = "no_output"
    caption = (
        f"<figcaption>{escape_text(' '.join(element.caption.split()))}</figcaption>"
        if element.caption and element.caption.strip()
        else ""
    )
    body = f"<figure>{ctx.original(element, TABLE_NOT_CONVERTED_ALT)}{caption}</figure>"
    return ctx.flag_block(element, reason, body, TABLE_FLAG_LABEL, TABLE_NOTE)
