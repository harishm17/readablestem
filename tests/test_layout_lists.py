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


def test_a_spaced_minus_before_a_number_is_not_a_dash_bullet():
    """Two lines starting "- 40 m/s" and "- 20 m/s" matched the sibling rule: the sign
    went into the marker and the values were read out as positive."""
    dash = "-"
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


@pytest.mark.parametrize("bullet_text_x", [112, 110])
def test_a_bullet_of_another_series_set_right_of_a_number_is_one_level_below_it(bullet_text_x):
    """Slides: the sub-bullets sit under "1)" with their text almost where its text starts
    (inside the nesting tolerance of a large font), but their markers are set further in.
    With their text exactly on its text edge (Word sets them so), "2)" still goes back to the
    level of "1)": it was read one level down, and each step after it one more."""

    def fill(page):
        x = bullet_text_x
        rows = [("1)", 72, 110, "Lecture review"), ("•", 90, x, "if you missed the lecture")]
        rows += [("•", 90, x, "if you need another take"), ("2)", 72, 110, "Problem solving")]
        rows += [("•", 90, x, "a couple of harder questions")]
        for i, (marker, mx, tx, text) in enumerate(rows):
            page.insert_text((mx, 100 + 40 * i), marker, fontsize=20)
            page.insert_text((tx, 100 + 40 * i), text, fontsize=20)

    got = [(m, lvl) for m, _, lvl, _ in items(analyze(one_page(fill)).elements)]
    assert got == [("1)", 1), ("·", 2), ("·", 2), ("2)", 1), ("·", 2)]


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


def test_a_series_that_starts_again_is_a_level_down_and_a_list_goes_on_after_a_paragraph():
    """An exam: "1." then "(a)" then "(i)", set with indents inside the nesting tolerance. Each
    series that starts again is a level down, "(b)" goes back to the letters, and "(ii)" after
    the answer words "negative" and "positive" still belongs to the list they interrupt."""

    def fill(page):
        x = {"1.": 72, "(a)": 74, "(i)": 78, "(ii)": 78, "(b)": 74}
        rows = [("1.", "Thermodynamics of a gas"), ("(a)", "A reaction is run twice")]
        rows += [("(i)", "Circle the sign of the change"), (None, "negative"), (None, "positive")]
        rows += [("(ii)", "Briefly explain your answer"), ("(b)", "A second reaction is run")]
        rows += [("(i)", "Circle the sign of the change")]
        for i, (marker, text) in enumerate(rows):
            y = 100 + 24 * i
            if marker is None:
                page.insert_text((100, y), text, fontsize=BODY)
                continue
            page.insert_text((x[marker], y), marker, fontsize=BODY)
            page.insert_text((x[marker] + 20, y), text, fontsize=BODY)

    got = [(m, lvl) for m, _, lvl, _ in items(analyze(one_page(fill)).elements)]
    assert got == [("1.", 1), ("(a)", 2), ("(i)", 3), ("(ii)", 3), ("(b)", 2), ("(i)", 3)]


def test_the_letter_i_after_an_h_with_bullets_under_it_goes_on_with_the_letters():
    """ "(i)" is the letter after "(h)" and the roman one. The dashes under "(h)" are the
    innermost open level when it comes: read against them it was roman one, a list under
    "(h)" that "(j)" then left."""

    def fill(page):
        y = 100
        for letter in "ghij":
            y = write(page, 72, y, [f"({letter}) Item text of the letter {letter} goes here"])
            if letter == "h":
                y = write(page, 100, y, ["- first point under h", "- second point under h"])

    got = [(m, lvl) for m, _, lvl, _ in items(analyze(one_page(fill)).elements)]
    assert got == [("(g)", 1), ("(h)", 1), ("-", 2), ("-", 2), ("(i)", 1), ("(j)", 1)]


