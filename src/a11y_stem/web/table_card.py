"""The table card of the review screens (`templates/_table_card.html`).

A table fix is shown as the student would get it: the proposal (`Fix.table`) written by
`pipeline.table_html.render_table`, the same serialiser the output uses, so the card
cannot show a table the document would not have. Beside it: the grid read from the
PDF's own words and lines (`Element.table`) when it could be used instead, whether the
first row and column are headers, the cells for "Change a cell", and the structural
problems of the proposal in plain English (`web.review.GRID_PROBLEM_SENTENCES`; the
step's own failed checks are explained by `web.messages`, beside them).

The rules for what an edit may do are in `web.review` ("tables"); this module only says
what the card shows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from markupsafe import Markup

from a11y_stem.core.types import Element, Fix, TableCell, TableGrid, grid_problems
from a11y_stem.pipeline.assemble_tables import id_prefix, rebuilt_grid
from a11y_stem.pipeline.table_html import render_table
from a11y_stem.web.review import (
    cell_label,
    find_cell,
    first_col_is_header,
    first_row_is_header,
    grid_problem_sentence,
    usable_local_grid,
)

CELL_PREVIEW_CHARS = 40  # of a cell's text in the "Change a cell" list


@dataclass(frozen=True)
class CellChoice:
    """One cell in the "Change a cell" list."""

    ref: str  # "r1c2": its anchor, 0-based, as the list sends it
    row: int
    col: int
    position: str  # "Row 2, column 3"
    label: str  # "Row 2, column 3: Speed (m/s)"
    maths: bool  # edited as LaTeX
    value: str  # its text, or its LaTeX for a maths cell


@dataclass
class TableCard:
    proposal: Markup | None  # the proposal as the student would get it; None without one
    is_data: bool  # False: the proposal is a layout table, so the image is kept
    in_document: bool  # the document has the rebuilt table now (else the image)
    local: Markup | None  # the grid read from the PDF, when it can be used instead
    header_row: bool  # the first row is a header row
    header_col: bool  # the first column holds row headers
    cells: list[CellChoice] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # structural, in plain English
    editing: CellChoice | None = None  # the cell whose form is open ("Change a cell")

    def pick(self, ref: str) -> None:
        """Open the form of the cell `ref` ("r1c2"); an unknown ref opens nothing."""
        self.editing = next((c for c in self.cells if c.ref == ref), None)

    def pick_at(self, row: str, col: str) -> None:
        """Open the form of the cell at (row, col) as a refused form sent them."""
        self.pick(f"r{row}c{col}")


def _preview(cell: TableCell) -> str:
    shown = " ".join((cell.text or cell.latex or "").split())
    if not shown:
        return "(empty)"
    if len(shown) > CELL_PREVIEW_CHARS:
        shown = shown[: CELL_PREVIEW_CHARS - 1].rstrip() + "…"
    return shown


def cell_choices(grid: TableGrid) -> list[CellChoice]:
    """Every cell, in reading order (row, then column)."""
    out = []
    for cell in sorted(grid.cells, key=lambda c: (c.row, c.col)):
        if find_cell(grid, cell.row, cell.col) is not cell:
            continue  # a second cell at the same place (a broken grid): never reachable
        position = cell_label(cell)
        maths = cell.latex is not None
        out.append(
            CellChoice(
                ref=f"r{cell.row}c{cell.col}",
                row=cell.row,
                col=cell.col,
                position=position,
                label=f"{position}: {_preview(cell)}",
                maths=maths,
                value=(cell.latex or "") if maths else cell.text,
            )
        )
    return out


def _rendered(grid: TableGrid, prefix: str) -> Markup:
    # Escaped and sanitised by the serialiser (cell text escaped, MathML rebuilt from an
    # allowlisted tree): the same HTML the student gets.
    return Markup(render_table(grid, id_prefix=prefix, math_display="inline"))


def table_card(fix: Fix, element: Element | None) -> TableCard:
    eid = id_prefix(fix.element_id)
    local = usable_local_grid(element)
    grid = fix.table.grid if fix.table is not None else None
    if local is not None and grid is not None and local == grid:
        local = None  # the proposal is the grid read from the PDF: nothing else to offer
    card = TableCard(
        proposal=_rendered(grid, f"tp-{eid}") if grid is not None else None,
        is_data=grid.is_data if grid is not None else True,
        in_document=rebuilt_grid(fix) is not None,
        local=_rendered(local, f"tl-{eid}") if local is not None else None,
        header_row=grid is not None and first_row_is_header(grid),
        header_col=grid is not None and first_col_is_header(grid),
    )
    if grid is not None:
        card.cells = cell_choices(grid)
        reported = {c.code for c in fix.failed_checks}
        card.problems = [grid_problem_sentence(p) for p in grid_problems(grid) if p not in reported]
    return card
