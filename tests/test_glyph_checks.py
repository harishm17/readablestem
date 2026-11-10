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

from readablestem.core.types import Element, GlyphContext, Kind
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import layout
from readablestem.pipeline import text_layer_compare as tl
from readablestem.pipeline.equation_step import (
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
        ("A", "ABCDEF+LMMathSymbols10-Regular", 6, None),  # calligraphic, flagged italic
        ("a", "ABCDEF+MysteryFont", 0, None),
        ("a", "CourierNew", 8, None),
        ("5", "ABCDEF+CMR10", 0, None),
        ("α", "ABCDEF+CMMI10", 0, None),
    ],
)
def test_the_style_of_a_printed_letter(c, font, flags, style):
    assert glyph_style(char(c, font, flags)) == style


# ------------------------------------------------------------- Element.glyphs


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


def with_glyphs(pdf: bytes, element: Element) -> Element:
    return element.model_copy(update={"glyphs": layout.line_glyphs(pdf, element, (72, 540))})


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


# ------------------------------------------------ bold italic from the glyph's font

BOLD_W = "\U0001d430"  # what the text layer says of LMMathItalic10-Bold: bold, not bold italic


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