def test_lines_of_a_reference_list_that_start_with_initials_are_not_a_lettered_list():
    """A wrapped reference line starts "D. Horgan, J. Quan": a capital letter and a full stop. A
    "C." and a "D." a few lines apart are not a run of items; "A." "B." "C." in a row are."""

    def fill(page):
        lines = [
            "C. Zhang, S. Agarwal and K. Slama, Training language models",
            "T. Lampe and M. Riedmiller, Learning to act",
            "D. Horgan and J. Quan, Deep q-learning from demonstrations",
            "S. Savarese and L. Fei-Fei, Surreal",
        ]
        write(page, 72, 100, lines)

    assert not items(analyze(one_page(fill)).elements)

    def alphabetical(page):  # sorted by surname, the first initials run on by chance
        lines = [
            "M. Zhang, S. Agarwal and K. Slama, Training language models",
            "N. Lampe and M. Riedmiller, Learning to act",
            "O. Horgan and J. Quan, Deep q-learning from demonstrations",
        ]
        write(page, 72, 100, lines)

    assert not items(analyze(one_page(alphabetical)).elements)


def test_a_lettered_title_set_in_italics_is_a_heading_and_text_under_it_is_not_its_item():
    # The second title opens like a surname and a comma, and a line that starts with an
    # initial stands between it and "A.": a line of authors is not set whole in italics.
    def fill(page):
        y = write(page, 72, 100, ["A. Reinforcement Learning"], fontname="heit")
        y = write(
            page,
            72,
            y,
            [
                "Let us consider a decision process described by states and actions.",
                "T. Lampe and M. Riedmiller describe the reward that the robot collects.",
            ],
        )
        y = write(page, 72, y + 10, ["B. Offline, Online and Optimistic Learning"], fontname="heit")
        y = write(page, 72, y, ["In this paper we consider tasks with one robot arm."])
        wrapped = ["C. Offline, Online and Optimistic Learning with", "a Title on Two Rows"]
        y = write(page, 72, y + 10, wrapped, fontname="heit")  # a title that wraps is one heading
        write(page, 72, y, ["The third title goes over two rows and its text is under it."])

    got = [(e.kind, e.text) for e in analyze(one_page(fill)).elements]
    assert [k for k, _ in got].count(Kind.HEADING) == 3
    assert not items(analyze(one_page(fill)).elements)

    def options(page):  # the options of a question, set in italics, are still its options
        y = write(page, 72, 100, ["1. Which law relates the force on a body to its motion?"])
        rows = ["A. Newton's first law", "B. Newton's second law", "C. Hooke's law of springs"]
        y = write(page, 90, y, rows, fontname="heit")
        write(page, 72, y + 10, ["Explain your choice in one or two sentences below."])

    got = analyze(one_page(options)).elements
    assert [m for m, *_ in items(got)] == ["1.", "A.", "B.", "C."]
    assert Kind.HEADING not in kinds(got)


def test_a_bullet_item_set_in_bold_is_a_list_item_not_a_heading():
    def fill(page):
        write(page, 72, 100, ["Questions for the datasheet", ""], fontname="hebo")
        for i, text in enumerate(
            [
                "Who funded the creation of the dataset?",
                "What data does each instance hold?",
                "Is any information missing from it?",
            ]
        ):
            page.insert_text((72, 140 + 24 * i), "•", fontsize=BODY, fontname="hebo")
            page.insert_text((86, 140 + 24 * i), text, fontsize=BODY, fontname="hebo")

    got = analyze(one_page(fill)).elements
    assert len(items(got)) == 3
    assert [e.text for e in got if e.kind == Kind.HEADING] == ["Questions for the datasheet"]


