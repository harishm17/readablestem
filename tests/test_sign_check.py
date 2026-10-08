"""Sign and relation check between the PDF text layer and the transcription.

Review gap (2026-10-08): the symbol check compares letters, digits and Greek letters
only, so a dropped or flipped sign was accepted with nothing to review: e^{-λt} read as
e^{λt}, a = -9.8 read as a = 9.8, x ≤ 1 read as x < 1. Harish's decision: sign and
operator differences are meaning-level and need a decision (`sign_mismatch`, review
"required"); multiplication style (× vs · vs juxtaposition) stays out of it except for a
dot product read as a cross product; a lone bold digit stays a cosmetic style note.

Text layers here are what layout really reads: Chromium writes math-italic letters and
U+2212 for a minus, TeX's CMSY minus is U+2212 too, and Chromium prints a vector accent
as a standalone arrow and \\overline as U+2015.
"""

from __future__ import annotations

import json
import unicodedata
from functools import cache
from pathlib import Path

import pytest

from a11y_stem.config import CHEAP_MODEL, MAIN_MODEL
from a11y_stem.core.triage import review_load, tier_reasons
from a11y_stem.core.types import Check, DocumentResult, Element, Kind, Status
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline import equation_step, signs
from a11y_stem.pipeline.equation_step import _symbol_bag, transcribe
from a11y_stem.pipeline.layout import Layout, analyze
from a11y_stem.pipeline.signs import compare_signs, layer_signs
from a11y_stem.pipeline.validate import safe_latex_to_mathml
from a11y_stem.web.messages import explain_check
from test_steps_support import equation_element, make_settings, use_fallback_router

FIXTURES = Path(__file__).parent / "fixtures" / "layout"
LATEX = FIXTURES / "latex"
DEMO_REPORT = Path(__file__).resolve().parents[1] / "src" / "a11y_stem" / "web" / "static" / "demo"

PNG = b"\x89PNG equation crop"
SETTINGS = make_settings()

# Chromium-style text layers (math italic letters, U+2212 minus).
IT = {ch: unicodedata.lookup(f"MATHEMATICAL ITALIC SMALL {ch.upper()}") for ch in "abcdegtxyv"}
IT_N = "\U0001d441"  # italic capital N
IT_LAMBDA = "\U0001d706"
MINUS = "−"


def mathml(latex: str, display: str = "block") -> str:
    return safe_latex_to_mathml(latex, display)  # type: ignore[arg-type]


def differs(text_layer: str | None, latex: str) -> tuple[str, ...] | None:
    result = compare_signs(text_layer, mathml(latex))
    return None if result is None else result.differs


# ------------------------------------------------------------------ flips and drops

