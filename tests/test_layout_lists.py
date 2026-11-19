"""List detection in layout.analyze: bullets, ordered markers, nesting, false lists.

A row that starts a list item becomes a `Kind.LIST_ITEM` element with `list_marker` (as
printed), `list_ordered` and `list_level`, and `text` without the marker. Items found in
the 2026-10-08 smoke test: multiple-choice options "a. ... b. ... c. ... d. ..." had been
merged into one paragraph, and "b. - 40 m/s" had become a display equation.
"""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline.layout import analyze

BODY = 11.0
PITCH = 15.0


def write(page: pymupdf.Page, x: float, y: float, rows: list[str], **kw) -> float:
    size = kw.pop("fontsize", BODY)
    for row in rows:
        page.insert_text((x, y), row, fontsize=size, **kw)
        y += PITCH * size / BODY
    return y


def one_page(fill) -> bytes:
    doc = pymupdf.open()
    fill(doc.new_page(width=612, height=792))
    return doc.tobytes()


def html_page(html: str) -> bytes:
    return one_page(lambda page: page.insert_htmlbox(pymupdf.Rect(72, 72, 540, 500), html))


def items(elements: list[Element]) -> list[tuple[str | None, bool | None, int | None, str]]:
    return [
        (e.list_marker, e.list_ordered, e.list_level, e.text or "")
        for e in elements
        if e.kind == Kind.LIST_ITEM
    ]


def kinds(elements: list[Element]) -> list[Kind]:
    return [e.kind for e in elements]


# ---------------------------------------------------------------- bullets


# ---------------------------------------------------------------- ordered markers


def test_openstax_spaced_full_stop_is_part_of_the_marker():
    def fill(page):
        page.insert_text((60, 100), "3", fontsize=BODY)
        page.insert_text((68, 100), ".", fontsize=BODY)
        page.insert_text(
            (76, 100), "In 1999 the orbiter crashed because of a unit error.", fontsize=BODY
        )

    assert items(analyze(one_page(fill)).elements) == [
        ("3.", True, 1, "In 1999 the orbiter crashed because of a unit error.")
    ]


# ---------------------------------------------------------------- option grids


def question_with_options(options: dict[str, tuple[float, float]]) -> bytes:
    """A numbered question with its points at the right margin, then options at x, y."""

    def fill(page):
        page.insert_text((72, 100), "1. Which organelle makes ATP?", fontsize=BODY)
        page.insert_text((490, 100), "(2 points)", fontsize=BODY)
        for text, at in options.items():
            page.insert_text(at, text, fontsize=BODY)
        page.insert_text((72, 190), "2. Name the stages of mitosis.", fontsize=BODY)

    return one_page(fill)


ROW_MAJOR = {
    "(A) Nucleus": (90, 120),
    "(B) Mitochondrion": (300, 120),
    "(C) Ribosome": (90, 135),
    "(D) Golgi body": (300, 135),
}


def test_options_two_across_are_one_list_below_their_question_with_its_points():
    elements = analyze(question_with_options(ROW_MAJOR)).elements
    assert kinds(elements) == [Kind.LIST_ITEM] * 6  # no paragraph for the points
    assert items(elements) == [
        ("1.", True, 1, "Which organelle makes ATP? (2 points)"),
        ("(A)", True, 2, "Nucleus"),
        ("(B)", True, 2, "Mitochondrion"),
        ("(C)", True, 2, "Ribosome"),
        ("(D)", True, 2, "Golgi body"),
        ("2.", True, 1, "Name the stages of mitosis."),
    ]


def test_options_set_down_the_columns_are_read_in_label_order():
    down = {
        "(A) Nucleus": (90, 120),
        "(C) Ribosome": (300, 120),
        "(B) Mitochondrion": (90, 135),
        "(D) Golgi body": (300, 135),
    }
    elements = analyze(question_with_options(down)).elements
    assert [(marker, level, text) for marker, _, level, text in items(elements)[1:5]] == [
        ("(A)", 2, "Nucleus"),
        ("(B)", 2, "Mitochondrion"),
        ("(C)", 2, "Ribosome"),
        ("(D)", 2, "Golgi body"),
    ]