def test_a_bold_item_in_a_run_of_its_series_is_an_item_and_a_bold_title_over_text_is_a_heading():
    def key(page):  # the right answer of a key is set in bold among upright options
        y = write(page, 72, 100, ["1. Which law relates the force on a body to its motion?"])
        y = write(page, 90, y, ["A. Newton's first law"])
        y = write(page, 90, y, ["B. Newton's second law"], fontname="hebo")
        write(page, 90, y, ["C. Hooke's law of springs"])

    def agenda(page):  # every line of the list is bold
        y = write(page, 72, 100, ["The course has three parts, in this order:"])
        write(
            page,
            72,
            y,
            ["A. Course Goals", "B. Grading Policy", "C. Office Hours"],
            fontname="hebo",
        )

    def outline(page):  # "I." is the letter after "H." and not a roman section number
        y = write(page, 72, 100, ["The talk:"])
        write(page, 90, y, [f"{c}. Part number {c}" for c in "GHIJ"])

    for fill, markers in ((key, ["1.", "A.", "B.", "C."]), (agenda, ["A.", "B.", "C."])):
        got = analyze(one_page(fill)).elements
        assert [m for m, *_ in items(got)] == markers
        assert Kind.HEADING not in kinds(got)
    got = analyze(one_page(outline)).elements
    assert [m for m, *_ in items(got)] == ["G.", "H.", "I.", "J."]
    assert Kind.HEADING not in kinds(got)

    def titles(page):  # the same bold lines, each with text under it, are section titles
        text = [
            "The text of the section runs on for a full line of the page, and then",
            "a second one.",
        ]
        y = write(page, 72, 100, ["A. Methods"], fontname="hebo")
        y = write(page, 72, y, text)
        y = write(page, 72, y + 10, ["B. Results"], fontname="hebo")
        write(page, 72, y, text)

    got = analyze(one_page(titles)).elements
    assert [e.text for e in got if e.kind == Kind.HEADING] == ["A. Methods", "B. Results"]

    def picture(height):  # a title with only a picture under it, then the next title
        def fill(page):
            y = write(page, 72, 100, ["1. Setup"], fontname="hebo")
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, int(height)))
            pix.set_rect(pix.irect, (90, 120, 200))
            page.insert_image(pymupdf.Rect(72, y, 72 + 200, y + height), pixmap=pix)
            y = write(page, 72, y + height + 16, ["2. Procedure"], fontname="hebo")
            write(page, 72, y, ["The text of the section runs on for a full line of the page."])

        return fill

    for height in (6, 20, 120):
        got = analyze(one_page(picture(height))).elements
        assert [e.text for e in got if e.kind == Kind.HEADING] == ["1. Setup", "2. Procedure"]

    def spaced(page):  # the questions of a worksheet, set far apart to leave room to answer
        write(page, 72, 100, ["Answer in the space under each question."])
        for i, text in enumerate(["Define momentum", "Define impulse", "State the third law"]):
            write(page, 72, 140 + 90 * i, [f"{i + 1}. {text}"], fontname="hebo")

    got = analyze(one_page(spaced)).elements
    assert [m for m, *_ in items(got)] == ["1.", "2.", "3."]
    assert Kind.HEADING not in kinds(got)

    def topics(page):  # "X and Y" lines are an outline's items, not a list of authors
        y = write(page, 72, 100, ["The outline:"])
        write(page, 90, y, ["B. Predation and herbivory", "C. Mutualism and commensalism"])

    assert [m for m, *_ in items(analyze(one_page(topics)).elements)] == ["B.", "C."]


def test_a_series_that_starts_again_after_a_figure_or_a_paragraph_is_under_the_item_above():
    def fill(page):
        y = write(page, 72, 100, ["1. Consider a cart rolling down a ramp of known slope"])
        y = write(
            page, 72, y + 20, ["Figure 1 shows the ramp used for both parts of this question."]
        )
        for marker, text in (
            ("(a)", "Find the net force on the cart"),
            ("(b)", "Find its acceleration"),
        ):
            page.insert_text((74, y + 20), marker, fontsize=BODY)
            page.insert_text((94, y + 20), text, fontsize=BODY)
            y += 25
        write(page, 72, y + 20, ["2. Now the cart is pushed up the same ramp"])

    got = [(m, lvl) for m, _, lvl, _ in items(analyze(one_page(fill)).elements)]
    assert got == [("1.", 1), ("(a)", 2), ("(b)", 2), ("2.", 1)]

    def next_section(page):  # the letters of the next section are a list of their own
        y = write(page, 72, 100, ["1. Solve linear equations", "2. Draw the graph of a line"])
        y = write(page, 72, y + 20, ["Grading"], fontname="hebo", fontsize=16)
        y = write(page, 72, y, ["The final mark is made of two parts, weighted as follows."])
        for marker, text in (("(a)", "Homework is worth a third"), ("(b)", "The examination")):
            page.insert_text((74, y + 20), marker, fontsize=BODY)
            page.insert_text((94, y + 20), text, fontsize=BODY)
            y += 25

    got = [(m, lvl) for m, _, lvl, _ in items(analyze(one_page(next_section)).elements)]
    assert got == [("1.", 1), ("2.", 1), ("(a)", 1), ("(b)", 1)]
