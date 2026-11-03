# ruff: noqa: RUF001
"""A reading is checked against the glyphs it covers, and its letter styles against the fonts.

Extent: a reading of the line goes on past the box (`T = 2\\pi` boxed, `\\sqrt{m/k}` after
it), so the text layer of the box alone is the wrong thing to compare it with
(`text_layer_compare.extend_text_layer`, `layout.line_glyphs`, `Element.glyphs`).
Styles: the letters of a reading against the style of each glyph (CMMIB is bold italic, CMBX bold
upright, CMR upright, CMMI italic, MSBM double-struck): `compare_letter_styles`.
"""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.core.types import Check, Element, GlyphContext, Kind
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import layout
from readablestem.pipeline import text_layer_compare as tl
from readablestem.pipeline.equation_step import (
    glyph_style_check,
    reading_layer,
    transcribe,
)
from readablestem.pipeline.layout import _Char, glyph_style
from readablestem.pipeline.validate import safe_latex_to_mathml
from test_steps_support import make_settings, use_fallback_router


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def ml(latex: str) -> str:
    return safe_latex_to_mathml(latex, "inline")


def g(text: str, style: str | None = "italic") -> list[tuple[str, str | None]]:
    return [(c, style if c.isalpha() else None) for c in text]


# ------------------------------------------------------------------ the extent


def test_a_reading_that_reaches_past_the_span_is_compared_with_the_glyphs_it_covers():
    # The box holds "T = 2π"; the line goes on "√ m / k is the period".
    extent = tl.extend_text_layer(
        "T = 2π", ml(r"T = 2\pi\sqrt{m/k}"), g("T=2π"), g("("), g("√m/k is the", "upright")
    )
    assert extent is not None
    assert (extent.before, extent.after) == (0, 4)
    assert extent.text == "T = 2π√m/k"


def test_the_extent_can_go_left_of_the_span_too():
    # The box holds "x = 2"; the reading also has the "v" of "v x" written before it.
    extent = tl.extend_text_layer(
        "= 2 s", ml(r"d = 2 s"), g("= 2 s"), g(" d"), g("is long", "upright")
    )
    assert extent is not None and extent.before == 2 and extent.after == 0
    assert extent.text == "d = 2 s"


def test_the_least_extent_wins_and_words_after_the_formula_stay_out():
    extent = tl.extend_text_layer(
        "E = m", ml(r"E = mc^2"), g("E = m"), (), g("c2 is the energy", "upright")
    )
    assert extent is not None
    assert extent.text == "E = mc2"  # not "... is the energy"


def test_a_reading_the_glyphs_cannot_make_has_no_extent():
    # The reading has a "q" no glyph beside the span has: the difference stays a difference.
    assert tl.extend_text_layer("T = 2π", ml(r"T = 2\pi q"), g("T=2π"), (), g("m/k")) is None
    # ... and so has one that has less than the span.
    assert tl.extend_text_layer("T = 2π m", ml(r"T = 2\pi"), g("T=2πm"), (), g("m")) is None


def test_a_reading_that_agrees_with_the_span_needs_no_extent():
    assert tl.extend_text_layer("x + 1", ml("x + 1"), g("x+1"), (), g("y")) is None
    assert tl.extend_text_layer(None, ml("x"), (), (), g("x")) is None


# ------------------------------------------------------------ the letter styles


@pytest.mark.parametrize(
    ("latex", "styles"),
    [
        (r"d x", {("d", "italic"), ("x", "italic")}),
        (r"\mathrm{d}x", {("d", "upright"), ("x", "italic")}),
        (r"\mathbf{v} = \boldsymbol{w}", {("v", "bold"), ("w", "bold-italic")}),
        (r"\bm{F} = m\mathbf a", {("F", "bold-italic"), ("m", "italic"), ("a", "bold")}),
        (r"\mathbb{R}, \mathcal{L}", {("R", "double-struck"), ("L", "script")}),
        (r"\sin x", {("s", "upright"), ("i", "upright"), ("n", "upright"), ("x", "italic")}),
        (
            r"x_{\text{max}}",
            {("x", "italic"), ("m", "upright"), ("a", "upright"), ("x", "upright")},
        ),
        (r"{\rm d} t", {("d", "upright"), ("t", "italic")}),
        (r"\frac{\mathrm{d}y}{\mathrm{d}x}", {("d", "upright"), ("y", "italic"), ("x", "italic")}),
        (r"\begin{cases} a & b \end{cases}", {("a", "italic"), ("b", "italic")}),
        (r"\hat{\mathbf{n}} \cdot \vec{r}", {("n", "bold"), ("r", "italic")}),
        (r"\mathsf{A} B", {("B", "italic")}),
    ],
)
def test_letter_styles_of_a_reading(latex, styles):
    assert set(tl.latex_letter_styles(latex)) == styles


