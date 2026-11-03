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
import re
from functools import cache
from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline import layout, page_labels, slides
from readablestem.pipeline.layout import analyze

LATEX = Path(__file__).parent / "fixtures" / "layout" / "latex"
FIXTURES = Path(__file__).parent / "fixtures" / "layout"


@cache
def load(name: str) -> layout.Layout:
    return analyze((LATEX / f"{name}.pdf").read_bytes())


def pages_with_elements(result: layout.Layout) -> set[int]:
    return {e.page for e in result.elements}


def text_lines(path: Path, page: int) -> list[str]:
    """The lines of one page as the PDF's text layer has them, spaces removed."""
    with pymupdf.open(path) as doc:
        return [
            "".join(line.split())
            for block in doc[page].get_text("blocks")
            for line in block[4].splitlines()
            if line.strip()
        ]


def text_lines_spaced(path: Path, page: int) -> list[str]:
    with pymupdf.open(path) as doc:
        return [ln for block in doc[page].get_text("blocks") for ln in block[4].splitlines()]


def deck_text(result: layout.Layout, page: int) -> str:
    """What the output says on a page: element text, inline equations put back."""
    parts = [
        e.text or ""
        for e in result.elements
        if e.page == page and e.kind in (Kind.HEADING, Kind.PARAGRAPH, Kind.LIST_ITEM)
    ]
    parts += [e.text or "" for e in result.elements if e.page == page and e.kind == Kind.EQUATION]
    return "".join("".join(re.sub(r"⟦[^⟧]*⟧", "", p).split()) for p in parts)


# ------------------------------------------------------------------ the pure rules


def row(text: str, y: float = 100.0, size: float = 10.0, x: float = 30.0) -> slides.RowView:
    return slides.RowView(text, (x, y, x + 6 * len(text), y + size), size)


def view(
    number: int,
    rows: list[slides.RowView],
    label: str | None = None,
    *,
    size: tuple[float, float] = (363.0, 272.0),
    pictures: frozenset[int] = frozenset(),
) -> slides.PageView:
    return slides.PageView(number, size[0], size[1], label, rows, pictures)


def title_row(text: str) -> slides.RowView:
    return row(text, y=12.0, size=14.5, x=9.0)


def test_slide_shapes():
    assert slides.slide_shaped(363, 272)  # Beamer 4:3
    assert slides.slide_shaped(454, 255)  # Beamer 16:9
    assert slides.slide_shaped(960, 540)
    assert slides.slide_shaped(720, 450)  # 16:10
    assert not slides.slide_shaped(792, 612)  # US Letter on its side
    assert not slides.slide_shaped(842, 595)  # A4 on its side
    assert not slides.slide_shaped(612, 792)  # portrait


def test_earlier_overlay_steps_are_dropped_when_the_last_page_has_everything():
    views = [
        view(0, [title_row("Frame"), row("one line of body text goes here")], "1"),
        view(
            1,
            [
                title_row("Frame"),
                row("one line of body text goes here"),
                row("two lines of body text go here too", 120),
            ],
            "1",
        ),
        view(
            2,
            [
                title_row("Frame"),
                row("one line of body text goes here"),
                row("two lines of body text go here too", 120),
                row("three", 140),
            ],
            "1",
        ),
        view(3, [title_row("Next"), row("other")], "2"),
    ]
    deck = slides.detect("LaTeX with Beamer class", views)
    assert deck is not None
    assert deck.dropped == {0: 2, 1: 2}
    assert deck.steps == {}
    assert sorted(deck.titles) == [2, 3]


def test_a_step_with_text_the_last_page_lacks_is_kept_as_a_step():
    views = [
        view(0, [title_row("Cases"), row("light needs no medium")], "1"),
        view(1, [title_row("Cases"), row("sound needs a medium")], "1"),
        view(2, [title_row("Next"), row("x")], "2"),
    ]
    deck = slides.detect("LaTeX with Beamer class", views)
    assert deck is not None
    assert deck.dropped == {}
    assert deck.steps == {0: (1, 2), 1: (2, 2)}


