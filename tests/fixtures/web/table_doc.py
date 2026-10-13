"""A small, real-shaped document with tables, for the table card of the review screens.

`table_pdf()` draws a two-page PDF with PyMuPDF (ruled tables, a caption above each);
`table_output()` is the result the pipeline would make of it, every table's box measured
on that PDF, with a fix in each review tier (docs/design/content-features.md, "Review
tiers for the new items"):

- decision 1, table p1-e1 ("Table 1 Motion of two objects"): a model proposal that
  needs a decision (a check doubts it), its first row not marked as a header, a maths
  cell spanning two columns; the grid read from the PDF has the header row;
- decision 2, table p1-e3 ("Table 2 Units"): kept as an image (`table_not_rebuilt`), with
  a grid read from the PDF that could be used instead;
- decision 3, table p2-e0: kept as an image, no grid read from the PDF (only "Keep the
  image");
- an optional note, table p2-e1: accepted with a cosmetic warning;
- accepted with nothing to note: heading p1-e0, and table p2-e2, twelve columns wide
  (wider than a phone: it scrolls in its own box).

Fix tiers and checks are fake (the table step is not built yet); everything else (the
serialiser, the assembler, page images) is real.
"""

from __future__ import annotations

from functools import cache

import pymupdf

from a11y_stem.core.types import (
    Check,
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    Fix,
    HeadingFix,
    Kind,
    Status,
    TableCell,
    TableFix,
    TableGrid,
)

PAGE = (612.0, 792.0)
ROW_H = 18.0

# (caption, top-left corner, column widths, rows of cell text)
TABLE_1 = (
    "Table 1 Motion of two objects",
    (72.0, 130.0),
    (110.0, 110.0, 110.0),
    [
        ["Object", "Distance (m)", "Time (s)"],
        ["Cart", "10", "4"],
        ["Ball", "6", "2"],
        ["Speed", "v = d / t", ""],
    ],
)
TABLE_2 = (
    "Table 2 Units",
    (72.0, 300.0),
    (120.0, 80.0),
    [["Quantity", "Unit"], ["Force", "N"], ["Energy", "J"]],
)
TABLE_3 = (
    "Table 3 Measured times",
    (72.0, 90.0),
    (90.0, 90.0),
    [["Trial", "Time (s)"], ["1", "2.31"], ["2", "2.29"]],
)
TABLE_4 = (
    "Table 4 Constants",
    (72.0, 230.0),
    (150.0, 120.0),
    [["Constant", "Value"], ["g", "9.81 m/s²"]],
)
WIDE_HEAD = ["t (s)", *[str(n) for n in range(11)]]
WIDE_ROW = ["x (m)", *[f"{0.5 * n * n:.1f}" for n in range(11)]]
TABLE_5 = ("Table 5 Position over time", (40.0, 330.0), (44.0,) * 12, [WIDE_HEAD, WIDE_ROW])
MATHS_LATEX = r"v = \frac{d}{t}"


def _draw(page: pymupdf.Page, table) -> tuple[float, float, float, float]:
    caption, (x0, y0), widths, rows = table
    page.insert_text((x0, y0 - 6), caption, fontname="hebo", fontsize=10)
    x1 = x0 + sum(widths)
    y1 = y0 + ROW_H * len(rows)
    for r in range(len(rows) + 1):
        page.draw_line((x0, y0 + r * ROW_H), (x1, y0 + r * ROW_H), color=(0, 0, 0), width=0.6)
    x = x0
    for c in range(len(widths) + 1):
        page.draw_line((x, y0), (x, y1), color=(0, 0, 0), width=0.6)
        x += widths[c] if c < len(widths) else 0
    for r, row in enumerate(rows):
        x = x0
        for c, text in enumerate(row):
            font = "hebo" if r == 0 else "helv"
            page.insert_text((x + 4, y0 + r * ROW_H + 13), text, fontname=font, fontsize=9)
            x += widths[c]
    return (x0, y0, x1, y1)


@cache
def table_pdf() -> bytes:
    doc = pymupdf.open()
    one = doc.new_page(width=PAGE[0], height=PAGE[1])
    one.insert_text((72, 84), "2 Motion", fontname="hebo", fontsize=14)
    _draw(one, TABLE_1)
    _draw(one, TABLE_2)
    two = doc.new_page(width=PAGE[0], height=PAGE[1])
    _draw(two, TABLE_3)
    _draw(two, TABLE_4)
    _draw(two, TABLE_5)
    doc.subset_fonts()
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data


def _box(table) -> tuple[float, float, float, float]:
    _caption, (x0, y0), widths, rows = table
    return (x0, y0, x0 + sum(widths), y0 + ROW_H * len(rows))


