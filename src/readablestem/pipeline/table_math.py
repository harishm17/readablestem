"""The maths of a table cell, read by the equation step and written into the cell.

Layout finds a cell that is one formula from its glyphs (`layout._cell_maths`) and makes it an
inline equation of the table, its id on `TableCell.equation_id`. The equation step reads that
cell's crop like any inline equation, with its line context, its checks and its place in the
review. The table step's model reads the whole table once and writes a cell's maths as LaTeX
when it sees it; the cells it wrote as text (a lone plus-or-minus sign, a symbol, `p < 0.05`)
were most of the maths of a table that the pipeline missed (docs/eval-equation-diagnostics.md,
D1). This puts what the equation step read into those cells when the page is written.

Rules, all on the grids and fixes (no model, no text layer):

- only a table the table step rebuilt (`fix.table`), and only when its grid has the size of the
  grid read from the PDF, so a cell stands where its counterpart does (`table_step._counterparts`);
- a cell the model already wrote as maths keeps its LaTeX: what works today is not touched;
- a cell with no maths from the model takes the equation step's reading when the equation has
  MathML (a reading that is flagged for review is still the reading: the equation has its
  own decision, as an inline formula of a paragraph does); a failed or refused equation leaves
  the cell as the model wrote it, text that is what the PDF has;
- nothing else of the cell changes: its text, its header, its span.

The element of a cell is never written on its own (`assemble._placed_equations`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from readablestem.core.types import (
    KEPT_ORIGINAL_STATUSES,
    Element,
    Fix,
    Kind,
    TableCell,
    TableFix,
    TableGrid,
)
from readablestem.pipeline import table_step


def cell_equation_ids(elements: Iterable[Element]) -> set[str]:
    """The ids of the equations that stand for a table cell (their cell names them)."""
    return {
        cell.equation_id
        for element in elements
        if element.kind is Kind.TABLE and element.table is not None
        for cell in element.table.cells
        if cell.equation_id
    }


def _with_readings(grid: TableGrid, local: TableGrid, fixes: Mapping[str, Fix]) -> TableGrid | None:
    """`grid` with the equation step's reading in each cell that has none, or None when no cell
    takes one."""
    if (grid.rows, grid.cols) != (local.rows, local.cols):
        return None
    owner = table_step._owners(local)
    cells: list[TableCell] = []
    changed = False
    for cell in grid.cells:
        if cell.latex or cell.mathml:
            cells.append(cell)
            continue
        reading = _reading(cell, local, owner, fixes)
        if reading is None:
            cells.append(cell)
            continue
        latex, mathml = reading
        cells.append(cell.model_copy(update={"latex": latex, "mathml": mathml}))
        changed = True
    return grid.model_copy(update={"cells": cells}) if changed else None


def _reading(
    cell: TableCell, local: TableGrid, owner: dict[tuple[int, int], int], fixes: Mapping[str, Fix]
) -> tuple[str, str] | None:
    """The (LaTeX, MathML) the equation step read for the local cells behind `cell`: one cell
    only. A model cell that spans several local cells with maths in more than one has no single
    reading and takes none."""
    behind = [local.cells[i] for i in table_step._counterparts(cell, owner)]
    ids = [c.equation_id for c in behind if c.equation_id]
    if len(ids) != 1 or len([c for c in behind if c.text.strip()]) != 1:
        return None
    fix = fixes.get(ids[0])
    if fix is None or fix.status in KEPT_ORIGINAL_STATUSES or fix.equation is None:
        return None
    equation = fix.equation
    if not equation.latex.strip() or not equation.mathml.strip():
        return None
    return equation.latex, equation.mathml


def with_cell_readings(elements: Iterable[Element], fixes: Mapping[str, Fix]) -> dict[str, Fix]:
    """`fixes` with each rebuilt table's cells filled from the equations of its cells. The fixes
    of the tables are copies; no other fix is touched, and none is when no table has a cell
    equation."""
    out = dict(fixes)
    for element in elements:
        if element.kind is not Kind.TABLE or element.table is None:
            continue
        if not any(c.equation_id for c in element.table.cells):
            continue
        fix = fixes.get(element.id)
        if fix is None or fix.table is None:
            continue
        grid = _with_readings(fix.table.grid, element.table, fixes)
        if grid is not None:
            out[element.id] = fix.model_copy(update={"table": TableFix(grid=grid)})
    return out


def without_cell_equations(elements: list[Element]) -> list[Element]:
    """`elements` without the equations of table cells, and the cells without their id. With the
    tables feature off a table is kept as its picture, so nothing would write these equations and
    the equation step would read them for nothing."""
    ids = cell_equation_ids(elements)
    if not ids:
        return elements
    out: list[Element] = []
    for element in elements:
        if element.id in ids:
            continue
        if element.kind is Kind.TABLE and element.table is not None:
            cells = [c.model_copy(update={"equation_id": None}) for c in element.table.cells]
            grid = element.table.model_copy(update={"cells": cells})
            element = element.model_copy(update={"table": grid})
        out.append(element)
    return out
