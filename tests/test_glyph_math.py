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
from pathlib import Path

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


def test_a_comma_at_the_baseline_or_at_the_end_of_an_index_is_not():
    chars = row("pm,q")
    assert not glyph_math.script_punctuation(chars, [False, True, False, True], 2)
    assert not glyph_math.script_punctuation(chars, [False, True, True, False], 2)  # index ends
    assert not glyph_math.script_punctuation(row("pm,"), [False, True, True], 2)


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


# ------------------------------------------------------------------ short formulas the runs miss


def relations(text: str, maths: str, script: str = "") -> list[tuple[int, int, str]]:
    chars = row(text)
    return glyph_math.relation_runs(chars, [c in maths for c in text], [c in script for c in text])


def test_a_relation_that_is_no_maths_glyph_or_has_no_number_is_left_alone():
    assert relations("is ≥2 and", "") == []
    assert relations("is ≥ n and", "≥") == []
    assert relations("is ≥2x and", "≥") == []  # not a bare number: something longer follows
    assert relations("is ≥1.2.3 and", "≥") == []


FIXTURE = Path(__file__).parent / "fixtures" / "layout" / "latex" / "short_maths.pdf"


def fixture_equations() -> list[str]:
    return [e.text or "" for e in analyze(FIXTURE.read_bytes()).elements if e.kind == Kind.EQUATION]


def test_a_relation_before_a_number_is_inline_maths_in_a_tex_paper():
    assert fixture_equations() == ["≈0.14", "≥2", "≥90%"]


def test_plain_numbers_percentages_years_citations_and_markers_stay_text():
    """The text of the same paper that looks like short maths: none of it is an equation, and
    all of it is still in the paragraphs and list items."""
    layout = analyze(FIXTURE.read_bytes())
    text = " ".join(e.text or "" for e in layout.elements if e.kind != Kind.EQUATION)
    for plain in ("95% was reported in 2024", "12 participants", "34.5 years", "5 mg", "[3]"):
        assert plain in text
    for plain in ("Figure 3", "Table 2 on page 7", "(Section 4.1)", "25% longer", "10 minutes"):
        assert plain in text


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
    """Index comma, percent and unit rules are TeX conventions: a browser print is as before."""

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