FLIPS = [
    pytest.param(f"{IT['e']}{MINUS}{IT['x']}", r"e^{x}", (signs.MINUS,), id="e^-x-read-as-e^x"),
    pytest.param(
        f"{IT_N}= {IT_N}0 {IT['e']}{MINUS}{IT_LAMBDA}{IT['t']}",
        r"N = N_0 e^{\lambda t}",
        (signs.MINUS,),
        id="decay-exponent-sign-dropped",
    ),
    pytest.param(
        f"{IT['a']}= {MINUS}9.8 m/s2", r"a = 9.8\ \text{m/s}^2", (signs.MINUS,), id="-9.8-as-9.8"
    ),
    pytest.param(
        f"{IT['x']}≤1",
        r"x < 1",
        (signs.LESS, signs.LESS_EQUAL),
        id="le-read-as-lt",
    ),
    pytest.param(
        f"{IT['a']}±{IT['b']}", r"a + b", (signs.PLUS, signs.PLUS_MINUS), id="pm-read-as-plus"
    ),
    pytest.param(
        f"{IT['a']}∓{IT['b']}", r"a \pm b", (signs.MINUS_PLUS, signs.PLUS_MINUS), id="mp-as-pm"
    ),
    pytest.param(
        f"{IT['y']}= {IT['a']}{MINUS}{IT['b']}{MINUS}{IT['c']}",
        r"y = a - b c",
        (signs.MINUS,),
        id="one-of-two-minus-signs-missing",
    ),
    pytest.param(
        f"{IT_N}= {IT_N}0{IT['e']}{IT_LAMBDA}{IT['t']}",
        r"N = N_0 e^{-\lambda t}",
        (signs.MINUS,),
        id="minus-added",
    ),
    pytest.param(f"{IT['x']}= 2", r"x \approx 2", (signs.APPROX, signs.EQUALS), id="eq-as-approx"),
    pytest.param(f"{IT['a']}≠{IT['b']}", r"a = b", (signs.EQUALS, signs.NOT_EQUAL), id="ne-as-eq"),
    pytest.param(f"{IT['x']} > 0", r"x \ge 0", (signs.GREATER, signs.GREATER_EQUAL), id="gt-ge"),
    pytest.param("𝐴⇒𝐵", r"A \to B", (signs.IMPLIES, signs.RIGHT_ARROW), id="implies-as-arrow"),
    pytest.param(f"{IT['x']} =", "x", (signs.EQUALS,), id="relation-dropped"),
    # A plain-text page (no maths font): an ASCII hyphen-minus standing alone is a minus.
    pytest.param("a = -9.8 m/s2", r"a = 9.8\ \text{m/s}^2", (signs.MINUS,), id="ascii-minus"),
    # Word turns " - " into an en dash with spaces: still a minus.
    pytest.param("x – y", r"x + y", (signs.MINUS, signs.PLUS), id="spaced-en-dash"),
    # A dot product read as a cross product (bold vectors on both sides of the sign).
    pytest.param(
        "𝐯 = 𝛚 × 𝐫.",
        r"\mathbf{v} = \boldsymbol{\omega} \cdot \mathbf{r}.",
        (signs.VECTOR_PRODUCT,),
        id="cross-product-read-as-dot",
    ),
    pytest.param(
        "𝐮 · 𝐯 = 0", r"\mathbf{u} \times \mathbf{v} = 0", (signs.VECTOR_PRODUCT,), id="dot-as-x"
    ),
]


@pytest.mark.parametrize(("text_layer", "latex", "expected"), FLIPS)
def test_a_sign_or_relation_that_differs_is_found(text_layer, latex, expected):
    assert differs(text_layer, latex) == expected


# ------------------------------------------------------------------ must not flag

