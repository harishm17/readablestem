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

from readablestem.core.types import Element, Fix, TableCell, TableGrid, grid_problems
from readablestem.pipeline.assemble_tables import (
    flattened_cells,
    id_prefix,
    rebuilt_grid,
    text_grid,
    text_html,
)
from readablestem.pipeline.table_html import render_table
from readablestem.pipeline.table_step import NOTE_CODES, STRUCTURE_PROBLEMS
from readablestem.web.review import (
    cell_label,
    cell_region,
    find_cell,
    first_col_is_header,
    first_row_is_header,
    grid_as_text,
    grid_problem_sentence,
    is_maths_cell,
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
    flat: str = ""  # the PDF's flattened text of a cell to be written as LaTeX
    readable: bool = False  # a maths cell the equation step can read from the page (T3-11)


@dataclass
class TableCard:
    proposal: Markup | None  # the proposal as the student would get it; None without one
    is_data: bool  # False: not a table of data, written as its cells' text in reading order
    in_document: bool  # the document has the rebuilt table now (else the image)
    local: Markup | None  # the grid read from the PDF, when it can be used instead
    header_row: bool  # the first row is a header row
    header_col: bool  # the first column holds row headers
    cells: list[CellChoice] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # structural, in plain English
    editing: CellChoice | None = None  # the cell whose form is open ("Change a cell")
    local_flat: bool = False  # the grid read from the PDF gives maths flat: not the main action
    # The only thing the checks found is that no cell is a header (T1-19): "Use the first
    # row as headers" is the main action, and one click settles the table.
    only_headers_missing: bool = False
    # "This grid is not a table" (T3-11): its words can be written one after another.
    can_text: bool = False

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


def cell_choices(
    grid: TableGrid, fix: Fix | None = None, element: Element | None = None
) -> list[CellChoice]:
    """Every cell, in reading order (row, then column). With the fix and its element, a
    maths cell with a place on the page is marked readable by the equation step."""
    out = []
    flat = {id(c) for c in flattened_cells(grid)}
    for cell in sorted(grid.cells, key=lambda c: (c.row, c.col)):
        if find_cell(grid, cell.row, cell.col) is not cell:
            continue  # a second cell at the same place (a broken grid): never reachable
        position = cell_label(cell)
        maths = is_maths_cell(grid, cell)
        out.append(
            CellChoice(
                ref=f"r{cell.row}c{cell.col}",
                row=cell.row,
                col=cell.col,
                position=position,
                label=f"{position}: {_preview(cell)}",
                maths=maths,
                value=(cell.latex or "") if maths else cell.text,
                flat=cell.text if id(cell) in flat else "",
                readable=maths
                and fix is not None
                and element is not None
                and not isinstance(cell_region(fix, element, str(cell.row), str(cell.col)), str),
            )
        )
    return out


def _rendered(grid: TableGrid, prefix: str) -> Markup:
    # Escaped and sanitised by the serialiser (cell text escaped, MathML rebuilt from an
    # allowlisted tree): the same HTML the student gets.
    return Markup(render_table(grid, id_prefix=prefix, math_display="inline"))


def _same_table(one: TableGrid, other: TableGrid) -> bool:
    """The same table for a reader, whoever built it (`source`, the cells' boxes and
    their order in the list aside)."""

    def plain(grid: TableGrid) -> TableGrid:
        cells = sorted(
            (c.model_copy(update={"bbox": None}) for c in grid.cells),
            key=lambda c: (c.row, c.col),
        )
        return grid.model_copy(update={"source": "local", "cells": cells})

    return plain(one) == plain(other)


def table_card(fix: Fix, element: Element | None) -> TableCard:
    eid = id_prefix(fix.element_id)
    local = usable_local_grid(element)
    grid = fix.table.grid if fix.table is not None else None
    if local is not None and grid is not None and _same_table(local, grid):
        local = None  # the proposal is the grid read from the PDF: nothing else to offer
    if grid is not None and not grid.is_data:
        # Written as text: the card shows the text as the student gets it.
        caption = grid.caption or (element.caption if element is not None else None)
        proposal: Markup | None = Markup(text_html(grid, caption))
    else:
        proposal = _rendered(grid, f"tp-{eid}") if grid is not None else None
    card = TableCard(
        proposal=proposal,
        is_data=grid.is_data if grid is not None else True,
        in_document=rebuilt_grid(fix) is not None or text_grid(fix) is not None,
        local=_rendered(local, f"tl-{eid}") if local is not None else None,
        local_flat=local is not None and bool(flattened_cells(local)),
        header_row=grid is not None and first_row_is_header(grid),
        header_col=grid is not None and first_col_is_header(grid),
    )
    if grid is not None:
        card.cells = cell_choices(grid, fix, element)
        card.problems = [
            grid_problem_sentence(p) for p in grid_problems(grid) if not _reported(p, fix)
        ]
        card.only_headers_missing = _only_headers_missing(fix, grid, card)
    card.can_text = grid_as_text(fix, element) is not None and not (
        card.in_document and not card.is_data
    )
    return card


def _only_headers_missing(fix: Fix, grid: TableGrid, card: TableCard) -> bool:
    """A table still waiting whose one finding is "no cell is marked as a header" and whose
    first row can be that header: a data table of at least two rows with nothing else wrong
    (no other failed check, no structural problem)."""
    failed = fix.failed_checks
    return (
        fix.pending
        and grid.is_data
        and card.in_document
        and not card.header_row
        and grid.rows >= 2
        and not card.problems
        and any(c.code == "no_headers" for c in failed)
        and all(c.code in {"no_headers", *NOTE_CODES} for c in failed)
    )


def _reported(problem: str, fix: Fix) -> bool:
    """A structural problem the step's failed checks already explain on the card: the
    problem's own code, or the table step's broader code for it (`table.grid`
    grid_invalid for a broken grid, `table.headers` header_problem for the headers), so
    the card does not say the same thing twice."""
    reported = {c.code for c in fix.failed_checks}
    if problem in reported:
        return True
    if problem in STRUCTURE_PROBLEMS:
        return "grid_invalid" in reported
    return "header_problem" in reported
