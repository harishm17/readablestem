"""Live smoke test of the table step against the real OpenAI API (costs money; opt-in).

Run with: uv run --env-file .env pytest -m live_model tests/test_tables_live.py -s

Each test reserves spend through a JobBudget capped at `MAX_USD_PER_TEST` and goes
through the real client and router (`models.make_vision_client`), with FEATURE_TABLES on.

- `test_live_drawn_table`: a small table drawn with PyMuPDF (synthetic text: free fall,
  a signed column and a squared unit), with a local grid made from the PDF's words by
  position, as tables-detect would make it. Works on its own.
- `test_live_latex_fixture_tables`: every TABLE element layout finds in the LaTeX
  fixtures (`tests/fixtures/layout/latex`), with its real local grid. It is skipped until
  tables-detect has merged and a fixture has a table.
  TODO(integrator): once tables-detect has merged, run this test and compare with the
  smoke test results in docs/tables.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import (
    ConversionOptions,
    Element,
    Fix,
    Kind,
    Status,
    TableCell,
    TableGrid,
)
from readablestem.models import make_vision_client
from readablestem.pipeline import layout, options
from readablestem.pipeline.table_step import rebuild
from test_steps_support import make_settings

pytestmark = pytest.mark.live_model

LATEX_FIXTURES = Path(__file__).parent / "fixtures" / "layout" / "latex"
MAX_USD_PER_TEST = 0.10
CAPTION = "Table 1 A ball dropped from rest."
# Hyphen-minus signs: PyMuPDF's built-in Helvetica has no U+2212 (Word PDFs use the
# hyphen too; the checks fold both into one minus sign).
SQUARED = "\N{SUPERSCRIPT TWO}"
ROWS = [
    ["Time (s)", "Height (m)", "Velocity (m/s)", "Acceleration"],
    ["0", "20.0", "0", f"-9.8 m/s{SQUARED}"],
    ["1", "15.1", "-9.8", f"-9.8 m/s{SQUARED}"],
    ["2", "0.4", "-19.6", f"-9.8 m/s{SQUARED}"],
]


@pytest.fixture
def settings() -> Settings:
    s = make_settings(feature_tables=True)  # still reads OPENAI_API_KEY from the environment
    if s.openai_api_key is None or not s.openai_api_key.get_secret_value():
        pytest.skip("OPENAI_API_KEY is not set")
    return s


def _budget() -> JobBudget:
    return JobBudget(
        guard=InMemorySpendGuard(cap_usd=MAX_USD_PER_TEST), max_usd=MAX_USD_PER_TEST, max_calls=8
    )


def _drawn_table() -> tuple[bytes, Element]:
    """A ruled table drawn with PyMuPDF, and its element with a local grid filled from
    the page's own words cell by cell (a stand-in for tables-detect)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "A ball is dropped from a height of 20 m.", fontsize=11)
    page.insert_text((72, 128), CAPTION, fontsize=10)
    widths = [80.0, 90.0, 100.0, 110.0]
    x0, y0, height = 72.0, 140.0, 22.0
    edges = [x0]
    for width in widths:
        edges.append(edges[-1] + width)
    for r, row in enumerate(ROWS):
        top = y0 + r * height
        for c, text in enumerate(row):
            rect = pymupdf.Rect(edges[c], top, edges[c + 1], top + height)
            page.draw_rect(rect, color=(0, 0, 0), width=0.6)
            base = text.removesuffix(SQUARED)
            page.insert_text((rect.x0 + 6, rect.y1 - 7), base, fontsize=10)
            if base != text:  # the squared unit: a raised, smaller 2
                end = rect.x0 + 6 + pymupdf.get_text_length(base, fontsize=10)
                page.insert_text((end + 0.5, rect.y1 - 11), "2", fontsize=7)
    pdf = doc.tobytes()
    cells = []
    for r in range(len(ROWS)):
        for c in range(len(widths)):
            box = (edges[c], y0 + r * height, edges[c + 1], y0 + (r + 1) * height)
            text = " ".join(page.get_text("text", clip=pymupdf.Rect(*box)).split())
            cells.append(TableCell(row=r, col=c, text=text, bbox=box))
    grid = TableGrid(rows=len(ROWS), cols=len(widths), cells=cells, caption=CAPTION)
    bbox = (x0, y0, edges[-1], y0 + len(ROWS) * height)
    element = Element(
        id="p1-e2", kind=Kind.TABLE, page=0, bbox=bbox, order=2, caption=CAPTION, table=grid
    )
    return pdf, element


def _report(name: str, fix: Fix) -> None:
    print(f"\n{name}: {fix.status.value} review={fix.review} cost=${fix.cost_usd:.4f}")
    print("  failed:", sorted(str(c.code) for c in fix.failed_checks))
    print("  calls:", [(c.model, c.outcome, c.input_tokens, c.output_tokens) for c in fix.calls])


def test_live_drawn_table(settings):
    pdf, element = _drawn_table()
    png = layout.crop(pdf, element)
    client = make_vision_client(settings, _budget())
    with options.using(ConversionOptions(tables=True)):
        fix = rebuild(
            element,
            png,
            client=client,
            settings=settings,
            context_text="A ball is dropped from a height of 20 m.\n[this table]",
        )
    _report("drawn table", fix)
    assert fix.status is Status.ACCEPTED, sorted(str(c.code) for c in fix.failed_checks)
    assert fix.table is not None and fix.table.grid.header_rows == 1
    assert fix.cost_usd < MAX_USD_PER_TEST


def test_live_latex_fixture_tables(settings):
    found = [
        (path.stem, pdf, e)
        for path in sorted(LATEX_FIXTURES.glob("*.pdf"))
        for pdf in [path.read_bytes()]
        for e in layout.analyze(pdf).elements
        if e.kind is Kind.TABLE
    ]
    if not found:
        pytest.skip("no TABLE element in the LaTeX fixtures yet (tables-detect)")
    client = make_vision_client(settings, _budget())
    with options.using(ConversionOptions(tables=True)):
        for name, pdf, element in found:
            fix = rebuild(element, layout.crop(pdf, element), client=client, settings=settings)
            _report(f"{name} {element.id}", fix)
            # A real local grid can still disagree with a right answer (docs/tables.md
            # "Known limits"), so a decision is allowed; an image kept is not.
            assert fix.status in {Status.ACCEPTED, Status.NEEDS_REVIEW}, fix.reason
            assert fix.table is not None