def test_a_page_with_a_picture_the_last_page_lacks_is_kept():
    views = [
        view(0, [title_row("Plot"), row("text")], "1", pictures=frozenset({7})),
        view(1, [title_row("Plot"), row("text")], "1"),
        view(2, [title_row("Next"), row("x")], "2"),
    ]
    deck = slides.detect("LaTeX with Beamer class", views)
    assert deck is not None and deck.dropped == {}


def test_a_page_with_no_text_is_never_taken_for_an_earlier_step():
    """A page kept whole as its image reaches `detect` with no rows."""
    views = [
        view(0, [], "1"),
        view(1, [title_row("Frame"), row("text")], "1"),
        view(2, [title_row("Next"), row("x")], "2"),
    ]
    deck = slides.detect("LaTeX with Beamer class", views)
    assert deck is not None and deck.dropped == {}


def test_without_page_labels_the_same_title_makes_a_frame():
    views = [
        view(0, [title_row("Frame"), row("one line of body text goes here")]),
        view(
            1,
            [
                title_row("Frame"),
                row("one line of body text goes here"),
                row("two lines of body text go here too", 120),
            ],
        ),
        view(2, [title_row("Other"), row("x")]),
    ]
    deck = slides.detect("LaTeX with Beamer class", views)
    assert deck is not None and deck.dropped == {0: 1}


def test_a_line_may_wrap_differently_and_still_be_on_the_last_page():
    views = [
        view(0, [title_row("Frame"), row("a b c")], "1"),
        view(1, [title_row("Frame"), row("a b"), row("c", 112)], "1"),
        view(2, [title_row("Next"), row("x")], "2"),
    ]
    deck = slides.detect("LaTeX with Beamer class", views)
    assert deck is not None and deck.dropped == {0: 1}


def test_files_that_are_not_decks_are_not_touched():
    steps = [
        [title_row("Frame"), row("one line of body text goes here")],
        [
            title_row("Frame"),
            row("one line of body text goes here"),
            row("two lines of body text go here too", 120),
        ],
    ]
    paper = (792.0, 612.0)
    assert (
        slides.detect(
            "LaTeX with Beamer class", [view(i, r, "1", size=paper) for i, r in enumerate(steps)]
        )
        is None
    )
    assert slides.detect("pdfTeX", [view(i, r, "1") for i, r in enumerate(steps)]) is not None
    # Slide-shaped, repeated labels, not Beamer: a deck (a PowerPoint export with steps)...
    assert slides.detect(None, [view(i, r, "1") for i, r in enumerate(steps)]) is not None
    # ... but with no repeated label and no Beamer there is nothing to say it is one.
    assert slides.detect("Word", [view(0, steps[0], "1"), view(1, steps[1], "2")]) is None
    assert slides.detect("Beamer", [view(0, steps[0])]) is None  # one page


def test_the_title_is_the_largest_text_in_the_top_third():
    views = [
        view(
            0,
            [
                row("Small top", 4.0, 7.0),
                title_row("The Title"),
                row("a longer line of body text for the deck", 120),
            ],
            "1",
        ),
        view(1, [title_row("Second"), row("a longer line of body text for the deck", 120)], "2"),
    ]
    deck = slides.detect("Beamer", views)
    assert deck is not None
    assert deck.titles[0].text == "The Title"
    assert deck.context[0] == ("Small top",)


def element(id_: str, kind: Kind, page: int, bbox, text: str = "", **kw) -> Element:
    return Element(id=id_, kind=kind, page=page, bbox=bbox, order=0, text=text, **kw)