def test_a_points_label_at_the_right_margin_joins_a_question_with_no_options():
    def fill(page):
        page.insert_text((72, 100), "1. Name the stages of mitosis.", fontsize=BODY)
        page.insert_text((490, 100), "(4 points)", fontsize=BODY)

    assert items(analyze(one_page(fill)).elements) == [
        ("1.", True, 1, "Name the stages of mitosis. (4 points)")
    ]


def test_labels_that_are_not_a_full_run_are_left_in_page_order():
    gap = {
        "(A) Nucleus": (90, 120),
        "(C) Ribosome": (300, 120),
        "(B) Mitochondrion": (90, 135),
        "(E) Lysosome": (300, 135),
    }
    elements = analyze(question_with_options(gap)).elements
    assert [e.text for e in elements[1:5]] == [
        "Nucleus",
        "(C) Ribosome",
        "Mitochondrion",
        "(E) Lysosome",
    ]


def test_two_labelled_series_side_by_side_are_two_lists_below_their_question():
    """A matching exercise: blanks and numbers on the left, letters on the right. Read row by
    row it ran the question into the first blank and cut the lists apart."""
    terms = ["___ 1. Osmosis", "___ 2. Diffusion", "___ 3. Active transport"]
    meanings = ["A. Movement against a gradient", "B. Movement of water", "C. Movement downhill"]

    def fill(page):
        page.insert_text((72, 100), "7. Match each term with its definition.", fontsize=BODY)
        for k, (term, meaning) in enumerate(zip(terms, meanings, strict=True)):
            page.insert_text((90, 120 + PITCH * k), term, fontsize=BODY)
            page.insert_text((250, 120 + PITCH * k), meaning, fontsize=BODY)
        page.insert_text((72, 190), "8. Explain why cells are small.", fontsize=BODY)

    assert items(analyze(one_page(fill)).elements) == [
        ("7.", True, 1, "Match each term with its definition."),
        ("1.", True, 2, "___ Osmosis"),
        ("2.", True, 2, "___ Diffusion"),
        ("3.", True, 2, "___ Active transport"),
        ("A.", True, 2, "Movement against a gradient"),
        ("B.", True, 2, "Movement of water"),
        ("C.", True, 2, "Movement downhill"),
        ("8.", True, 1, "Explain why cells are small."),
    ]


def test_a_bullet_set_against_its_text_is_an_item_when_another_line_has_the_same_bullet():
    """Word's square touches its text ("\u25aaGrading", no gap, another font): a word that
    starts with the sign is no item, so the line needs a sibling with the same bullet."""
    glued = word_list_page([(1, "Grading", 11), (3, "approx. 20% quiz"), (1, "Recitations", 11)])
    assert [(lvl, text) for _, _, lvl, text in items(analyze(glued).elements)] == [
        (1, "Grading"),
        (2, "approx. 20% quiz"),
        (1, "Recitations"),
    ]
    assert not items(analyze(word_list_page([(1, "Grading", 11)])).elements)


def test_a_bold_phrase_that_goes_on_over_the_end_of_an_items_line_stays_in_the_item():
    """A style change cuts a line off its block, and the pieces are joined again; a list item
    was left out of the joining, so its last words were a paragraph of their own."""

    def fill(page):
        page.insert_text(
            (72, 100), "\u2022 The interpreter is a special program that executes", fontsize=BODY
        )
        page.insert_text((90, 115), "each instruction in order", fontsize=BODY, fontname="hebo")
        page.insert_text((72, 130), "\u2022 Variables name values", fontsize=BODY)

    assert [(lvl, text) for _, _, lvl, text in items(analyze(one_page(fill)).elements)] == [
        (1, "The interpreter is a special program that executes each instruction in order"),
        (1, "Variables name values"),
    ]


# ---------------------------------------------------------------- nesting and indents


# ---------------------------------------------------------------- not lists


# ---------------------------------------------------------------- marker readings


# ---------------------------------------------------------------- not lists (review round 2)


