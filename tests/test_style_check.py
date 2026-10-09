"""Letter-style check between the PDF text layer and the transcription.

Error analysis of the first real eval (2026-10-08): with correct rendering, 5 of the 7
real model misses were bold vectors read as plain scalars by the cheap model, all
auto-accepted at confidence >= 0.88. The text layer already knows the letter is bold, so
a deterministic comparison catches it before any model judge would.

Review triage (2026-10-08, "meaning-level only"): of four style flags on two real
documents only one mattered (a bold omega read as a plain omega). A bold subscript or
digit, and a double-struck R the text layer cannot show (MSBM), are cosmetic: an
optional note, no lower confidence, no decision.

Review of the triage (2026-10-08): a bold vector in the exponent or limit of a plain
base (e^{i k.r}, lim_{x -> 0} with bold k, r, x) is a vector of its own, not the label of
a bold symbol, and a double-struck or script letter the text layer does encode (the
reals, a Lagrangian, from Chromium, Word, unicode-math) read as a plain letter is a
different symbol: both meaning-level. A
cosmetic difference on a cheap-model answer still escalates to the main model, as a
second reading of something the heuristic might have under-rated.
"""

from __future__ import annotations

from a11y_stem.config import CHEAP_MODEL, MAIN_MODEL
from a11y_stem.core.mathchars import base_char, char_variant, styled_char
from a11y_stem.core.types import Status
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline import equation_step
from a11y_stem.pipeline.equation_step import (
    text_layer_style,
    text_layer_style_mismatch,
    transcribe,
)
from a11y_stem.pipeline.layout import _Char, _regular_letters
from a11y_stem.pipeline.validate import safe_latex_to_mathml
from test_steps_support import equation_element, make_settings, use_fallback_router

BOLD_D = "\U0001d41d"  # MATHEMATICAL BOLD SMALL D


def mathml(latex: str) -> str:
    return safe_latex_to_mathml(latex, "inline")


# ---------------------------------------------------------------- shared helpers


def test_styled_char_round_trips_through_variant_and_base():
    for ch, variant in [("d", "bold"), ("D", "bold"), ("0", "bold"), ("Δ", "bold"),
                        ("R", "double-struck"), ("L", "script"), ("h", "italic")]:  # fmt: skip
        styled = styled_char(ch, variant)
        assert styled != ch
        assert char_variant(styled) == variant
        assert base_char(styled) == ch


def test_unstyleable_characters_are_unchanged():
    assert styled_char("+", "bold") == "+"
    assert char_variant("d") is None and base_char("d") == "d"


# ---------------------------------------------------------------- layout


def _char(c: str, font: str, flags: int = 0) -> _Char:
    return _Char(c, 0, 0, 1, 1, baseline=1, size=10, font=font, flags=flags)


def test_layout_writes_bold_font_letters_as_math_bold():
    assert _char("d", "ABCDEF+CMBX10").styled == BOLD_D  # LaTeX \mathbf
    assert _char("d", "DejaVuSerif-Bold").styled == BOLD_D  # Word bold
    assert _char("d", "CMMI10").styled == "d"  # plain math italic
    assert _char("=", "CMBX10").styled == "="  # bold operators have no styled form


# ---------------------------------------------------------------- the check


def test_bold_vector_read_as_plain_is_a_mismatch():
    assert text_layer_style_mismatch(f"Δ{BOLD_D} = 0", mathml(r"\Delta d = 0")) is True


def test_bold_kept_bold_passes():
    assert text_layer_style_mismatch(f"Δ{BOLD_D} = 0", mathml(r"\Delta \mathbf{d} = 0")) is False


def test_plain_made_bold_is_a_mismatch():
    layer = f"{BOLD_D} = v t"
    assert text_layer_style_mismatch(layer, mathml(r"\mathbf{d} = \mathbf{v} t")) is True
    assert text_layer_style_mismatch(layer, mathml(r"\mathbf{d} = v t")) is False


def test_no_style_information_is_not_evidence():
    # A text layer without any styled letter says nothing about style.
    assert text_layer_style_mismatch("Δd = 0", mathml(r"\Delta \mathbf{d} = 0")) is None
    assert text_layer_style_mismatch(None, mathml(r"x")) is None