def test_finish_drops_the_headline_and_slide_numbers_and_makes_the_title_a_heading():
    deck = slides.Deck(
        beamer=True,
        titles={0: slides.Title("Frame", (9.0, 22.0, 80.0, 36.0), 14.5)},
        steps={0: (1, 2)},
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


def test_finish_turns_a_bar_that_holds_the_title_into_its_heading():
    deck = slides.Deck(
        beamer=True, titles={0: slides.Title("Frame", (9.0, 22.0, 80.0, 36.0), 14.5)}
    )
    elements = [
        element("p1-e0", Kind.FIGURE, 0, (0, 2, 363, 39), "Waves Frame"),
        element("p1-e1", Kind.PARAGRAPH, 0, (30, 100, 200, 112), "Body"),
    ]
    out = slides.finish(elements, deck, {0: (363.0, 272.0)})
    assert [(e.id, e.kind, e.text) for e in out] == [
        ("p1-e0", Kind.HEADING, "Frame"),
        ("p1-e1", Kind.PARAGRAPH, "Body"),
    ]


def test_finish_keeps_a_tall_figure_at_the_top_of_the_slide():
    deck = slides.Deck(
        beamer=True, titles={0: slides.Title("Frame", (9.0, 22.0, 80.0, 36.0), 14.5)}
    )
    elements = [
        element("p1-e0", Kind.PARAGRAPH, 0, (9, 22, 80, 36), "Frame"),
        element("p1-e1", Kind.FIGURE, 0, (20, 10, 340, 200), "plot"),
    ]
    out = slides.finish(elements, deck, {0: (363.0, 272.0)})
    assert [e.id for e in out] == ["p1-e0", "p1-e1"]


# ------------------------------------------------------------------ the fixture decks


def test_overlay_steps_are_read_once_and_the_page_count_falls_by_two_thirds():
    result = load("beamer_overlay")
    assert pages_with_elements(result) == {2, 5}
    assert result.pages == [2, 5]
    assert 1 - len(result.pages) / result.page_count >= 0.3


def test_no_line_of_the_last_step_is_lost_or_repeated():
    """Every word of the last page's text layer is in the output, and no paragraph or item
    comes out twice."""
    path = LATEX / "beamer_overlay.pdf"
    result = load("beamer_overlay")
    for last in (2, 5):
        said = deck_text(result, last)
        words = {
            w for line in text_lines_spaced(path, last) for w in re.findall(r"[A-Za-z]{4,}", line)
        }
        assert words
        assert not [w for w in words if w not in said], last
    texts = [e.text for e in result.elements if e.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM)]
    assert len(texts) == len(set(texts))


def test_frame_titles_are_headings_with_the_frames_content_after_them():
    result = load("beamer_overlay")
    headings = [(e.page, e.text) for e in result.elements if e.kind == Kind.HEADING]
    assert headings == [(2, "Deriving the Range"), (5, "Checking the Result")]
    assert result.elements[0].kind == Kind.HEADING


def test_a_theme_headline_and_footline_are_furniture():
    result = load("beamer_theme_bars")
    assert [e.kind for e in result.elements if e.kind in (Kind.FIGURE, Kind.HEADING)] == [
        Kind.HEADING,
        Kind.HEADING,
    ]
    said = " ".join(e.text or "" for e in result.elements)
    for name in ("Superposition", "Basics", "Beats", "R. Example", "Waves and Oscillations"):
        assert name not in said


def test_every_kind_of_step():
    result = load("slides_steps")
    # \pause (3 pages) and \item<n-> (4) and \uncover (3) collapse onto their last page; the
    # \only frame's two pages differ, so both stay.
    assert result.pages == [2, 3, 4, 8, 11, 12]
    assert result.page_count == 13
    assert 1 - len(result.pages) / result.page_count >= 0.3
    headings = [e.text for e in result.elements if e.kind == Kind.HEADING]
    assert headings == [
        "Wave Speed",
        "Cases (step 1 of 2)",
        "Cases (step 2 of 2)",
        "Checks",
        "Uncovered",
        "Summary",
    ]
    said = " ".join(e.text or "" for e in result.elements)
    assert "Light waves need no medium." in said and "Sound waves need a medium." in said
    assert "Where a crest meets a trough, it cancels." in said
    assert "Longer waves move faster" in said


def test_the_last_step_has_every_line_of_the_earlier_ones():
    """The rule's premise, checked on the PDF itself: what each dropped page prints is also
    printed on its frame's last page."""
    path = LATEX / "slides_steps.pdf"
    result = load("slides_steps")
    kept = set(result.pages)
    frames = {0: 2, 1: 2, 5: 8, 6: 8, 7: 8, 9: 11, 10: 11}
    for dropped, last in frames.items():
        assert dropped not in kept
        last_text = "".join(text_lines(path, last))
        for line in text_lines(path, dropped):
            assert line in last_text, (dropped, line)


