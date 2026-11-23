"""Two-column reading order where one column starts lower than the other (docs/layout.md
"Reading order").

Found on the real-world development set (docs/eval-equation-round2.md): a figure and its
caption fill
the top of the left column of a TeX paper while the right column starts with text. A line of
the right
column that sits beside the gap under the figure's caption read as a heading across both
columns, and
the right column was read before the end of the left one ("the collec-" then the right column's
"tive character of ..."). Two things said so wrongly: the caption held by its figure was no part of
the left column, and a line as wide as its column counted as a heading.
"""

from __future__ import annotations

import pytest

from readablestem.pipeline import layout

GUTTER = (299.0, 317.0)
BODY = 10.0

# 2404.15495 page 3, in points: the figure (left, 52 to 442), the right column's paragraphs, the
# line "where Z1(k) is the contribution ..." (right, one full line), then the left column's text.
FIGURE = (54.0, 52.0, 305.0, 442.0)
CAPTION = (54.0, 455.0, 299.0, 537.0)
RIGHT_TOP = (317.0, 54.0, 562.0, 472.0)
FULL_LINE = (317.0, 539.0, 562.0, 550.0)
RIGHT_AFTER = (317.0, 549.0, 562.0, 724.0)
LEFT_TEXT = (54.0, 562.0, 299.0, 584.0)
LEFT_HEADING = (54.0, 608.0, 168.0, 617.0)
LEFT_BODY = (54.0, 634.0, 299.0, 724.0)


def order(boxes, context=()):
    return layout._reading_order(boxes, GUTTER, BODY, context)


def test_a_full_line_of_a_column_is_not_a_heading_across_both():
    boxes = [FIGURE, RIGHT_TOP, FULL_LINE, RIGHT_AFTER, LEFT_TEXT, LEFT_HEADING, LEFT_BODY]
    read = [boxes[i] for i in order(boxes, [CAPTION])]
    left = [b for b in read if b[0] < 300]
    right = [b for b in read if b[0] > 300]
    assert read == left + right  # the whole left column, then the whole right one
    assert left == [FIGURE, LEFT_TEXT, LEFT_HEADING, LEFT_BODY]


def test_a_short_heading_beside_a_gap_in_the_other_column_still_breaks_the_columns():
    # A heading narrower than its column, nothing beside it on the left, both columns resuming
    # together below it: the reading order of a two-column paper with a full-width section break.
    above_left = (54.0, 100.0, 299.0, 300.0)
    above_right = (317.0, 100.0, 562.0, 300.0)
    heading = (317.0, 330.0, 440.0, 342.0)
    below_left = (54.0, 360.0, 299.0, 600.0)
    below_right = (317.0, 358.0, 562.0, 600.0)
    boxes = [above_left, above_right, heading, below_left, below_right]
    read = [boxes[i] for i in order(boxes)]
    assert read == [above_left, above_right, heading, below_left, below_right]


@pytest.mark.parametrize(
    ("note", "paragraphs", "lines"),
    [
        (False, None, (24, 24)),
        (True, None, (24, 24)),
        (False, (5, 4), (24, 24)),
        # a column of nine lines with one skipped line (a figure's space, a heading's) beside
        # one of fourteen: two runs of four and a half lines, not the option pairs of an exam
        (False, (99, 5), (14, 9)),
    ],
)
def test_the_columns_under_the_head_of_a_first_page_are_read_one_after_the_other(
    note, paragraphs, lines
):
    # A title, authors and affiliations centered over both columns (more lines across the
    # gutter than a column may have), an abstract across the page, then two columns; with a
    # note across the page under them whose last line is short and sits at the left; or with
    # paragraphs set a line apart, of five lines on the left and four on the right, so each
    # column has holes of its own at its own places.
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    y = 60.0
    for text in ("A Title", "Of Two Lines", "First Author", "Second Author", "Third Author"):
        width = pymupdf.get_text_length(text, "helv", 12)
        page.insert_text((306 - width / 2, y), text, fontsize=12, fontname="helv")
        y += 16
    for text in ("Institute One", "Institute Two", "Institute Three"):
        width = pymupdf.get_text_length(text, "helv", 9)
        page.insert_text((306 - width / 2, y), text, fontsize=9, fontname="helv")
        y += 12
    for _ in range(4):
        page.insert_text((54, y + 8), "abstract " * 11, fontsize=10, fontname="helv")
        y += 12
    top = y + 20
    for side, (x, name) in enumerate(((54.0, "left"), (317.0, "right"))):
        for i in range(lines[side]):
            line = f"{name} column line {i} " + "word " * 7
            gaps = i // paragraphs[side] if paragraphs else 0
            page.insert_text((x, top + 12 * (i + gaps)), line, fontsize=10, fontname="helv")
    if note:
        page.insert_text((54, top + 12 * 25), "A note across the page. " * 9, fontsize=8)
        page.insert_text((54, top + 12 * 25 + 10), "The last line.", fontsize=8)
    text = " ".join(e.text or "" for e in layout.analyze(doc.tobytes()).elements)
    assert text.rindex("left column line") < text.index("right column line")