def test_letters_only_on_one_side_are_ignored():
    # The text layer may miss or reorder glyphs; only shared letters are compared.
    assert (
        text_layer_style_mismatch(f"{BOLD_D}", mathml(r"\mathbf{d} = \mathbf{d}_0 + vt")) is False
    )


# ---------------------------------------------------------------- the step


def test_cheap_model_dropping_bold_escalates_and_main_model_wins(monkeypatch):
    use_fallback_router(monkeypatch)
    element = equation_element(display="inline", text=f"Δ{BOLD_D}")
    answers = {
        CHEAP_MODEL: {"is_equation": True, "latex": r"\Delta d", "confidence": 0.99},
        MAIN_MODEL: {"is_equation": True, "latex": r"\Delta \mathbf{d}", "confidence": 0.97},
    }
    client = FakeVisionClient(responders={"equation": lambda request: answers[request.model]})
    fix = transcribe(element, b"png", client=client, settings=make_settings())
    assert [c.model for c in fix.calls] == [CHEAP_MODEL, MAIN_MODEL]
    assert fix.equation is not None and fix.equation.latex == r"\Delta \mathbf{d}"
    assert fix.status == Status.ACCEPTED


def test_main_model_dropping_bold_goes_to_review(monkeypatch):
    use_fallback_router(monkeypatch)
    element = equation_element(display="block", text=f"Δ{BOLD_D} = 0")
    client = FakeVisionClient(
        defaults={"equation": {"is_equation": True, "latex": r"\Delta d = 0", "confidence": 0.99}}
    )
    fix = transcribe(element, b"png", client=client, settings=make_settings())
    assert fix.status == Status.NEEDS_REVIEW
    assert fix.confidence is not None
    assert fix.confidence < 0.99 * equation_step.MEANING_MISMATCH_FACTOR + 1e-9
    assert any(c.code == "style_mismatch" for c in fix.failed_checks)
    assert fix.review == "required"


# ---------------------------------------------------------------- meaning vs cosmetic

BOLD_OMEGA = "\U0001d6da"  # MATHEMATICAL BOLD SMALL OMEGA
ITALIC_V = "\U0001d463"  # MATHEMATICAL ITALIC SMALL V (Chromium writes plain variables so)


def verdict(layer: str, latex: str, regular: str | None = None):
    return text_layer_style(layer, mathml(latex), regular_letters=regular)


def test_a_bold_vector_read_as_a_plain_variable_is_meaning_level():
    assert verdict(BOLD_OMEGA, r"\omega") == "meaning"  # LaTeX handout, bold omega
    assert verdict(f"{BOLD_D} = {BOLD_D}0", r"\mathbf{d} = d_0") == "meaning"  # one of two
    assert verdict("\U0001d42fvg", r"v_{\text{vg}}") == "meaning"  # base v, not the word


def test_a_letter_known_to_be_plain_made_bold_is_meaning_level():
    # Known plain: a math italic code point, or a letter layout found in a regular font
    # with a bold face (Element.regular_letters, e.g. CMMI in LaTeX).
    assert verdict(f"distance{ITALIC_V}avg = time", r"\mathbf{v}_{\text{avg}}") == "meaning"
    assert verdict("v = at", r"\mathbf{v} = at", regular="atv") == "meaning"
    # A vector's component u_i read as a vector, next to the vector u itself.
    layer = "\U0001d42e \u00b7 \U0001d42f = \u2211nuivi. i=1"
    assert verdict(layer, r"\mathbf{u} \cdot \mathbf{v} = \sum_{i=1}^n u_i v_i", "inuv") == "match"
    assert (
        verdict(layer, r"\mathbf{u} \cdot \mathbf{v} = \sum_{i=1}^n \mathbf{u}_i v_i", "inuv")
        == "meaning"
    )


def test_unknown_plain_made_bold_is_only_cosmetic():
    # An ASCII "v" from a font layout does not know says nothing about weight.
    assert verdict(f"{BOLD_D} = v t", r"\mathbf{d} = \mathbf{v} t") == "cosmetic"