def grid_of(table, *, header_rows: int = 1, source: str = "local") -> TableGrid:
    """A plain grid of the printed text: one cell per slot, the first `header_rows` rows
    column headers."""
    caption, _corner, widths, rows = table
    cells = [
        TableCell(row=r, col=c, text=text, header="col" if r < header_rows else None)
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    return TableGrid(
        rows=len(rows),
        cols=len(widths),
        cells=cells,
        header_rows=header_rows,
        caption=caption,
        source=source,  # type: ignore[arg-type]
    )


def table_1_proposal() -> TableGrid:
    """The model's grid: the header row not marked, and the formula as a maths cell
    spanning the last two columns."""
    from a11y_stem.pipeline.validate import safe_latex_to_mathml

    caption, _corner, _widths, rows = TABLE_1
    cells = [
        TableCell(row=r, col=c, text=text)
        for r, row in enumerate(rows[:3])
        for c, text in enumerate(row)
    ]
    cells.append(TableCell(row=3, col=0, text="Speed"))
    cells.append(
        TableCell(
            row=3,
            col=1,
            col_span=2,
            text="v = d / t",
            latex=MATHS_LATEX,
            mathml=safe_latex_to_mathml(MATHS_LATEX, "inline"),
        )
    )
    return TableGrid(rows=4, cols=3, cells=cells, caption=caption, source="model")


def table_1_local() -> TableGrid:
    """The grid read from the PDF: the header row marked, the formula as plain text over
    the two columns."""
    grid = grid_of(TABLE_1)
    cells = [c for c in grid.cells if not (c.row == 3 and c.col >= 1)]
    cells.append(TableCell(row=3, col=1, col_span=2, text="v = d / t"))
    return grid.model_copy(update={"cells": cells})


def _crop(page: pymupdf.Page, box: tuple[float, float, float, float]) -> bytes:
    x0, y0, x1, y1 = box
    clip = pymupdf.Rect(x0 - 2, y0 - 2, x1 + 2, y1 + 2)
    return page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False).tobytes("png")


DOUBT = Check(name="table.words", passed=False, severity="warning", code="words_differ")
COSMETIC = Check(name="table.words", passed=False, severity="warning", code="whitespace")


def table_output() -> ConversionOutput:
    doc = pymupdf.open(stream=table_pdf(), filetype="pdf")
    one, two = doc[0], doc[1]
    heading_box = tuple(round(v, 2) for v in one.search_for("2 Motion")[0])
    boxes = {
        "p1-e1": _box(TABLE_1),
        "p1-e3": _box(TABLE_2),
        "p2-e0": _box(TABLE_3),
        "p2-e1": _box(TABLE_4),
        "p2-e2": _box(TABLE_5),
    }
    crops = {eid: _crop(one if eid.startswith("p1") else two, box) for eid, box in boxes.items()}
    doc.close()

    def table(eid: str, order: int, spec, grid: TableGrid | None) -> Element:
        page = 0 if eid.startswith("p1") else 1
        return Element(id=eid, kind=Kind.TABLE, page=page, bbox=boxes[eid], order=order,
                       caption=spec[0], table=grid)  # fmt: skip

    elements = [
        Element(id="p1-e0", kind=Kind.HEADING, page=0, bbox=heading_box, order=0,
                text="2 Motion", font_size=14, bold=True),  # type: ignore[arg-type]
        table("p1-e1", 1, TABLE_1, table_1_local()),
        Element(id="p1-e2", kind=Kind.PARAGRAPH, page=0, bbox=(72.0, 220.0, 540.0, 240.0),
                order=2, text="The units of the quantities are below."),
        table("p1-e3", 3, TABLE_2, grid_of(TABLE_2)),
        table("p2-e0", 4, TABLE_3, None),
        table("p2-e1", 5, TABLE_4, grid_of(TABLE_4)),
        table("p2-e2", 6, TABLE_5, grid_of(TABLE_5)),
    ]  # fmt: skip
    fixes = [
        Fix(element_id="p1-e0", kind=Kind.HEADING, status=Status.ACCEPTED, confidence=0.95,
            heading=HeadingFix(level=1, text="2 Motion")),
        Fix(element_id="p1-e1", kind=Kind.TABLE, status=Status.NEEDS_REVIEW, confidence=0.62,
            table=TableFix(grid=table_1_proposal()), checks=[DOUBT], review="required"),
        Fix(element_id="p1-e3", kind=Kind.TABLE, status=Status.FAILED,
            reason="table_not_rebuilt", review="required"),
        Fix(element_id="p2-e0", kind=Kind.TABLE, status=Status.FAILED,
            reason="table_not_rebuilt", review="required"),
        Fix(element_id="p2-e1", kind=Kind.TABLE, status=Status.ACCEPTED, confidence=0.9,
            table=TableFix(grid=grid_of(TABLE_4, source="model")), checks=[COSMETIC],
            review="optional"),
        Fix(element_id="p2-e2", kind=Kind.TABLE, status=Status.ACCEPTED, confidence=0.95,
            table=TableFix(grid=grid_of(TABLE_5, source="model"))),
    ]  # fmt: skip
    result = DocumentResult(
        pipeline_version="test",
        title="Motion",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(pages=2, cost_usd=0.01, latency_ms=5, model_calls=3),
        html="<!doctype html><html lang='en'><head><title>Motion</title></head>"
        "<body><main><h1>Motion</h1></main></body></html>",
        page_sizes={0: PAGE, 1: PAGE},
    )
    return ConversionOutput(result=result, crops=crops)