def rows(glyphs, latex):
    return [r.letter for r in tl.compare_letter_styles(glyphs, latex)]


def test_an_upright_d_read_as_an_italic_d_differs():
    pdf = [("d", "upright"), ("x", "italic")]
    assert rows(pdf, "d x") == ["d"]
    assert rows(pdf, r"\mathrm{d}x") == []


def test_bold_against_bold_italic_differs_both_ways():
    assert rows([("v", "bold-italic")], r"\mathbf{v}") == ["v"]
    assert rows([("v", "bold")], r"\boldsymbol{v}") == ["v"]
    assert rows([("v", "bold")], r"\mathbf{v}") == []
    assert rows([("v", "bold-italic")], r"\bm{v}") == []


def test_a_plain_letter_where_the_glyph_is_script_or_double_struck_differs():
    assert rows([("L", "script")], "L") == ["L"]
    assert rows([("L", "script")], r"\mathcal{L}") == []
    assert rows([("R", "double-struck")], "R") == ["R"]
    assert rows([("R", "double-struck")], r"\mathbb{R}") == []


def test_a_glyph_with_no_style_says_nothing_and_cannot_cause_a_difference():
    assert rows([("d", None), ("x", "italic")], r"\mathrm{d}x") == []
    assert rows([("d", None)], "d") == []
    # one known upright d and one unknown glyph against two italic readings: one pairs
    # with the unknown, the other cannot, so the known one differs
    assert rows([("d", "upright"), ("d", None)], "d d") == ["d"]
    assert rows([("d", "upright"), ("d", None)], r"\mathrm{d} d") == []


def test_letters_the_reading_does_not_have_are_not_compared():
    assert rows([("a", "upright"), ("b", "italic")], "b") == []
    assert rows([("α", "italic")], r"\alpha") == []


def test_a_letter_shown_two_ways_is_counted_per_occurrence():
    pdf = [("e", "upright"), ("e", "italic")]  # e^{...} the constant, and a variable e
    assert rows(pdf, r"\mathrm{e}^{x} e") == []
    assert rows(pdf, r"e^{x} e") == ["e"]


# ------------------------------------------------------- the style of one glyph


def char(c: str, font: str, flags: int = 0) -> _Char:
    return _Char(c, 0, 0, 5, 8, 8, 10, font, flags)


@pytest.mark.parametrize(
    ("c", "font", "flags", "style"),
    [
        ("x", "ABCDEF+CMMI10", 0, "italic"),
        ("d", "ABCDEF+CMR10", 0, "upright"),
        ("v", "ABCDEF+CMBX12", 16, "bold"),
        ("v", "ABCDEF+CMMIB10", 0, "bold-italic"),
        ("L", "ABCDEF+CMSY10", 0, "script"),
        ("R", "ABCDEF+MSBM10", 0, "double-struck"),
        ("a", "Times-Italic", 2, "italic"),
        ("a", "Times-Roman", 0, "upright"),
        ("a", "Times-Bold", 16, "bold"),
        ("a", "Times-BoldItalic", 18, "bold-italic"),
        ("\U0001d465", "CambriaMath", 0, "italic"),
        ("\U0001d41d", "CambriaMath", 0, "bold"),
        ("a", "ABCDEF+MysteryFont", 0, None),
        ("a", "CourierNew", 8, None),
        ("5", "ABCDEF+CMR10", 0, None),
        ("α", "ABCDEF+CMMI10", 0, None),
    ],
)
def test_the_style_of_a_printed_letter(c, font, flags, style):
    assert glyph_style(char(c, font, flags)) == style


# ------------------------------------------------------------- Element.glyphs


def test_glyphs_round_trip_through_the_element():
    glyphs = [("T", "italic"), ("=", None), ("\U0001d41d", "bold")]
    context = GlyphContext(span=GlyphContext.encode(glyphs), after=GlyphContext.encode(g("k")))
    element = Element(
        id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=0, text="T", glyphs=context
    )
    back = Element.model_validate_json(element.model_dump_json())
    assert back.glyphs is not None
    assert back.glyphs.span_glyphs == tuple(glyphs)
    assert back.glyphs.after_glyphs == (("k", "italic"),)
    assert (
        Element(id="p1-e2", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=1).glyphs is None
    )
    assert "glyphs" not in Element(
        id="p1-e2", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=1
    ).model_dump(exclude_none=True)


# ----------------------------------------------------- a real page and the step

PAGE_W, PAGE_H = 612, 792


