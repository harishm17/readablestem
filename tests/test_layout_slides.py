"""Slide decks (`pipeline.slides`, docs/layout.md "Slide decks"): overlay steps, frame titles,
headline and footline furniture, two-column frames, and non-slide files left as they were.

The decks are the Tectonic-built fixtures in `tests/fixtures/layout/latex/`: `beamer_overlay`
(two frames of three steps), `beamer_theme_bars` (a header and footer bar theme),
`beamer_columns_table` (columns and a table) and `slides_steps` (every kind of step on a
Warsaw-style theme: `\\pause`, `\\only`, `\\item<n->`, `\\uncover`). The pure rules are tested
on made-up page views.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from readablestem.core.types import Element, Kind
from readablestem.pipeline import layout, slides
from readablestem.pipeline.layout import analyze

LATEX = Path(__file__).parent / "fixtures" / "layout" / "latex"
FIXTURES = Path(__file__).parent / "fixtures" / "layout"


@cache
def load(name: str) -> layout.Layout:
    return analyze((LATEX / f"{name}.pdf").read_bytes())


# ------------------------------------------------------------------ the pure rules


def row(text: str, y: float = 100.0, size: float = 10.0, x: float = 30.0) -> slides.RowView:
    return slides.RowView(text, (x, y, x + 6 * len(text), y + size), size)


def view(
    number: int,
    rows: list[slides.RowView],
    label: str | None = None,
    *,
    size: tuple[float, float] = (363.0, 272.0),
    marks: list[slides.Mark] | None = None,
) -> slides.PageView:
    return slides.PageView(number, size[0], size[1], label, rows, marks or [])


def title_row(text: str) -> slides.RowView:
    return row(text, y=12.0, size=14.5, x=9.0)


def test_a_page_with_no_text_is_never_taken_for_an_earlier_step():
    """A page kept whole as its image reaches `detect` with no rows."""
    views = [
        view(0, [], "1"),
        view(1, [title_row("Frame"), row("text")], "1"),
        view(2, [title_row("Next"), row("x")], "2"),
    ]
    deck = slides.detect("LaTeX with Beamer class", views)
    assert deck is not None and deck.dropped == {}


def element(id_: str, kind: Kind, page: int, bbox, text: str = "", **kw) -> Element:
    return Element(id=id_, kind=kind, page=page, bbox=bbox, order=0, text=text, **kw)


def test_finish_drops_the_headline_and_slide_numbers_and_makes_the_title_a_heading():
    deck = slides.Deck(
        beamer=True,
        titles={0: slides.Title("Frame", (9.0, 22.0, 80.0, 36.0), 14.5)},
        steps={0: (1, 2)},
        labels={0: "3"},
    )
    elements = [
        element("p1-e0", Kind.FIGURE, 0, (0, 2, 363, 39), "Waves"),  # a bar holding the title
        element("p1-e1", Kind.PARAGRAPH, 0, (190, 6, 206, 10), "Basics", font_size=6.0),
        element("p1-e2", Kind.PARAGRAPH, 0, (9, 22, 80, 36), "Frame", font_size=14.5),
        element("p1-e3", Kind.PARAGRAPH, 0, (30, 100, 200, 112), "Body text", font_size=10.0),
        element("p1-e4", Kind.PARAGRAPH, 0, (345, 262, 357, 270), "3 / 20", font_size=6.0),
    ]
    out = slides.finish(elements, deck, {0: (363.0, 272.0)}, ("larger font",))
    assert [e.id for e in out] == ["p1-e2", "p1-e3"]
    assert out[0].kind == Kind.HEADING and out[0].text == "Frame (step 1 of 2)"
    assert "larger font" in out[0].reasons
    assert [e.order for e in out] == [0, 1]


# ------------------------------------------------------------------ the fixture decks


def test_a_selection_of_pages_is_read_as_a_deck_of_those_pages():
    pdf = (LATEX / "slides_steps.pdf").read_bytes()
    result = analyze(pdf, pages=[0, 1, 2])
    assert result.pages == [2]
    assert [e.text for e in result.elements if e.kind == Kind.HEADING] == ["Wave Speed"]


# ------------------------------------------------------------------ non-slide files


def snapshot(result: layout.Layout) -> str:
    return json.dumps(
        {
            "pages": result.pages,
            "elements": [e.model_dump(mode="json") for e in result.elements],
            "kept": result.kept_pages,
        },
        sort_keys=True,
    )


# ------------------------------------------------------------------ labels and ligatures


def chars(spec: list[tuple[str, float, float]]) -> list[dict]:
    return [{"c": c, "bbox": (x0, 0.0, x1, 10.0), "origin": (x0, 8.0)} for c, x0, x1 in spec]


# ------------------------------------------------------------------ the whole pipeline


def test_a_bare_number_at_the_edge_is_dropped_only_when_it_is_furniture():
    deck = slides.Deck(beamer=True, labels={0: "3", 1: "4", 2: "5"})
    sizes = {0: (363.0, 272.0), 1: (363.0, 272.0), 2: (363.0, 272.0)}

    def number(page: int, text: str, x: float = 345.0, y: float = 262.0) -> Element:
        return element(f"p{page + 1}-e9", Kind.PARAGRAPH, page, (x, y, x + 12, y + 8), text)

    # the page's own number, alone or as "n / m": furniture
    out = slides.finish([number(0, "3"), number(1, "4 / 20")], deck, sizes)
    assert out == []
    # another number near the bottom of one slide (a count in a figure): content
    out = slides.finish([number(0, "7", x=100.0)], deck, sizes)
    assert [e.text for e in out] == ["7"]
    # the same place on most slides: furniture whatever it says
    out = slides.finish([number(0, "9"), number(1, "9"), number(2, "9")], deck, sizes)
    assert out == []
