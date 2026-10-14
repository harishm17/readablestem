"""`TableGrid` -> one accessible HTML `<table>`. Shared by tables and figure data tables.

Follows the W3C WAI tables tutorial (https://www.w3.org/WAI/tutorials/tables/):

- `<caption>` only when the grid has one (a "Table N" caption found in the PDF; never
  invented). It gets the id `{id_prefix}-caption` so a wrapper can be named by it.
- The first `header_rows` rows go in `<thead>`, the rest in `<tbody>`.
- A header cell that is only maths gets its reading (`speech.mathml_to_speech`) as
  visually hidden text, the MathML hidden from assistive technology: browsers name a
  `<th>` from its text, never from MathML, and screen readers announce a cell's
  headers by name.
- Header cells are `<th>`: a column header ("col") gets `scope="col"`, or
  `scope="colgroup"` when it is in the first row and spans several columns (the column
  groups are then written as `<colgroup>` elements, which that scope needs); a row
  header ("row") gets `scope="row"`, which also covers every row it spans. A row header
  across the full width of the body is a section heading: the body is split into one
  `<tbody>` per section and it gets `scope="rowgroup"` (the tutorial's irregular
  headers). An empty header cell (the top-left corner) is written as an empty `<td>`.
- `headers`/`id` only when scope cannot say it (`needs_header_ids`): a row header after
  data cells in its row (two tables side by side), a column header below the header
  rows, or multi-level column headers whose spans do not nest inside the top row's
  groups. Then every header cell gets an id (`{id_prefix}-r{row}c{col}`) and every
  non-empty data cell lists its headers: its section heading, its column headers (the
  nearest block above it, top to bottom) and its row headers (the nearest block to its
  left, left to right).

Untrusted content: cell text and the caption are escaped (`safe_html.escape_text`) and
never read as markup; a maths cell's MathML is written only after `safe_html
.sanitize_mathml` rebuilt it from an allowlisted tree, else the cell's text is shown.
Model HTML is never passed through.

Best effort: a grid with `grid_problems` still renders, and nothing here raises on one.
Cells are placed in reading order (row, then column); a cell whose first slot is taken
is dropped, spans are cut at the grid's edge, at the header rows and at slots already
taken, and a slot no cell covers becomes an empty `<td>`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from readablestem.core.types import TableCell, TableGrid
from readablestem.pipeline.safe_html import Display, escape_text, sanitize_mathml

# The class of the tool's own words (`assemble.AUTHORED_CLASS`: `lang="en"` in a document
# in another language).
AUTHORED_CLASS = "readablestem"
# An id prefix must itself be a plain id, so every id made from it is one too.
_ID_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9_.:\-]{0,63}")


@dataclass
class _Placed:
    """A cell as it is written: anchored at (row, col), with its spans after clipping."""

    cell: TableCell
    row: int
    col: int
    rows: int
    cols: int

    @property
    def is_header(self) -> bool:
        return self.cell.header is not None and not self.cell.is_empty

    @property
    def is_data(self) -> bool:
        return not self.is_header and not self.cell.is_empty

    def columns(self) -> range:
        return range(self.col, self.col + self.cols)


@dataclass
class _Layout:
    """Every slot of the grid mapped to the cell written there."""

    rows: int
    cols: int
    header_rows: int
    placed: list[_Placed] = field(default_factory=list)
    owner: dict[tuple[int, int], _Placed] = field(default_factory=dict)

    @classmethod
    def of(cls, grid: TableGrid) -> _Layout:
        # Header rows that leave no body row are ignored (a grid problem, rendered flat).
        header_rows = grid.header_rows if grid.header_rows < grid.rows else 0
        layout = cls(grid.rows, grid.cols, header_rows)
        order = sorted(
            range(len(grid.cells)), key=lambda i: (grid.cells[i].row, grid.cells[i].col, i)
        )
        for index in order:
            layout._place(grid.cells[index])
        return layout

    def _place(self, cell: TableCell) -> None:
        row, col = cell.row, cell.col
        if row >= self.rows or col >= self.cols or (row, col) in self.owner:
            return
        rows = min(cell.row_span, self.rows - row)
        if row < self.header_rows < row + rows:
            rows = self.header_rows - row  # a header cell never spans into the body
        cols = min(cell.col_span, self.cols - col)
        cols = next((k for k in range(1, cols) if (row, col + k) in self.owner), cols)
        for r in range(row + 1, row + rows):
            if any((r, c) in self.owner for c in range(col, col + cols)):
                rows = r - row
                break
        placed = _Placed(cell, row, col, rows, cols)
        self.placed.append(placed)
        for r in range(row, row + rows):
            for c in range(col, col + cols):
                self.owner[(r, c)] = placed

    def at(self, row: int, col: int) -> _Placed | None:
        return self.owner.get((row, col))

    def anchored(self, row: int, col: int) -> _Placed | None:
        placed = self.owner.get((row, col))
        return placed if placed is not None and (placed.row, placed.col) == (row, col) else None

    # ----- structure

    def is_section(self, placed: _Placed) -> bool:
        """A row header across the whole body width: a heading for the rows below it."""
        return (
            placed.is_header
            and placed.cell.header == "row"
            and placed.row >= self.header_rows
            and placed.col == 0
            and placed.cols == self.cols
            and self.cols > 1
        )

    def section_rows(self) -> list[int]:
        return sorted(p.row for p in self.placed if self.is_section(p))

    def top_groups(self) -> list[range]:
        """The column ranges the first row's cells cover, left to right (a slot no
        cell covers is a group of its own)."""
        groups: list[range] = []
        col = 0
        while col < self.cols:
            placed = self.at(0, col)
            width = placed.cols - (col - placed.col) if placed is not None else 1
            groups.append(range(col, col + width))
            col += width
        return groups

    def colgroup_headers(self) -> bool:
        """The first header row has a column header spanning several columns."""
        return self.header_rows > 0 and any(
            p.row == 0 and p.cols > 1 and p.is_header and p.cell.header == "col"
            for p in self.placed
        )


def needs_header_ids(grid: TableGrid) -> bool:
    """True when `scope` cannot express which headers apply to which cells (see the
    module docstring), so the table is written with `headers`/`id` instead."""
    return _needs_ids(_Layout.of(grid))


def _needs_ids(layout: _Layout) -> bool:
    boundaries = {g.start for g in layout.top_groups()} if layout.header_rows > 1 else set()
    for placed in layout.placed:
        if not placed.is_header:
            continue
        if placed.cell.header == "col":
            if placed.row >= layout.header_rows:
                return True  # a column header below the header rows
            if placed.row > 0 and any(b in boundaries for b in placed.columns()[1:]):
                return True  # crosses a group of the first header row
        elif not layout.is_section(placed):
            left = (layout.at(placed.row, c) for c in range(placed.col))
            if any(other is not None and other.is_data for other in left):
                return True  # a row header after data cells in its row
    return False


def render_table(grid: TableGrid, *, id_prefix: str, math_display: Display = "inline") -> str:
    """The grid as one `<table>` element (see the module docstring). `id_prefix` must be
    unique in the document (e.g. the element id); every id written starts with it."""
    if not _ID_PREFIX.fullmatch(id_prefix):
        raise ValueError("id_prefix must be a plain id (a letter, then letters, digits, -_.:)")
    layout = _Layout.of(grid)
    if not grid.is_data:
        return _layout_table(layout, math_display)
    writer = _Writer(layout, id_prefix, math_display, ids=_needs_ids(layout))
    parts = ["<table>"]
    if grid.caption and grid.caption.strip():
        caption = escape_text(" ".join(grid.caption.split()))
        parts.append(f'<caption id="{id_prefix}-caption">{caption}</caption>')
    if layout.colgroup_headers():
        for group in layout.top_groups():
            span = f' span="{len(group)}"' if len(group) > 1 else ""
            parts.append(f"<colgroup{span}></colgroup>")
    if layout.header_rows:
        parts.append(f"<thead>{writer.rows(range(layout.header_rows))}</thead>")
    starts = [layout.header_rows, *(r for r in layout.section_rows() if r > layout.header_rows)]
    for start, end in zip(starts, [*starts[1:], layout.rows], strict=True):
        if end > start:
            parts.append(f"<tbody>{writer.rows(range(start, end))}</tbody>")
    parts.append("</table>")
    return "".join(parts)


def _layout_table(layout: _Layout, math_display: Display) -> str:
    """A layout table (`is_data` False): cells only, no headers, marked presentational."""
    writer = _Writer(layout, "", math_display, ids=False, plain=True)
    return f'<table role="presentation">{writer.rows(range(layout.rows))}</table>'


@dataclass
class _Writer:
    layout: _Layout
    id_prefix: str
    math_display: Display
    ids: bool  # write headers/id (irregular table)
    plain: bool = False  # layout table: every cell a <td>, no scope

    def rows(self, rows: range) -> str:
        return "".join(f"<tr>{self.row(r)}</tr>" for r in rows)

    def row(self, row: int) -> str:
        cells = []
        for col in range(self.layout.cols):
            placed = self.layout.at(row, col)
            if placed is None:
                cells.append("<td></td>")  # a hole: no cell covers this slot
            elif (placed.row, placed.col) == (row, col):
                cells.append(self.cell(placed))
        return "".join(cells)

    def cell(self, placed: _Placed) -> str:
        attrs = ""
        header = placed.is_header and not self.plain
        if header and self.ids:
            attrs += f' id="{self.header_id(placed)}"'
        if header:
            attrs += f' scope="{self.scope(placed)}"'
        if placed.rows > 1:
            attrs += f' rowspan="{placed.rows}"'
        if placed.cols > 1:
            attrs += f' colspan="{placed.cols}"'
        if self.ids and placed.is_data and not self.plain:
            headers = self.headers_of(placed)
            if headers:
                attrs += f' headers="{" ".join(headers)}"'
        tag = "th" if header else "td"
        content = self.content(placed.cell)
        if header and content.startswith("<math"):
            content = self.named_maths(placed.cell, content)
        return f"<{tag}{attrs}>{content}</{tag}>"

    def named_maths(self, cell: TableCell, mathml: str) -> str:
        """A header that is only maths, with its reading as text: browsers give a header
        cell no name from MathML, and screen readers announce a column's or a row's
        header by its name as they move through the cells. The MathML is hidden from
        them so the header is not read twice; the reading is the tool's own English."""
        from readablestem.pipeline.speech import SpeechError, mathml_to_speech

        try:
            reading = mathml_to_speech(cell.mathml or "")
        except SpeechError:
            reading = ""
        reading = " ".join((reading or cell.text or cell.latex or "").split())
        if not reading:
            return mathml
        return (
            f'<span class="visually-hidden {AUTHORED_CLASS}">{escape_text(reading)}</span>'
            f'<span aria-hidden="true">{mathml}</span>'
        )

    def scope(self, placed: _Placed) -> str:
        if placed.cell.header == "col":
            if placed.row == 0 and placed.cols > 1 and self.layout.colgroup_headers():
                return "colgroup"
            return "col"
        return "rowgroup" if self.layout.is_section(placed) else "row"

    def content(self, cell: TableCell) -> str:
        if cell.mathml:
            mathml = sanitize_mathml(cell.mathml, display=self.math_display)
            if mathml is not None:
                return mathml
        return escape_text(cell.text)

    def header_id(self, placed: _Placed) -> str:
        return f"{self.id_prefix}-r{placed.row}c{placed.col}"

    # ----- headers/id for irregular tables

    def headers_of(self, data: _Placed) -> list[str]:
        found: list[_Placed] = []
        section = self.section_above(data)
        if section is not None:
            found.append(section)
        column = [h for col in data.columns() for h in self.scan(data, col, up=True)]
        found += sorted(column, key=lambda h: (h.row, h.col))
        row = [h for r in range(data.row, data.row + data.rows) for h in self.scan(data, r)]
        found += sorted(row, key=lambda h: (h.col, h.row))
        ids: list[str] = []
        for header in found:
            header_id = self.header_id(header)
            if header_id not in ids:
                ids.append(header_id)
        return ids

    def section_above(self, data: _Placed) -> _Placed | None:
        for row in range(data.row - 1, self.layout.header_rows - 1, -1):
            placed = self.layout.anchored(row, 0)
            if placed is not None and self.layout.is_section(placed):
                return placed
        return None

    def scan(self, data: _Placed, line: int, *, up: bool = False) -> list[_Placed]:
        """The nearest block of headers above (`up`, in column `line`) or to the left (in
        row `line`) of a data cell: data cells before the block are passed over, the
        first data cell after it ends the scan. Headers of the other direction and
        section headings are passed over."""
        wanted = "col" if up else "row"
        start = data.row - 1 if up else data.col - 1
        found: list[_Placed] = []
        for i in range(start, -1, -1):
            placed = self.layout.at(i, line) if up else self.layout.at(line, i)
            if placed is None or placed is data:
                continue
            if placed.is_header:
                wanted_here = placed.cell.header == wanted and not self.layout.is_section(placed)
                if wanted_here and placed not in found:
                    found.append(placed)
            elif placed.is_data and found:
                break
        return found