def test_a_ruled_step_table_on_a_page_of_one_column_is_not_read_column_by_column():
    # A textbook's worked example: a sentence across the page, then a grid of steps whose left
    # cells hold most of the lines and whose right cells hold a few (answers, pictures). The gap
    # between the two is a table's, not a gutter: the steps are read row by row.
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    y = 50.0
    head = ("Solution", "Read the problem", "Name what we look for")
    for text in (*head, "Let x be a rate", "Solve it", "Check the answer"):
        width = pymupdf.get_text_length(text, "helv", 11)
        page.insert_text((306 - width / 2, y), text, fontsize=11, fontname="helv")
        y += 14
    sentence = "A sentence across the page that leads into the table. " * 2
    page.insert_text((54, y + 6), sentence, fontsize=10)
    top = y + 40
    for i in range(24):
        page.insert_text((60, top + 12 * i), f"step {i} " + "word " * 7, fontsize=10)
    for i in range(4):
        page.insert_text((317, top + 12 * i), f"answer {i} " + "word " * 7, fontsize=10)
    page.draw_rect(pymupdf.Rect(54, top - 12, 560, top + 24 * 12))
    page.draw_line((300, top - 12), (300, top + 24 * 12))
    text = " ".join(e.text or "" for e in layout.analyze(doc.tobytes()).elements)
    assert text.index("answer 0") < text.index("step 10")


@pytest.mark.parametrize(
    ("question", "options", "questions", "space", "pitch", "section", "wraps", "labeled"),
    [
        # a question that runs the whole width of the page; one that stops short of the gutter,
        # set at the pitch of its options (single spacing) with no space between the questions
        ("A question that runs the whole width of the page, number {q}?", 4, 3, 12, 16, 0, (), 0),
        ("Which statement about the forces is correct?", 4, 4, 0, 13.2, 0, (), 0),
        # two short questions only: the gutter runs through them
        ("Which statement about the forces is correct?", 4, 2, 12, 16, 0, (), 0),
        # one question, six options: three lines a side under it
        ("A question that runs the whole width of the page, number {q}?", 6, 1, 12, 16, 0, (), 0),
        # a section line set a line apart above the first question
        ("Which statement about the forces is correct?", 4, 3, 12, 16, 1, (), 0),
        # an option runs on to a second line, single spacing: b of the first of three questions
        # (right), a of the second of four (left)
        ("Which statement about the forces is correct?", 4, 3, 0, 14, 0, ((0, 334),), 0),
        ("Which statement about the forces is correct?", 4, 4, 0, 14, 0, ((1, 98),), 0),
        # options with labels, "(a)": four questions of which two have a right option that runs
        # on (two holes beside three breaks); five questions, two with a wrapped option (taken
        # by the gutter of the whole page)
        ("Which statement about the forces is correct?", 4, 4, 0, 14, 0, ((0, 334), (2, 334)), 1),
        ("Which statement about the forces is correct?", 4, 5, 0, 14, 0, ((1, 334), (3, 334)), 1),
    ],
)
def test_options_standing_two_by_two_under_questions_are_not_read_as_columns(
    question, options, questions, space, pitch, section, wraps, labeled
):
    # An exam page of one column: centered head lines, an instruction across the page, then
    # questions with their options two by two under each. The option pairs look like two columns
    # under the head, but the questions between them cut them up: the options of a question are
    # read one after the other (a, b, c, d), not all of the left ones first (a, c, b, d).
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    y = 90.0
    for text in (
        "Physics Midterm Examination",
        "Department of Physics",
        "Fall Semester",
        "Ninety minutes",
    ):
        width = pymupdf.get_text_length(text, "helv", 11)
        page.insert_text((306 - width / 2, y), text, fontsize=11, fontname="helv")
        y += 16
    y += 12
    page.insert_text(
        (72, y), "Answer every question and mark the single best option on the sheet.", fontsize=11
    )
    y += 28
    if section:
        page.insert_text((72, y), "Section A: Multiple choice", fontsize=11)
        y += 36
    for q in range(questions):
        page.insert_text((72, y), f"{q + 1}. " + question.format(q=q), fontsize=11)
        y += 18 if space else pitch
        for row in range(options // 2):
            for x, k in ((84, 2 * row), (320, 2 * row + 1)):
                label = f"({'abcdef'[k]}) " if labeled else ""
                page.insert_text(
                    (x, y),
                    f"{label}option {'abcdef'[k]}{q} the net force on the block is zero",
                    fontsize=11,
                )
            for wrap_q, wrap_x in wraps:
                if wrap_q == q and row == 0:
                    y += pitch
                    page.insert_text(
                        (wrap_x, y), "and the block moves at a steady speed", fontsize=11
                    )
            y += pitch
        y += space
    text = " ".join(e.text or "" for e in layout.analyze(doc.tobytes()).elements)
    assert text.index("option b0") < text.index("option c0" if options > 2 else "option a1")
