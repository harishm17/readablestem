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

import pymupdf

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


def test_scanned_slides_do_not_count_against_the_titles_of_a_deck():
    """Half the slides are kept whole as images: the typed ones have titles, so it is a deck;
    a file of kept-whole pages alone is not."""
    typed = [
        view(n, [title_row(f"Slide {n}"), row("Some text of the slide that is the body")])
        for n in (0, 2, 4)
    ]
    scanned = [slides.PageView(n, 363.0, 272.0, None, [], kept_whole=True) for n in (1, 3, 5)]
    pages = sorted([*typed, *scanned], key=lambda v: v.number)
    assert slides.detect(None, pages) is not None
    assert slides.detect(None, scanned) is None


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


# ------------------------------------------------------------------ decks with no Beamer, no labels

SLIDE = (720.0, 540.0)
WIDE = (720.0, 405.0)  # 16:9
LETTER = (792.0, 612.0)  # a letter sheet on its side


def title_slide(text: str, size: float = 66.0, size_of_page=SLIDE) -> tuple:
    return size_of_page, [(text, 240, size), ("A short subtitle for this talk", 330, 24)]


def slide(title: str, *lines: str, size_of_page=SLIDE, ordinary: float = 36.0) -> tuple:
    body = [(line, 150 + 50 * i, 20) for i, line in enumerate(lines)]
    return size_of_page, [(title, 80, ordinary), *body]


def deck_pdf(pages: list[tuple]) -> bytes:
    """A PDF with no creator, no labels and no theme: a page is (size, [(text, y, font size)])."""
    doc = pymupdf.open()
    for (width, height), lines in pages:
        page = doc.new_page(width=width, height=height)
        for text, y, size in lines:
            page.insert_text((60, y), text, fontsize=size)
    data = doc.tobytes()
    doc.close()
    return data


def heading_levels_of(result: layout.Layout) -> dict[str, int]:
    from readablestem.pipeline import heading_step

    fixes = heading_step.heading_fixes(result.elements)
    level = {f.element_id: f.heading.level for f in fixes if f.heading is not None}
    return {e.text: level[e.id] for e in result.elements if e.id in level and e.text}


def test_a_powerpoint_style_deck_loses_its_earlier_steps_and_only_when_it_has_titles():
    one = ["Wave one rises"]
    two = [*one, "Wave two follows"]
    three = [*two, "Wave three ends"]
    # A deck printed to PDF is on a letter sheet turned on its side: its titles make it a deck.
    for sheet in (SLIDE, LETTER):
        pages = [
            slide("Waves", *one, size_of_page=sheet),
            slide("Waves", *two, size_of_page=sheet),
            slide("Waves", *three, size_of_page=sheet),
            slide("Energy", "Energy is conserved", size_of_page=sheet),
        ]
        result = analyze(deck_pdf(pages))
        assert result.pages == [2, 3]  # the first two pages are steps of the third
        assert [e.text for e in result.elements if e.kind == Kind.HEADING] == ["Waves", "Energy"]
        # the same pages with no title on any of them are not slides: nothing is dropped
        plain = [(size, [(t, y, 20) for t, y, _ in lines[1:]]) for size, lines in pages]
        assert analyze(deck_pdf(plain)).pages == [0, 1, 2, 3]


def test_a_second_title_slide_or_a_new_page_size_starts_a_second_deck_at_the_same_level():
    for second in (SLIDE, WIDE):
        pages = [
            title_slide("First Lecture"),
            slide("Alpha", "First point"),
            slide("Beta", "Second point"),
            title_slide("Second Lecture", 50.0, second),
            slide("Gamma", "Third point", size_of_page=second),
            slide("Delta", "Fourth point", size_of_page=second),
        ]
        if (
            second == WIDE
        ):  # a new size alone starts a deck: its first page need not be a title slide
            pages[3] = slide("Second Lecture", "A line", size_of_page=second, ordinary=50.0)
        levels = heading_levels_of(analyze(deck_pdf(pages)))
        assert levels["First Lecture"] == levels["Second Lecture"] == 1
        assert {levels[t] for t in ("Alpha", "Beta", "Gamma", "Delta")} == {2}


def test_a_formula_larger_than_the_slide_title_does_not_make_a_title_slide():
    def formula(title: str) -> tuple:
        return SLIDE, [
            (title, 80, 36),
            ("x = -b / 2a", 270, 50),
            ("The roots of the polynomial follow from this formula", 480, 20),
        ]

    pages = [title_slide("First Lecture"), formula("Alpha"), formula("Beta"), formula("Gamma")]
    levels = heading_levels_of(analyze(deck_pdf(pages)))
    assert levels["First Lecture"] == 1
    assert {levels[t] for t in ("Alpha", "Beta", "Gamma")} == {2}


def test_a_formula_in_the_top_of_a_slide_is_not_its_title():
    """The slide's title is the text above a large formula, not the formula."""
    pages = [
        view(
            n,
            [
                row(title, y=20, size=30.0),
                row("x = -b / 2a", y=60, size=50.0),
                row("The roots of a quadratic follow from this formula", y=200),
            ],
        )
        for n, title in enumerate(["Alpha", "Beta", "Gamma"])
    ]
    deck = slides.detect(None, pages)
    assert deck is not None
    assert [t.text for t in deck.titles.values()] == ["Alpha", "Beta", "Gamma"]


def test_a_slide_titled_with_a_name_that_has_a_plus_in_it_keeps_its_title():
    pages = [
        view(
            n, [row(title, y=20, size=30.0), row("Body text of the slide goes in this line", y=200)]
        )
        for n, title in enumerate(["C++", "Q+A", "Alpha"])
    ]
    deck = slides.detect(None, pages)
    assert deck is not None
    assert [t.text for t in deck.titles.values()] == ["C++", "Q+A", "Alpha"]


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