def test_scripts_digits_words_and_unencodable_styles_are_cosmetic():
    assert verdict(f"{BOLD_D}\U0001d7ce", r"\mathbf{d}_0") == "cosmetic"  # bold subscript 0
    assert verdict(f"{BOLD_D}\U0001d41f", r"\mathbf{d}_{\text{f}}") == "cosmetic"  # bold f
    # MSBM's double-struck R is a plain "R" in the text layer.
    assert verdict("\U0001d42f \u2208Rn", r"\mathbf{v} \in \mathbb{R}^{n}", "n") == "cosmetic"
    # OpenStax eq 2.2-007: v_avg with "avg" bold in the PDF, plain in the transcription.
    bold_vavg = "\U0001d42f\U0001d41a\U0001d42f\U0001d420"
    layer = f"displacement \U0001d6e5{BOLD_D} {BOLD_D}f \u2212{BOLD_D}0{bold_vavg}= time"
    latex = r"\mathbf{v}_{\text{avg}} = \frac{\text{displacement}}{\text{time}} = \Delta \mathbf{d}"
    assert verdict(layer, latex) == "cosmetic"


def test_a_bold_vector_in_the_script_of_a_plain_base_is_meaning_level():
    # Not the label of a bold symbol (d_f) but a vector of its own: the wave vector in a
    # plane wave's exponent, the point of a limit, the index of a sum.
    bold_k, bold_r, bold_x = "\U0001d424", "\U0001d42b", "\U0001d431"
    plane_wave = f"\U0001d452\U0001d456{bold_k}·{bold_r}"  # e^{i k.r}, Chromium-style
    assert verdict(plane_wave, r"e^{i k \cdot r}") == "meaning"
    assert verdict(plane_wave, r"e^{i \mathbf{k} \cdot \mathbf{r}}") == "match"
    assert verdict(f"lim{bold_x}\u21920", r"\lim_{x \to 0}") == "meaning"
    assert verdict(f"∑{bold_k}{bold_k}", r"\sum_{k} \mathbf{k}") == "meaning"
    # The labels of a bold symbol stay cosmetic however they are written.
    assert verdict(f"{BOLD_D}\U0001d41f", r"{\mathbf{d}}_{f}") == "cosmetic"
    assert verdict(f"{BOLD_D}\U0001d41f", r"\mathbf{d_f}") == "match"


def test_a_bold_digit_stays_cosmetic_even_alone():
    # Policy: digits are cosmetic. A lone bold zero (the zero vector) read as 0 is a note;
    # on a cheap-model answer it escalates (test_cosmetic_difference_..._escalates).
    assert verdict("\U0001d405net = \U0001d7ce", r"\mathbf{F}_{\text{net}} = 0") == "cosmetic"


def test_an_encoded_double_struck_or_script_letter_read_plain_is_meaning_level():
    # Chromium, Word and unicode-math write these as their own code points, so the text
    # layer knows the symbol: the reals read as a variable R, a Lagrangian read as L.
    italic_x, italic_f, italic_n = "\U0001d465", "\U0001d453", "\U0001d45b"
    assert verdict(f"{italic_x}\u2208\u211d", r"x \in R") == "meaning"
    assert verdict(f"{italic_x}\u2208\u211d", r"x \in \mathbb{R}") == "match"
    assert verdict(f"{italic_f}:\u211d{italic_n}\u2192\u211d", r"f: R^n \to R") == "meaning"
    assert verdict("\U0001d446=\u222b\u2112\U0001d451\U0001d461", r"S = \int L\,dt") == "meaning"
    assert verdict("\U0001d446=\u222b\u2112\U0001d451\U0001d461", r"S = \int \mathcal{L}\,dt") == (
        "match"
    )
    assert verdict("\U0001d524", r"g") == "meaning"  # fraktur g (a Lie algebra)
    # The other direction stays cosmetic: MSBM's R is a plain "R" in the text layer.
    assert verdict(f"{italic_x}\u2208R", r"x \in \mathbb{R}") == "cosmetic"


def test_the_old_any_difference_helper_still_reports_cosmetic_ones():
    assert text_layer_style_mismatch(f"{BOLD_D}\U0001d7ce", mathml(r"\mathbf{d}_0")) is True
    assert text_layer_style_mismatch(BOLD_D, mathml(r"\mathbf{d}")) is False


def test_known_plain_letters_without_any_bold_still_get_a_check():
    assert verdict("v = at", r"v = at", regular="atv") == "match"
    assert verdict("v = at", r"v = at") is None  # nothing known about weight at all


