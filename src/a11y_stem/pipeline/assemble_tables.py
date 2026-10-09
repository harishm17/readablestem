"""How `assemble` writes a TABLE element (owned by the tables-step builder).

`render_table_element` is the one place a table element becomes HTML, so table work
lands here and not in `assemble.py` (docs/design/content-features.md). It writes:

- a rebuilt table, when the fix has a usable one (`Fix.table`, its status not in
  `KEPT_ORIGINAL_STATUSES`, a data table): `table_html.render_table` inside a wrapper
  that scrolls sideways on a narrow screen (WCAG 1.4.10 lets a data table keep its two
  dimensions). Whether a table is wider than the screen is only known in the reader's
  browser, and the file works without scripts, so every rebuilt table gets the wrapper:
  a narrow one simply does not scroll. A table that may be too wide for a 320 px screen
  (`estimate_min_width_em`) also takes focus, so keyboard users can scroll it, and is a
  named group (the caption, else "Table"), so the extra tab stop says what it is. A
  table with no caption of its own gets the element's "Table N" caption, which layout
  took out of the text, so the caption is never lost;
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
import unicodedata
from typing import Protocol

from a11y_stem.core.types import KEPT_ORIGINAL_STATUSES, Element, Fix, TableCell, TableGrid
from a11y_stem.pipeline.safe_html import escape_text
from a11y_stem.pipeline.table_html import render_table

_NOT_ID = re.compile(r"[^A-Za-z0-9_.:\-]")

TABLE_NOT_CONVERTED_ALT = "Table not converted"
TABLE_NOTE = (
    "This table is shown as an image of the original, so screen readers cannot read its "
    "rows and columns. Ask your instructor for a version you can read."
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
# A table estimated at least this wide (em of the body text) may not fit a 320 CSS px
# screen: the column is then 288 px, 16 em of the 18 px body text. The estimate counts
# 0.6 em a character (system-ui averages about 0.5, bold header text a little more) and
# 1.4 em of padding and borders a cell, so it errs wide: one more tab stop is cheap, a
# table that overflows without one cannot be scrolled from the keyboard.
WIDE_TABLE_EM = 13.0
_CHAR_EM = 0.6
_CELL_EDGES_EM = 1.4  # 0.6 em padding either side, and the borders
_MATH_SCALE = 1.1  # maths is set at 1.1 em (assemble.STYLE)


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


# Spacing accents a text layer may give for maths accents (as tables._ACCENTS).
_SPACING_ACCENTS = frozenset("\u02c6\u02dc\u00af\u02d9\u00a8\u02c7\u02d8")
# A superscript character with a baseline digit right after it: the numerator of a
# stacked fraction, raised, before its denominator (K = ¹2mv² for K = ½mv²).
_STACKED_DIGITS = re.compile("[\u00b9\u00b2\u00b3\u2070\u2074-\u2079][0-9]")


def flattened_maths(cell: TableCell) -> bool:
    """Does this cell of the grid read from the PDF hold maths its text gives flat, so it
    must not be shown as it is: an accent (a vector arrow, a hat) as a combining mark or
    a spacing accent, a stacked fraction (a raised digit before a baseline one), or a
    raised run written "^(...)". A maths cell (`latex`) and a superscript on its own
    ("m/s²", "10⁵") read fine. Pure: the text is all it looks at."""
    if cell.latex is not None or not cell.text:
        return False
    text = cell.text
    if "^(" in text or _STACKED_DIGITS.search(text):
        return True
    return any(unicodedata.category(ch) == "Mn" or ch in _SPACING_ACCENTS for ch in text)


def flattened_cells(grid: TableGrid) -> list[TableCell]:
    """The cells of a grid read from the PDF (source "local") that hold flattened maths;
    none for a model's grid, whose maths cells are LaTeX."""
    if grid.source != "local":
        return []
    return [c for c in grid.cells if flattened_maths(c)]


def rebuilt_grid(fix: Fix | None) -> TableGrid | None:
    """The grid to write as a table, or None when the image is kept. A grid read from the
    PDF with flattened maths in it is never written (`flattened_cells`): "K = 12mv2"
    for K = ½mv² would read as something else; the image is kept until a
    reviewer writes those cells as LaTeX."""
    if fix is None or fix.status in KEPT_ORIGINAL_STATUSES or fix.table is None:
        return None
    grid = fix.table.grid
    if flattened_cells(grid):
        return None
    return grid if grid.is_data and grid.cells else None