def line_pdf() -> bytes:
    """`where x = 2 m/k is the period`, the formula in Times-Italic and a bold upright v and a
    bold italic w after it."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_text((72, 400), "where ", fontsize=11, fontname="Times-Roman")
    x = 72 + pymupdf.get_text_length("where ", "Times-Roman", 11)
    page.insert_text((x, 400), "x = 2 m/k", fontsize=11, fontname="Times-Italic")
    x += pymupdf.get_text_length("x = 2 m/k", "Times-Italic", 11)
    page.insert_text((x, 400), " is the period of ", fontsize=11, fontname="Times-Roman")
    page.insert_text((72, 430), "vector ", fontsize=11, fontname="Times-Roman")
    x = 72 + pymupdf.get_text_length("vector ", "Times-Roman", 11)
    page.insert_text((x, 430), "v", fontsize=11, fontname="Times-Bold")
    page.insert_text((x + 20, 430), "w", fontsize=11, fontname="Times-BoldItalic")
    page.insert_text((x + 40, 430), "d", fontsize=11, fontname="Times-Roman")
    return doc.tobytes()


def span_of(pdf: bytes, text: str, *, y: float = 400) -> Element:
    """An inline element over the glyphs of `text` on the line at `y`."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    hits = doc[0].search_for(text, quads=False)
    assert hits, text
    rect = min(hits, key=lambda r: abs(r.y1 - y))
    doc.close()
    return Element(
        id="p1-e1",
        kind=Kind.EQUATION,
        page=0,
        bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
        order=0,
        display="inline",
        text=text,
    )


@pytest.fixture
def pdf():
    layout.release()
    yield line_pdf()
    layout.release()


def test_the_glyphs_of_the_span_and_its_neighbours_are_read(pdf):
    element = span_of(pdf, "x = 2")
    context = layout.line_glyphs(pdf, element, (72, 540))
    assert context is not None
    assert "".join(c for c, _ in context.span_glyphs).replace(" ", "") == "x=2"
    assert [s for c, s in context.span_glyphs if c == "x"] == ["italic"]
    after = "".join(c for c, _ in context.after_glyphs)
    assert after.startswith(" m/k") or after.startswith("m/k")
    before = "".join(c for c, _ in context.before_glyphs)
    assert before.startswith(" ereh") or before.startswith("ereh")  # nearest first
    assert dict(context.before_glyphs)["e"] == "upright"


def test_a_display_equation_has_its_own_glyphs_only(pdf):
    element = span_of(pdf, "x = 2").model_copy(update={"display": "block"})
    context = layout.line_glyphs(pdf, element, None)
    assert context is not None
    assert context.before == "" and context.after == ""
    assert layout.line_glyphs(pdf, span_of(pdf, "x = 2"), None, reach=False).after == ""


def test_the_reach_is_the_region_the_line_image_shows(pdf):
    element = span_of(pdf, "x = 2")
    # across= the paragraph's x-range: nothing outside it is read
    narrow = layout.line_glyphs(pdf, element, (element.bbox[0], element.bbox[2] + 12))
    assert narrow is not None
    assert len(narrow.after_glyphs) < 6 and len(narrow.before_glyphs) <= 1


def with_glyphs(pdf: bytes, element: Element) -> Element:
    return element.model_copy(update={"glyphs": layout.line_glyphs(pdf, element, (72, 540))})


def test_reading_layer_uses_the_extent_the_reading_covers(pdf):
    element = with_glyphs(pdf, span_of(pdf, "x = 2"))
    wide = reading_layer(element, ml(r"x = 2\frac{m}{k}"))
    assert wide.extended and wide.text is not None and wide.text.replace(" ", "") == "x=2m/k"
    same = reading_layer(element, ml("x = 2"))
    assert not same.extended and same.text == "x = 2"
    wrong = reading_layer(element, ml(r"x = 2\frac{m}{j}"))
    assert not wrong.extended and wrong.text == "x = 2"  # nothing the glyphs make: a difference
    assert reading_layer(span_of(pdf, "x = 2"), ml(r"x = 2\frac{m}{k}")).text == "x = 2"


def answer(latex: str):
    def respond(req):
        return {"is_equation": True, "latex": latex, "confidence": 0.95}

    return lambda req: respond(req)


def run(element: Element, latex: str):
    client = FakeVisionClient(responders={"equation": answer(latex)})
    return transcribe(element, b"\x89PNG", client=client, settings=make_settings())


def failed_codes(fix) -> set[str]:
    return {c.code for c in fix.checks if not c.passed and c.code}


