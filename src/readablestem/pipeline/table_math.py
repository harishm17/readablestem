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
- the equation of a cell is the one source of the cell's maths: when it has LaTeX and MathML the
  cell shows them (a reading flagged for review is still the reading: the equation has its own
  decision, as an inline formula of a paragraph does, and a reviewer's edit of it is the cell's);
  a failed, abstained or rejected equation leaves the cell as the table step wrote it;
- where the table step wrote LaTeX that differs from the equation's reading (canonical
  comparison), `settle` makes the equation a required decision, so neither is kept silently;
- an equation whose table is not written as a table (kept as a picture, a grid of another size)
  is dropped by `settle`: no review item for something nothing shows;
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
    """`grid` with the equation step's reading in each cell that has one (the equation is the one
    source of a cell's maths: a reviewer's edit of it is the cell's), or None when none changes."""
    if (grid.rows, grid.cols) != (local.rows, local.cols):
        return None
    owner = table_step._owners(local)
    cells: list[TableCell] = []
    changed = False
    for cell in grid.cells:
        reading = _reading(cell, local, owner, fixes)
        if reading is None or (cell.latex, cell.mathml) == reading:
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
    return without_cell_equations_of(elements, ids) if ids else elements


def _placed(element: Element, fix: Fix | None) -> TableGrid | None:
    """The table step's grid when this table is written as a table and its cells stand where the
    PDF's do (the cell equations have a place), else None."""
    if fix is None or fix.table is None or fix.status in KEPT_ORIGINAL_STATUSES:
        return None
    grid, local = fix.table.grid, element.table
    if local is None or not grid.is_data or (grid.rows, grid.cols) != (local.rows, local.cols):
        return None
    return grid


def settle(elements: list[Element], fixes: dict[str, Fix]) -> tuple[list[Element], dict[str, Fix]]:
    """Make the cell equations consistent with their tables once every step is done.

    An equation of a table that is not written as a table is dropped with its fix and its cell's
    id. Where a placed table's own LaTeX for a cell differs from the equation's reading, the
    equation is a required decision with a failed check, so the page never keeps one silently."""
    from readablestem.core.types import Check, Status
    from readablestem.pipeline import math_canonical

    out_fixes = dict(fixes)
    drop: set[str] = set()
    for element in elements:
        if element.kind is not Kind.TABLE or element.table is None:
            continue
        ids = {c.equation_id for c in element.table.cells if c.equation_id}
        if not ids:
            continue
        grid = _placed(element, fixes.get(element.id))
        if grid is None:
            drop |= ids
            continue
        owner = table_step._owners(element.table)
        for cell in grid.cells:
            reading = _reading(cell, element.table, owner, fixes)
            if reading is None or not (cell.latex or "").strip():
                continue
            behind = table_step._counterparts(cell, owner)
            eq_id = next(
                (
                    element.table.cells[i].equation_id
                    for i in behind
                    if element.table.cells[i].equation_id
                ),
                None,
            )
            fix = out_fixes.get(eq_id or "")
            if fix is None or math_canonical.compare(cell.latex or "", reading[0]).equal:
                continue
            check = Check(
                name="table.cell_reading", passed=False, code="differs_from_table_reading"
            )
            out_fixes[fix.element_id] = fix.model_copy(
                update={
                    "status": Status.NEEDS_REVIEW if fix.status is Status.ACCEPTED else fix.status,
                    "review": "required",
                    "checks": [*fix.checks, check],
                }
            )
    if not drop:
        return elements, out_fixes
    kept = without_cell_equations_of(elements, drop)
    for eq_id in drop:
        out_fixes.pop(eq_id, None)
    return kept, out_fixes


def without_cell_equations_of(elements: list[Element], ids: set[str]) -> list[Element]:
    """`elements` without the equations in `ids`, and the cells that named them without the id."""
    out: list[Element] = []
    for element in elements:
        if element.id in ids:
            continue
        if (
            element.kind is Kind.TABLE
            and element.table is not None
            and any(c.equation_id in ids for c in element.table.cells)
        ):
            cells = [
                c.model_copy(update={"equation_id": None}) if c.equation_id in ids else c
                for c in element.table.cells
            ]
            element = element.model_copy(
                update={"table": element.table.model_copy(update={"cells": cells})}
            )
        out.append(element)
    return out