def test_layout_knows_which_letters_are_plain():
    assert _char("v", "ABCDEF+CMMI10").known_regular  # LaTeX maths italic
    assert _char("x", "TimesNewRomanPSMT").known_regular
    assert not _char("v", "CMBX10").known_regular  # bold
    assert not _char("v", "DejaVuSerif", flags=16).known_regular  # bold flag
    assert not _char("R", "MSBM10").known_regular  # double-struck, style unknown
    assert not _char("F", "CMSY10").known_regular  # calligraphic
    assert not _char("x", "F12").known_regular  # unknown font
    assert not _char(BOLD_D, "STIXTwoMath").known_regular  # the code point says bold
    assert not _char("2", "CMR10").known_regular  # digits are never variables
    chars = [_char("v", "CMMI10"), _char("R", "MSBM10"), _char("n", "CMMI10"),
             _char("v", "CMBX10")]  # fmt: skip
    assert _regular_letters(chars) == "nv"
    assert _regular_letters([_char("R", "MSBM10")]) is None


def _style_client(latex: str, confidence: float = 0.97):
    return FakeVisionClient(
        defaults={"equation": {"is_equation": True, "latex": latex, "confidence": confidence}}
    )


def test_cosmetic_difference_is_accepted_with_a_note(monkeypatch):
    use_fallback_router(monkeypatch)
    element = equation_element(display="inline", text=f"{BOLD_D}\U0001d7ce")
    fix = transcribe(
        element, b"png", client=_style_client(r"\mathbf{d}_0"), settings=make_settings()
    )
    # The main model is asked too (a second reading), and says the same: still a note.
    assert [c.model for c in fix.calls] == [CHEAP_MODEL, MAIN_MODEL]
    assert fix.status is Status.ACCEPTED and fix.review == "optional"
    assert fix.confidence == 0.97  # not lowered
    assert [c.code for c in fix.failed_checks] == ["style_cosmetic"]


def test_cosmetic_difference_on_the_cheap_model_escalates_and_the_main_answer_wins(
    monkeypatch,
):
    # The cheap model wrote the bold subscript 0 plain (cosmetic): the main model still
    # gets a second look, and its answer is kept.
    use_fallback_router(monkeypatch)
    element = equation_element(display="inline", text=f"{BOLD_D}\U0001d7ce")
    answers = {
        CHEAP_MODEL: {"is_equation": True, "latex": r"\mathbf{d}_0", "confidence": 0.99},
        MAIN_MODEL: {"is_equation": True, "latex": r"\mathbf{d}_\mathbf{0}", "confidence": 0.95},
    }
    client = FakeVisionClient(responders={"equation": lambda request: answers[request.model]})
    fix = transcribe(element, b"png", client=client, settings=make_settings())
    assert [c.model for c in fix.calls] == [CHEAP_MODEL, MAIN_MODEL]
    assert fix.equation is not None and fix.equation.latex == r"\mathbf{d}_\mathbf{0}"
    assert fix.status is Status.ACCEPTED and fix.review == "none"


def test_meaning_level_difference_needs_a_decision(monkeypatch):
    use_fallback_router(monkeypatch)
    element = equation_element(display="block", text=BOLD_OMEGA)
    fix = transcribe(element, b"png", client=_style_client(r"\omega"), settings=make_settings())
    assert fix.status is Status.NEEDS_REVIEW and fix.review == "required"
    assert [c.code for c in fix.failed_checks] == ["style_mismatch"]


def test_regular_letters_reach_the_check_from_the_element(monkeypatch):
    use_fallback_router(monkeypatch)
    element = equation_element(display="block", text="v = at")
    element.regular_letters = "atv"
    fix = transcribe(
        element, b"png", client=_style_client(r"\mathbf{v} = at"), settings=make_settings()
    )
    assert fix.status is Status.NEEDS_REVIEW and fix.review == "required"


def test_a_clean_transcription_has_nothing_to_review(monkeypatch):
    use_fallback_router(monkeypatch)
    element = equation_element(display="block", text=f"{BOLD_D} = 0")
    fix = transcribe(
        element, b"png", client=_style_client(r"\mathbf{d} = 0"), settings=make_settings()
    )
    assert fix.status is Status.ACCEPTED and fix.review == "none"
