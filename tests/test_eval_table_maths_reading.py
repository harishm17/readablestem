"""The document task read as an HTML reader reads it: maths cells of a rebuilt table count.

The scorer sees equations that are elements of their own, and a table the table step rebuilt is one
TABLE element, so a formula in one of its cells was never an equation to it, while the chatbots'
HTML (every `<math>` in it) was read cell by cell: 223 of the real-world development set's gold
equations are typed in a table, and ReadableSTEM could not be credited for any of them."""

from __future__ import annotations

from readablestem.core.types import (
    Element,
    Fix,
    Kind,
    Status,
    TableCell,
    TableFix,
    TableGrid,
)
from readablestem.eval import pipeline_eval as pe
from readablestem.pipeline.validate import safe_latex_to_mathml


def table_fix(cells: list[TableCell]) -> Fix:
    grid = TableGrid(rows=2, cols=2, cells=cells, header_rows=1)
    return Fix(
        element_id="p1-e1",
        kind=Kind.TABLE,
        status=Status.ACCEPTED,
        confidence=0.9,
        table=TableFix(grid=grid),
    )


def cell(row: int, col: int, text: str, latex: str | None = None) -> TableCell:
    mathml = safe_latex_to_mathml(latex, "inline") if latex else None
    return TableCell(row=row, col=col, text=text, latex=latex, mathml=mathml)


def elements() -> list[Element]:
    paragraph = Element(id="p1-e0", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 10, 10), order=0)
    table = Element(id="p1-e1", kind=Kind.TABLE, page=0, bbox=(0, 20, 100, 80), order=1)
    after = Element(id="p1-e2", kind=Kind.PARAGRAPH, page=0, bbox=(0, 90, 100, 100), order=2)
    return [paragraph, table, after]


def test_the_maths_cells_of_a_table_become_equations_after_it():
    fix = table_fix(
        [
            cell(0, 0, "n", "n"),
            cell(0, 1, "Mean"),
            cell(1, 0, "eta_r", r"\eta_{\mathbf{r}}"),
            cell(1, 1, "0.5"),
        ]
    )
    out, fixes = pe.with_table_maths(elements(), {"p1-e1": fix})
    kinds = [(e.id, e.kind.value, e.order) for e in out]
    assert kinds == [
        ("p1-e0", "paragraph", 0),
        ("p1-e1", "table", 1),
        ("p1-e1-m0", "equation", 2),
        ("p1-e1-m1", "equation", 3),
        ("p1-e2", "paragraph", 4),
    ]
    assert fixes["p1-e1-m1"].equation is not None
    assert fixes["p1-e1-m1"].equation.latex == r"\eta_{\mathbf{r}}"


def test_a_table_without_maths_cells_adds_nothing():
    fix = table_fix([cell(0, 0, "a"), cell(0, 1, "b"), cell(1, 0, "1"), cell(1, 1, "2")])
    out, fixes = pe.with_table_maths(elements(), {"p1-e1": fix})
    assert [e.id for e in out] == ["p1-e0", "p1-e1", "p1-e2"]
    assert set(fixes) == {"p1-e1"}


def test_the_cell_equations_are_scored_like_any_other():
    gold = [
        {
            "id": "g0",
            "kind": "equation",
            "display": "inline",
            "mathml": safe_latex_to_mathml("n", "inline"),
            "latex": "n",
        }
    ]
    fix = table_fix([cell(0, 0, "n", "n"), cell(0, 1, "x"), cell(1, 0, "1"), cell(1, 1, "2")])
    item = pe.document_page_item("s", gold, elements(), {"p1-e1": fix}, [], [])
    assert item["pairs"] == []
    out, fixes = pe.with_table_maths(elements(), {"p1-e1": fix})
    reading = pe.document_page_item("s", gold, out, fixes, [], [])
    assert [p["exact_lenient"] for p in reading["pairs"]] == [True]
