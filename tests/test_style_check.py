"""Letter-style check between the PDF text layer and the transcription.

Error analysis of the first real eval (2026-10-08): with correct rendering, 5 of the 7
real model misses were bold vectors read as plain scalars by the cheap model, all
auto-accepted at confidence >= 0.88. The text layer already knows the letter is bold, so
a deterministic comparison catches it before any model judge would.
"""

from __future__ import annotations

from a11y_stem.config import CHEAP_MODEL, MAIN_MODEL
from a11y_stem.core.mathchars import base_char, char_variant, styled_char
from a11y_stem.core.types import Status
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline import equation_step
from a11y_stem.pipeline.equation_step import text_layer_style_mismatch, transcribe
from a11y_stem.pipeline.layout import _Char
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
    assert fix.confidence < 0.99 * equation_step.STYLE_MISMATCH_FACTOR + 1e-9
    assert any(c.code == "style_mismatch" for c in fix.failed_checks)
