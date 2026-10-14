"""Bold and bold-italic Greek letters in output MathML are Unicode mathematical bold letters.

MathML Core, which Chromium implements, draws only `mathvariant="normal"`: `\\boldsymbol{\\omega}`
came out of the converter as `<mi mathvariant="bold-italic">ω</mi>` and was drawn as a plain
omega, so "Use bold omega" in the course notation changed nothing a reader could see. Latin
letters were already written as code points (`\\mathbf{d}` is U+1D41D). The converter's
output and the assembler's sanitiser now do the same for Greek. The browser tests draw the
page in Chromium and compare how much ink the letters have.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET

import pytest

from readablestem.core.mathchars import bake_bold_variants, char_variant, styled_char
from readablestem.core.types import Element, EquationFix, Fix, Kind, Status
from readablestem.pipeline.assemble import render_html, sanitize_mathml
from readablestem.pipeline.equation_step import text_layer_style
from readablestem.pipeline.speech import mathml_to_speech
from readablestem.pipeline.validate import safe_latex_to_mathml


def tokens(mathml: str) -> list[tuple[str, str | None]]:
    root = ET.fromstring(mathml)
    return [
        (el.text or "", el.get("mathvariant"))
        for el in root.iter()
        if el.tag.rpartition("}")[2] in {"mi", "mn", "mo"}
    ]


@pytest.mark.parametrize(
    ("latex", "letter", "variant"),
    [
        (r"\boldsymbol{\omega}", "\u03c9", "bold-italic"),
        (r"\bm{\omega}", "\u03c9", "bold-italic"),
        (r"\mathbf{\omega}", "\u03c9", "bold"),
        (r"\boldsymbol{\Omega}", "\u03a9", "bold-italic"),
        (r"\mathbf{\Delta}", "\u0394", "bold"),
        (r"\boldsymbol{\alpha}", "\u03b1", "bold-italic"),
    ],
)
def test_bold_greek_from_latex_is_a_math_bold_code_point_with_no_attribute(latex, letter, variant):
    ((text, attribute),) = tokens(safe_latex_to_mathml(latex, "inline"))
    assert attribute is None
    assert text == styled_char(letter, variant) != letter
    assert char_variant(text) == variant
    assert 0x1D400 <= ord(text) <= 0x1D7FF  # the Mathematical Alphanumeric Symbols block


def test_latin_bold_is_unchanged():
    assert tokens(safe_latex_to_mathml(r"\mathbf{d}", "inline")) == [("\U0001d41d", None)]


def test_plain_greek_and_other_variants_are_left_alone():
    assert tokens(safe_latex_to_mathml(r"\omega", "inline")) == [("\u03c9", None)]
    (blackboard,) = tokens(safe_latex_to_mathml(r"\mathbb{R}", "inline"))
    assert blackboard == ("\u211d", None)
    mono = ET.fromstring('<math><mi mathvariant="monospace">x</mi></math>')
    bake_bold_variants(mono)
    assert mono[0].get("mathvariant") == "monospace" and mono[0].text == "x"


def test_a_mixed_expression_keeps_each_letter_in_its_own_style():
    mathml = safe_latex_to_mathml(r"\boldsymbol{\tau} = I \boldsymbol{\alpha}", "inline")
    texts = [t for t, _ in tokens(mathml)]
    assert texts == [
        styled_char("\u03c4", "bold-italic"),
        "=",
        "I",
        styled_char("\u03b1", "bold-italic"),
    ]
    assert all(attribute is None for _, attribute in tokens(mathml))


def test_the_sanitiser_writes_old_stored_mathml_the_same_way():
    stored = (
        '<math><mstyle mathvariant="bold"><mi>\u03c9</mi><mo>+</mo><mi>x</mi></mstyle>'
        '<mi mathvariant="bold-italic">θ</mi><mi>y</mi></math>'
    )
    shown = sanitize_mathml(stored, display="inline")
    assert shown is not None and "mathvariant" not in shown
    letters = [t for t, _ in tokens(shown)]
    assert [char_variant(t) for t in letters] == ["bold", None, "bold", "bold-italic", None]


def test_inheritance_stops_at_a_container_with_another_variant():
    root = ET.fromstring(
        '<math><mstyle mathvariant="bold"><mi>\u03c9</mi>'
        '<mrow><mstyle mathvariant="normal"><mi>\u03b1</mi></mstyle></mrow></mstyle></math>'
    )
    bake_bold_variants(root)
    letters = [el.text for el in root.iter() if el.tag == "mi"]
    assert [char_variant(t or "") for t in letters] == ["bold", None]


def test_speech_and_the_text_layer_check_read_the_baked_letters():
    mathml = safe_latex_to_mathml(r"\boldsymbol{\omega} = \mathbf{v} / r", "inline")
    assert mathml_to_speech(mathml).startswith("bold omega equals")
    # A PDF whose text layer has the bold omega agrees with the transcription.
    assert text_layer_style(styled_char("\u03c9", "bold-italic") + " = \U0001d42f / r", mathml) in {
        "match",
        "cosmetic",
    }
    # A plain omega in the text layer against a bold transcription differs.
    assert text_layer_style("ω = v / r", mathml, regular_letters="ωvr") == "meaning"


def test_a_bold_omega_fix_reaches_the_student_file_as_a_code_point():
    mathml = safe_latex_to_mathml(r"\boldsymbol{\omega}", "block")
    element = Element(id="p1-e0", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=0)
    fix = Fix(
        element_id="p1-e0", kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=0.99,
        equation=EquationFix(latex=r"\boldsymbol{\omega}", mathml=mathml, speech="x"),
    )  # fmt: skip
    html = render_html(title="T", lang="en", elements=[element], fixes=[fix], crops={})
    shown = html[html.index("<math") : html.index("</math>")]
    assert "mathvariant" not in shown and styled_char("\u03c9", "bold-italic") in shown


# ----------------------------------------------------------------------------- browser


def page(mathml: str) -> str:
    return (
        "<!doctype html><meta charset=utf-8><body style='margin:0;background:#fff'>"
        f"<p style='font-size:80px;margin:20px'>{mathml}</p>"
    )


def ink(mathml: str) -> int:
    """Dark pixels of the letter as Chromium draws it."""
    sync_api = pytest.importorskip("playwright.sync_api")
    pil = pytest.importorskip("PIL.Image")
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error:
            pytest.skip("Playwright's Chromium is not installed")
        try:
            tab = browser.new_page(viewport={"width": 300, "height": 200})
            tab.set_content(page(mathml))
            png = tab.locator("math").screenshot()
        finally:
            browser.close()
    grey = pil.open(io.BytesIO(png)).convert("L")
    return sum(value < 128 for value in grey.tobytes())


def math(token: str) -> str:
    return f'<math xmlns="http://www.w3.org/1998/Math/MathML">{token}</math>'


@pytest.mark.browser
def test_chromium_ignores_the_mathvariant_attribute_on_greek():
    """The premise: if this fails, Chromium now draws the attribute and the code points
    are no longer needed for it (they still read as "bold" to every screen reader)."""
    plain = ink(math("<mi>\u03c9</mi>"))
    attribute = ink(math('<mi mathvariant="bold-italic">ω</mi>'))
    assert attribute == plain


@pytest.mark.browser
@pytest.mark.parametrize(
    "latex", [r"\boldsymbol{\omega}", r"\mathbf{\omega}", r"\bm{\Omega}", r"\boldsymbol{\alpha}"]
)
def test_chromium_draws_bold_greek_from_the_converter_heavier_than_plain(latex):
    plain_latex = latex.replace(r"\boldsymbol", "").replace(r"\mathbf", "").replace(r"\bm", "")
    bold = ink(sanitize_mathml(safe_latex_to_mathml(latex, "inline"), display="inline") or "")
    plain = ink(
        sanitize_mathml(safe_latex_to_mathml(plain_latex.strip("{}"), "inline"), display="inline")
        or ""
    )
    assert bold != plain and bold > plain * 1.05, (bold, plain)


@pytest.mark.browser
def test_chromium_draws_old_stored_bold_greek_heavier_than_plain():
    stored = '<math><mi mathvariant="bold">ω</mi></math>'
    shown = sanitize_mathml(stored, display="inline")
    assert shown is not None
    assert ink(shown) > ink(math("<mi>\u03c9</mi>")) * 1.05
