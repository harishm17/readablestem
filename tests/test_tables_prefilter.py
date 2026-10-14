"""The page test before table detection (`tables.may_hold_table`) only leaves out pages on
which the detector's tables would not be kept, so the layout is the same with and without
it. The detector is most of a page's analysis time; see docs/tables.md, "Which pages are
looked at"."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from readablestem.pipeline import layout, table_model, tables

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
TABLE_PDFS = [
    FIXTURES / "layout" / "latex" / "tables.pdf",
    FIXTURES / "tables" / "word_tables.pdf",
    *sorted((ROOT / "data" / "authored" / "tables").glob("*/*.pdf")),
]
ALL_PDFS = sorted(
    {*FIXTURES.rglob("*.pdf"), *(ROOT / "data" / "authored").rglob("*.pdf")} - set(TABLE_PDFS)
)


@pytest.fixture(autouse=True)
def no_model(tmp_path, monkeypatch):
    """As the other table tests: the SLANet file hidden, so the result does not depend on
    whether this machine has fetched it."""
    monkeypatch.setenv(table_model.MODEL_DIR_ENV, str(tmp_path))
    tables.reset_models()
    layout.release()
    yield
    tables.reset_models()
    layout.release()
    layout.TABLE_PREFILTER = True


def read(pdf: Path, *, prefilter: bool) -> str:
    layout.TABLE_PREFILTER = prefilter
    try:
        result = layout.analyze(pdf.read_bytes(), max_pages=40)
    except layout.LayoutError as exc:
        return f"error {exc.code}"
    finally:
        layout.release()
    return json.dumps(
        {
            "elements": [e.model_dump(mode="json") for e in result.elements],
            "notices": [n.model_dump(mode="json") for n in result.notices],
            "kept": result.kept_pages,
        },
        sort_keys=True,
        default=str,
    )


@pytest.mark.parametrize("pdf", TABLE_PDFS, ids=lambda p: p.parent.name + "/" + p.name)
def test_documents_with_tables_come_out_the_same(pdf):
    with_it, without = read(pdf, prefilter=True), read(pdf, prefilter=False)
    assert with_it == without
    assert '"kind": "table"' in without  # and there were tables to lose


@pytest.mark.slow
@pytest.mark.parametrize("pdf", ALL_PDFS, ids=lambda p: "/".join(p.parts[-3:]))
def test_every_other_pdf_in_the_repository_comes_out_the_same(pdf):
    assert read(pdf, prefilter=True) == read(pdf, prefilter=False)


# --------------------------------------------------------------------------- the decision


def decisions(build) -> list[bool]:
    """`may_hold_table` for each page of a PDF made by `build(doc)`; the detector itself is
    not run (the test is about its gate)."""
    doc = pymupdf.open()
    build(doc)
    data = doc.tobytes()
    seen: list[bool] = []
    original = tables.find

    def record(page, lines, paths, **kw):
        seen.append(tables.may_hold_table(page, lines, paths))
        return []

    tables.find = record  # ty: ignore[invalid-assignment]
    layout.TABLE_PREFILTER = False  # the gate is asked for, whatever it says
    try:
        layout.analyze(data)
    finally:
        tables.find = original
        layout.release()
    return seen


def prose(page, top: float = 100, lines: int = 12) -> None:
    for i in range(lines):
        page.insert_text(
            (72, top + 14 * i), "A line of running text that goes on and on.", fontsize=11
        )


def test_plain_text_is_left_out():
    def build(doc):
        prose(doc.new_page())

    assert decisions(build) == [False]


def test_a_run_of_aligned_columns_is_let_through():
    def build(doc):
        page = doc.new_page()
        for i, row in enumerate(("t (s)", "0.0", "1.0", "2.0", "3.0")):
            page.insert_text((100, 120 + 16 * i), row, fontsize=11)
            page.insert_text((250, 120 + 16 * i), f"{i * 4.9:.1f}", fontsize=11)
            page.insert_text((400, 120 + 16 * i), f"{i * 9.8:.1f}", fontsize=11)

    assert decisions(build) == [True]


def test_a_drawn_grid_is_let_through():
    def build(doc):
        page = doc.new_page()
        for i in range(4):
            page.draw_line((100, 100 + 20 * i), (400, 100 + 20 * i))
        for j in range(4):
            page.draw_line((100 + 100 * j, 100), (100 + 100 * j, 160))
        page.insert_text((110, 115), "Mass", fontsize=11)
        page.insert_text((210, 115), "Speed", fontsize=11)

    assert decisions(build) == [True]


def test_a_boxed_callout_with_running_text_is_left_out():
    """Two rules each way is a ruled table's least, but a box round a paragraph has no text
    in two columns."""

    def build(doc):
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(60, 90, 540, 260))
        prose(page, top=110, lines=8)

    assert decisions(build) == [False]


def test_a_few_equations_with_their_numbers_are_left_out_and_many_are_let_through():
    def page_with(count):
        def build(doc):
            page = doc.new_page()
            prose(page, top=80, lines=3)
            for i in range(count):
                page.insert_text((150, 200 + 40 * i), "v = d / t", fontsize=11)
                page.insert_text((500, 200 + 40 * i), f"({i + 1})", fontsize=11)

        return build

    assert decisions(page_with(2)) == [False]
    assert decisions(page_with(4)) == [True]  # a column of numbers beside a column of formulas


def test_rule_positions_are_counted_once_per_rule_not_per_piece():
    assert tables._positions([10.0, 10.4, 11.0]) == 1  # one rule drawn in three pieces
    assert tables._positions([10.0, 20.0, 20.9, 40.0]) == 3
    assert tables._positions([]) == 0
