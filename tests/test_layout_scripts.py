"""Raised and lowered text in running text (backlog T2-4, T2-5): the scripts of upright
text (H2O, Na+, SO4 2-, cm3, 226Ra) come out as `Element.script_runs`, not flat and not as
inline equations; those of a variable (an italic x) stay an inline equation; what cannot be
carried is flagged `scripts_flattened`. Pages are built here glyph by glyph, so the sizes
and offsets are the ones the test says."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from a11y_stem.core.types import Element, Kind, Notice
from a11y_stem.pipeline import layout

REPO = Path(__file__).resolve().parents[1]
BODY = 11.0
SMALL = 7.0
UP, DOWN = 3.5, -1.8  # raise and lowering of a script, pt


class Line:
    """One line of text put on a page piece by piece: (text, kind) with kind None for
    body text, "sup" or "sub" for a script, "italic" for an italic body-size piece."""

    def __init__(self, page: pymupdf.Page, y: float, x: float = 72.0) -> None:
        self.page, self.y, self.x = page, y, x

    def add(self, text: str, kind: str | None = None) -> Line:
        size = SMALL if kind in ("sup", "sub") else BODY
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


def runs_of(element: Element) -> list[tuple[str, str]]:
    text = element.text or ""
    return [(text[r.start : r.end], r.kind) for r in element.script_runs]


def test_a_formula_and_an_ion_keep_their_subscripts_and_charges():
    pdf = build(
        [
            [("Water is H", None), ("2", "sub"), ("O and table salt is Na", None), ("+", "sup")],
            [("Cl", None), ("-", "sup"), (" is the chloride ion and Ca", None), ("2+", "sup")],
        ]
    )
    (paragraph,) = paragraphs(pdf)
    assert "H2O" in (paragraph.text or "") and "Na+" in (paragraph.text or "")
    assert runs_of(paragraph) == [("2", "sub"), ("+", "sup"), ("-", "sup"), ("2+", "sup")]
    assert not any(e.kind is Kind.EQUATION for e in layout.analyze(pdf).elements)


def test_a_count_under_a_charge_is_read_count_first():
    pdf = build([[("The sulfate ion SO", None), ("4|2-", "stack"), (" is common.", None)]])
    (paragraph,) = paragraphs(pdf)
    assert (paragraph.text or "").startswith("The sulfate ion SO42-")
    assert runs_of(paragraph) == [("4", "sub"), ("2-", "sup")]


def test_a_unit_exponent_and_a_mass_number_are_raised_text():
    doc = pymupdf.open()
    line = Line(doc.new_page(), 100)
    line.add("Gold is 19.3 g/cm").add("3", "sup").add(" and the isotope ").add("226", "sup")
    line.add("Ra decays.")
    (paragraph,) = paragraphs(doc.tobytes())
    assert paragraph.text == "Gold is 19.3 g/cm3 and the isotope 226Ra decays."
    assert runs_of(paragraph) == [("3", "sup"), ("226", "sup")]


def test_a_variable_with_an_exponent_stays_an_equation():
    pdf = build([[("The area is ", None), ("x", "italic"), ("2", "sup"), (" in all.", None)]])
    elements = layout.analyze(pdf).elements
    (paragraph,) = [e for e in elements if e.kind is Kind.PARAGRAPH]
    assert paragraph.script_runs == []
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.EQUATION]


def test_text_without_scripts_has_no_runs_and_no_notice():
    pdf = build([[("Plain words with no raised or lowered text at all.", None)]])
    result = layout.analyze(pdf)
    assert [e.script_runs for e in result.elements] == [[]]
    assert not any(n.code == "scripts_flattened" for n in result.notices)


def test_a_script_with_nothing_before_it_is_flagged():
    # A raised mark at the start of a line has no base to belong to: written flat, and said.
    pdf = build([[("2", "sup"), ("abc def ghi jkl mno pqr", None)]])
    result = layout.analyze(pdf)
    notices: list[Notice] = [n for n in result.notices if n.code == "scripts_flattened"]
    assert len(notices) == 1 and notices[0].review == "optional"
    assert notices[0].element_id == result.elements[0].id


def test_a_nuclides_numbers_stacked_before_its_symbol_read_mass_number_first():
    pdf = build(
        [[("The nuclide ", None), ("6|14", "stack-up-first"), ("C has six protons.", None)]]
    )
    (paragraph,) = paragraphs(pdf)
    assert paragraph.text == "The nuclide 146C has six protons."
    assert runs_of(paragraph) == [("14", "sup"), ("6", "sub")]


def test_display_nuclear_equations_of_a_latex_page_keep_each_nuclides_numbers_together():
    # The committed development page: each display line is one equation (it was cut into
    # pieces, the symbol of a nuclide lost), with mass number, atomic number, symbol in order.
    pdf = (REPO / "data/dev-v2/dev/authored/latex-chemistry/latex-chemistry.pdf").read_bytes()
    texts = [
        e.text or "" for e in layout.analyze(pdf, pages=[4]).elements if e.kind is Kind.EQUATION
    ]
    assert "23892 U \u2212\u219223490 Th + 42He" in texts
    assert "22688 Ra \u2212\u219222286 Rn + 42He" in texts


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


def test_a_lowered_script_in_a_table_cell_is_flagged_and_a_plain_cell_is_not():
    flagged = layout.analyze(table_page(lowered=True))
    plain = layout.analyze(table_page(lowered=False))
    tables = [e for e in flagged.elements if e.kind is Kind.TABLE]
    assert len(tables) == 1
    assert [n.element_id for n in flagged.notices if n.code == "scripts_flattened"] == [
        tables[0].id
    ]
    assert not any(n.code == "scripts_flattened" for n in plain.notices)


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


def test_a_row_of_mostly_scripts_is_set_in_the_size_of_its_text():
    # "92 U -> 234": five glyphs at 8 pt lifted or dropped, three at 10.9 pt on the baseline.
    row = _row([("92", 8.0, 318.6), ("U", 10.9, 315.9), ("→", 10.9, 315.9), ("234", 8.0, 311.4)])
    assert row.size == 11.0  # 10.9 rounded as sizes are
    assert row.baseline == 315.9


@pytest.mark.parametrize(
    "parts",
    [
        # the numbers of a fraction beside an operator: nothing to hang scripts on
        [("3", 4.5, 100.0), ("+", 6.5, 103.0), ("1", 4.5, 106.0)],
        # a sentence with small numbers in it: the larger glyphs hold a word
        [("Simplify", 9.0, 100.0), ("5", 6.5, 97.0), ("12", 6.5, 104.0)],
        # half the size is not a script
        [("A", 9.0, 100.0), ("12", 4.5, 97.0)],
    ],
)
def test_the_numbers_of_a_fraction_are_not_taken_for_scripts_of_the_row(parts):
    sizes = {round(size * 2) / 2 for _, size, _ in parts}
    dominant = max(
        sizes,
        key=lambda s: sum(len(t) for t, size, _ in parts if round(size * 2) / 2 == s) * 1000 + s,
    )
    assert _row(parts).size == dominant
