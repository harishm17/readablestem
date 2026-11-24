"""Raised and lowered text in running text (backlog T2-4, T2-5): the scripts of a unit
(cm3) come out as `Element.script_runs`, not flat and not as inline equations; formulas and
ions (H2O, Na+, SO4 2-) and nuclides (226Ra) are inline equations since 2026-10-21; those of
a variable (an italic x) stay an inline equation; what cannot be carried is flagged
`scripts_flattened`. Pages are built here glyph by glyph, so the sizes
and offsets are the ones the test says."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from readablestem.core.types import Element, Kind, Notice
from readablestem.pipeline import layout

REPO = Path(__file__).resolve().parents[1]
BODY = 11.0
SMALL = 7.0
UP, DOWN = 3.5, -1.8  # raise and lowering of a script, pt


class Line:
    """One line of text put on a page piece by piece: (text, kind) with kind None for
    body text, "sup" or "sub" for a script, "italic" for an italic body-size piece, "caps"
    for small capitals (80 % size, on the baseline)."""

    def __init__(self, page: pymupdf.Page, y: float, x: float = 72.0) -> None:
        self.page, self.y, self.x = page, y, x

    def add(self, text: str, kind: str | None = None) -> Line:
        size = SMALL if kind in ("sup", "sub") else 0.8 * BODY if kind == "caps" else BODY
        rise = {"sup": UP, "sub": DOWN}.get(kind or "", 0.0)
        font = "heit" if kind == "italic" else "helv"
        self.page.insert_text((self.x, self.y - rise), text, fontsize=size, fontname=font)
        self.x += pymupdf.get_text_length(text, fontname=font, fontsize=size)
        return self

    def stack(self, down: str, up: str, *, up_first: bool = False) -> Line:
        """A lowered and a raised piece one above the other at the same place, the raised
        one written first in the PDF when `up_first` (TeX writes a nuclide's so)."""
        x = self.x
        pieces = [(up, "sup"), (down, "sub")] if up_first else [(down, "sub"), (up, "sup")]
        right = x
        for text, kind in pieces:
            self.x = x
            self.add(text, kind)
            right = max(right, self.x)
        self.x = right
        return self


def build(lines: list[list[tuple[str, str | None]]]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 100.0
    for pieces in lines:
        line = Line(page, y)
        for text, kind in pieces:
            if kind in ("stack", "stack-up-first"):
                down, up = text.split("|")
                line.stack(down, up, up_first=kind == "stack-up-first")
            else:
                line.add(text, kind)
        y += 16.0
    data = doc.tobytes()
    doc.close()
    return data


def paragraphs(pdf: bytes) -> list[Element]:
    return [e for e in layout.analyze(pdf).elements if e.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM)]


def test_a_formula_and_an_ion_are_inline_equations_not_raised_text():
    """Since 2026-10-21 (docs/chemistry.md) formulas and ions with raised or lowered text are
    inline equations: the equation step reads them, the speech says "H 2 O", "N a plus"."""
    pdf = build(
        [
            [("Water is H", None), ("2", "sub"), ("O and table salt is Na", None), ("+", "sup")],
            [("Cl", None), ("-", "sup"), (" is the chloride ion and Ca", None), ("2+", "sup")],
        ]
    )
    elements = layout.analyze(pdf).elements
    (paragraph,) = [e for e in elements if e.kind is Kind.PARAGRAPH]
    assert paragraph.script_runs == []
    assert [e.text for e in elements if e.kind is Kind.EQUATION] == ["H2O", "Na+", "Cl-", "Ca2+"]
    assert all(e.display == "inline" for e in elements if e.kind is Kind.EQUATION)


def test_a_script_with_nothing_before_it_is_flagged():
    # A raised mark at the start of a line has no base to belong to: written flat, and said.
    pdf = build([[("2", "sup"), ("abc def ghi jkl mno pqr", None)]])
    result = layout.analyze(pdf)
    notices: list[Notice] = [n for n in result.notices if n.code == "scripts_flattened"]
    assert len(notices) == 1 and notices[0].review == "optional"
    assert notices[0].element_id == result.elements[0].id


def table_page(*, lowered: bool) -> bytes:
    """A ruled table whose last cell reads "CO2" (the 2 lowered when `lowered`)."""
    doc = pymupdf.open()
    page = doc.new_page()
    cols, rows = [100, 220, 340], [100, 130, 160, 190]
    for x in cols:
        page.draw_line((x, rows[0]), (x, rows[-1]), width=0.8)
    for y in rows:
        page.draw_line((cols[0], y), (cols[-1], y), width=0.8)
    cells = [["Gas", "Mass"], ["Methane", "16"], [None, "44"]]
    for r, row in enumerate(cells):
        for c, text in enumerate(row):
            if text is not None:
                page.insert_text((cols[c] + 6, rows[r] + 19), text, fontsize=11, fontname="helv")
    line = Line(page, rows[2] + 19, x=cols[0] + 6)
    line.add("Carbon dioxide CO").add("2", "sub" if lowered else None)
    data = doc.tobytes()
    doc.close()
    return data


def _row(parts: list[tuple[str, float, float]], *, font: str = "CMR10") -> layout._Row:
    """A row built glyph by glyph: (text, size, baseline), each glyph 4 pt wide, side by side."""
    chars, x = [], 100.0
    for text, size, baseline in parts:
        for c in text:
            chars.append(
                layout._Char(c, x, baseline - size, x + 4, baseline, baseline, size, font, 0)
            )
            x += 4
    return layout._Row(chars)


def test_small_capitals_are_text_and_real_capital_scripts_are_not():
    """A name set in small capitals (a full-size capital, then smaller ones on the baseline) is
    one word, not a base with scripts: no equation, and the letters are what the source had.
    A capital that is raised or lowered, on the same page, is still a script."""
    pdf = build(
        [
            [
                ("The L", None),
                ("OTKA", "caps"),
                ("-V", None),
                ("OLTERRA", "caps"),
                (" model and ", None),
                ("L", None),
                ("ONG", "caps"),
                ("M", None),
                ("EM", "caps"),
                ("E", None),
                ("VAL", "caps"),
                (" ", None),
                ("AND", "caps"),
                (" more", None),
            ],
            [("Take X", None), ("T", "sup"), (" and Y", None), ("N", "sub"), (" here", None)],
        ]
    )
    elements = layout.analyze(pdf).elements
    assert not [e for e in elements if e.kind is Kind.EQUATION]
    names, scripts = [e for e in elements if e.kind is Kind.PARAGRAPH]
    # "AND" in small capitals only: the line's other small capitals say what it is.
    assert names.text == "The Lotka-Volterra model and LongMemEval and more"
    assert [(scripts.text[r.start : r.end], r.kind) for r in scripts.script_runs] == [
        ("T", "sup"),
        ("N", "sub"),
    ]


def test_a_lowered_script_under_a_name_is_read_before_the_whole_name():
    # 2503.00504 p8: the filter of a method is a letter with a typewriter name raised over a
    # lowered letter that starts under the name's first glyph and ends inside the second.
    # Sorted by x the letter landed inside the name ("KRwR"); it is read before the name.
    pdf = build([[("The filter ", None), ("x", "italic"), ("w|KRR", "stack-up-first")]])
    (equation,) = [e for e in layout.analyze(pdf).elements if e.kind is Kind.EQUATION]
    assert equation.text == "xwKRR"


def test_small_words_set_over_a_far_larger_word_are_no_script_of_it():
    # A slide (MIT 6.0001 lecture 1) sets "PROBLEM SOLVING" in 20 points over an 80-point
    # "PRACTICE": the small line is its own text, not read inside the large word letter by letter.
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=540)
    page.insert_text((108, 240), "PRACTICE", fontsize=80, fontname="hebo")
    page.insert_text((304, 152), "PROBLEM", fontsize=20, fontname="helv")
    page.insert_text((309, 176), "SOLVING", fontsize=20, fontname="helv")
    pdf = doc.tobytes()
    doc.close()
    words = [w for e in layout.analyze(pdf).elements for w in (e.text or "").split()]
    assert sorted(words) == ["PRACTICE", "PROBLEM", "SOLVING"]