SAME = [
    pytest.param(f"{IT['e']}{MINUS}{IT['x']}", r"e^{-x}", id="e^-x"),
    pytest.param(f"{IT['a']}= {MINUS}9.8 m/s2", r"a = -9.8\ \text{m/s}^2", id="-9.8"),
    # A unary minus typed as an ASCII hyphen in the PDF, U+2212 in the MathML.
    pytest.param("a = -9.8 m/s2", r"a = -9.8\ \text{m/s}^2", id="hyphen-minus-vs-u2212"),
    # Hyphens in words: in \text{} they are skipped; in the PDF between two letters they
    # may be a hyphen, so they cannot make a flag.
    pytest.param("time-dependent 𝑣", r"\text{time-dependent } v", id="text-hyphen"),
    pytest.param("time-dependent 𝑣", r"\mathrm{time-dependent}\ v", id="hyphen-read-as-mo"),
    pytest.param(
        f"𝑣(𝑡) = {MINUS}𝑔𝑡 (time-dependent)",
        r"v(t) = -g t \quad \text{(time-dependent)}",
        id="hyphen-and-a-real-minus",
    ),
    # Ranges: TeX's "--" in text mode, an en dash between digits.
    pytest.param("problems 1--3: 𝑥", r"\text{problems 1--3: } x", id="ascii-range"),
    pytest.param("problems 1–3: 𝑥", r"\text{problems 1--3: } x", id="en-dash-range"),
    pytest.param("problems 1–3: 𝑥", r"\text{problems } 1\text{--}3\text{: } x", id="range-split"),
    # Multiplication style: ×, · and juxtaposition are the same for scalars.
    pytest.param("𝐹= 2.0 × 103 N · m", r"F = 2.0 \cdot 10^{3}\ \text{N m}", id="times-as-cdot"),
    pytest.param("2 × 𝑥 = 6", r"2x = 6", id="times-as-juxtaposition"),
    pytest.param("2 · 𝑥 = 6", r"2 \times x = 6", id="cdot-as-times"),
    # Bold vectors with a scalar ×: scientific notation is not a vector product.
    pytest.param("𝛥𝐝 = 1.1 × 102 m", r"\Delta\mathbf{d} = 1.1 \cdot 10^{2}\ \text{m}", id="sci"),
    # An ASCII letter x is never a times sign.
    pytest.param("2 x 3 = 6", r"2 \times 3 = 6", id="letter-x-is-not-times"),
    # Fraction bars are drawn lines: never in the text layer, a / in it is division.
    pytest.param(f"{IT['v']}= 𝛥{IT['x']} 𝛥{IT['t']}", r"v = \frac{\Delta x}{\Delta t}", id="bar"),
    pytest.param(f"{IT['v']}= 𝛥{IT['x']}/ 𝛥{IT['t']}", r"v = \frac{\Delta x}{\Delta t}", id="/"),
    # Chromium prints a vector accent as a standalone arrow, \overline as U+2015 and a
    # tilde accent as ~; TeX leaves combining accents. None of them is a sign.
    pytest.param(
        f"→{IT['d']}= →{IT['d']}{IT['g']}{MINUS} →{IT['d']}0",
        r"\vec{d} = \vec{d}_g - \vec{d}_0",
        id="chromium-vector-arrows",
    ),
    pytest.param(
        f"{IT['d']}\u20d7 = {IT['d']}\u20d7{IT['g']} {MINUS} {IT['d']}\u20d70",
        r"\vec{d} = \vec{d}_g - \vec{d}_0",
        id="combining-vector-arrows",
    ),
    pytest.param(f"―{IT['x']}+ ¯{IT['v']}", r"\overline{x} + \bar{v}", id="overline-and-bar"),
    pytest.param(f"~{IT['x']}~{IT['y']}", r"\tilde{x} \sim y", id="tilde-accent-and-sim"),
    # TeX builds long arrows from a minus (or =) and an arrow.
    pytest.param(f"{IT['x']} {MINUS}→ 0", r"x \longrightarrow 0", id="tex-long-arrow"),
    pytest.param(f"{IT['x']} {MINUS}→ 0", r"x \to 0", id="tex-long-arrow-as-short"),
    pytest.param("𝐴 =⇒ 𝐵", r"A \implies B", id="tex-long-implies"),
    pytest.param("𝐴 ⇐⇒ 𝐵", r"A \iff B", id="tex-iff"),
    pytest.param(f"lim{IT['x']}→{MINUS}∞", r"\lim_{x \to -\infty}", id="arrow-then-minus"),
    # Arrows and relations typed as ASCII; "x<-1" is x < -1, not a left arrow.
    pytest.param("x -> 0", r"x \to 0", id="ascii-arrow"),
    pytest.param("x >= 1", r"x \ge 1", id="ascii-ge"),
    pytest.param("x<-1", r"x < -1", id="ascii-less-than-minus-one"),
    # Variants of one symbol.
    pytest.param(f"{IT['x']}⩽1", r"x \le 1", id="leqslant-vs-le"),
    pytest.param(f"{IT['x']}⁻¹", r"x^{-1}", id="superscript-minus"),
    pytest.param(f"{IT['a']}=\u0338{IT['b']}", r"a \neq b", id="negation-slash"),
    pytest.param(f"{IT['a']} ˙= {IT['b']}", r"a \doteq b", id="decorated-equals"),
    pytest.param("𝐯 = 𝛚 × 𝐫.", r"\mathbf{v} = \boldsymbol{\omega} \times \mathbf{r}.", id="cross"),
]


@pytest.mark.parametrize(("text_layer", "latex"), SAME)
def test_the_same_signs_do_not_flag(text_layer, latex):
    assert differs(text_layer, latex) == ()


def test_a_vector_arrow_chromium_prints_must_stay_an_arrow():
    # Chromium prints \vec's arrow as a standalone → (gold 2.2 eq-008 is "→𝑠"; the real
    # answers wrote \vec{s}). Dropping it reads a vector as a scalar, like a lost bold.
    # Writing the arrow vector in bold instead is flagged too: the prompt asks for \vec
    # for arrows, so a person confirms the notation.
    layer = f"→{IT['x']}"
    assert differs(layer, r"\vec{x}") == ()
    assert differs(layer, r"x") == (signs.RIGHT_ARROW,)
    assert differs(layer, r"\mathbf{x}") == (signs.RIGHT_ARROW,)
    # TeX prints the accent as a combining arrow, which is no token: nothing to compare.
    assert differs(f"{IT['x']}\u20d7", r"\vec{x}") is None


