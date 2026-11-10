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
    GlyphContext,
    Kind,
    Status,
    TableCell,
    TableFix,
    TableGrid,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import equation_step, figure_step, layout, run, table_math, table_step
from readablestem.pipeline.assemble import render_html
from readablestem.pipeline.layout import (
    Layout,
    analyze,
    crop_line,
    line_across,
    line_glyphs,
)

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


def symbol_page(producer: str, marks: list[tuple[str, str]]) -> bytes:
    """A ruled table, a word column and a column of one glyph each: `marks` are (text, font)."""
    doc = pymupdf.open()
    doc.set_metadata({"producer": producer})
    page = doc.new_page(width=612, height=792)
    x = [100, 220, 340]
    y = [100 + 30 * k for k in range(len(marks) + 2)]
    for k in x:
        page.draw_line((k, y[0]), (k, y[-1]))
    for k in y:
        page.draw_line((x[0], k), (x[-1], k))
    page.insert_text((106, y[0] + 19), "Method", fontsize=11, fontname="helv")
    page.insert_text((226, y[0] + 19), "Seen", fontsize=11, fontname="helv")
    for r, (text, font) in enumerate(marks, start=1):
        page.insert_text((106, y[r] + 19), f"Row {r}", fontsize=11, fontname="helv")
        page.insert_text((226, y[r] + 19), text, fontsize=11, fontname=font)
    page.insert_text((106, y[-2] + 19), "Last", fontsize=11, fontname="helv")
    return doc.tobytes()


def cell_equation_texts(data: bytes) -> list[str]:
    layout, found = tables_of(data)
    by_id = {e.id: e for e in layout.elements}
    return [
        by_id[c.equation_id].text for t in found for c in t.table.cells if c.equation_id is not None
    ]


def test_a_cell_of_only_a_maths_symbol_glyph_is_an_equation():
    """A lone `\\checkmark` or `\\times` (a maths symbol font) has no run of its own: the cell
    is the formula. "Symbol" is the PDF base-14 maths symbol face."""
    texts = cell_equation_texts(
        symbol_page("pdfTeX-1.40.25", [("\N{MULTIPLICATION SIGN}", "symb"), ("±", "symb")])
    )
    assert sorted(texts) == ["±", "\N{MULTIPLICATION SIGN}"]


def test_a_dingbat_or_a_symbol_bullet_in_a_cell_is_not_maths():
    """ZapfDingbats ticks and the list bullet of the plain Symbol face are decoration."""
    marks = [("4", "zadb"), ("•", "symb")]
    assert cell_equation_texts(symbol_page("pdfTeX-1.40.25", marks)) == []


def neighbour_page() -> bytes:
    """A ruled table whose formula cell has the next column's "(5)" 20 points to its right."""
    doc = pymupdf.open()
    doc.set_metadata({"producer": "pdfTeX-1.40.25"})
    page = doc.new_page(width=612, height=792)
    x = [100, 220, 340]
    y = [100, 130, 160]
    for k in x:
        page.draw_line((k, y[0]), (k, y[-1]))
    for k in y:
        page.draw_line((x[0], k), (x[-1], k))
    page.insert_text((106, y[0] + 19), "Name", fontsize=11, fontname="helv")
    page.insert_text((226, y[0] + 19), "Ref", fontsize=11, fontname="helv")
    x0 = 106
    for text, font in (
        ("T", "heit"),
        (" = 2", "helv"),
        ("p", "heit"),
        ("/", "helv"),
        ("w", "heit"),
    ):
        page.insert_text((x0, y[1] + 19), text, fontsize=11, fontname=font)
        x0 += pymupdf.get_text_length(text, fontname=font, fontsize=11)
    page.insert_text((226, y[1] + 19), "(5)", fontsize=11, fontname="helv")
    return doc.tobytes()


def test_a_cell_equations_line_is_cut_to_its_own_column():
    """The line the equation step sees (the crop and the glyphs around the box) stops at the
    cell's column: the table's width took the next column's "(5)" into the reading."""
    data = neighbour_page()
    layout, found = tables_of(data)
    table = found[0]
    cell = next(c for c in table.table.cells if c.equation_id)
    equation = next(e for e in layout.elements if e.id == cell.equation_id)
    across = line_across(table, equation)
    assert across == (cell.bbox[0], cell.bbox[2])
    assert across[1] < 226  # not the table's right edge
    glyphs = glyph_context(data, equation, across)
    assert "(" not in glyphs.after and "5" not in glyphs.after
    wide = glyph_context(data, equation, (table.bbox[0], table.bbox[2]))
    assert "(5)" in "".join(c for c, _ in GlyphContext.decode(wide.after))
    narrow_png = crop_line(data, equation, across)
    wide_png = crop_line(data, equation, (table.bbox[0], table.bbox[2]))
    assert len(narrow_png) != len(wide_png)


def test_a_paragraph_equations_line_still_spans_the_paragraph():
    parent = Element(id="p1-e0", kind=Kind.PARAGRAPH, page=0, bbox=(72, 100, 500, 120), order=0)
    child = Element(id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(100, 100, 120, 110), order=1)
    assert line_across(parent, child) == (72, 500)
    assert line_across(None, child) is None


def glyph_context(data: bytes, equation: Element, across: tuple[float, float]) -> GlyphContext:
    context = line_glyphs(data, equation, across)
    assert context is not None
    return context


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


def test_a_grid_of_another_size_than_the_pdfs_takes_nothing():
    small = TableGrid(rows=1, cols=2, cells=model_grid().cells[:2], header_rows=1, source="model")
    out = merged([table_fix(small), equation_fix()])
    assert out["p1-e1"].table.grid is small or out["p1-e1"].table.grid == small


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


# ------------------------------------------------------------------ a glyph belongs to one cell


def _glyphs(text: str, x: float, font: str = "CMR10", y: float = 100.0) -> list:
    from readablestem.pipeline.layout import _Char

    out = []
    for ch in text:
        out.append(_Char(ch, x, y, x + 5, y + 10, y + 8, 10.0, font, 4))
        x += 5
    return out


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
