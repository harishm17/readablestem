"""The maths of a table cell: layout finds it, the equation step reads it, the page writes it.

`layout._cell_maths` makes a cell that is one formula (by the glyphs, TeX-made PDFs only) an
inline equation of its table; `pipeline.table_math` puts what the equation step read into the
table step's cells that have no maths of their own when the page is written
(docs/tables.md, "Maths cells from glyphs"). Everything here is offline: small PDFs built in the
test, grids and fixes written by hand.
"""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import (
    Element,
    EquationFix,
    Fix,
    Kind,
    Status,
    TableCell,
    TableFix,
    TableGrid,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import equation_step, figure_step, layout, run, table_math, table_step
from readablestem.pipeline.assemble import render_html
from readablestem.pipeline.layout import Layout, analyze

# ------------------------------------------------------------------ the layout side


def cell_page(producer: str, *, italic_font: str = "heit") -> bytes:
    """A ruled table of three rows and two columns: words, a variable and a formula."""
    doc = pymupdf.open()
    doc.set_metadata({"producer": producer})
    page = doc.new_page(width=612, height=792)
    x = [100, 220, 340]
    y = [100, 130, 160, 190]
    for k in x:
        page.draw_line((k, y[0]), (k, y[-1]))
    for k in y:
        page.draw_line((x[0], k), (x[-1], k))
    rows = [("Quantity", "Symbol"), ("Mass", None), ("Period", None)]
    for r, (left, right) in enumerate(rows):
        page.insert_text((106, y[r] + 19), left, fontsize=11, fontname="helv")
        if right:
            page.insert_text((226, y[r] + 19), right, fontsize=11, fontname="helv")
    page.insert_text((226, y[1] + 19), "m", fontsize=11, fontname=italic_font)
    x0 = 226
    for text, font in (("T", "heit"), (" = 2", "helv"), ("p", "heit"), ("/", "helv")):
        page.insert_text((x0, y[2] + 19), text, fontsize=11, fontname=font)
        x0 += pymupdf.get_text_length(text, fontname=font, fontsize=11)
    page.insert_text((x0, y[2] + 19), "w", fontsize=11, fontname="heit")
    return doc.tobytes()


def tables_of(data: bytes):
    layout = analyze(data)
    return layout, [e for e in layout.elements if e.kind == Kind.TABLE and e.table]


def test_a_table_cell_that_is_a_formula_is_an_equation_of_the_table():
    layout, found = tables_of(cell_page("pdfTeX-1.40.25"))
    assert len(found) == 1
    table = found[0]
    by_id = {e.id: e for e in layout.elements}
    cells = {c.text: c for c in table.table.cells}
    ids = {text: c.equation_id for text, c in cells.items() if c.equation_id}
    assert set(ids) == {"m", "T = 2p/w"}  # the variable and the formula; words are not maths
    for equation_id in ids.values():
        child = by_id[equation_id]
        assert child.kind == Kind.EQUATION and child.display == "inline"
        assert child.parent_id == table.id


def test_a_document_not_made_by_tex_has_no_cell_equations():
    _, found = tables_of(cell_page("Skia/PDF m120"))
    assert all(c.equation_id is None for t in found for c in t.table.cells)


# ------------------------------------------------------------------ the page side

MATHML = '<math xmlns="http://www.w3.org/1998/Math/MathML" display="inline"><mi>m</mi></math>'


def table(local_cells: list[TableCell]) -> Element:
    return Element(
        id="p1-e1",
        kind=Kind.TABLE,
        page=0,
        bbox=(72, 100, 300, 200),
        order=1,
        table=TableGrid(rows=2, cols=2, cells=local_cells, source="local"),
    )


def cell_equation(eid: str = "p1-e2") -> Element:
    return Element(
        id=eid,
        kind=Kind.EQUATION,
        page=0,
        bbox=(130, 130, 140, 140),
        order=2,
        text="m",
        display="inline",
        parent_id="p1-e1",
    )


def local_cells(equation_id: str | None = "p1-e2") -> list[TableCell]:
    return [
        TableCell(row=0, col=0, text="Symbol", header="col"),
        TableCell(row=0, col=1, text="Value", header="col"),
        TableCell(row=1, col=0, text="m", equation_id=equation_id),
        TableCell(row=1, col=1, text="0.5"),
    ]


def model_grid(first: TableCell | None = None) -> TableGrid:
    cells = [
        TableCell(row=0, col=0, text="Symbol", header="col"),
        TableCell(row=0, col=1, text="Value", header="col"),
        first or TableCell(row=1, col=0, text="m"),
        TableCell(row=1, col=1, text="0.5"),
    ]
    return TableGrid(rows=2, cols=2, cells=cells, header_rows=1, source="model")


def table_fix(grid: TableGrid) -> Fix:
    return Fix(
        element_id="p1-e1", kind=Kind.TABLE, status=Status.ACCEPTED, table=TableFix(grid=grid)
    )


def equation_fix(status: Status = Status.ACCEPTED, *, latex: str = "m") -> Fix:
    return Fix(
        element_id="p1-e2",
        kind=Kind.EQUATION,
        status=status,
        equation=EquationFix(latex=latex, mathml=MATHML, speech="m", display="inline"),
    )


def merged(fixes: list[Fix], elements: list[Element] | None = None) -> dict[str, Fix]:
    elements = elements or [table(local_cells()), cell_equation()]
    return table_math.with_cell_readings(elements, {f.element_id: f for f in fixes})


def test_a_cell_the_model_wrote_as_text_takes_the_equation_steps_reading():
    out = merged([table_fix(model_grid()), equation_fix()])
    cell = out["p1-e1"].table.grid.cells[2]
    assert (cell.latex, cell.mathml, cell.text) == ("m", MATHML, "m")


def test_the_equation_is_the_one_source_of_a_cells_maths():
    """A reviewer edit of the cell equation is what the table shows, whatever the model wrote."""
    own = TableCell(row=1, col=0, text="m", latex="\\mu", mathml=MATHML.replace("m<", "\u03bc<"))
    edited = equation_fix(Status.EDITED, latex="M")
    out = merged([table_fix(model_grid(own)), edited])
    assert out["p1-e1"].table.grid.cells[2].latex == "M"


def test_a_failed_or_rejected_reading_leaves_the_cell_as_it_is():
    for status in (Status.FAILED, Status.ABSTAINED, Status.REJECTED):
        out = merged([table_fix(model_grid()), equation_fix(status)])
        assert out["p1-e1"].table.grid.cells[2].latex is None


def test_a_reading_that_needs_review_is_still_written_and_keeps_its_own_decision():
    out = merged([table_fix(model_grid()), equation_fix(Status.NEEDS_REVIEW)])
    assert out["p1-e1"].table.grid.cells[2].latex == "m"
    assert out["p1-e2"].status is Status.NEEDS_REVIEW  # the equation's fix is not touched


def test_a_grid_of_another_size_than_the_pdfs_takes_nothing():
    small = TableGrid(rows=1, cols=2, cells=model_grid().cells[:2], header_rows=1, source="model")
    out = merged([table_fix(small), equation_fix()])
    assert out["p1-e1"].table.grid is small or out["p1-e1"].table.grid == small


def test_a_table_without_a_cell_equation_is_left_alone():
    elements = [table(local_cells(None))]
    fixes = {"p1-e1": table_fix(model_grid())}
    assert table_math.with_cell_readings(elements, fixes) is not fixes
    assert table_math.with_cell_readings(elements, fixes) == fixes


def test_without_cell_equations_drops_them_and_their_ids():
    out = table_math.without_cell_equations([table(local_cells()), cell_equation()])
    assert [e.id for e in out] == ["p1-e1"]
    assert all(c.equation_id is None for c in out[0].table.cells)
    plain = [table(local_cells(None))]
    assert table_math.without_cell_equations(plain) is plain


def test_the_page_writes_the_reading_in_the_cell_and_not_below_the_table():
    html = render_html(
        title="Tables",
        lang="en",
        elements=[table(local_cells()), cell_equation()],
        fixes=[table_fix(model_grid()), equation_fix()],
        crops={},
    )
    assert html.count("<math") == 1  # in the cell, once: the element is not written alone
    assert html.index("<math") < html.index("</table>")  # inside the table
    assert 'class="equation' not in html


# ------------------------------------------------------------------ in the run


def png() -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    pixmap.set_rect(pixmap.irect, (30, 30, 30))
    return pixmap.tobytes("png")


@pytest.fixture
def steps(monkeypatch) -> list[str]:
    """The ids the equation step is asked about; layout hands over a table with a cell formula."""
    asked: list[str] = []

    def transcribe(element, png, *, client, settings, context_text=None) -> Fix:
        asked.append(element.id)
        return equation_fix().model_copy(update={"element_id": element.id})

    def rebuild(element, png, *, client, settings, context_text=None) -> Fix:
        return table_fix(model_grid())

    document = Layout(
        elements=[table(local_cells()), cell_equation()],
        page_count=1,
        pages=[0],
        title="T",
        lang="en",
    )
    monkeypatch.setattr(layout, "analyze", lambda pdf, *, pages=None, max_pages=None: document)
    monkeypatch.setattr(layout, "crop", lambda pdf, element, **kw: png())
    monkeypatch.setattr(equation_step, "transcribe", transcribe)
    monkeypatch.setattr(figure_step, "describe", lambda *a, **k: None)
    monkeypatch.setattr(table_step, "rebuild", rebuild)
    return asked


def convert(**flags):
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=100)
    return run.convert_pdf(
        b"%PDF-",
        settings=Settings(_env_file=None, **flags),  # type: ignore[call-arg]
        client=FakeVisionClient(budget=budget),
    )


