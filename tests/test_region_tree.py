"""The region tree (`REGION_TREE`, docs/layout.md "The region tree"): pages the one-gutter reading
order gets wrong and the tree gets right.

Each page is generated here: lines of text in a few blocks whose first word is a tag (a letter and
two digits). The reading order is the tags' letters, with a run of one letter counted once, so
"ABC" means all of block A, then all of block B, then all of block C.
"""

from __future__ import annotations

import itertools
import re

import pymupdf

from readablestem import config
from readablestem.pipeline import layout

TAG = re.compile(r"[A-Z]\d\d")
OPTION = re.compile(r"[a-f]\)")
WORDS = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho"


def _lines(page: pymupdf.Page, tag: str, x: float, top: float, count: int, width: int) -> None:
    for i in range(count):
        page.insert_text((x, top + 13 * i), f"{tag}{i:02d} {WORDS}"[:width], fontsize=9)


def _read(doc: pymupdf.Document, pattern: re.Pattern[str] = TAG, *, on: bool) -> str:
    """The letters of the tags in reading order, a run of one letter counted once."""
    result = layout.analyze(doc.tobytes(), region_tree=on)
    words = [w for e in result.elements for w in (e.text or "").split() if pattern.fullmatch(w)]
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


def test_an_option_grid_is_read_in_label_order():
    doc, page = _page()
    page.insert_text((40, 100), "Which of the following statements is true?", fontsize=10)
    for k, label in enumerate("abcdef"):
        x, y = (40, 300)[k % 2], 130 + 62 * (k // 2)
        page.insert_text((x, y), f"{label}) the first line of option {k} is", fontsize=10)
        for n in range(4):
            page.insert_text(
                (x + 12, y + 12 * (n + 1)), "a following line of the option", fontsize=10
            )
    _right_only_on(doc, "abcdef", OPTION)


def test_the_setting_is_off_by_default():
    assert config.Settings.model_fields["region_tree"].default == "off"