def id_prefix(element_id: str) -> str:
    """The element id as a prefix `render_table` accepts (ids are "p1-e3" already)."""
    prefix = _NOT_ID.sub("-", element_id)[:64]
    return prefix if prefix[:1].isalpha() else f"t{prefix}"[:64]


def _text_width_em(text: str) -> float:
    """Width of the longest word of `text` (a table cell wraps between words only)."""
    return max((len(word) for word in text.split()), default=0) * _CHAR_EM


def estimate_min_width_em(grid: TableGrid) -> float:
    """Approximate narrowest width of the rendered table, in em of the body text: per
    column its widest unbreakable content (the longest word, or the whole of a maths
    cell), plus each cell's padding and borders. A spanning cell wider than the columns
    it covers widens them. Errs wide on purpose (see `WIDE_TABLE_EM`)."""
    from a11y_stem.pipeline.assemble import estimate_width_em  # assemble imports this module

    def content_em(cell: TableCell) -> float:
        if cell.mathml:
            return estimate_width_em(cell.mathml) * _MATH_SCALE
        return _text_width_em(cell.text)

    columns = [0.0] * grid.cols
    spanning = []
    for cell in grid.cells:
        if cell.col >= grid.cols:
            continue
        if cell.col_span == 1:
            columns[cell.col] = max(columns[cell.col], content_em(cell))
        else:
            spanning.append(cell)
    for cell in spanning:
        covered = range(cell.col, min(cell.col + cell.col_span, grid.cols))
        room = sum(columns[c] for c in covered) + _CELL_EDGES_EM * (len(covered) - 1)
        extra = content_em(cell) - room
        for c in covered if extra > 0 else ():
            columns[c] += extra / len(covered)
    return sum(columns) + _CELL_EDGES_EM * grid.cols


def _scroll_attrs(grid: TableGrid, prefix: str) -> str:
    """Attributes that make a wide table's box a named tab stop ("" if not wide).

    `.table-scroll` scrolls sideways when the table is wider than the screen (WCAG
    1.4.10: the table scrolls, not the page). Keyboard users can only scroll a box that
    can take focus (axe `scrollable-region-focusable`), but a tab stop on every small
    table would make a long document tedious to tab through, so only tables that may be
    too wide for a phone get one, named by the caption (else "Table"), as equations do.
    """
    if estimate_min_width_em(grid) < WIDE_TABLE_EM:
        return ""
    name = (
        f'aria-labelledby="{prefix}-caption"'
        if grid.caption and grid.caption.strip()
        else 'aria-label="Table"'
    )
    return f' role="group" {name} tabindex="0"'


def render_table_element(element: Element, fix: Fix | None, ctx: RenderContext) -> str:
    grid = rebuilt_grid(fix)
    if grid is not None:
        ctx.add_style("table", TABLE_CSS)
        prefix = id_prefix(element.id)
        if not (grid.caption and grid.caption.strip()) and element.caption:
            # Layout took the "Table N" caption out of the text, so a proposal without it
            # (an edited grid) would lose it: it is the table's caption either way.
            grid = grid.model_copy(update={"caption": element.caption})
        table = render_table(grid, id_prefix=prefix)
        attrs = ctx.id_attr(element.id) + _scroll_attrs(grid, prefix)
        return f'<div class="table-scroll"{attrs}>{table}</div>'
    if fix is None:
        reason = "no_fix"
    elif fix.status in KEPT_ORIGINAL_STATUSES:
        reason = fix.reason or ""
    elif fix.table is not None and flattened_cells(fix.table.grid):
        reason = "flattened_maths"
    else:
        reason = "no_output"
    caption = (
        f"<figcaption>{escape_text(' '.join(element.caption.split()))}</figcaption>"
        if element.caption and element.caption.strip()
        else ""
    )
    body = f"<figure>{ctx.original(element, TABLE_NOT_CONVERTED_ALT)}{caption}</figure>"
    return ctx.flag_block(element, reason, body, TABLE_FLAG_LABEL, TABLE_NOTE)
