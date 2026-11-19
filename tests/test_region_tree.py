"""The region tree (`REGION_TREE`, docs/layout.md "The region tree"): pages the one-gutter reading
order gets wrong and the tree gets right.

Each page is generated here: lines of text in a few blocks whose first word is a tag (a letter and
two digits). The reading order is the tags' letters, with a run of one letter counted once, so
"ABC" means all of block A, then all of block B, then all of block C.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pymupdf

from readablestem import config
from readablestem.pipeline import layout
from readablestem.pipeline.layout import regions
from test_tables_detect import grid_table

TAG = re.compile(r"[A-Z]\d\d")
OPTION = re.compile(r"[a-f]\)")
WORDS = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho"


def _lines(page: pymupdf.Page, tag: str, x: float, top: float, count: int, width: int) -> None:
    for i in range(count):
        page.insert_text((x, top + 13 * i), f"{tag}{i:02d} {WORDS}"[:width], fontsize=9)


def _read(doc: pymupdf.Document, pattern: re.Pattern[str] = TAG, *, on: bool) -> str:
    """The letters of the tags in reading order, a run of one letter counted once."""
    result = layout.analyze(doc.tobytes(), region_tree=on)
    words = [
        w
        for e in result.elements
        for w in [e.list_marker or "", *(e.text or "").split()]
        if pattern.fullmatch(w)
    ]
    return "".join(k for k, _ in itertools.groupby(w[0] for w in words))


def _page() -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    return doc, doc.new_page(width=595, height=842)


def _right_only_on(doc: pymupdf.Document, wanted: str, pattern: re.Pattern[str] = TAG) -> None:
    assert _read(doc, pattern, on=False) != wanted
    assert _read(doc, pattern, on=True) == wanted


def test_three_columns_are_read_one_after_another():
    doc, page = _page()
    for tag, x in (("A", 40), ("B", 215), ("C", 390)):
        _lines(page, tag, x, 100, 30, 34)
    _right_only_on(doc, "ABC")


def test_a_notes_column_is_read_after_the_body():
    doc, page = _page()
    _lines(page, "A", 40, 100, 30, 58)
    _lines(page, "N", 440, 100, 12, 20)
    _right_only_on(doc, "AN")


def test_text_on_both_sides_of_a_picture_is_two_columns():
    doc, page = _page()
    _lines(page, "A", 40, 100, 30, 32)
    _lines(page, "B", 380, 100, 30, 32)
    pixels = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 200), False)
    pixels.set_rect(pixels.irect, (120, 160, 200))
    page.insert_image(pymupdf.Rect(250, 120, 350, 400), pixmap=pixels)
    _right_only_on(doc, "AB")


def test_a_heading_across_two_columns_starts_a_second_two_column_section():
    doc, page = _page()
    _lines(page, "A", 40, 100, 14, 44)
    _lines(page, "B", 320, 100, 14, 44)
    page.insert_text((40, 320), "H00 A heading across both columns", fontsize=14)
    _lines(page, "C", 40, 350, 14, 44)
    _lines(page, "D", 320, 350, 14, 44)
    _right_only_on(doc, "ABHCD")


def test_a_boxed_region_is_read_whole_after_the_text_that_wraps_it():
    doc, page = _page()
    _lines(page, "P", 40, 100, 7, 140)
    page.draw_rect(pymupdf.Rect(330, 190, 556, 280), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
    _lines(page, "B", 338, 205, 5, 40)
    _lines(page, "P", 40, 191, 7, 66)
    _lines(page, "Q", 40, 296, 5, 140)
    _right_only_on(doc, "PBQ")


def test_an_option_grid_is_read_in_label_order_with_the_tree_on_or_off():
    doc, page = _page()
    page.insert_text((40, 100), "Which of the following statements is true?", fontsize=10)
    for k, label in enumerate("abcdef"):
        x, y = (40, 300)[k % 2], 116 + 62 * (k // 2)
        page.insert_text((x, y), f"{label}) the first line of option {k} is", fontsize=10)
        for n in range(4):
            page.insert_text(
                (x + 12, y + 12 * (n + 1)), "a following line of the option", fontsize=10
            )
    # The default reading puts the options in label order too (`layout._option_grids`).
    assert _read(doc, OPTION, on=False) == _read(doc, OPTION, on=True) == "abcdef"


def test_a_table_at_the_top_of_a_column_is_read_before_the_text_beside_it():
    doc, page = _page()
    grid_table(
        page,
        [["T00", "x", "y"], ["T01", "1", "2"], ["T02", "3", "4"]],
        x0=40,
        y0=100,
        widths=[60.0, 60.0, 60.0],
    )
    _lines(page, "A", 40, 200, 30, 44)
    _lines(page, "B", 320, 130, 40, 44)
    assert _read(doc, on=True) == "TAB"


SELFMADE = Path(__file__).parent / "workflows" / "corpus" / "selfmade"


def _tables(name: str, page: int, *, on: bool = True) -> tuple[list, layout.Layout]:
    result = layout.analyze((SELFMADE / f"{name}.pdf").read_bytes(), region_tree=on)
    return [e for e in result.elements if e.page == page and e.kind.value == "table"], result


def test_a_table_proposal_across_a_gutter_with_prose_on_both_sides_is_refused():
    # The detector boxes lines of the body and of the sidebar of a syllabus together and takes
    # their sentence ends for cells; with the tree on they stay in their columns and the
    # reason is recorded. The grading table, in one column, is kept.
    tables, result = _tables("s02_syllabus_sidebar", 0)
    assert [t.bbox[2] < 400 for t in tables] == [True]
    assert [why for _, why in result.table_refusals[0]] == [regions.REFUSED_PROSE]
    texts = {e.text for e in result.elements if e.page == 0 and e.kind.value == "heading"}
    assert {"Learning Outcomes", "Key Dates"} <= texts
    assert len(_tables("s02_syllabus_sidebar", 0, on=False)[0]) == 2  # off: the old reading


def test_a_ruled_table_that_spans_the_columns_on_purpose_is_kept():
    doc, page = _page()
    _lines(page, "A", 40, 80, 12, 44)
    _lines(page, "B", 320, 80, 12, 44)
    rows = [["term and its meaning", "what the term means here", "an example of the term"]] + [
        [f"term {i} is described", f"meaning {i} in plain words", f"example {i} given here"]
        for i in range(5)
    ]
    grid_table(page, rows, x0=40, y0=260, widths=[170.0, 170.0, 170.0], height=20)
    _lines(page, "C", 40, 420, 12, 44)
    _lines(page, "D", 320, 420, 12, 44)
    result = layout.analyze(doc.tobytes(), region_tree=True)
    assert [e.kind.value for e in result.elements].count("table") == 1
    assert not result.table_refusals


def test_a_matching_table_is_kept():
    # Terms to number and definitions to letter, side by side, and nothing else of the page in
    # the other column: the table's own columns are no gutter of the page.
    tables, result = _tables("s04_worksheet", 1)
    assert len(tables) == 1
    assert not result.table_refusals

def _picture(page: pymupdf.Page, rect: pymupdf.Rect) -> None:
    pixels = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 200), False)
    pixels.set_rect(pixels.irect, (120, 160, 200))
    page.insert_image(rect, pixmap=pixels)


def test_a_picture_that_starts_a_little_below_the_first_line_beside_it_is_in_its_row():
    doc, page = _page()
    _picture(page, pymupdf.Rect(40, 96, 280, 300))
    _lines(page, "A", 40, 320, 30, 44)
    _lines(page, "B", 320, 100, 40, 44)
    assert _read(doc, on=True) == "AB"


def test_a_rule_under_one_column_is_no_blank_of_the_line_in_the_other():
    doc, page = _page()
    _lines(page, "A", 40, 100, 40, 62)
    _lines(page, "B", 320, 100, 8, 48)
    _lines(page, "B", 320, 240, 20, 48)
    page.draw_line((308, 219), (550, 219), width=0.6)  # under the 10th line of A, in a gap of B
    assert _read(doc, on=True) == "AB"


def test_line_numbers_in_the_margins_do_not_stop_the_columns():
    doc, page = _page()
    _lines(page, "A", 72, 100, 30, 34)
    _lines(page, "B", 330, 100, 30, 34)
    for i in range(0, 30, 5):
        page.insert_text((56, 100 + 13 * i), str(i + 5), fontsize=7)
        page.insert_text((560, 100 + 13 * i), str(i + 35), fontsize=7)
    assert _read(doc, on=True) == "AB"


def test_gaps_in_two_lines_of_a_paragraph_are_not_two_columns():
    doc, page = _page()
    _lines(page, "P", 40, 100, 3, 110)
    page.insert_text((40, 150), "A01 alpha beta gamma delta eps", fontsize=9)
    page.insert_text((210, 150), "B01 " + WORDS[:60], fontsize=9)
    page.insert_text((40, 163), "A02 alpha beta", fontsize=9)
    page.insert_text((110, 163), "A03 gamma delta", fontsize=9)
    page.insert_text((210, 163), "B02 " + WORDS[:60], fontsize=9)
    page.insert_text((210, 176), "B03 " + WORDS[:60], fontsize=9)
    assert _read(doc, on=True) == "PABAB"


def test_the_setting_is_off_by_default():
    assert config.Settings.model_fields["region_tree"].default == "off"
