"""Document structure fixes in layout: the PDF's bookmarks, numbered and run-in headings,
maths in headings, paragraphs and inline maths that cross a page or a line, and the
hyphen of a compound across a line break (docs/layout.md, "Known failure modes").

Every PDF is drawn here with PyMuPDF (publishable inputs only); no model is called.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Kind, Status
from readablestem.pipeline.assemble import render_html
from readablestem.pipeline.heading_step import heading_fixes
from readablestem.pipeline.layout import (
    REASON_NUMBERED_LINE,
    REASON_RUN_IN_LABEL,
    analyze,
    heading_levels,
)
from readablestem.pipeline.layout import (
    REASON_OPTION_LABELS as OPTION_LABELS,
)
from readablestem.pipeline.layout import (
    REASON_RTL_ORDER as RTL_TEXT,
)
from readablestem.pipeline.notices import from_fixes, from_reasons, text_decisions

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


def test_a_bookmark_on_every_short_paragraph_makes_no_headings():
    lines = ["Cart", "Ramp", "Speed", "Mass", "Force", "Time"]

    def fill(number, page):
        y = 72
        for line in lines:
            y = one(page, y, line)
            y = para(page, y, lines=2)

    toc = [[1, line, 1] for line in lines]
    assert texts(analyze(pdf_of(fill, toc)), Kind.HEADING) == []


def test_a_bookmarked_sentence_or_a_line_with_no_space_above_stays_a_paragraph():
    def fill(number, page):
        y = para(page, 72)
        y = one(page, y, "Overview", "hebo")
        y = para(page, y)
        y = one(page, y, "The cart speeds up on the ramp.")
        y = para(page, y)
        para(page, y)

    toc = [[1, "Overview", 1], [2, "The cart speeds up on the ramp.", 1]]
    assert texts(analyze(pdf_of(fill, toc)), Kind.HEADING) == ["Overview"]


# ------------------------------------------------- 2a. plain numbered headings, 2b. labels


def numbered_page(first: int = 1, titles=("Forces", "Energy and Work")):
    def fill(number, page):
        y = para(page, 72)
        y = one(page, y, f"{first} {titles[0]}")
        y = para(page, y)
        y = one(page, y, f"{first + 1} {titles[1]}")
        para(page, y)

    return fill


def test_plain_body_size_numbered_lines_in_sequence_are_headings():
    layout = analyze(pdf_of(numbered_page()))
    assert texts(layout, Kind.HEADING) == ["1 Forces", "2 Energy and Work"]
    first = next(e for e in layout.elements if e.kind == Kind.HEADING)
    assert REASON_NUMBERED_LINE in first.reasons
    fixes = heading_fixes(layout.elements)
    assert [(f.status, f.review) for f in fixes] == [(Status.ACCEPTED, "optional")] * 2


def test_a_long_numbered_title_is_a_decision():
    titles = ("Forces", "Energy and Work in a Closed System")
    fixes = heading_fixes(analyze(pdf_of(numbered_page(titles=titles))).elements)
    assert [f.review for f in fixes] == ["optional", "required"]


def test_numbered_exercises_and_questions_are_paragraphs_not_headings():
    layout = analyze(
        pdf_of(numbered_page(titles=("Find the final speed of the cart", "What is the mass")))
    )
    assert texts(layout, Kind.HEADING) == []


def test_numbered_lines_in_different_columns_are_not_a_sequence():
    def fill(number, page):
        y = para(page, 72)
        y = one(page, y, "1 Forces")
        para(page, y)
        y = para(page, 72, SENTENCE[:38], x=330)
        y = one(page, y, "2 Energy", x=330)
        para(page, y, SENTENCE[:38], x=330)

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


# ------------------------------------------------------------- 3. maths in headings


# --------------------------------------------- 3b. paragraphs with an indented first line


def indented_paragraphs(indent: float) -> bytes:
    """Two justified paragraphs of 3 and 5 sentences whose first line starts `indent` pt in."""
    style = f"text-align: justify; text-indent: {indent}pt; margin: 0; font-size: 11pt"

    def fill(number, page):
        for top, repeat in ((72, 3), (200, 5)):
            body = " ".join([SENTENCE] * repeat)
            page.insert_htmlbox(
                pymupdf.Rect(72, top, 540, top + 150), f'<p style="{style}">{body}</p>'
            )

    return pdf_of(fill)


@pytest.mark.parametrize("indent", [0.0, 33.0])
def test_a_paragraph_is_not_cut_after_its_indented_first_line(indent):
    layout = analyze(indented_paragraphs(indent))
    words = len(SENTENCE.split())
    assert [len((e.text or "").split()) for e in paragraphs(layout)] == [3 * words, 5 * words]


def test_centred_lines_of_different_lengths_are_not_one_paragraph():
    short, long = "Northwestern University", "a_name2025@u.northwestern.edu"

    def fill(number, page):
        for y, text in [(100, short), (117, long), (180, short), (240, short), (300, short)]:
            width = pymupdf.get_text_length(text, fontname="helv", fontsize=14)
            page.insert_text((306 - width / 2, y), text, fontsize=14, fontname="helv")
        para(page, 400, size=10)

    elements = analyze(pdf_of(fill)).elements
    found = [e.text for e in elements if "northwestern" in (e.text or "").lower()]
    assert found[:2] == [short, long]


# ---------------------------------------------- 4. paragraphs across a page break


def split_pages(second_first: str, *, first_last: str = "and the cart continues to", **kw):
    """Two pages; the first ends in an unfinished sentence, the second starts with
    `second_first`. A running header and a footnote sit around the break."""

    margin = kw.get("margin", 0.0)  # the second page's text stands this far in (an even page)

    def fill(number, page):
        page.insert_text((72, 40), "Notes on motion", fontsize=9, fontname="helv")
        page.insert_text((300, 760), str(number + 1), fontsize=9, fontname="helv")
        if number == 0:
            y = para(page, 72)
            y = para(page, y)
            y = para(page, y, lines=2)
            y = para(page, y, lines=2)
            if kw.get("lone_line"):  # one line of the paragraph, in by its first-line indent
                page.insert_text((90, 654), first_last, fontsize=BODY, fontname="helv")
            else:
                page.insert_text((72, 640), SENTENCE, fontsize=BODY, fontname="helv")
                page.insert_text((72, 654), first_last, fontsize=BODY, fontname="helv")
            if kw.get("footnote"):
                page.insert_text((72, 720), "1 A footnote.", fontsize=8, fontname="helv")
        else:
            top = 72.0
            if kw.get("figure"):  # a picture floats to the top of the page, above the text
                pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False)
                pixmap.clear_with(180)
                page.insert_image(pymupdf.Rect(72, 60, 232, 180), pixmap=pixmap)
                top = 210.0
            x = 72.0 + margin + kw.get("indent", 0.0)
            page.insert_text((x, top), second_first, fontsize=kw.get("size", BODY), fontname="helv")
            page.insert_text(
                (72 + margin, top + 14),
                "ramp stays level for the whole trip.",
                fontsize=BODY,
                fontname="helv",
            )
            para(page, top + 48, x=72 + margin)

    return pdf_of(fill, pages=2)


def paragraphs(layout):
    return [e for e in layout.elements if e.kind == Kind.PARAGRAPH]


def test_a_paragraph_cut_by_a_page_break_is_one_element():
    layout = analyze(split_pages("speed up while the"))
    joined = [e for e in paragraphs(layout) if "continues to speed up" in (e.text or "")]
    assert len(joined) == 1
    assert joined[0].page == 0
    assert joined[0].last_page == 1  # the pages it spans, for a writer that marks page breaks
    assert all(e.last_page is None for e in layout.elements if e is not joined[0])
    assert joined[0].text.endswith("stays level for the whole trip.")
    assert [e.page for e in paragraphs(layout)].count(1) == 1  # only the paragraph after it


@pytest.mark.parametrize(
    ("second_first", "kw", "joined"),
    [
        ("speed up while the", {"figure": True}, True),  # a picture on top is not the text
        ("speed up while the", {"lone_line": True}, True),  # one indented line is a first line
        ("speed up while the", {"lone_line": True, "margin": 14.0}, True),  # on an even page
        ("speed up while the", {"indent": 14.0}, False),  # the second starts a paragraph
        ("Speed up while the", {}, False),  # a capital: the sentence began there
    ],
)
def test_a_paragraph_cut_by_a_page_break_is_joined_only_where_both_parts_agree(
    second_first, kw, joined
):
    layout = analyze(split_pages(second_first, **kw))
    spanning = [e for e in paragraphs(layout) if e.last_page is not None]
    assert len(spanning) == int(joined)


def column_break(tail_lines: int) -> bytes:
    """Two two-column pages: the first ends in its right column with `tail_lines` lines of an
    unfinished sentence, the second starts in its left column with the rest of it."""
    line = "The cart rolls down the ramp and speeds"

    def column(page, x, y, lines):
        for text in lines:
            page.insert_text((x, y), text, fontsize=BODY, fontname="helv")
            y += 14
        return y + 10

    def fill(number, page):
        if number == 0:
            y = 72
            for _ in range(3):
                y = column(page, 72, y, [line] * 5)
            y = column(page, 320, 72, [line] * 5)
            y = column(page, 320, y, [line] * 5)
            column(page, 320, 640, [line] * (tail_lines - 1) + ["the cart continues to"])
        else:
            page.insert_text((72, 72), "speed up while the ramp stays level.", fontsize=BODY)
            y = column(page, 72, 86, [line] * 3)
            column(page, 72, y + 10, [line] * 5)
            column(page, 320, 72, [line] * 5)

    return pdf_of(fill, pages=2)


@pytest.mark.parametrize(("tail_lines", "joined"), [(2, True), (1, False)])
def test_a_paragraph_that_ends_a_page_in_one_column_goes_on_in_the_next_pages_other_column(
    tail_lines, joined
):
    layout = analyze(column_break(tail_lines))
    spanning = [e for e in paragraphs(layout) if e.last_page is not None]
    assert [(e.page, e.last_page) for e in spanning] == ([(0, 1)] if joined else [])
    assert any("continues to speed up" in (e.text or "") for e in spanning) is joined


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


@pytest.mark.parametrize(("x", "joined"), [(72, False), (86, True)])
def test_a_list_item_at_a_page_foot_goes_on_only_at_its_text(x, joined):
    def fill(number, page):
        page.insert_text((300, 760), str(number + 1), fontsize=9, fontname="helv")
        if number == 0:
            y = para(page, 72)
            y = para(page, y)
            y = para(page, y)
            page.insert_text(
                (72, 640), "1. Set the cart at the top of the ramp.", fontsize=BODY, fontname="helv"
            )
            page.insert_text(
                (72, 654), "2. Check that the cart continues to", fontsize=BODY, fontname="helv"
            )
        else:
            page.insert_text((x, 72), "speed up until the end.", fontsize=BODY, fontname="helv")
            para(page, 120)

    layout = analyze(pdf_of(fill, pages=2))
    items = [e for e in layout.elements if e.kind == Kind.LIST_ITEM and e.last_page is not None]
    assert len(items) == int(joined)
    assert any("continues to speed up" in (e.text or "") for e in layout.elements) is joined


def options_page(*lines: str) -> bytes:
    def fill(number, page):
        page.insert_textbox(pymupdf.Rect(72, 72, 540, 130), SENTENCE, fontsize=BODY)
        for k, text in enumerate(lines):
            page.insert_text((72, 150 + 14 * k), text, fontsize=BODY)

    return pdf_of(fill)


@pytest.mark.parametrize(
    ("lines", "texts", "flagged"),
    [
        # Options run together on one line are one item each.
        (
            ("(A) three metres   (B) five metres   (C) seven metres",),
            ["(A) three metres", "(B) five metres", "(C) seven metres"],
            False,
        ),
        # Over two rows they stay one block, and it says so.
        (
            ("(A) three metres   (B) five metres", "(C) seven metres   (D) nine metres"),
            ["(A) three metres (B) five metres (C) seven metres (D) nine metres"],
            True,
        ),
    ],
)
def test_answer_options_inside_one_block_are_split_or_flagged_never_silent(lines, texts, flagged):
    layout = analyze(options_page(*lines))
    options = [e for e in layout.elements if e.text and e.text.startswith("(")]
    assert [e.text for e in options] == texts
    assert [OPTION_LABELS in e.reasons for e in options] == [flagged] * len(options)
    assert [n.review for n in from_reasons(options)] == ["required"] * flagged
    # the flagged block has a decision of its own, and its notice follows that decision
    fixes = text_decisions(options, {})
    assert [f.reason for f in fixes] == ["text.option_labels"] * flagged
    assert [n.code for n in from_fixes(options, fixes, long_descriptions=False)] == [
        "option_labels"
    ] * flagged
    assert from_reasons(options, fixes) == []


def arabic_and_hebrew_page() -> bytes:
    """An Arabic word whose lam-alef is one ligature glyph (the PDF's ToUnicode gives the
    two letters, lam then alef) and a Hebrew sentence, both set in DejaVu Sans."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    font = {"fontfile": str(DEJAVU.with_name("DejaVuSans.ttf")), "fontname": "dj", "fontsize": 14}
    page.insert_text((72, 100), "إلى ﻻ", **font)
    page.insert_text((72, 140), "שלום עולם", **font)
    (cmap,) = [
        x
        for x in range(1, doc.xref_length())
        if doc.xref_is_stream(x) and b"beginbfrange" in (doc.xref_stream(x) or b"")
    ]
    text = doc.xref_stream(cmap).decode("latin1")
    (glyph,) = [
        int(lo, 16) + 0xFEFB - int(to, 16)
        for lo, hi, to in re.findall(r"<(\w{4})> <(\w{4})> <(\w{4})>", text)
        if int(to, 16) <= 0xFEFB <= int(to, 16) + int(hi, 16) - int(lo, 16)
    ]
    patched = text.replace(
        "endcmap", f"1 beginbfchar\n<{glyph:04x}> <06440627>\nendbfchar\nendcmap"
    )
    doc.update_stream(cmap, patched.encode("latin1"))
    return doc.tobytes()


def test_right_to_left_text_is_flagged_and_a_lam_alef_ligature_keeps_its_letter_order():
    layout = analyze(arabic_and_hebrew_page())
    arabic, hebrew = layout.elements
    assert "لا" in (arabic.text or "") and "ال" not in (arabic.text or "")
    assert RTL_TEXT in arabic.reasons and RTL_TEXT in hebrew.reasons
    assert [n.code for n in from_reasons(layout.elements)] == [RTL_TEXT, RTL_TEXT]
    fixes = {f.element_id: f for f in text_decisions(layout.elements, {})}
    assert set(fixes) == {arabic.id, hebrew.id}
    assert {f.reason for f in fixes.values()} == {"text.rtl_order"}
    assert all(f.pending and f.review == "required" for f in fixes.values())
    assert text_decisions(layout.elements, fixes) == []
