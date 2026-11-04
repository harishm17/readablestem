"""A table cell that is also an equation (`TableCell.equation_id`): one reading, not two.

A reviewer who types a cell's LaTeX on the table card changes the equation behind it too (same
checks, recorded as its own change, taken back with it by Undo), and one who edits the equation
changes the cell. The page, every re-render of the review screen and the files made from the page
show the person's reading. Offline: a result built by hand, the real apply_review, undo_last and
renderer (docs/tables.md, "Maths cells from glyphs").
"""

from __future__ import annotations

from readablestem.core.types import (
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    Fix,
    Kind,
    Status,
    TableCell,
    TableFix,
    TableGrid,
)
from readablestem.pipeline import table_math
from readablestem.web.review import apply_review, rerender_html, undo_last
from readablestem.web.table_card import cell_choices

MATHML = '<math xmlns="http://www.w3.org/1998/Math/MathML" display="inline"><mi>{}</mi></math>'


def result() -> DocumentResult:
    local = TableGrid(
        rows=2,
        cols=2,
        source="local",
        cells=[
            TableCell(row=0, col=0, text="Symbol", header="col"),
            TableCell(row=0, col=1, text="Value", header="col"),
            TableCell(row=1, col=0, text="m", equation_id="p1-e2"),
            TableCell(row=1, col=1, text="0.5"),
        ],
    )
    model = local.model_copy(
        update={
            "source": "model",
            "header_rows": 1,
            "cells": [c.model_copy(update={"equation_id": None}) for c in local.cells],
        }
    )
    table = Element(
        id="p1-e1", kind=Kind.TABLE, page=0, bbox=(72, 100, 300, 200), order=1, table=local
    )
    cell = Element(
        id="p1-e2", kind=Kind.EQUATION, page=0, bbox=(80, 130, 90, 140), order=2, text="m",
        display="inline", parent_id="p1-e1",
    )  # fmt: skip
    fixes = [
        Fix(
            element_id="p1-e1", kind=Kind.TABLE, status=Status.ACCEPTED,
            table=TableFix(grid=model),
        ),
        Fix(
            element_id="p1-e2", kind=Kind.EQUATION, status=Status.ACCEPTED,
            equation=EquationFix(
                latex="m", mathml=MATHML.format("m"), speech="m", display="inline"
            ),
        ),
    ]  # fmt: skip
    elements, settled = table_math.settle([table, cell], {f.element_id: f for f in fixes})
    return DocumentResult(
        pipeline_version="test",
        elements=elements,
        fixes=list(settled.values()),
        stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0, model_calls=0),
        html="",
    )


def page(r: DocumentResult) -> str:
    return rerender_html(r, {})


def test_a_cell_typed_on_the_table_card_is_written_to_its_equation_and_shown_everywhere():
    r = result()
    change = apply_review(
        r, "p1-e1", "edit", {"change": "cell", "row": "1", "col": "0", "cell": "m_{0}"}, anyway=True
    )
    assert [c.element_id for c in change.every] == ["p1-e1", "p1-e2"]
    assert r.fix_for("p1-e2").equation.latex == "m_{0}"
    assert r.fix_for("p1-e2").status is Status.EDITED
    assert r.fix_for("p1-e1").table.grid.cells[2].latex == "m_{0}"
    html = page(r)  # the page, and every file made from it: Word, EPUB, Canvas, the record
    assert "<msub>" in html and html.index("<msub>") < html.index("</table>")
    assert html.count("<math") == 1


def test_undo_takes_back_the_cell_and_the_equation_together():
    r = result()
    before_html = page(r)
    change = apply_review(
        r, "p1-e1", "edit", {"change": "cell", "row": "1", "col": "0", "cell": "m_{0}"}, anyway=True
    )
    undo_last(r, [(c.element_id, c.before) for c in change.every])
    assert r.fix_for("p1-e2").equation.latex == "m"
    assert r.fix_for("p1-e1").table.grid.cells[2].latex == "m"
    assert page(r) == before_html


def test_editing_the_equation_changes_the_cell_the_card_and_the_page():
    r = result()
    change = apply_review(r, "p1-e2", "edit", {"latex": "m_{1}"}, anyway=True)
    assert [c.element_id for c in change.every] == ["p1-e2", "p1-e1"]
    assert r.fix_for("p1-e1").table.grid.cells[2].latex == "m_{1}"
    assert "<msub>" in page(r)
    undo_last(r, [(c.element_id, c.before) for c in change.every])
    assert r.fix_for("p1-e1").table.grid.cells[2].latex == "m"


def test_the_card_says_which_cells_are_also_equations():
    r = result()
    grid = r.fix_for("p1-e1").table.grid
    choices = cell_choices(grid, r.fix_for("p1-e1"), r.elements[0])
    tied = {c.position: c.tied for c in choices}
    assert tied["Row 2, column 1"] and not tied["Row 2, column 2"]
    assert "(also an equation)" in next(c.label for c in choices if c.tied)
