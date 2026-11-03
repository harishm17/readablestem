"""Where an inline formula starts and ends, from the glyphs (`pipeline/glyph_math.py`, T11).

The rules are pure functions of a row's characters, tested here on hand-made rows; the
layout-level tests build small PDFs of a pdfTeX producer with the base-14 fonts, so no real
document is needed. Cases come from the real-world development set
(docs/eval-equation-diagnostics.md, "Inline span edges"): `\\rho_{m,q}(x)` read as `q(x)`,
`\\mathcal O(\\sqrt T)` as `O(\\sqrt`, `\\Delta t = 24` read with its `h`, `\\phi = 5\\%` cut short,
`\\operatorname{supp}(X)` as `X`.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from readablestem.core.types import Element, Kind
from readablestem.pipeline import glyph_math
from readablestem.pipeline.layout import analyze


@dataclass
class G:
    """A glyph as `glyph_math` reads it."""

    c: str
    x0: float = 0.0
    x1: float = 0.0
    size: float = 10.0

    @property
    def blank(self) -> bool:
        return self.c.isspace()


def row(text: str, width: float = 5.0, gaps: dict[int, float] | None = None) -> list[G]:
    """Glyphs of `text` side by side; `gaps[i]` is the space before glyph i."""
    out, x = [], 0.0
    for i, ch in enumerate(text):
        x += (gaps or {}).get(i, 0.0)
        w = 0.0 if ch.isspace() else width
        out.append(G(ch, x, x + w))
        x += w
    return out


# ------------------------------------------------------------------ the rules


def test_a_comma_between_script_glyphs_is_part_of_the_index():
    chars = row("pm,q")
    script = [False, True, True, True]
    assert glyph_math.script_punctuation(chars, script, 2)


def test_a_comma_at_the_baseline_or_at_the_end_of_an_index_is_not():
    chars = row("pm,q")
    assert not glyph_math.script_punctuation(chars, [False, True, False, True], 2)
    assert not glyph_math.script_punctuation(chars, [False, True, True, False], 2)  # index ends
    assert not glyph_math.script_punctuation(row("pm,"), [False, True, True], 2)


def test_two_punctuation_marks_in_a_row_are_not_an_index():
    chars = row("p,,q")
    assert not glyph_math.script_punctuation(chars, [False, True, True, True, True][:4], 2)


def test_a_blank_between_touching_glyphs_is_no_space():
    chars = row("v v", gaps={})
    assert glyph_math.touching(chars, 1)
    chars = row("v v", gaps={2: 3.0})
    assert not glyph_math.touching(chars, 1)


def test_a_known_operator_name_touching_an_opening_bracket_is_a_function_name():
    assert glyph_math.function_name(row("Cov("), 0, 3)
    assert glyph_math.function_name(row("Pr("), 0, 2)
    assert not glyph_math.function_name(row("Cov(", gaps={3: 3.0}), 0, 3)


def test_an_acronym_a_word_or_a_chemical_formula_is_no_function_name():
    assert not glyph_math.function_name(row("ARFIMA("), 0, 6)
    assert not glyph_math.function_name(row("CTCS("), 0, 4)
    assert not glyph_math.function_name(row("form("), 0, 4)
    assert not glyph_math.function_name(row("NaOH("), 0, 4)
    assert not glyph_math.function_name(row("Mg("), 0, 2)


def unit_of(text: str, *, upright: str, script: str = "") -> int | None:
    chars = row(text)
    return glyph_math.trailing_unit(
        chars,
        0,
        len(chars),
        upright=[c in upright for c in text],
        script=[c in script for c in text],
    )


def test_a_unit_after_a_number_is_cut_off():
    assert unit_of("= 24h", upright="h") == 4
    assert unit_of("t = 5 s", upright="s") == 5
    assert unit_of("x = 9.8 m", upright="m") == 7


def test_a_unit_with_exponents_is_cut_off_whole():
    # 6.6 x 10^-11 m^3 kg^-1
    text = "6.6x10-11 m3 kg-1"
    script = "-11" + ""
    chars = row(text)
    flags_script = [False] * len(text)
    for k in (6, 7, 8, 11, 15, 16):  # "-11", "3", "-1" are raised
        flags_script[k] = True
    out = glyph_math.trailing_unit(
        chars,
        0,
        len(text),
        upright=[c in "mkg" and not flags_script[k] for k, c in enumerate(text)],
        script=flags_script,
    )
    assert out == 9
    assert script  # the raised figures are the ones listed above


def test_a_letter_after_a_variable_is_no_unit():
    assert unit_of("x m", upright="m") is None
    assert unit_of("2 + x", upright="x" * 0) is None


def test_raised_figures_before_an_element_symbol_are_a_nuclide_not_a_number():
    # "(b a)15O": the 15 is raised and the O upright, a nuclide
    text = "a15O"
    script = [False, True, True, False]
    chars = row(text)
    assert (
        glyph_math.trailing_unit(chars, 0, 4, upright=[False, False, False, True], script=script)
        is None
    )


def test_a_percent_sign_after_a_digit_is_part_of_a_formula():
    assert glyph_math.percent_of_number(row("5%"), 1)
    assert not glyph_math.percent_of_number(row("a%"), 1)
    assert not glyph_math.percent_of_number(row("%"), 0)


# ------------------------------------------------------------------ in the layout

BODY = 11.0


def one_page(fill, producer: str = "pdfTeX-1.40.25") -> bytes:
    doc = pymupdf.open()
    doc.set_metadata({"producer": producer})
    fill(doc.new_page(width=612, height=792))
    return doc.tobytes()


def put(page, x: float, y: float, text: str, font: str, size: float = BODY) -> float:
    page.insert_text((x, y), text, fontsize=size, fontname=font)
    return x + pymupdf.get_text_length(text, fontname=font, fontsize=size)


def equations(elements: list[Element]) -> list[str]:
    return [(e.text or "").replace(" ", "") for e in elements if e.kind == Kind.EQUATION]


def sentence(page, before: str, after: str, parts) -> None:
    """`before`, the formula `parts` ((text, font, size, rise) each), then `after`, on one line."""
    x = put(page, 72, 100, before, "helv")
    for text, font, size, rise in parts:
        x = put(page, x, 100 + rise, text, font, size)
    put(page, x, 100, after, "helv")


def test_a_comma_in_an_index_keeps_the_formula_whole():
    def fill(page):
        sentence(
            page,
            "We write the density profile ",
            " for every boundary condition we study in this paper.",
            [
                ("p", "heit", BODY, 0),
                ("m,q", "heit", 7.7, 2.5),
                ("(", "helv", BODY, 0),
                ("x", "heit", BODY, 0),
                (")", "helv", BODY, 0),
            ],
        )

    assert equations(analyze(one_page(fill)).elements) == ["pm,q(x)"]


def test_a_unit_glued_to_a_number_stays_outside_the_formula():
    def fill(page):
        sentence(
            page,
            "The step is ",
            " in every experiment of this series of runs.",
            [("t", "heit", BODY, 0), (" = 24", "helv", BODY, 0), ("h", "helv", BODY, 0)],
        )

    found = equations(analyze(one_page(fill)).elements)
    assert found == ["t=24"]


def test_a_percent_sign_after_the_number_is_in_the_formula():
    def fill(page):
        sentence(
            page,
            "At a volume fraction ",
            " the sample stays clear throughout the whole run.",
            [("f", "heit", BODY, 0), (" = 5%", "helv", BODY, 0)],
        )

    assert equations(analyze(one_page(fill)).elements) == ["f=5%"]


def test_an_upright_name_touching_its_bracket_is_part_of_the_formula():
    def fill(page):
        sentence(
            page,
            "The support ",
            " is finite for every distribution we use.",
            [
                ("supp", "helv", BODY, 0),
                ("(", "helv", BODY, 0),
                ("X", "heit", BODY, 0),
                (")", "helv", BODY, 0),
            ],
        )

    assert equations(analyze(one_page(fill)).elements) == ["supp(X)"]


def test_an_english_word_before_a_bracket_is_not_taken_into_the_formula():
    def fill(page):
        sentence(
            page,
            "Every function",
            " is measured on the page and the other pieces are fine.",
            [("(", "helv", BODY, 0), ("x", "heit", BODY, 0), (")", "helv", BODY, 0)],
        )

    assert all(not t.startswith("function") for t in equations(analyze(one_page(fill)).elements))


def test_the_rules_leave_a_document_not_made_by_tex_alone():
    def fill(page):
        sentence(
            page,
            "The step is ",
            " in every experiment of this series of runs.",
            [("t", "heit", BODY, 0), (" = 24", "helv", BODY, 0), ("h", "helv", BODY, 0)],
        )

    assert equations(analyze(one_page(fill)).elements) == ["t=24"]
    # A browser print sets units inside the maths: its formulas keep the unit.
    assert equations(analyze(one_page(fill, producer="Skia/PDF m120")).elements) == ["t=24h"]


def test_a_word_in_brackets_after_an_indexed_variable_stays_prose():
    """`$x_i$ Eq(4)`: a script-ended operand does not make the next word a function name."""
    for word in ("Eq", "Fig", "Prob", "Smith"):

        def fill(page, word=word):
            sentence(
                page,
                "As shown for ",
                " in the text above and in every later section of it.",
                [
                    ("x", "heit", BODY, 0),
                    ("i", "heit", 7.7, 2.5),
                    (" ", "helv", BODY, 0),
                    (word, "helv", BODY, 0),
                    ("(4)", "helv", BODY, 0),
                ],
            )

        found = equations(analyze(one_page(fill)).elements)
        assert not any(t.startswith(word) for t in found), (word, found)


def test_the_edge_rules_do_not_touch_a_document_not_made_by_tex():
    """Index comma, percent sign and unit rules are TeX conventions: a browser print is read as before."""

    def comma(page):
        sentence(
            page,
            "We write the density profile ",
            " for every boundary condition we study in this paper.",
            [
                ("p", "heit", BODY, 0),
                ("m,q", "heit", 7.7, 2.5),
                ("(", "helv", BODY, 0),
                ("x", "heit", BODY, 0),
                (")", "helv", BODY, 0),
            ],
        )

    def percent(page):
        sentence(
            page,
            "At a volume fraction ",
            " the sample stays clear throughout the whole run.",
            [("f", "heit", BODY, 0), (" = 5%", "helv", BODY, 0)],
        )

    chrome = "Skia/PDF m120"
    assert equations(analyze(one_page(comma, producer=chrome)).elements) != ["pm,q(x)"]
    assert equations(analyze(one_page(percent, producer=chrome)).elements) == ["f=5"]
