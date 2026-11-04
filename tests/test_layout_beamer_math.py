"""layout.analyze on Beamer slides: tests/fixtures/layout/latex/beamer_*.pdf, with gold.

Four small decks written for these tests and compiled with Tectonic (`make_pdfs.py` in that
folder): frame titles, itemize with inline maths, display equations and align, `\\pause` and
`\\item<n->` overlays (one PDF page per step, each repeating the last), two columns, a table
with maths, a slide-number footer, and a theme with header and footer bars. Beside each deck
is `beamer_<name>.gold.json` (`make_beamer_gold.py`): the frames and the equations as the
author typed them, read from the .tex with `readablestem.gold.arxiv_tex`.

These tests state today's behaviour. What passes is a guarantee; what does not is a strict
xfail with the reason, so the change that fixes it turns the xfail into a failure and the
mark gets removed with it.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline.layout import Layout, analyze

LATEX = Path(__file__).parent / "fixtures" / "layout" / "latex"
NAMES = ("beamer_basic", "beamer_overlay", "beamer_columns_table", "beamer_theme_bars")


def known_failure(name: str, failing: dict[str, str]):
    """A parametrize entry for `name`: a strict xfail with the reason when it is listed."""
    if name in failing:
        return pytest.param(name, marks=pytest.mark.xfail(strict=True, reason=failing[name]))
    return pytest.param(name)


def cases(failing: dict[str, str] | None = None):
    return [known_failure(name, failing or {}) for name in NAMES]


@cache
def load(name: str) -> Layout:
    return analyze((LATEX / f"{name}.pdf").read_bytes())


@cache
def gold(name: str) -> dict:
    return json.loads((LATEX / f"{name}.gold.json").read_text(encoding="utf-8"))


def on_page(layout: Layout, page: int) -> list[Element]:
    return [e for e in layout.elements if e.page == page]


def text_of(layout: Layout) -> list[str]:
    return [e.text or "" for e in layout.elements if e.kind != Kind.EQUATION]


def test_the_gold_matches_the_decks():
    """The gold is complete: every deck has frames, equations and a PDF page for each frame."""
    for name in NAMES:
        g = gold(name)
        pages = [p for frame in g["frames"] for p in frame["pages"]]
        assert pages == list(range(len(pages)))
        with pymupdf.open(LATEX / f"{name}.pdf") as doc:
            assert doc.page_count == len(pages)
        assert g["equations"]
        assert {e["frame"] for e in g["equations"]} <= set(range(len(g["frames"])))


# ---------------------------------------------------------------- what works today


@pytest.mark.parametrize("name", cases())
def test_each_frame_title_is_a_heading_once(name):
    """On the frame's first page, or, when its earlier overlay steps are not read again
    (`pipeline.slides`), on its last: one heading with the title, on one page."""
    layout = load(name)
    for frame in gold(name)["frames"]:
        found = [
            e.page
            for p in frame["pages"]
            for e in on_page(layout, p)
            if e.kind == Kind.HEADING and e.text == frame["title"]
        ]
        assert len(found) == 1, frame["title"]


@pytest.mark.parametrize("name", cases())
def test_display_equations_on_the_last_page_of_a_frame_match_the_gold_count(name):
    layout = load(name)
    for k, frame in enumerate(gold(name)["frames"]):
        expected = sum(1 for e in gold(name)["equations"] if e["frame"] == k and e["display"])
        blocks = [
            e
            for e in on_page(layout, frame["pages"][-1])
            if e.kind == Kind.EQUATION and e.display == "block"
        ]
        assert len(blocks) == expected, frame["title"]


@pytest.mark.parametrize(
    "name",
    cases(
        {
            "beamer_columns_table": (
                "six of the table's eight maths cells are equation elements; the two numbers set "
                "in maths mode with their unit as text ('0.50 kg') are in a text font, no glyph "
                "says they are maths"
            )
        }
    ),
)
def test_inline_equations_on_the_last_page_of_a_frame_match_the_gold_count(name):
    layout = load(name)
    for k, frame in enumerate(gold(name)["frames"]):
        expected = sum(1 for e in gold(name)["equations"] if e["frame"] == k and not e["display"])
        inline = [
            e
            for e in on_page(layout, frame["pages"][-1])
            if e.kind == Kind.EQUATION and e.display == "inline"
        ]
        assert len(inline) == expected, frame["title"]


@pytest.mark.parametrize("name", cases())
def test_a_slide_number_or_footer_bar_is_not_read_as_text(name):
    footer = re.compile(r"^\d+\s*/\s*\d+$|R\. Example|Waves and Oscillations|Spring 2027")
    assert not [t for t in text_of(load(name)) if footer.search(t)]


# ---------------------------------------------------------------- known failures


@pytest.mark.parametrize("name", cases())
def test_overlay_steps_do_not_repeat_content(name):
    layout = load(name)
    shown = [
        (e.text or "").strip()
        for e in layout.elements
        if e.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM, Kind.HEADING) and (e.text or "").strip()
    ]
    shown += [(e.text or "").strip() for e in layout.elements if e.kind == Kind.EQUATION and e.text]
    # Inline placeholders carry the page number; compare the text without them.
    shown = [re.sub(r"⟦[^⟧]*⟧", "", s) for s in shown]
    duplicates = {s for s in shown if shown.count(s) > 1}
    assert not duplicates


def test_a_theme_header_bar_is_not_content():
    layout = load("beamer_theme_bars")
    bar = re.compile(r"Superposition|Basics|Beats|^Waves$")
    assert not [
        e
        for e in layout.elements
        if e.kind in (Kind.PARAGRAPH, Kind.FIGURE, Kind.HEADING) and bar.search(e.text or "")
    ]


def test_two_columns_are_read_left_then_right():
    layout = load("beamer_columns_table")
    texts = [(e.text or "") for e in on_page(layout, 0)]
    order = [
        next(i for i, t in enumerate(texts) if key in t)
        for key in ("Force view", "Hooke", "force points", "Energy view", "Stored", "energy is")
    ]
    assert order == sorted(order)


def test_a_table_cell_that_is_one_formula_is_an_inline_equation_of_the_table():
    """Layout reads a cell whose glyphs say it is maths as an equation of the table: the cell
    names it (`TableCell.equation_id`), the equation step reads its crop, and the page writes
    what it read into the cell (`pipeline.table_math`)."""
    layout = load("beamer_columns_table")
    table = next(e for e in layout.elements if e.kind == Kind.TABLE)
    by_id = {e.id: e for e in layout.elements}
    cells = {c.text: c for c in table.table.cells}
    for text in ("m", "k", "ω = √k/m", "T = 2π/ω"):
        child = by_id[cells[text].equation_id]
        assert child.kind == Kind.EQUATION and child.display == "inline"
        assert child.parent_id == table.id
        x0, y0, x1, y1 = table.bbox
        assert x0 <= child.bbox[0] and child.bbox[2] <= x1 and y0 <= child.bbox[1] <= y1
        assert (child.text or "").replace(" ", "") == text.replace(" ", "")
    for text in ("Mass", "Stiffness", "Quantity"):  # words are not maths
        assert cells[text].equation_id is None


def test_table_text_with_a_ligature_is_intact():
    layout = load("beamer_columns_table")
    table = next(e for e in layout.elements if e.kind == Kind.TABLE)
    assert "Stiffness" in {c.text for c in table.table.cells}
