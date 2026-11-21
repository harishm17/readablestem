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


def test_the_columns_under_the_head_of_a_first_page_are_read_one_after_the_other():
    # A title, authors and affiliations centered over both columns (more lines across the
    # gutter than a column may have), an abstract across the page, then two columns.
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
    for x, name in ((54.0, "left"), (317.0, "right")):
        for i in range(24):
            line = f"{name} column line {i} " + "word " * 7
            page.insert_text((x, top + 12 * i), line, fontsize=10, fontname="helv")
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