def test_with_tables_on_the_equation_step_reads_the_cell_and_the_page_writes_it(steps):
    done = convert(feature_tables=True)
    assert steps == ["p1-e2"]
    assert "<math" in done.result.html and done.result.html.index("<math") < done.result.html.index(
        "</table>"
    )


def test_with_tables_off_the_cell_equations_are_never_asked(steps):
    done = convert(feature_tables=False)
    assert steps == []
    assert all(e.kind is not Kind.EQUATION for e in done.result.elements)


# ------------------------------------------------------------------ a glyph belongs to one cell


def _glyphs(text: str, x: float, font: str = "CMR10", y: float = 100.0) -> list:
    from readablestem.pipeline.layout import _Char

    out = []
    for ch in text:
        out.append(_Char(ch, x, y, x + 5, y + 10, y + 8, 10.0, font, 4))
        x += 5
    return out


def test_a_glyph_in_two_overlapping_cell_boxes_goes_to_the_cell_it_overlaps_most(monkeypatch):
    from readablestem.pipeline import layout
    from readablestem.pipeline.layout import _Row

    monkeypatch.setattr(layout, "_TEX_MADE", True)
    left = _glyphs("65.1", 100) + _glyphs("±", 120, "CMSY10") + _glyphs("0.4", 125)
    right = _glyphs("p", 138, "CMMI10") + _glyphs("<", 143, "CMMI10") + _glyphs("0.05", 148)
    cells = [
        TableCell(row=0, col=0, text="65.1±0.4", bbox=(95, 95, 142, 115)),  # reaches 4 pt in
        TableCell(row=0, col=1, text="p<0.05", bbox=(136, 95, 190, 115)),
    ]
    grid = TableGrid(rows=1, cols=2, cells=cells, source="local")
    found = layout._cell_maths(grid, [_Row(left), _Row(right)])
    assert found[(0, 0)].text.replace(" ", "") == "65.1±0.4"  # no `p` from its neighbour
    assert found[(0, 1)].text.replace(" ", "") == "p<0.05"


