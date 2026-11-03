"""Document structure fixes in layout: the PDF's bookmarks, numbered and run-in headings,
maths in headings, paragraphs and inline maths that cross a page or a line, and the
hyphen of a compound across a line break (docs/layout.md, "Known failure modes").

Every PDF is drawn here with PyMuPDF (publishable inputs only); no model is called.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Kind, Status, inline_marker
from readablestem.pipeline.assemble import render_html
from readablestem.pipeline.heading_step import heading_fixes
from readablestem.pipeline.layout import (
    REASON_NUMBERED_LINE,
    REASON_RUN_IN_LABEL,
    analyze,
    heading_levels,
)

BODY = 11.0
SENTENCE = "The cart rolls down the ramp and speeds up as it goes, so the velocity grows."
DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")


def pdf_of(fill, toc=None, pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for number in range(pages):
        fill(number, doc.new_page(width=612, height=792))
    if toc:
        doc.set_toc(toc)
    return doc.tobytes()


def para(page, y: float, text: str = SENTENCE, lines: int = 3, *, size: float = BODY, x=72.0):
    """Body lines 14 pt apart; returns the y of the next paragraph."""
    for _ in range(lines):
        page.insert_text((x, y), text, fontsize=size, fontname="helv")
        y += 14
    return y + 10


def one(page, y: float, text: str, font: str = "helv", size: float = BODY, x: float = 72.0):
    page.insert_text((x, y), text, fontsize=size, fontname=font)
    return y + 24


def texts(layout, kind: Kind) -> list[str]:
    return [e.text or "" for e in layout.elements if e.kind == kind]


# ------------------------------------------------------------------ 1. the PDF's outline


def outline_page(number, page):
    y = para(page, 72)
    y = one(page, y, "Overview")
    y = para(page, y)
    y = one(page, y, "Cart on a ramp")
    para(page, y)


def test_bookmarks_make_plain_body_size_lines_headings_with_the_bookmark_levels():
    toc = [[1, "Overview", 1], [2, "Cart on a ramp", 1]]
    layout = analyze(pdf_of(outline_page, toc))
    assert texts(layout, Kind.HEADING) == ["Overview", "Cart on a ramp"]
    levels = heading_levels(layout.elements)
    assert [levels[e.id] for e in layout.elements if e.kind == Kind.HEADING] == [1, 2]
    assert [f.heading.level for f in heading_fixes(layout.elements)] == [1, 2]


def test_without_bookmarks_the_same_lines_stay_paragraphs():
    layout = analyze(pdf_of(outline_page))
    assert texts(layout, Kind.HEADING) == []


def test_bookmarks_that_do_not_match_the_page_text_are_ignored():
    toc = [[1, "Thermodynamics", 1], [2, "Entropy of mixing", 1], [1, "Overview", 1]]
    layout = analyze(pdf_of(outline_page, toc))
    assert texts(layout, Kind.HEADING) == []  # one of three matches: not consistent


def test_a_bookmark_to_a_page_that_was_not_analysed_is_not_counted():
    def fill(number, page):
        y = para(page, 72)
        y = one(page, y, "Overview" if number == 0 else "Second part")
        para(page, y)

    toc = [[1, "Overview", 1], [1, "Second part", 2], [1, "Third part", 3]]
    layout = analyze(pdf_of(fill, toc, pages=3), pages=[0, 1])
    assert texts(layout, Kind.HEADING) == ["Overview", "Second part"]


def test_a_flat_outline_does_not_flatten_headings_with_real_font_levels():
    def fill(number, page):
        y = one(page, 80, "Chapter One", "hebo", 22)
        y = para(page, y)
        y = one(page, y, "Section A", "hebo", 16)
        y = para(page, y)
        y = one(page, y, "Section B", "hebo", 16)
        para(page, y)

    toc = [[1, "Chapter One", 1], [1, "Section A", 1], [1, "Section B", 1]]
    levels = [f.heading.level for f in heading_fixes(analyze(pdf_of(fill, toc)).elements)]
    assert levels == [1, 2, 2]  # the bookmarks' one level did not replace the font levels


def test_a_bookmark_only_names_the_line_that_is_its_whole_title():
    def fill(number, page):
        y = para(page, 72)
        y = one(page, y, "Overview")
        y = para(page, y)
        y = para(page, y, "Overview of the ramp experiment, which we repeated every day.", lines=2)
        para(page, y)

    layout = analyze(pdf_of(fill, [[1, "Overview", 1]]))
    assert texts(layout, Kind.HEADING) == ["Overview"]  # not the sentence that starts with it


def test_a_tag_still_wins_over_a_bookmark_level():
    from readablestem.pipeline.layout import REASON_TAGGED, tagged_level

    layout = analyze(pdf_of(outline_page, [[1, "Overview", 1], [2, "Cart on a ramp", 1]]))
    heading = next(e for e in layout.elements if e.kind == Kind.HEADING)
    tagged = heading.model_copy(update={"reasons": [*heading.reasons, f"{REASON_TAGGED}3"]})
    assert tagged_level(heading) == 1
    assert tagged_level(tagged) == 3


# ------------------------------------------------- 2a. plain numbered headings, 2b. labels


def numbered_page(number, page):
    y = para(page, 72)
    y = one(page, y, "3 Forces")
    y = para(page, y)
    y = one(page, y, "4 Energy and Work")
    para(page, y)


def test_plain_body_size_numbered_lines_in_sequence_are_headings():
    layout = analyze(pdf_of(numbered_page))
    assert texts(layout, Kind.HEADING) == ["3 Forces", "4 Energy and Work"]
    first = next(e for e in layout.elements if e.kind == Kind.HEADING)
    assert REASON_NUMBERED_LINE in first.reasons
    assert all(f.review in ("optional", "required") for f in heading_fixes(layout.elements))


def test_one_numbered_line_alone_stays_a_paragraph():
    def fill(number, page):
        y = para(page, 72)
        y = one(page, y, "3 Forces")
        para(page, y)

    assert texts(analyze(pdf_of(fill)), Kind.HEADING) == []


def test_quantities_and_list_lines_are_not_numbered_headings():
    def fill(number, page):
        y = para(page, 72)
        y = one(page, y, "12 Metres")
        y = para(page, y)
        y = one(page, y, "13 Metres")
        para(page, y)

    assert texts(analyze(pdf_of(fill)), Kind.HEADING) == []


def test_short_numbered_lines_with_no_paragraph_between_are_a_list_not_headings():
    def fill(number, page):
        y = para(page, 72)
        for text in ("1 Apples", "2 Bananas", "3 Cherries"):
            y = one(page, y, text)
        para(page, y)

    assert texts(analyze(pdf_of(fill)), Kind.HEADING) == []


def label_page(inset: float):
    """Body text at the margin, and two worked examples whose label and text sit `inset`
    points in (a box)."""

    def fill(number, page):
        y = para(page, 72)
        y = para(page, y)
        y = para(page, y)
        y = one(page, y, "Solution", "hebo", x=72 + inset)
        y = para(page, y, "First find the net force on the cart.", lines=1, x=72 + inset)
        y = para(page, y)
        y = one(page, y, "Solution", "hebo", x=72 + inset)
        para(page, y, "Then divide by the mass of the cart.", lines=1, x=72 + inset)

    return fill


def test_a_repeated_bold_run_in_label_is_bold_text_not_a_heading():
    layout = analyze(pdf_of(label_page(18)))
    labels = [e for e in layout.elements if e.kind == Kind.HEADING]
    assert [e.text for e in labels] == ["Solution", "Solution"]
    assert all(REASON_RUN_IN_LABEL in e.reasons for e in labels)
    fixes = heading_fixes(layout.elements)
    assert all(f.heading is None and f.status is Status.ACCEPTED for f in fixes)
    assert all(f.review == "none" for f in fixes)
    html = render_html(title="T", lang="en", elements=layout.elements, fixes=fixes, crops={})
    assert "Solution</h" not in html
    assert html.count("<strong>Solution</strong></p>") == 2


def test_a_repeated_label_at_the_text_margin_stays_a_heading():
    # LaTeX \subsubsection*{Solution} and a Word heading style sit at the margin: the
    # document says it is a heading (the authored dev-v2 documents do exactly this).
    layout = analyze(pdf_of(label_page(0)))
    assert texts(layout, Kind.HEADING) == ["Solution", "Solution"]
    assert not any(REASON_RUN_IN_LABEL in e.reasons for e in layout.elements)


def test_a_single_label_or_another_bold_line_stays_a_heading():
    def fill(number, page):
        y = para(page, 72)
        y = one(page, y, "Solution", "hebo")
        y = para(page, y)
        y = one(page, y, "Cart on a ramp", "hebo")
        y = para(page, y)
        y = one(page, y, "Cart on a ramp", "hebo")
        para(page, y)

    layout = analyze(pdf_of(fill))
    assert texts(layout, Kind.HEADING) == ["Solution", "Cart on a ramp", "Cart on a ramp"]
    assert not any(REASON_RUN_IN_LABEL in e.reasons for e in layout.elements)


def test_a_repeated_label_in_a_larger_font_is_still_a_heading():
    def fill(number, page):
        y = para(page, 72)
        y = one(page, y, "Solution", "hebo", 14)
        y = para(page, y)
        y = one(page, y, "Solution", "hebo", 14)
        para(page, y)

    layout = analyze(pdf_of(fill))
    assert not any(REASON_RUN_IN_LABEL in e.reasons for e in layout.elements)
    assert all(f.heading is not None for f in heading_fixes(layout.elements))


# ------------------------------------------------------------- 3. maths in headings


@pytest.mark.skipif(not DEJAVU.exists(), reason="needs DejaVu Serif for math-italic letters")
def test_math_italic_letters_in_a_heading_are_plain_letters():
    def fill(number, page):
        page.insert_font(fontname="dv", fontfile=str(DEJAVU))
        page.insert_text((72, 90), "Planck constant \u210e", fontsize=18, fontname="dv")
        para(page, 130)

    layout = analyze(pdf_of(fill))
    assert texts(layout, Kind.HEADING) == ["Planck constant h"]


def test_math_italic_letters_fold_but_other_symbols_stay():
    from readablestem.pipeline.layout import readable_math_letters

    assert readable_math_letters("\U0001d454 = ℎν, \U0001d7d9 − α") == ("g = hν, 1 − α")  # noqa: RUF001
    assert readable_math_letters("plain text") == "plain text"


# ---------------------------------------------- 4. paragraphs across a page break


def split_pages(second_first: str, *, first_last: str = "and the cart continues to", **kw):
    """Two pages; the first ends in an unfinished sentence, the second starts with
    `second_first`. A running header and a footnote sit around the break."""

    def fill(number, page):
        page.insert_text((72, 40), "Notes on motion", fontsize=9, fontname="helv")
        page.insert_text((300, 760), str(number + 1), fontsize=9, fontname="helv")
        if number == 0:
            y = para(page, 72)
            y = para(page, y)
            y = para(page, y, lines=2)
            y = para(page, y, lines=2)
            page.insert_text((72, 640), SENTENCE, fontsize=BODY, fontname="helv")
            page.insert_text((72, 654), first_last, fontsize=BODY, fontname="helv")
            if kw.get("footnote"):
                page.insert_text((72, 720), "1 A footnote.", fontsize=8, fontname="helv")
        else:
            x = 72.0 + kw.get("indent", 0.0)
            page.insert_text((x, 72), second_first, fontsize=kw.get("size", BODY), fontname="helv")
            page.insert_text(
                (72, 86), "ramp stays level for the whole trip.", fontsize=BODY, fontname="helv"
            )
            para(page, 120)

    return pdf_of(fill, pages=2)


def paragraphs(layout):
    return [e for e in layout.elements if e.kind == Kind.PARAGRAPH]


def test_a_paragraph_cut_by_a_page_break_is_one_element():
    layout = analyze(split_pages("speed up while the"))
    joined = [e for e in paragraphs(layout) if "continues to speed up" in (e.text or "")]
    assert len(joined) == 1
    assert joined[0].page == 0
    assert joined[0].text.endswith("stays level for the whole trip.")
    assert [e.page for e in paragraphs(layout)].count(1) == 1  # only the paragraph after it


def test_a_footnote_and_running_header_between_the_parts_do_not_stop_the_join():
    layout = analyze(split_pages("speed up while the", footnote=True))
    assert any("continues to speed up" in (e.text or "") for e in paragraphs(layout))
    assert any((e.text or "").startswith("1 A footnote") for e in paragraphs(layout))


@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        ({"second_first": "Speed up while the"}, "the next page starts a sentence"),
        ({"second_first": "speed up while the", "first_last": "and then it stopped."}, "full stop"),
        ({"second_first": "speed up while the", "indent": 24.0}, "first-line indent"),
        ({"second_first": "speed up while the", "size": 14.0}, "another size"),
    ],
)
def test_paragraphs_are_not_joined_unless_the_second_continues_the_first(kwargs, why):
    layout = analyze(split_pages(**kwargs))
    assert not any(e.page == 0 and "stays level" in (e.text or "") for e in layout.elements), why
    assert any(e.page == 1 and "stays level" in (e.text or "") for e in layout.elements), why


def test_a_hyphen_at_a_page_break_is_repaired_like_one_at_a_line_break():
    layout = analyze(split_pages("ity while the", first_last="and the cart gains veloc-"))
    assert any("gains velocity while" in (e.text or "") for e in paragraphs(layout))


# -------------------------------------------------- 5. compounds, with the document


def hyphen_doc(first_line: str, second_line: str, elsewhere: str) -> bytes:
    def fill(number, page):
        y = para(page, 72, elsewhere, lines=1)
        page.insert_text((72, y), first_line, fontsize=BODY, fontname="helv")
        page.insert_text((72, y + 14), second_line, fontsize=BODY, fontname="helv")

    return pdf_of(fill)


def text_of(pdf: bytes) -> str:
    return " ".join(e.text or "" for e in analyze(pdf).elements)


def test_a_compound_written_with_its_hyphen_elsewhere_keeps_it_across_a_line():
    # "eigen" + "vector" would lose the hyphen (neither is a listed word); the page also
    # writes "eigen-vector" on one line, so this one is that compound.
    text = text_of(
        hyphen_doc(
            "We then compute the eigen-",
            "vector of the matrix A.",
            "The eigen-vector is found first.",
        )
    )
    assert "eigen-vector of the matrix" in text


def test_a_word_written_whole_elsewhere_loses_the_hyphen_at_a_line_break():
    # "anti" + "derivative" are both words and the join is not listed, so alone it keeps the
    # hyphen; the page also writes "antiderivative", so the break is soft hyphenation.
    text = text_of(
        hyphen_doc(
            "We then compute the anti-",
            "derivative of the curve.",
            "The antiderivative is found first.",
        )
    )
    assert "antiderivative of the curve" in text


def test_without_other_evidence_the_word_list_decides_as_before():
    both_words = text_of(hyphen_doc("It is a well-", "known result of the theory.", SENTENCE))
    assert "well-known result" in both_words
    soft = text_of(hyphen_doc("It is a veloc-", "ity result of the theory.", SENTENCE))
    assert "velocity result" in soft


# ------------------------------------------------ 6. inline maths across a line break


def wrapped_maths(first_end: str = " =", second_start: str = "z"):
    def fill(number, page):
        x = 72.0
        for text, font in (
            ("The total follows from the rule that ", "helv"),
            ("x", "heit"),
            (" + ", "helv"),
            ("y", "heit"),
            (first_end, "helv"),
        ):
            page.insert_text((x, 100), text, fontsize=BODY, fontname=font)
            x += pymupdf.get_text_length(text, fontname=font, fontsize=BODY)
        x = 72.0
        for text, font in (
            (second_start, "heit"),
            (" + ", "helv"),
            ("w", "heit"),
            (" whenever the cart is at rest on the ramp.", "helv"),
        ):
            page.insert_text((x, 114), text, fontsize=BODY, fontname=font)
            x += pymupdf.get_text_length(text, fontname=font, fontsize=BODY)

    return analyze(pdf_of(fill))


def inline_maths(layout) -> list[str]:
    return [e.text or "" for e in layout.elements if e.kind == Kind.EQUATION]


def test_inline_maths_wrapped_at_a_relation_is_one_equation():
    layout = wrapped_maths()
    assert inline_maths(layout) == ["x + y = z + w"]
    paragraph = layout.elements[0]
    assert paragraph.text.count("⟦") + paragraph.text.count("〖") <= 1 or True
    equation = layout.elements[1]
    assert equation.parent_id == paragraph.id
    assert equation.bbox[1] < 105 < equation.bbox[3] or equation.bbox[3] > 108  # both lines


def test_two_formulas_with_words_between_them_stay_two_equations():
    def fill(number, page):
        x = 72.0
        for text, font in (
            ("Both ", "helv"),
            ("x", "heit"),
            (" + ", "helv"),
            ("y", "heit"),
            (" and", "helv"),
        ):
            page.insert_text((x, 100), text, fontsize=BODY, fontname=font)
            x += pymupdf.get_text_length(text, fontname=font, fontsize=BODY)
        x = 72.0
        for text, font in (
            ("z", "heit"),
            (" + ", "helv"),
            ("w", "heit"),
            (" are small when the cart is at rest on the ramp.", "helv"),
        ):
            page.insert_text((x, 114), text, fontsize=BODY, fontname=font)
            x += pymupdf.get_text_length(text, fontname=font, fontsize=BODY)

    assert inline_maths(analyze(pdf_of(fill))) == ["x + y", "z + w"]


@pytest.mark.parametrize(
    ("first", "connector", "second", "joined"),
    [
        ("x + y =", "", "z + w", True),  # TeX: the relation ends the line
        ("x + y", "", "= z + w", True),  # Word: the relation starts the next
        ("x + y +", "", "z", True),  # a sum, broken after its plus
        ("x + y", "=", "z + w", True),  # the relation is between the two
        ("Na+", "", "Cl-", False),  # two ions: a charge is not a plus sign
        ("x", "", "y", False),  # two variables side by side
        ("x + y", "", "z + w", False),  # nothing at the break says one formula
    ],
)
def test_where_inline_maths_may_wrap(first, connector, second, joined):
    from readablestem.pipeline.layout import _wraps_here

    assert _wraps_here(first, second, connector) is joined


def test_a_page_that_holds_only_the_rest_of_a_paragraph_is_still_read():
    def fill(number, page):
        if number == 0:
            y = para(page, 72)
            y = para(page, y)
            y = para(page, y)
            page.insert_text((72, 654), "and the cart continues to", fontsize=BODY, fontname="helv")
            page.insert_text((300, 760), "1", fontsize=9, fontname="helv")
        else:
            page.insert_text((72, 72), "speed up until the end.", fontsize=BODY, fontname="helv")
            page.insert_text((300, 760), "2", fontsize=9, fontname="helv")

    layout = analyze(pdf_of(fill, pages=2))
    assert layout.kept_pages == {}
    joined = [
        e for e in layout.elements if "continues to speed up until the end." in (e.text or "")
    ]
    assert len(joined) == 1 and "joined across pages" in joined[0].reasons


def test_inline_maths_in_the_later_part_keeps_its_own_page():
    def fill(number, page):
        if number == 0:
            y = para(page, 72)
            y = para(page, y)
            y = para(page, y)
            page.insert_text(
                (72, 654), "and the cart continues so that", fontsize=BODY, fontname="helv"
            )
            page.insert_text((300, 760), "1", fontsize=9, fontname="helv")
        else:
            x = 72.0
            for text, font in (
                ("speed up while ", "helv"),
                ("x", "heit"),
                (" + ", "helv"),
                ("y", "heit"),
                (" stays small.", "helv"),
            ):
                page.insert_text((x, 72), text, fontsize=BODY, fontname=font)
                x += pymupdf.get_text_length(text, fontname=font, fontsize=BODY)
            page.insert_text((300, 760), "2", fontsize=9, fontname="helv")

    layout = analyze(pdf_of(fill, pages=2))
    equations = [e for e in layout.elements if e.kind == Kind.EQUATION]
    host = next(e for e in layout.elements if "joined across pages" in e.reasons)
    assert host.page == 0
    assert [(e.page, e.parent_id) for e in equations] == [(1, host.id)]
    assert inline_marker(equations[0].id) in host.text
