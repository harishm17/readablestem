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
