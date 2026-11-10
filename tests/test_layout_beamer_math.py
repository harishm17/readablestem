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


# ---------------------------------------------------------------- known failures
