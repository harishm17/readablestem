# ruff: noqa: RUF001, RUF003
"""Corrections of a reading from the PDF's own glyphs (`EQUATION_GLYPH_RECONCILE`).

The cases are the ones the reading diagnosis found in the development papers (2305.06353,
2412.12319, 2404.15495, 2505.11658, 2501.06896, 2503.00937, 2503.02792, 2404.14123), with their
glyph strings as layout wrote them, and the negative cases that must stay as they are: an unnamed
glyph, two differences, a symbol that occurs twice, a unit inside the box, `f(1)`, a stacked
fraction. Every rule is checked as a pure function and once through `transcribe`.
"""

from __future__ import annotations

import pytest

from readablestem.core.types import (
    Check,
    Element,
    GlyphContext,
    Kind,
    TableCell,
    TableGrid,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import glyph_reconcile as gr
from readablestem.pipeline import table_step
from readablestem.pipeline.equation_step import transcribe
from readablestem.pipeline.validate import safe_latex_to_mathml
from test_steps_support import make_settings, use_fallback_router


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def ml(latex: str, display: str = "inline") -> str:
    return safe_latex_to_mathml(latex, display)  # type: ignore[arg-type]


def ctx(span: str, after: str = "", **kw) -> GlyphContext:
    return GlyphContext(span=span, after=after, **kw)


def equation(text: str, glyphs: GlyphContext | None, display: str = "inline", **kw) -> Element:
    return Element(
        id="p1-e1",
        kind=Kind.EQUATION,
        page=0,
        bbox=(0, 0, 40, 10),
        order=1,
        text=text,
        display=display,  # type: ignore[arg-type]
        glyphs=glyphs,
        **kw,
    )


def run(element: Element, latex: str, *, reconcile: bool = True):
    client = FakeVisionClient(
        responders={
            "equation": lambda req: {"is_equation": True, "latex": latex, "confidence": 0.95}
        }
    )
    settings = make_settings(equation_glyph_reconcile=reconcile)
    return transcribe(element, b"\x89PNG", client=client, settings=settings)


def codes(fix) -> set[str]:
    return {c.code for c in fix.checks if not c.passed and c.code}


# ------------------------------------------------------------------------- F1: one symbol


def test_a_relation_misread_for_another_is_the_glyphs_relation():
    # 2305.06353/eq-109: the glyphs are `n_k ≍ n_l` (CMSY), read `\succsim` twice.
    latex = r"n_k \succsim n_l"
    assert gr.reconcile_symbol(latex, ml(latex), "nk ≍nl", True) == r"n_k \asymp n_l"


def test_a_greek_letter_read_as_its_latin_lookalike_is_the_greek_letter():
    # 2412.12319/eq-046: the glyph is υ, read v by Haiku and again by Opus.
    assert gr.reconcile_symbol("v(x)", ml("v(x)"), "υ(x)", True) == r"\upsilon (x)"
    # and the other way round: the glyph is a Latin v, the reading has \nu
    assert gr.reconcile_symbol(r"\nu(x)", ml(r"\nu(x)"), "v(x)", True) == "v(x)"


def test_a_text_caret_for_a_wedge_is_the_wedge():
    # 2505.03099/eq-031: a spreadsheet formula, `10∧(−11)`, read `10\text{^}(-11)`.
    latex = r"= 10\text{^}(-11) * 0.5\text{^}2"
    text = "= 10∧(−11) ∗0.5∧2"
    assert gr.reconcile_symbol(latex, ml(latex), text, True) == r"= 10\wedge (-11) * 0.5\wedge 2"
    assert gr.reconcile_symbol(latex, ml(latex), "= 10∧(−11) ∗0.5", True) is None  # a wedge short


@pytest.mark.parametrize(
    ("latex", "text", "why"),
    [
        ("v(x)", "υ(x)", "the PDF is not TeX-made"),  # tex=False below
        ("v(x)", "�(x)", "an unnamed glyph"),
        ("v(x) + v", "υ(x) + v", "the letter occurs twice in the reading"),
        ("v(x) + w", "υ(x) + z", "two letters differ"),
        ("v(x)", "x(x)", "x for v is no lookalike pair"),
        (r"n_k \succsim n_l", "nk ≍nl ≍", "a relation more in the glyphs"),
        (r"n_k \succsim n_l \succsim n_m", "nk ≍nl ≍nm", "two relations differ"),
        (r"n_k \succsim n_l", "nk =nl", "= is no lookalike of ≿"),
    ],
)
def test_a_difference_that_is_not_one_named_lookalike_is_left_to_the_checks(latex, text, why):
    tex = why != "the PDF is not TeX-made"
    assert gr.reconcile_symbol(latex, ml(latex), text, tex) is None, why


def test_a_misread_symbol_is_corrected_in_the_fix_and_noted():
    element = equation("υ(x)", ctx("υ-(-xi)-", tex=True))
    fix = run(element, "v(x)")
    assert fix.equation is not None
    assert fix.equation.latex == r"\upsilon (x)"
    assert fix.equation.model_latex == "v(x)"
    assert ("equation.pdf_correction", "glyph_reconciled") in {
        (c.name, c.code) for c in fix.checks if not c.passed
    }
    assert "symbol_mismatch" not in codes(fix)  # the corrected reading agrees with the PDF
    assert fix.status.value == "accepted"  # a note, not a decision


def test_with_the_setting_off_nothing_changes():
    element = equation("υ(x)", ctx("υ-(-xi)-", tex=True))
    fix = run(element, "v(x)", reconcile=False)
    assert fix.equation is not None and fix.equation.latex == "v(x)"
    assert fix.equation.model_latex is None
    assert "symbol_mismatch" in codes(fix)  # flagged, as before


def test_a_pdf_that_is_not_tex_made_is_not_corrected():
    element = equation("υ(x)", ctx("υ-(-xi)-", tex=False))
    fix = run(element, "v(x)")
    assert fix.equation is not None and fix.equation.latex == "v(x)"
    assert "symbol_mismatch" in codes(fix)


# ----------------------------------------------------------------------- F2: a unit past the box


def test_a_unit_added_after_a_number_the_box_ends_with_goes():
    # 2404.15495/eq-009, eq-011: the box is `∆t = 1`, `∆t = 24`; the page sets "h" after it.
    span = "∆-ti -=- -1-"
    after = "hu,- -suuuiutuaubulueu"
    got = ctx(span, after)
    one = r"\Delta t = 1\ \text{h}"
    assert (
        gr.drop_unit_past_box(one, ml(one), "∆t = 1", got.span_glyphs, got.after_glyphs)
        == r"\Delta t = 1"
    )
    day = ctx("∆-ti -=- -2-4-", "hu,- -aupupuluiucuaubulueu")
    latex = r"\Delta t = 24\text{h}"
    assert (
        gr.drop_unit_past_box(latex, ml(latex), "∆t = 24", day.span_glyphs, day.after_glyphs)
        == r"\Delta t = 24"
    )


def test_a_unit_past_a_power_of_ten_goes():
    # 2505.11658/eq-038: `x = 8.12836 × 10^6` boxed (wrapped across two lines), "m" after it.
    span = "xi -=- -8-.-1-2-8-3-6-×-1-0-6- -mu,-"
    got = ctx(span)
    latex = r"x = 8.12836 \times 10^6\ \text{m}"
    text = "x = 8.12836 × 106"
    assert (
        gr.drop_unit_past_box(latex, ml(latex), text, got.span_glyphs, got.after_glyphs)
        == r"x = 8.12836 \times 10^6"
    )


@pytest.mark.parametrize(
    ("latex", "text", "span", "after", "why"),
    [
        # the unit is inside the box: its glyphs are the box's, the text has it
        (r"x = 5\,\mathrm{m}", "x = 5 m", "xi -=- -5- -mu", "", "a unit typed in maths"),
        # the box ends in a letter, not a digit
        (r"\Delta t\ \text{h}", "∆t", "∆-ti", "hu", "no number"),
        # the unit is in italic: a variable, not a unit
        (r"t = 1\ \text{h}", "t = 1", "ti -=- -1-", "hi", "an italic h"),
        # the unit is followed by more letters: a word
        (r"t = 1\ \text{h}", "t = 1", "ti -=- -1-", "huououuuru", "a word"),
        # two units: not a single one
        (r"t = 1\ \text{km}", "t = 1", "ti -=- -1-", "hu", "a different unit"),
    ],
)
def test_a_unit_that_is_not_the_one_after_the_number_stays(latex, text, span, after, why):
    got = ctx(span, after)
    assert (
        gr.drop_unit_past_box(latex, ml(latex), text, got.span_glyphs, got.after_glyphs) is None
    ), why


def test_a_unit_past_the_box_is_dropped_in_the_fix():
    element = equation("∆t = 1", ctx("∆-ti -=- -1-", "hu,- -suuuiutuaubulueu", tex=True))
    fix = run(element, r"\Delta t = 1\ \text{h}")
    assert fix.equation is not None and fix.equation.latex == r"\Delta t = 1"
    assert fix.equation.model_latex == r"\Delta t = 1\ \text{h}"
    assert ("equation.pdf_correction", "unit_dropped") in {
        (c.name, c.code) for c in fix.checks if not c.passed
    }
    assert "symbol_mismatch" not in codes(fix)


# ----------------------------------------------------------------------- F3: a printed slash


@pytest.mark.parametrize(
    ("latex", "text", "expected"),
    [
        (r"-\frac{2}{3}", "−2/3", "-2/3"),  # 2501.06896/eq-002
        (r"\frac{Cn}{w}", "Cn/w", "Cn/w"),  # 2503.00937/eq-041
        (  # 2503.02792/eq-093: the parentheses the glyphs show
            r"\frac{(K - \mu\langle N\rangle)}{(1 - \sigma^2\rho\chi)}",
            "(K −µ〈N〉)/(1 −σ2ρχ)",
            r"(K - \mu\langle N\rangle)/(1 - \sigma^2\rho\chi)",
        ),
        (r"\frac{a+b}{c+d}", "(a+b)/(c+d)", "(a+b)/(c+d)"),  # parentheses the reading lacks
    ],
)
def test_a_printed_slash_stays_a_slash(latex, text, expected):
    assert gr.keep_slash(latex, text, fraction_bar=False) == expected


@pytest.mark.parametrize(
    ("latex", "text", "fraction_bar", "why"),
    [
        (r"\frac{a}{b}", "a/b", True, "layout found a fraction bar in the box"),
        (r"\frac{a}{b}", "ab", False, "no slash in the text"),
        (r"\frac{a}{b}", "a/b/c", False, "two slashes"),
        (r"\frac{a}{c}", "a/b", False, "the letters on a side differ"),
        (r"x = \frac{a}{b}", "x = a/b", False, "the fraction is not the whole reading"),
        (r"\frac{\frac{a}{b}}{c}", "a/b/c", False, "a fraction inside a fraction"),
        (r"\frac{a}{b} + 1", "a/b + 1", False, "something after the fraction"),
    ],
)
def test_a_fraction_that_is_not_a_printed_slash_stays(latex, text, fraction_bar, why):
    assert gr.keep_slash(latex, text, fraction_bar=fraction_bar) is None, why


def test_a_printed_slash_is_kept_in_the_fix():
    element = equation("Cn/w", ctx("Cini/-wi", tex=True))
    fix = run(element, r"\frac{Cn}{w}")
    assert fix.equation is not None and fix.equation.latex == "Cn/w"
    assert fix.equation.model_latex == r"\frac{Cn}{w}"
    assert ("equation.pdf_correction", "slash_kept") in {
        (c.name, c.code) for c in fix.checks if not c.passed
    }


def test_a_stacked_fraction_in_a_box_with_a_slash_elsewhere_is_not_rewritten():
    element = equation("a + b/c", ctx("ai -+- -bi/-ci"), reasons=["fraction bar"])
    fix = run(element, r"\frac{a}{b}")
    assert fix.equation is not None and fix.equation.latex == r"\frac{a}{b}"


# ----------------------------------------------------------------- F4: a display's number


def test_a_trailing_number_after_a_comma_or_a_quad_goes_when_the_glyphs_have_it_apart():
    # 2404.15495/eq-034 (`, \quad (1)`) and 2404.14123/eq-023 (`, (8)`).
    assert gr.edge_label(r"A = \frac{1}{2}, \quad (1)", ["(1)"]) == (r"A = \frac{1}{2},", "(1)")
    assert gr.edge_label(r"P = w, (8)", ["(8)"]) == ("P = w,", "(8)")
    # 2412.12319/eq-022: the label at the start, a `\qquad` after it
    assert gr.edge_label(r"(5.5) \qquad E[D_n] = 1", ["(5.5)"]) == ("E[D_n] = 1", "(5.5)")


@pytest.mark.parametrize(
    ("latex", "labels", "why"),
    [
        (r"A = f(1)", ["(1)"], "f(1) has neither the separator nor the gap"),
        (r"A = f, (1)", [], "the glyphs have no label apart from the formula"),
        (r"A = f, (2)", ["(1)"], "another number"),
        (r"A = f \quad (x)", ["(x)"], "not a number"),
        (r"(1) + x", ["(1)"], "a leading (1) with no quad is a term"),
        (r", (1)", ["(1)"], "nothing would be left"),
    ],
)
def test_a_number_that_is_part_of_the_formula_stays(latex, labels, why):
    assert gr.edge_label(latex, labels) is None, why


def _row(text: str, x: float, size: float = 10.0, cy: float = 100.0) -> list[gr.Item]:
    items = []
    for ch in text:
        items.append(gr.Item(ch, x, x + size * 0.5, cy, size))
        x += size * 0.5
    return items


def test_edge_labels_need_a_gap_of_an_em_from_the_formula_on_the_row():
    formula = _row("x=y,", 0)  # ends at x = 20
    apart = [*formula, *_row("(1)", 60)]  # 40 pt away: four ems
    assert gr.edge_labels(apart) == ("(1)",)
    near = [*formula, *_row("(1)", 22)]  # f(1) written as a label would be this close
    assert gr.edge_labels(near) == ()
    assert gr.edge_labels(_row("f(1)", 0)) == ()  # one run: not a label
    left = [*_row("(5.5)", 0), *_row("E=x", 60)]
    assert gr.edge_labels(left) == ("(5.5)",)
    assert gr.edge_labels([]) == ()


def test_a_displays_number_leaves_the_reading_and_becomes_its_number():
    element = equation(
        "A = f , (1)", ctx("Ai -=- -fi,- -(-1-)-", labels="(1)", tex=True), display="block"
    )
    fix = run(element, r"A = f, \quad (1)")
    assert fix.equation is not None and fix.equation.latex == "A = f,"
    assert fix.equation.model_latex == r"A = f, \quad (1)"
    assert element.equation_label == "(1)"
    assert ("equation.pdf_correction", "number_dropped") in {
        (c.name, c.code) for c in fix.checks if not c.passed
    }


def test_a_number_the_element_already_has_is_not_replaced():
    element = equation(
        "A = f , (1)",
        ctx("Ai -=- -fi,- -(-1-)-", labels="(1)", tex=True),
        display="block",
        equation_label="(1.1)",
    )
    run(element, r"A = f, \quad (1)")
    assert element.equation_label == "(1.1)"


# -------------------------------------------------------------------------- F7: script faces


def test_a_capital_in_an_rsfs_face_is_mathscr_and_cmsy_stays_mathcal():
    assert gr.restyle_script(r"\mathcal{E}_{bd,h}", "E") == r"\mathscr{E}_{bd,h}"
    assert gr.restyle_script(r"\mathcal E_h", "E") == r"\mathscr{E}_h"
    assert gr.restyle_script(r"\mathcal{R}_T", "E") == r"\mathcal{R}_T"
    assert gr.restyle_script(r"\mathcal{E}", "") == r"\mathcal{E}"
    assert gr.restyle_script(r"\mathcal{E} + \mathscr{E}", "E") == r"\mathcal{E} + \mathscr{E}"


def test_the_script_face_is_written_in_the_fix_and_no_note_is_made():
    element = equation("ℰh", ctx("ℰsh", scr="E", tex=True))
    fix = run(element, r"\mathcal{E}_h")
    assert fix.equation is not None and fix.equation.latex == r"\mathscr{E}_h"
    assert fix.equation.model_latex is None  # a style restoration, like the bold italic one
    assert not any(c.name == "equation.pdf_correction" for c in fix.checks)


def test_the_table_cell_path_restyles_a_bold_italic_letter():
    # 2504.03499, the cells of its table: `\bm g` printed, `\mathbf{g}` read.
    cell = TableCell(row=0, col=0, text="", latex=r"\|\mathbf{g}_{t}\|_{2}^{2}")
    cell = cell.model_copy(update={"mathml": table_step.to_mathml(cell.latex or "")})
    grid = TableGrid(rows=1, cols=1, cells=[cell])
    pdf = TableCell(row=0, col=0, text="∥\U0001d49a𝒈t∥22".replace("\U0001d49a", ""))
    element = Element(
        id="p1-e2",
        kind=Kind.TABLE,
        page=0,
        bbox=(0, 0, 10, 10),
        order=2,
        table=TableGrid(rows=1, cols=1, cells=[pdf]),
    )
    out = table_step.restyle_cells(grid, element)
    assert out.cells[0].latex == r"\|\boldsymbol{g}_{t}\|_{2}^{2}"
    assert out.cells[0].mathml and "g" in out.cells[0].mathml
    # a cell the PDF draws bold upright stays `\mathbf`
    upright = pdf.model_copy(update={"text": "∥gt∥22"})
    plain = element.model_copy(update={"table": TableGrid(rows=1, cols=1, cells=[upright])})
    assert table_step.restyle_cells(grid, plain).cells[0].latex == cell.latex
    # a grid of another size is not touched
    wide = TableGrid(rows=1, cols=2, cells=[cell])
    assert table_step.restyle_cells(wide, element) is wide


def test_a_note_check_is_a_note_with_a_message():
    from readablestem.web.messages import CHECK_MESSAGES

    for code in gr.CODES:
        assert (gr.CHECK_NAME, code) in CHECK_MESSAGES
    assert Check(name=gr.CHECK_NAME, passed=False, code="slash_kept").code in gr.CODES
