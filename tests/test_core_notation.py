"""core.notation: one key for a symbol however the instructor or the pipeline spells it."""

from __future__ import annotations

import unicodedata

import pytest

from readablestem.core.mathchars import VARIANT_NAMES, styled_char
from readablestem.core.notation import (
    _TRAITS_OF_VARIANT,
    MAX_SYMBOL_CHARS,
    canonical_from_parts,
    canonical_symbol,
)

BOLD_D = "\U0001d41d"  # MATHEMATICAL BOLD SMALL D
BOLD_CAPITAL_D = "\U0001d403"
BOLD_OMEGA = "\U0001d6da"  # MATHEMATICAL BOLD SMALL OMEGA
BOLD_ITALIC_OMEGA = "\U0001d74e"
BOLD_CAPITAL_OMEGA = "\U0001d6c0"
REALS = "\u211d"  # DOUBLE-STRUCK CAPITAL R
SCRIPT_L = "\u2112"  # SCRIPT CAPITAL L
BOLD_SCRIPT_L = "\U0001d4db"
FRAKTUR_G = "\U0001d524"
BOLD_ZERO = "\U0001d7ce"
INDICATOR_ONE = "\U0001d7d9"  # MATHEMATICAL DOUBLE-STRUCK DIGIT ONE
ARROW = "\u20d7"  # COMBINING RIGHT ARROW ABOVE
HAT = "\u0302"  # COMBINING CIRCUMFLEX ACCENT


@pytest.mark.parametrize(
    ("spelling", "key"),
    [
        # The spellings the course page must understand (one per line of the spec).
        ("bold d", BOLD_D),
        (BOLD_D, BOLD_D),
        (r"\mathbf{d}", BOLD_D),
        (r"\boldsymbol{\omega}", BOLD_OMEGA),
        (r"\vec{v}", "v" + ARROW),
        ("double-struck R", REALS),
        (r"\mathbb{R}", REALS),
        (REALS, REALS),
        ("script L", SCRIPT_L),
        (r"\mathcal{L}", SCRIPT_L),
        # Other ways people write the same symbols.
        ("Bold d", BOLD_D),
        ("bold-d", BOLD_D),
        ("  bold   d  ", BOLD_D),
        ("bold face d", BOLD_D),
        ("boldface d", BOLD_D),
        ("d in bold", BOLD_D),
        ("bold italic d", BOLD_D),
        ("bold D", BOLD_CAPITAL_D),
        ("bold capital d", BOLD_CAPITAL_D),
        (r"\mathbf d", BOLD_D),
        (r"\mathbf {d}", BOLD_D),
        (r"\bm{d}", BOLD_D),
        (r"$\mathbf{d}$", BOLD_D),
        (r"\mathbf{\omega}", BOLD_OMEGA),
        ("bold omega", BOLD_OMEGA),
        (BOLD_ITALIC_OMEGA, BOLD_OMEGA),  # italic is a maths letter's default style
        ("bold Omega", BOLD_CAPITAL_OMEGA),
        ("capital omega in bold", BOLD_CAPITAL_OMEGA),
        ("R with double strike", REALS),
        ("R with a double strike", REALS),
        ("blackboard bold R", REALS),
        ("double struck R", REALS),
        ("bb R", REALS),
        (r"\mathbb R", REALS),
        ("calligraphic L", SCRIPT_L),
        (r"\mathscr{L}", SCRIPT_L),
        ("bold script L", BOLD_SCRIPT_L),
        (BOLD_SCRIPT_L, BOLD_SCRIPT_L),
        ("fraktur g", FRAKTUR_G),
        (r"\mathfrak{g}", FRAKTUR_G),
        ("bold 0", BOLD_ZERO),
        (r"\mathbf{0}", BOLD_ZERO),
        (r"\mathbb{1}", INDICATOR_ONE),
        # Arrows and hats.
        ("vec v", "v" + ARROW),
        ("v with an arrow", "v" + ARROW),
        ("v with an arrow above it", "v" + ARROW),
        (r"\vec v", "v" + ARROW),
        (r"\overrightarrow{v}", "v" + ARROW),
        ("v" + ARROW, "v" + ARROW),
        (r"\vec{\mathbf{v}}", "\U0001d42f" + ARROW),
        (r"\mathbf{\vec{v}}", "\U0001d42f" + ARROW),
        (r"\hat{x}", "x" + HAT),
        ("x hat", "x" + HAT),
        ("hat x", "x" + HAT),
        ("a with a hat", unicodedata.normalize("NFC", "a" + HAT)),
        ("\u0109", "\u0109"),  # c with circumflex, precomposed: the same key as c + hat
        ("c hat", "\u0109"),
        # Plain letters and Greek letters are themselves.
        ("e", "e"),
        (r"\mathrm{d}", "d"),
        ("omega", "\u03c9"),
        ("Omega", "\u03a9"),
        (r"\omega", "\u03c9"),
        (r"\Omega", "\u03a9"),
        (r"\lambda", "\u03bb"),
        ("lambda", "\u03bb"),
        ("\u03d5", "\u03c6"),  # the phi symbol is phi
        (r"\varphi", "\u03c6"),
        (r"\phi", "\u03c6"),
        (r"\varepsilon", "\u03b5"),
        ("\u00b5", "\u03bc"),  # micro sign -> mu
        # Operator symbols.
        (r"\nabla", "\u2207"),
        ("nabla", "\u2207"),
        (r"\partial", "\u2202"),
        ("h-bar", "\u210f"),
        (r"\hbar", "\u210f"),
        ("\u2113", "\u2113"),
        (r"\infty", "\u221e"),
    ],
)
def test_spellings_of_one_symbol_share_a_key(spelling, key):
    assert canonical_symbol(spelling) == key