def test_dropped_pages_are_not_in_the_layout_at_all():
    result = load("slides_steps")
    for page in (0, 1, 5, 6, 7, 9, 10):
        assert page not in result.pages
        assert page not in result.page_types
        assert page not in pages_with_elements(result)
    assert len(result.page_sizes) == len(result.pages)


def test_two_columns_are_read_left_then_right_on_a_slide():
    result = load("beamer_columns_table")
    texts = [e.text or "" for e in result.elements if e.page == 0]
    order = [
        next(i for i, t in enumerate(texts) if key in t)
        for key in ("Force view", "Hooke", "force points", "Energy view", "Stored", "zero at")
    ]
    assert order == sorted(order)
    assert "The force points to the origin." in texts
    assert any(t.startswith("The energy is zero at") for t in texts)


def test_a_wrapped_bullet_that_ends_in_maths_is_not_a_display_equation():
    result = load("beamer_columns_table")
    assert not [e for e in result.elements if e.page == 0 and e.display == "block"]
    inline = [e.text for e in result.elements if e.page == 0 and e.display == "inline"]
    assert "x = 0" in inline


def test_a_selection_of_pages_is_read_as_a_deck_of_those_pages():
    pdf = (LATEX / "slides_steps.pdf").read_bytes()
    result = analyze(pdf, pages=[0, 1, 2])
    assert result.pages == [2]
    assert [e.text for e in result.elements if e.kind == Kind.HEADING] == ["Wave Speed"]


# ------------------------------------------------------------------ non-slide files


NON_SLIDES = [
    *sorted(p for p in FIXTURES.glob("*.pdf") if not p.name.startswith("damaged")),
    LATEX / "calculus_notes.pdf",
    LATEX / "problem_set.pdf",
    LATEX / "tables.pdf",
    LATEX / "vectors_relations.pdf",
]


def snapshot(result: layout.Layout) -> str:
    return json.dumps(
        {
            "pages": result.pages,
            "elements": [e.model_dump(mode="json") for e in result.elements],
            "kept": result.kept_pages,
        },
        sort_keys=True,
    )


@pytest.mark.parametrize("path", NON_SLIDES, ids=lambda p: p.name)
def test_a_file_that_is_not_a_deck_is_read_as_before(path, monkeypatch):
    pdf = path.read_bytes()
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert not slides.slide_shaped(doc[0].rect.width, doc[0].rect.height)
    with_slides = snapshot(analyze(pdf))
    monkeypatch.setattr(slides, "detect", lambda *a, **k: None)
    assert snapshot(analyze(pdf)) == with_slides


def test_a_paper_sized_landscape_file_with_beamer_in_its_creator_is_not_a_deck():
    doc = pymupdf.open()
    for n in range(3):
        page = doc.new_page(width=792, height=612)
        page.insert_text((72, 100), "A line of a landscape handout.", fontsize=11)
        page.insert_text((72, 140), f"Table {n}", fontsize=11)
    doc.set_metadata({"creator": "LaTeX with Beamer class"})
    result = analyze(doc.tobytes())
    assert result.pages == [0, 1, 2]


# ------------------------------------------------------------------ labels and ligatures


def test_the_label_the_pdf_gives_a_beamer_page_is_read():
    pdf = (LATEX / "beamer_overlay.pdf").read_bytes()
    assert page_labels.read(pdf, range(6)) == {0: "1", 1: "1", 2: "1", 3: "2", 4: "2", 5: "2"}


def test_a_utf16_label_is_decoded_and_others_are_cleaned():
    class FakePage:
        def __init__(self, label: str) -> None:
            self.label = label

        def get_label(self) -> str:
            return self.label

    assert page_labels.pdf_label(FakePage("<FEFF0031>")) == "1"  # type: ignore[arg-type]
    assert page_labels.pdf_label(FakePage("<FEFF0041002D0033>")) == "A-3"  # type: ignore[arg-type]
    assert page_labels.pdf_label(FakePage("iv")) == "iv"  # type: ignore[arg-type]
    assert page_labels.pdf_label(FakePage("<FEFF00>")) is None  # type: ignore[arg-type]
    assert page_labels.pdf_label(FakePage("<script>")) is None  # type: ignore[arg-type]
    assert page_labels.pdf_label(FakePage("")) is None  # type: ignore[arg-type]