def test_a_cell_whose_glyphs_are_not_its_text_is_not_an_equation(monkeypatch):
    from readablestem.pipeline import layout
    from readablestem.pipeline.layout import _Row

    monkeypatch.setattr(layout, "_TEX_MADE", True)
    glyphs = _glyphs("65.1", 100) + _glyphs("±", 120, "CMSY10") + _glyphs("0.4", 125)
    cell = TableCell(row=0, col=0, text="65.1 ± 0.5", bbox=(95, 95, 150, 115))
    grid = TableGrid(rows=1, cols=1, cells=[cell], source="local")
    assert layout._cell_maths(grid, [_Row(glyphs)]) == {}


# ------------------------------------------------------------------ settle


def settled(fixes: list[Fix], elements: list[Element] | None = None):
    elements = elements or [table(local_cells()), cell_equation()]
    return table_math.settle(elements, {f.element_id: f for f in fixes})


def test_a_table_reading_that_differs_from_the_equations_makes_the_equation_a_decision():
    own = TableCell(row=1, col=0, text="m", latex="\\mu", mathml=MATHML)
    _, fixes = settled([table_fix(model_grid(own)), equation_fix(latex="m")])
    flagged = fixes["p1-e2"]
    assert flagged.review == "required" and flagged.status is Status.NEEDS_REVIEW
    assert any(c.code == "differs_from_table_reading" and not c.passed for c in flagged.checks)


def test_the_same_reading_twice_asks_nobody():
    own = TableCell(row=1, col=0, text="m", latex="m", mathml=MATHML)
    _, fixes = settled([table_fix(model_grid(own)), equation_fix(latex="m")])
    assert fixes["p1-e2"].review == "none"


def test_an_equation_of_a_table_that_is_not_written_as_a_table_is_dropped_with_its_fix():
    kept = table_fix(model_grid()).model_copy(update={"status": Status.ABSTAINED})
    elements, fixes = settled([kept, equation_fix()])
    assert [e.id for e in elements] == ["p1-e1"]
    assert "p1-e2" not in fixes
    assert all(c.equation_id is None for c in elements[0].table.cells)