@pytest.mark.parametrize("dash", ["-", "\u2013", "\u2014"])
def test_a_spaced_minus_before_a_number_is_not_a_dash_bullet(dash):
    """Two lines starting "- 40 m/s" and "- 20 m/s" matched the sibling rule: the sign
    went into the marker and the values were read out as positive."""
    html = (
        "<p>The velocities of the two carts after the collision are:<br>"
        f"{dash} 40 m/s<br>{dash} 20 m/s<br>so both carts move to the left after they collide.</p>"
    )
    elements = analyze(html_page(html)).elements
    assert not any(e.kind == Kind.LIST_ITEM for e in elements)
    text = " ".join(e.text or "" for e in elements)
    assert f"{dash} 40 m/s" in text and f"{dash} 20 m/s" in text


def word_list_page(rows: list[tuple[int, str] | tuple[int, str, float]]) -> bytes:
    """A page as Word prints a bulleted list: the first-level bullet is a Wingdings square
    ("\\247" is U+F0A7) set as large as its text and cut off from it by a move of the pen, the
    second-level bullet the letter "o" in Courier New. `rows` are (level, text) or (level, text,
    move): level 3 is a Wingdings square one step in, `move` the pen's move to the text."""
    size = 22
    stream = f"BT /F1 {size} Tf 72 700 Td\n"
    y = 700
    for level, text, *move in rows:
        if level in (1, 3):
            x = 72 if level == 1 else 96
            stream += (
                f"1 0 0 1 {x} {y} Tm /F1 {size} Tf (\\247) Tj /F3 {size} Tf"
                f" {move[0] if move else 14} 0 Td ({text}) Tj\n"
            )
        else:
            stream += f"1 0 0 1 120 {y} Tm /F2 {size} Tf (o) Tj /F3 {size} Tf 24 0 Td ({text}) Tj\n"
        y -= 40
    stream += "ET"
    fonts = [
        "<< /Type /Font /Subtype /Type1 /BaseFont /Wingdings /Encoding /WinAnsiEncoding >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        " /Resources << /Font << /F1 5 0 R /F2 6 0 R /F3 7 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        *fonts,
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def test_word_bullets_with_no_space_after_them_and_o_bullets_are_nested_list_items():
    """Word's first-level bullet touches its text in the text layer (no space character) and
    its second-level bullet is a Courier "o": both came out as paragraphs."""
    page = word_list_page(
        [
            (1, "First topic of the lecture"),
            (2, "a detail that belongs under it"),
            (2, "another detail under it"),
            (1, "Second topic of the lecture"),
        ]
    )
    got = [(m, lvl, t) for m, _, lvl, t in items(analyze(page).elements)]
    assert got == [
        ("\u25aa", 1, "First topic of the lecture"),
        ("\u25e6", 2, "a detail that belongs under it"),
        ("\u25e6", 2, "another detail under it"),
        ("\u25aa", 1, "Second topic of the lecture"),
    ]


def test_a_lone_o_set_in_a_monospaced_font_before_monospaced_text_is_not_a_bullet():
    def fill(page):
        for i, y in enumerate((100, 140, 180)):
            page.insert_text((72, y), "o", fontsize=11, fontname="cour")
            page.insert_text((90, y), f"value{i} = compute(x)", fontsize=11, fontname="cour")

    assert not [e for e in analyze(one_page(fill)).elements if e.kind == Kind.LIST_ITEM]


def test_a_bullet_with_only_an_operator_is_an_item_and_a_near_indent_takes_the_nearer_level():
    """Slides: "\u25aa **" is an item whose text is an operator, and a first-level item whose
    text starts a little right of its siblings' stays first level although the nested items'
    text is within the nesting tolerance of it."""
    page = word_list_page(
        [
            (1, "operator precedence"),
            (3, "**"),
            (3, "*"),
            (3, "and the rest executed left to right"),
            (1, "computers only know what you tell them", 25.4),
        ]
    )
    got = [(lvl, t) for _, _, lvl, t in items(analyze(page).elements)]
    assert got == [
        (1, "operator precedence"),
        (2, "**"),
        (2, "*"),
        (2, "and the rest executed left to right"),
        (1, "computers only know what you tell them"),
    ]