def test_a_dropped_multiplication_sign_between_vectors_is_not_a_swap():
    # Only a swap of × and · is meaning-level: a missing sign may be juxtaposition
    # (a scalar times a vector), which the text layer cannot tell apart.
    assert differs("𝐮 × 𝐯 = 𝐰", r"\mathbf{u}\mathbf{v} = \mathbf{w}") == ()


def test_word_hyphens_never_mask_a_dropped_minus():
    assert differs(
        f"𝑣(𝑡) = {MINUS}𝑔𝑡 (time-dependent)", r"v(t) = g t \quad \text{(time-dependent)}"
    ) == (signs.MINUS,)


def test_nothing_to_compare_without_a_sign_in_the_text_layer():
    # A lone variable says nothing about signs, and neither does a font whose operators
    # came out unnamed (private use): no check at all.
    assert differs(IT["x"], r"-x") is None
    assert differs(f"{IT['a']} \uf02d {IT['b']}", r"a - b") is None
    assert differs(None, r"-x") is None
    assert differs("", r"-x") is None


def test_unnamed_glyphs_widen_but_never_hide_a_dropped_sign():
    # U+F02D is Symbol's minus in a PDF without a ToUnicode map: it may be any sign.
    layer = f"{IT['a']}= \uf02d9.8"
    assert differs(layer, r"a = -9.8") == ()
    assert differs(layer, r"a = 9.8") == ()  # cannot tell: no flag
    assert differs(f"{IT['a']}= {MINUS}9.8 \uf02d", r"a = 9.8") == (signs.MINUS,)


def test_signs_inside_mn_and_hand_written_mathml_count():
    # latex2mathml writes "-9.8" as <mo>−</mo><mn>9.8</mn>; other MathML (a gold file,
    # a hand edit) may put the sign inside <mn> or use an ASCII hyphen in <mo>.
    ns = 'xmlns="http://www.w3.org/1998/Math/MathML"'
    inside = f"<math {ns}><mi>a</mi><mo>=</mo><mn>−9.8</mn></math>"
    ascii_mo = f"<math {ns}><mi>a</mi><mo>=</mo><mo>-</mo><mn>9.8</mn></math>"
    layer = f"{IT['a']}= {MINUS}9.8"
    for got in (inside, ascii_mo):
        result = compare_signs(layer, got)
        assert result is not None and not result.mismatch
    dropped = compare_signs(layer, f"<math {ns}><mi>a</mi><mo>=</mo><mn>9.8</mn></math>")
    assert dropped is not None and dropped.differs == (signs.MINUS,)


def test_layer_tokens_for_the_documented_normalisation():
    tokens = layer_signs(
        "a - b \N{MINUS SIGN} c \N{HYPHEN} d \N{EN DASH} e time-dependent 1\N{EN DASH}3 "
        "x\N{SUPERSCRIPT MINUS}\N{SUPERSCRIPT ONE} x\N{LESS-THAN OR EQUAL TO}"
        "\N{LESS-THAN OR SLANTED EQUAL TO}\N{LESS-THAN OVER EQUAL TO}"
    )
    # ASCII hyphen-minus, minus sign, hyphen and en dash standing alone; superscript minus
    assert tokens.certain[signs.MINUS] == 5
    assert tokens.uncertain[signs.MINUS] == 2  # the word hyphen and the range
    assert tokens.certain[signs.LESS_EQUAL] == 3
    assert signs.CROSS not in layer_signs("2 x 3").certain  # a letter x is not a times sign


# ------------------------------------------------------------------ the equation step


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def out(latex: str, confidence: float = 0.95) -> dict:
    return {"is_equation": True, "latex": latex, "confidence": confidence}


def by_model(**answers) -> FakeVisionClient:
    table = {MAIN_MODEL: answers.get("main"), CHEAP_MODEL: answers.get("cheap")}
    return FakeVisionClient(responders={"equation": lambda request: table[request.model]})


def run(client: FakeVisionClient, element: Element):
    return transcribe(element, PNG, client=client, settings=SETTINGS)


def codes(fix) -> set[str]:
    return {c.code for c in fix.failed_checks if c.code}


DECAY = f"{IT_N}= {IT_N}0 {IT['e']}{MINUS}{IT_LAMBDA}{IT['t']}"


