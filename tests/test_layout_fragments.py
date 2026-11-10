"""Inline-maths fragments that are not formulas (2026-10-08 smoke test and eval pages).

The OpenStax pages produced inline "equations" that were pieces of something else:
"/h)] = 40" (a plain-text formula cut by a line break), "-49 m" (a quantity with an
italic unit) and "b. - 40 m/s" (a multiple-choice option, now a list item; see
test_layout_lists.py). Real inline maths must still be found, including maths written
as plain text with italic variables.
"""

from __future__ import annotations

import pymupdf

from readablestem.core.types import Element, Kind
from readablestem.pipeline import layout

BODY = 11.0


def one_page(fill) -> bytes:
    doc = pymupdf.open()
    fill(doc.new_page(width=612, height=792))
    return doc.tobytes()


def line(page: pymupdf.Page, x: float, y: float, *pieces: tuple[str, str]) -> None:
    """Text pieces (text, base-14 font) set one after another on a baseline."""
    for text, font in pieces:
        page.insert_text((x, y), text, fontsize=BODY, fontname=font)
        x += pymupdf.get_text_length(text, fontname=font, fontsize=BODY)


def inline(elements: list[Element]) -> list[str]:
    return [e.text or "" for e in elements if e.kind == Kind.EQUATION]


def test_an_unpaired_bar_at_the_edge_of_a_run_is_still_dropped():
    chars = [
        layout._Char(c, 6 * i, 0, 6 * i + 5, 10, 9, 10.0, "Helv-Oblique" if c == "x" else "Helv", 0)
        for i, c in enumerate("|x + 1")
    ]
    row = layout._Row(chars)
    start, end = layout._trim_run(row.chars, layout._CharInfo.of(row), 0, len(chars))
    assert "".join(c.c for c in chars[start:end]) == "x + 1"