def test_an_extended_reading_is_no_symbol_mismatch_once_the_glyphs_are_known(pdf):
    element = span_of(pdf, "x = 2")
    latex = r"x = 2\frac{m}{k}"
    assert "symbol_mismatch" in failed_codes(run(element, latex))  # the box alone: a difference
    fix = run(with_glyphs(pdf, element), latex)
    assert failed_codes(fix) == set()
    assert fix.review != "required"


def test_an_extended_reading_with_a_wrong_letter_is_still_a_decision(pdf):
    fix = run(with_glyphs(pdf, span_of(pdf, "x = 2")), r"x = 2\frac{m}{j}")
    assert "symbol_mismatch" in failed_codes(fix)
    assert fix.review == "required"


def test_signs_and_numbers_are_checked_against_the_extent_too(pdf):
    # "x = 2 m/k" read as "x = -2 m/k": a sign the extent shows is not there
    fix = run(with_glyphs(pdf, span_of(pdf, "x = 2")), r"x = -2\frac{m}{k}")
    assert "sign_mismatch" in failed_codes(fix)
    # ... and a reading with the wrong letters never gets that far
    assert "symbol_mismatch" not in failed_codes(fix)


def test_a_bold_italic_vector_read_as_bold_upright_is_written_as_the_page_has_it(pdf):
    # `w` is Times-BoldItalic on the page; the reading says \mathbf (bold upright)
    fix = run(with_glyphs(pdf, span_of(pdf, "w", y=430)), r"\mathbf{w}")
    assert fix.equation is not None and fix.equation.latex == r"\boldsymbol{w}"
    assert failed_codes(fix) == set()


def test_a_bold_upright_vector_read_as_bold_italic_is_a_decision(pdf):
    # `v` is Times-Bold on the page
    fix = run(with_glyphs(pdf, span_of(pdf, "v", y=430)), r"\boldsymbol{v}")
    assert "glyph_style_mismatch" in failed_codes(fix)
    assert fix.review == "required"


def test_an_upright_d_read_as_italic_is_a_decision_and_the_right_reading_is_not(pdf):
    element = with_glyphs(pdf, span_of(pdf, "d", y=430))
    fix = run(element, "d")
    assert "glyph_style_mismatch" in failed_codes(fix)
    assert fix.review == "required"
    right = run(element, r"\mathrm{d}")
    assert failed_codes(right) == set()


def test_without_glyphs_nothing_changes(pdf):
    fix = run(span_of(pdf, "d", y=430), "d")
    assert "glyph_style_mismatch" not in failed_codes(fix)


def test_the_check_is_made_only_for_a_reading_with_a_style_to_compare():
    layer = reading_layer(
        Element(id="e", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=0, text="d"),
        ml("d"),
    )
    assert glyph_style_check(layer, "d") is None
    context = GlyphContext(span=GlyphContext.encode([("d", "upright")]))
    element = Element(
        id="e", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=0, text="d", glyphs=context
    )
    check = glyph_style_check(reading_layer(element, ml("d")), "d")
    assert isinstance(check, Check) and check.code == "glyph_style_mismatch"
    assert check.name == "equation.glyph_style" and not check.passed


# ------------------------------------------------ bold italic from the glyph's font

BOLD_W = "\U0001d430"  # what the text layer says of LMMathItalic10-Bold: bold, not bold italic


def test_a_bold_vector_in_a_bold_italic_face_is_written_boldsymbol():
    from readablestem.pipeline.equation_step import restyle_bold_italic

    glyphs = [(BOLD_W, "bold-italic")]
    assert restyle_bold_italic(r"\mathbf{w}^2", BOLD_W, glyphs) == r"\boldsymbol{w}^2"
    assert restyle_bold_italic(r"\mathbf{w}^2", BOLD_W) == r"\mathbf{w}^2"  # without the glyphs
    assert restyle_bold_italic(r"\mathbf{w}", BOLD_W, [(BOLD_W, "bold")]) == r"\mathbf{w}"


def test_a_lone_bold_letter_in_a_bold_italic_face_is_fixed_without_a_model():
    from readablestem.pipeline import notation_memory

    element = Element(
        id="p1-e1",
        kind=Kind.EQUATION,
        page=0,
        bbox=(0, 0, 9, 9),
        order=0,
        text=BOLD_W,
        display="inline",
        reasons=[notation_memory.BOLD_LETTER_REASON],
    )
    plain = notation_memory.bold_letter_fix(element)
    assert plain is not None and plain.equation is not None
    assert plain.equation.latex == r"\mathbf{w}"
    glyphs = GlyphContext(span=GlyphContext.encode([(BOLD_W, "bold-italic")]))
    fix = notation_memory.bold_letter_fix(element.model_copy(update={"glyphs": glyphs}))
    assert fix is not None and fix.equation is not None
    assert fix.equation.latex == r"\boldsymbol{w}"