def test_a_sign_mismatch_needs_a_decision_and_lowers_the_confidence():
    fix = run(by_model(main=out(r"N = N_0 e^{\lambda t}")), equation_element(text=DECAY))
    assert codes(fix) == {"sign_mismatch"}
    assert fix.status is Status.NEEDS_REVIEW and fix.review == "required"
    assert fix.confidence == pytest.approx(0.95 * equation_step.MEANING_MISMATCH_FACTOR)
    check = next(c for c in fix.checks if c.name == "equation.text_layer_signs")
    assert check.severity == "warning" and not check.passed
    assert tier_reasons(fix) == ["sign_mismatch"]
    assert fix.equation is not None  # the proposal stays for the reviewer to correct


def test_the_same_signs_pass_with_a_passed_check():
    fix = run(by_model(main=out(r"N = N_0 e^{-\lambda t}")), equation_element(text=DECAY))
    check = next(c for c in fix.checks if c.name == "equation.text_layer_signs")
    assert check.passed and check.code is None
    assert fix.status is Status.ACCEPTED and fix.review == "none"


def test_a_cheap_sign_mismatch_escalates_and_the_main_answer_wins():
    client = by_model(
        cheap=out(r"a = 9.8\ \text{m/s}^2", 0.99), main=out(r"a = -9.8\ \text{m/s}^2")
    )
    fix = run(client, equation_element(display="inline", text=f"{IT['a']}= {MINUS}9.8 m/s2"))
    assert [r.model for r in client.requests] == [CHEAP_MODEL, MAIN_MODEL]
    assert fix.equation is not None and fix.equation.latex == r"a = -9.8\ \text{m/s}^2"
    assert fix.status is Status.ACCEPTED and fix.review == "none"


def test_a_sign_mismatch_left_after_escalation_needs_a_decision():
    client = by_model(cheap=out(r"x < 1", 0.99), main=out(r"x < 1", 0.99))
    fix = run(client, equation_element(display="inline", text=f"{IT['x']}≤1"))
    assert [r.model for r in client.requests] == [CHEAP_MODEL, MAIN_MODEL]
    assert codes(fix) == {"sign_mismatch"} and fix.review == "required"


def test_style_and_sign_mismatch_lower_the_confidence_once():
    # Two meaning-level differences: one factor, so the proposal is not lost to the
    # abstain bar (0.95 * 0.6 * 0.6 = 0.34 would abstain).
    element = equation_element(text="𝐯 = 𝛚 × 𝐫.")
    fix = run(by_model(main=out(r"\mathbf{v} = \omega \cdot \mathbf{r}.")), element)
    assert codes(fix) == {"style_mismatch", "sign_mismatch"}
    assert fix.confidence == pytest.approx(0.95 * equation_step.MEANING_MISMATCH_FACTOR)
    assert fix.status is Status.NEEDS_REVIEW and fix.equation is not None


def test_symbol_and_sign_mismatch_are_both_reported():
    fix = run(by_model(main=out(r"N = N_0 e^{\lambda}")), equation_element(text=DECAY))
    assert codes(fix) == {"symbol_mismatch", "sign_mismatch"}
    assert fix.review == "required"


def test_no_sign_check_against_a_text_layer_that_is_a_different_equation():
    # A strong symbol mismatch: the text layer is no evidence for the signs either.
    fix = run(by_model(main=out(r"E = -mc^2")), equation_element(text="F = ma"))
    assert codes(fix) == {"text_layer_mismatch"}
    assert all(c.name != "equation.text_layer_signs" for c in fix.checks)


def test_no_sign_check_without_letters_or_digits_in_the_text_layer():
    fix = run(by_model(main=out("x = -1")), equation_element(text="= −"))
    assert all(c.name != "equation.text_layer_signs" for c in fix.checks)
    assert fix.status is Status.ACCEPTED


def test_a_lone_bold_zero_vector_stays_a_cosmetic_note():
    # Policy: digits are cosmetic, the zero vector too; the signs agree, so no decision.
    fix = run(
        by_model(main=out(r"\mathbf{F}_{\text{net}} = 0")),
        equation_element(text="\U0001d405net = \U0001d7ce"),
    )
    assert codes(fix) == {"style_cosmetic"}
    assert fix.status is Status.ACCEPTED and fix.review == "optional"