def chars(spec: list[tuple[str, float, float]]) -> list[dict]:
    return [{"c": c, "bbox": (x0, 0.0, x1, 10.0), "origin": (x0, 8.0)} for c, x0, x1 in spec]


def test_a_ligature_shares_its_glyphs_box_between_its_characters():
    """MuPDF gives "ff" the box of one glyph and the second f a box of no width at its end."""
    line = chars([("i", 0, 4), ("f", 4, 10), ("f", 10, 10), ("n", 10, 16)])
    layout._spread_ligatures(line, (1.0, 0.0))
    assert [round(c["bbox"][0], 2) for c in line] == [0, 4, 7, 10]
    assert [round(c["bbox"][2], 2) for c in line] == [4, 7, 10, 16]
    assert sorted(line, key=lambda c: c["bbox"][0]) == line  # position order is text order


def test_a_three_character_ligature_is_split_in_three():
    line = chars([("f", 0, 9), ("f", 9, 9), ("i", 9, 9), ("x", 9, 14)])
    layout._spread_ligatures(line, (1.0, 0.0))
    assert [round(c["bbox"][0], 2) for c in line] == [0, 3, 6, 9]


def test_characters_without_a_ligature_keep_their_boxes():
    line = chars([("a", 0, 5), ("b", 5, 10), ("c", 12, 17)])
    before = [dict(c) for c in line]
    layout._spread_ligatures(line, (1.0, 0.0))
    assert line == before
    # a space of no width after a letter is a ligature-shaped run too, but only when the
    # box is empty and touches the letter; one set apart is left alone
    line = chars([("a", 0, 5), (" ", 8, 8), ("b", 8, 13)])
    before = [dict(c) for c in line]
    layout._spread_ligatures(line, (1.0, 0.0))
    assert line == before


def test_text_turned_to_another_angle_is_left_alone():
    line = chars([("f", 0, 9), ("f", 9, 9)])
    before = [dict(c) for c in line]
    layout._spread_ligatures(line, (0.5, 0.5))
    assert line == before


def test_a_ligature_in_a_table_and_in_running_text_comes_out_whole():
    result = load("beamer_columns_table")
    table = next(e for e in result.elements if e.kind == Kind.TABLE)
    assert "Stiffness" in {c.text for c in table.table.cells}
    assert "Stifnf" not in " ".join(e.text or "" for e in result.elements)


# ------------------------------------------------------------------ the whole pipeline


def test_the_pipeline_makes_no_model_call_for_a_dropped_page_and_writes_one_section_a_frame():
    from readablestem.config import Settings
    from readablestem.core.budget import InMemorySpendGuard, JobBudget
    from readablestem.models import OFFLINE_DEFAULTS, FakeVisionClient
    from readablestem.pipeline import run

    budget = JobBudget(InMemorySpendGuard(100.0), max_usd=100.0, max_calls=1000)
    client = FakeVisionClient(defaults=dict(OFFLINE_DEFAULTS), budget=budget)
    output = run.convert_pdf(
        (LATEX / "slides_steps.pdf").read_bytes(),
        settings=Settings(_env_file=None, fake_models=True),  # type: ignore[call-arg]
        client=client,
        concurrency=2,
    )
    stats = output.result.stats
    assert stats.pages == 6
    assert [p.page for p in stats.per_page] == [2, 3, 4, 8, 11, 12]
    html = output.result.html
    headings = re.findall(r"<h2[^>]*>(.*?)</h2>", html)
    assert headings == [
        "Wave Speed",
        "Cases (step 1 of 2)",
        "Cases (step 2 of 2)",
        "Checks",
        "Uncovered",
        "Summary",
    ]
    assert len(re.findall(r'role="doc-pagebreak"', html)) <= 6


def test_a_mark_drawn_over_a_letter_is_not_a_ligature():
    """The arrow of a vector and a combining accent have no width and stay where they are."""
    for mark in ("⃗", "́", "̸"):
        line = chars([("p", 0, 6), (mark, 6, 6), ("q", 6, 12)])
        before = [dict(c) for c in line]
        layout._spread_ligatures(line, (1.0, 0.0))
        assert line == before, hex(ord(mark))