@pytest.mark.parametrize(
    "spelling",
    [
        "",
        "   ",
        "$$",
        "dx",
        "bold dx",
        r"\mathbf{F}_{net}",
        r"\frac{a}{b}",
        r"\unknown{x}",
        r"\mathbf{",
        r"\mathbf{d",
        r"\mathbf{}",
        r"\mathbf",
        "{d}}",
        "vector v",  # a meaning, not a spelling: bold or an arrow?
        "double-struck omega",  # Unicode has no such letter
        r"\mathbb{\omega}",
        "bold double-struck R",
        "0",  # a plain digit is not notation
        "7",
        "bold nabla",
        r"\vec{\nabla}",
        "v with an arrow and a hat",
        "\u00e9",  # e with an acute accent
        "+",
        "<script>",
        "x" * (MAX_SYMBOL_CHARS + 1),
        "bold " + "d" * 70,
    ],
)
def test_anything_else_is_not_understood(spelling):
    assert canonical_symbol(spelling) is None


def test_not_a_string_is_not_understood():
    assert canonical_symbol(None) is None  # type: ignore[arg-type]
    assert canonical_symbol(42) is None  # type: ignore[arg-type]


def test_keys_are_stable_and_fit_the_database_column():
    for spelling in ("bold d", r"\vec{\mathbf{v}}", "bold script L", "c hat"):
        key = canonical_symbol(spelling)
        assert key is not None and len(key) <= MAX_SYMBOL_CHARS
        assert canonical_symbol(key) == key  # a key is its own key
        assert unicodedata.normalize("NFC", key) == key


def test_every_style_mathchars_knows_has_a_meaning_here():
    assert set(_TRAITS_OF_VARIANT) == set(VARIANT_NAMES)


@pytest.mark.parametrize("letter", ["d", "R", "L", "g", "k"])
@pytest.mark.parametrize("variant", sorted(VARIANT_NAMES))
def test_every_styled_letter_reads_back_to_a_key(letter, variant):
    styled = styled_char(letter, variant)
    if styled == letter:
        pytest.skip("Unicode has no such letter")
    key = canonical_symbol(styled)
    assert key is not None
    # Italic folds into plain and bold italic into bold; the rest keep their style.
    if variant == "italic":
        assert key == letter
    elif variant == "bold-italic":
        assert key == styled_char(letter, "bold")


@pytest.mark.parametrize(
    ("parts", "key"),
    [
        (("d", "bold"), BOLD_D),
        (("\u03c9", "bold-italic"), BOLD_OMEGA),
        (("R", "double-struck"), REALS),
        (("L", "script"), SCRIPT_L),
        (("x", "italic"), "x"),
        (("x", "normal"), "x"),
        (("x", None), "x"),
        (("v", None, "vec"), "v" + ARROW),
        ((BOLD_D, None), BOLD_D),  # the token is already a styled letter
        ((BOLD_D, "bold"), BOLD_D),
        (("v", "bold", "vec"), "\U0001d42f" + ARROW),
    ],
)
def test_parts_from_mathml_give_the_same_keys(parts, key):
    assert canonical_from_parts(*parts) == key


@pytest.mark.parametrize(
    "parts",
    [
        ("dx", "bold"),
        ("d", "glowing"),
        ("\u03c9", "double-struck"),
        ("v", None, "tilde"),
        ("", None),
    ],
)
def test_parts_that_are_not_one_symbol_have_no_key(parts):
    assert canonical_from_parts(*parts) is None


@pytest.mark.parametrize(
    "spelling",
    ["\\hat{\\imath}", "\\hat{i}", "i hat", "\u0131\u0302", "$\\hat\\imath$"],
)
def test_the_unit_vector_i_hat_has_one_key_with_or_without_the_dot(spelling):
    # LaTeX's standard \hat{\imath} (a dotless i under the hat) is i-hat too.
    assert canonical_symbol(spelling) == "î"


def test_a_dotless_i_or_j_from_mathml_is_the_letter():
    assert canonical_from_parts("\u0131", None, "hat") == "\u00ee"
    assert canonical_from_parts("ȷ", None, "hat") == "ĵ"
    assert canonical_symbol("\\vec{\\jmath}") == canonical_symbol("\\vec{j}")