def test_a_cosmetic_product_style_is_not_even_a_note():
    fix = run(
        by_model(main=out(r"F = 2.0 \cdot 10^{3}\ \text{N}")),
        equation_element(text="𝐹= 2.0 × 103 N"),
    )
    assert codes(fix) == set()
    assert fix.status is Status.ACCEPTED and fix.review == "none"


def test_the_reviewer_reads_a_plain_sentence():
    explained = explain_check(
        Check(name="equation.text_layer_signs", passed=False, code="sign_mismatch")
    )
    assert explained.known
    assert "minus" in explained.text and "sign_mismatch" in explained.code


def test_results_stored_before_the_sign_check_still_load():
    # The committed demo was converted with pipeline 0.2.0 (no sign check): it loads,
    # keeps its tiers, and every failed check still has a sentence.
    stored = json.loads((DEMO_REPORT / "report.json").read_text(encoding="utf-8"))["result"]
    result = DocumentResult.model_validate(stored)
    assert result.pipeline_version == "0.2.0"
    assert all(c.name != "equation.text_layer_signs" for f in result.fixes for c in f.checks)
    load = review_load(result.fixes)
    assert "sign_mismatch" not in load.required_by_reason
    assert all(explain_check(c).known for f in result.fixes for c in f.failed_checks)


# ------------------------------------------------------------------ PDF level: LaTeX


@cache
def layout_of(path: Path) -> Layout:
    return analyze(path.read_bytes())


def equation(layout: Layout, text: str) -> Element:
    """The equation whose text layer is `text` (spaces ignored)."""
    squeeze = "".join(text.split())
    found = [
        e
        for e in layout.elements
        if e.kind == Kind.EQUATION and "".join((e.text or "").split()) == squeeze
    ]
    assert len(found) == 1, (text, [e.text for e in layout.elements if e.kind == Kind.EQUATION])
    return found[0]


