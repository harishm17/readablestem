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

from readablestem.config import CHEAP_MODEL, MAIN_MODEL
from readablestem.core.types import Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline.equation_step import (
    text_layer_style,
    text_layer_style_mismatch,
    transcribe,
)
from readablestem.pipeline.layout import _Char, _regular_letters
from readablestem.pipeline.validate import safe_latex_to_mathml
from test_steps_support import equation_element, make_settings, use_fallback_router

BOLD_D = "\U0001d41d"  # MATHEMATICAL BOLD SMALL D


def mathml(latex: str) -> str:
    return safe_latex_to_mathml(latex, "inline")


# ---------------------------------------------------------------- shared helpers


# ---------------------------------------------------------------- layout


def _char(c: str, font: str, flags: int = 0) -> _Char:
    return _Char(c, 0, 0, 1, 1, baseline=1, size=10, font=font, flags=flags)


def test_layout_writes_bold_font_letters_as_math_bold():
    assert _char("d", "ABCDEF+CMBX10").styled == BOLD_D  # LaTeX \mathbf
    assert _char("d", "DejaVuSerif-Bold").styled == BOLD_D  # Word bold
    assert _char("d", "CMMI10").styled == "d"  # plain math italic
    assert _char("=", "CMBX10").styled == "="  # bold operators have no styled form


# ---------------------------------------------------------------- the check


def test_plain_made_bold_is_a_mismatch():
    layer = f"{BOLD_D} = v t"
    assert text_layer_style_mismatch(layer, mathml(r"\mathbf{d} = \mathbf{v} t")) is True
    assert text_layer_style_mismatch(layer, mathml(r"\mathbf{d} = v t")) is False


def test_no_style_information_is_not_evidence():
    # A text layer without any styled letter says nothing about style.
    assert text_layer_style_mismatch("Δd = 0", mathml(r"\Delta \mathbf{d} = 0")) is None
    assert text_layer_style_mismatch(None, mathml(r"x")) is None


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


# ---------------------------------------------------------------- meaning vs cosmetic

BOLD_OMEGA = "\U0001d6da"  # MATHEMATICAL BOLD SMALL OMEGA
ITALIC_V = "\U0001d463"  # MATHEMATICAL ITALIC SMALL V (Chromium writes plain variables so)


def verdict(layer: str, latex: str, regular: str | None = None):
    return text_layer_style(layer, mathml(latex), regular_letters=regular)


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


def test_meaning_level_difference_needs_a_decision(monkeypatch):
    use_fallback_router(monkeypatch)
    element = equation_element(display="block", text=BOLD_OMEGA)
    fix = transcribe(element, b"png", client=_style_client(r"\omega"), settings=make_settings())
    assert fix.status is Status.NEEDS_REVIEW and fix.review == "required"
    assert [c.code for c in fix.failed_checks] == ["style_mismatch"]