# (fixture, text layer as layout reads it, the right LaTeX, a sign error)
LATEX_CASES = [
    (
        "calculus_notes",
        "f(a + h) −f(a) f′(a) = lim h→0 h",
        r"f'(a) = \lim_{h \to 0} \frac{f(a + h) - f(a)}{h}",
        r"f'(a) = \lim_{h \to 0} \frac{f(a + h) + f(a)}{h}",
    ),
    (
        "calculus_notes",
        "dxdxn = nxn−1",
        r"\frac{d}{dx} x^n = n x^{n-1}",
        r"\frac{d}{dx} x^n = n x^{n+1}",
    ),
    (
        "calculus_notes",
        "∫ab f(x) dx = F(b) −F(a).",
        r"\int_a^b f(x)\,dx = F(b) - F(a).",
        r"\int_a^b f(x)\,dx = F(b) F(a).",
    ),
    (
        "calculus_notes",
        "n ∑ f(xi) ∆x ≈ ∫abf(x) dx. i=1",
        r"\sum_{i=1}^{n} f(x_i)\,\Delta x \approx \int_a^b f(x)\,dx.",
        r"\sum_{i=1}^{n} f(x_i)\,\Delta x = \int_a^b f(x)\,dx.",
    ),
    ("calculus_notes", "e −1", r"e - 1", r"e + 1"),
    ("calculus_notes", "|r| < 1", r"\lvert r \rvert < 1", r"\lvert r \rvert \le 1"),
    (
        # The cases brace comes out as private-use glyphs (CMEX pieces): they widen the
        # ranges, but the dropped minus of "-1" is still certain.
        "calculus_notes",
        "\uf8f1 \uf8f21 x > 0 \uf8f4 sgn(x) = 0 x = 0 \uf8f4 \uf8f3 −1 x < 0",
        r"\operatorname{sgn}(x) = \begin{cases} 1 & x > 0 \\ 0 & x = 0 \\ -1 & x < 0 \end{cases}",
        r"\operatorname{sgn}(x) = \begin{cases} 1 & x > 0 \\ 0 & x = 0 \\ 1 & x < 0 \end{cases}",
    ),
    (
        "problem_set",
        "¯v = ∆x ∆t = xf −x0 tf −t0 .",
        r"\bar{v} = \frac{\Delta x}{\Delta t} = \frac{x_f - x_0}{t_f - t_0}.",
        r"\bar{v} = \frac{\Delta x}{\Delta t} = \frac{x_f - x_0}{t_f t_0}.",
    ),
    ("problem_set", "g = 9.8 m/s2", r"g = 9.8\ \mathrm{m/s^2}", r"g = -9.8\ \mathrm{m/s^2}"),
    (
        "problem_set",
        "v2 = v20 + 2ad = 0 + 2 3.0 m/s2) (12 m) ( = 72 m2/s2.",
        r"\begin{array}{rcl} v^2 & = & v_0^2 + 2 a d \\ & = & 0 + 2 \left(3.0\ \mathrm{m/s^2}"
        r"\right)\left(12\ \mathrm{m}\right) \\ & = & 72\ \mathrm{m^2/s^2}. \end{array}",
        r"\begin{array}{rcl} v^2 & = & v_0^2 + 2 a d \\ & = & 0 - 2 \left(3.0\ \mathrm{m/s^2}"
        r"\right)\left(12\ \mathrm{m}\right) \\ & = & 72\ \mathrm{m^2/s^2}. \end{array}",
    ),
    (
        "kinematics_slides",
        "v2 = v20 + 2a (x −x0) .",
        r"v^2 = v_0^2 + 2a\left(x - x_0\right).",
        r"v^2 = v_0^2 - 2a\left(x - x_0\right).",
    ),
    (
        "linear_algebra_twocol",
        "A = , det A = −2. ( ) 1 2 3 4",
        r"A = \begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}, \quad \det A = -2.",
        r"A = \begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}, \quad \det A = 2.",
    ),
    ("linear_algebra_twocol", "det A ≠ 0", r"\det A \neq 0", r"\det A = 0"),
    (
        "linear_algebra_twocol",
        "𝐱 = A−1𝐛",
        r"\mathbf{x} = A^{-1}\mathbf{b}",
        r"\mathbf{x} = A^{1}\mathbf{b}",
    ),
    (
        "linear_algebra_twocol",
        "det(A −λI) = 0",
        r"\det(A - \lambda I) = 0",
        r"\det(A + \lambda I) = 0",
    ),
    (
        "linear_algebra_twocol",
        "λ2 −5λ −2 = 0,",
        r"\lambda^2 - 5\lambda - 2 = 0,",
        r"\lambda^2 - 5\lambda + 2 = 0,",
    ),
    (
        "linear_algebra_twocol",
        "λ = 5±√233",
        r"\lambda = \frac{5 \pm \sqrt{33}}{2}",
        r"\lambda = \frac{5 + \sqrt{33}}{2}",
    ),
    (
        "linear_algebra_twocol",
        "𝐯 = 𝛚 × 𝐫.",
        r"\mathbf{v} = \boldsymbol{\omega} \times \mathbf{r}.",
        r"\mathbf{v} = \boldsymbol{\omega} \cdot \mathbf{r}.",
    ),
]


@pytest.mark.parametrize(
    ("name", "text", "right", "wrong"),
    LATEX_CASES,
    ids=[f"{case[0]}:{case[1][:16]}" for case in LATEX_CASES],
)
def test_latex_pdfs_cmsy_signs(name, text, right, wrong):
    element = equation(layout_of(LATEX / f"{name}.pdf"), text)
    # The text layer is trustworthy: the right answer has the same letters and digits.
    assert equation_step.text_layer_symbols_differ(element.text, mathml(right)) is False
    assert differs(element.text, right) == ()
    wrong_signs = differs(element.text, wrong)
    assert wrong_signs, wrong


def test_latex_pdf_sign_error_through_the_step():
    element = equation(layout_of(LATEX / "linear_algebra_twocol.pdf"), "λ2 −5λ −2 = 0,")
    fix = run(by_model(main=out(r"\lambda^2 - 5\lambda + 2 = 0,")), element)
    assert codes(fix) == {"sign_mismatch"} and fix.review == "required"
    right = run(by_model(main=out(r"\lambda^2 - 5\lambda - 2 = 0,")), element)
    assert right.status is Status.ACCEPTED and right.review == "none"


# ------------------------------------------------------------------ PDF level: Chromium

CHROMIUM_CASES = [
    ("lecture_notes", "−3 m", r"-3\ \text{m}", r"3\ \text{m}"),
    ("lecture_notes", "𝛥𝑥= 𝑥𝑓−𝑥0", r"\Delta x = x_f - x_0", r"\Delta x = x_f + x_0"),
    (
        "lecture_notes",
        "𝑣= 𝛥𝑥/ 𝛥𝑡",
        r"v = \frac{\Delta x}{\Delta t}",
        r"v = -\frac{\Delta x}{\Delta t}",
    ),
    (
        "lecture_notes",
        "𝑣= 𝛥𝑥 𝛥𝑡",
        r"v = \frac{\Delta x}{\Delta t}",
        r"v \approx \frac{\Delta x}{\Delta t}",
    ),
    (
        "worked_example",
        "𝑣𝑓= 𝑣0 + 𝑎𝑡 = 0 + 2 × 3 = 6",
        r"v_f = v_0 + at = 0 + 2 \cdot 3 = 6",  # \cdot for ×: cosmetic, no flag
        r"v_f = v_0 - at = 0 + 2 \times 3 = 6",
    ),
]


@pytest.mark.parametrize(("name", "text", "right", "wrong"), CHROMIUM_CASES)
def test_chromium_printed_pdfs(name, text, right, wrong):
    element = equation(layout_of(FIXTURES / f"{name}.pdf"), text)
    assert differs(element.text, right) == ()
    assert differs(element.text, wrong)


# A course page printed by Chromium on the fly: inline and display MathML among prose with
# word hyphens and an en-dash range, as an instructor's HTML export would have.
PAGE_CASES = [
    ("block", r"N = N_0 e^{-\lambda t}", r"N = N_0 e^{\lambda t}"),
    ("inline", r"a = -9.8\ \text{m/s}^2", r"a = 9.8\ \text{m/s}^2"),
    ("block", r"0 < x \le 1", r"0 < x < 1"),
    ("block", r"x = a \pm b", r"x = a + b"),
    ("block", r"y = a - b - c", r"y = a - b c"),
    ("block", r"\vec{d} = \vec{d}_f - \vec{d}_0", r"\vec{d} = \vec{d}_f + \vec{d}_0"),
    ("block", r"F = 2.0 \times 10^{3}\ \text{N}", None),
    ("block", r"\overline{x} = \frac{x_1 + x_2}{2}", r"\overline{x} = \frac{x_1 - x_2}{2}"),
    (
        "block",
        r"v(t) = -g t \quad \text{(time-dependent)}",
        r"v(t) = g t \quad \text{(time-dependent)}",
    ),
]


def _page_html() -> str:
    parts = []
    for i, (display, latex, _) in enumerate(PAGE_CASES):
        math = mathml(latex, display)
        if display == "inline":
            parts.append(
                f"<p>Case {i}: a stone has acceleration {math} in a time-dependent model.</p>"
            )
        else:
            parts.append(f"<p>Case {i}: the time-dependent relation for problems 1–3 is</p>{math}")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Signs</title><style>'
        'body { font-family: "Liberation Serif", "DejaVu Serif", serif; font-size: 12pt; }'
        'math { font-family: "Noto Sans Math", "STIX Two Math", "Latin Modern Math", math; }'
        'math[display="block"] { margin: 6pt 0; }'
        "</style></head><body><h1>Signs and relations</h1>" + "".join(parts) + "</body></html>"
    )


@pytest.mark.browser
def test_a_chromium_printed_course_page():
    from a11y_stem.eval.render import ChromiumRenderer

    with ChromiumRenderer() as renderer:
        pdf = renderer.pdf(_page_html())
    equations = [e for e in analyze(pdf).elements if e.kind == Kind.EQUATION]
    for display, right, wrong in PAGE_CASES:
        expected = equation_step._mathml_symbols(mathml(right, display))
        found = [e for e in equations if _symbol_bag(e.text or "") == expected]
        assert len(found) == 1, (right, [e.text for e in equations])
        (element,) = found
        assert element.display == display
        assert differs(element.text, right) == (), (element.text, right)
        if wrong is not None:
            assert differs(element.text, wrong), (element.text, wrong)
    decay = next(e for e in equations if _symbol_bag(e.text or "") == _symbol_bag("NN0eλt"))
    fix = run(by_model(main=out(r"N = N_0 e^{\lambda t}")), decay)
    assert codes(fix) == {"sign_mismatch"} and fix.review == "required"
