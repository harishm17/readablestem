"""MathML -> plain-English speech text. Deterministic.

Used on the review screen and in the report so a reviewer can read what a screen reader
would roughly say. The output HTML keeps MathML only (screen readers voice it natively);
speech is never injected as aria-label, which would hide the maths structure.

The input goes through the scorer's normaliser (`eval.equations.normalize`) first, so
OpenStax-style and latex2mathml-style markup read the same: `mstyle` bold becomes a bold
token, math alphanumerics (U+1D41D) become a letter plus `mathvariant`, `mfenced` becomes
explicit fences, and spacing elements disappear. The rules then follow ClearSpeak-style
conventions, kept short:

- fractions: "a over b" when both parts are one or two plain tokens, otherwise
  "the fraction ... over ... end fraction";
- powers: "x squared", "x cubed", "x to the power n" ("... end power" when compound);
- subscripts: "x sub 0", "v sub avg" ("... end sub" when compound);
- large operators: "the sum from i equals 1 to n of", "the limit as x approaches 0 of";
- accents: an arrow over a symbol is "vector d", a bold symbol is "bold d";
- course notation: a styled letter the instructor's course gives a meaning reads with the
  meaning in place of its style ("vector d" for a bold d the course calls a vector; see
  `readings` below and `pipeline.notation_memory`);
- tables: "row 1: ...; row 2: ..." (cells of an aligned equation read as one line);
- matrices: a table inside parentheses or brackets reads "the 2 by 2 matrix; row 1: a, b;
  row 2: c, d; end matrix" (bars: "determinant"; a column or row of numbers: "column
  matrix", "row matrix");
- cases: a table after an opening brace reads "cases: case 1: x squared, if x greater than
  0; case 2: 0, otherwise; end cases";
- nuclides: an empty base with mass and atomic number scripts before an element reads
  "carbon 14, atomic number 6" (`speech_nuclides`);
- units: upright letters or text after a number read as a unit, "5 m/s" as "5 meters per
  second" (`speech_units`); an italic letter is a variable and is never a unit;
- text is read verbatim, except words that are a single maths symbol ("=" -> "equals");
- a capital letter is said "cap K" (`CAP`; a Greek capital "cap delta"), so a voice does not read
  "capital" before every letter of a formula; the review screen's own sentences still say
  "capital" (`in_full_words`). An upright run of letters that is a name (`\\mathrm{Var}`: one
  `mi` per letter) adds no "cap" and no word, and an element symbol in a chemical formula adds
  none (`speech_chem`);
- the rules carry a version (`SPEECH_VERSION`): a reading stored by older rules, and not written
  by a person, is made again from its MathML when it is shown (`reading_for`, `refresh_readings`);
- two different prints are never said alike: a short fraction or subscript that a plain
  token follows directly closes itself ("1 over 2 end fraction m v squared" for `\\frac{1}{2}mv^2`,
  "1 over 2 m v squared" for `\\frac{1}{2m}v^2`);
- a sign is read for what it does in its place: a bar between the arguments of a probability or
  an expectation is "given" and in set braces "such that" (elsewhere "divides"); a centered dot or a
  cross between vectors is "dot" or "cross" (between numbers and scalars "times"); a transpose,
  a star, a dagger and an inverse function are "A transpose", "x star", "A dagger" and "inverse
  sine"; a negated relation is read as its own words ("is not less than or equal to").
"""

# This module is a table of maths symbols; characters that look like Latin letters
# (Greek nu, the multiplication sign, the tilde operator) are the point, not a mistake.
# ruff: noqa: RUF001

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from typing import Any

import defusedxml
import defusedxml.ElementTree as DET

from readablestem.core.mathml_intent import ALLOWED_INTENTS
from readablestem.core.notation import canonical_from_parts
from readablestem.core.types import EquationFix
from readablestem.eval.equations import normalize
from readablestem.pipeline import speech_chem
from readablestem.pipeline.speech_nuclides import ELEMENTS, nuclide_words
from readablestem.pipeline.speech_units import unit_words

MAX_DEPTH = 64

# The version of these rules. A reading is stored with the version that wrote it
# (`EquationFix.speech_version`); raise this number whenever a rule changes what is said, and
# every older stored reading is made again from its MathML where it is shown (`reading_for`).
# 2: "cap K" for a capital, no word for the letters of a name, a pause after a coefficient.
SPEECH_VERSION = 2


class SpeechError(ValueError):
    """The input is not MathML that can be read. The message never contains the input."""


GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ϵ": "epsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ϑ": "theta", "ι": "iota", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "ο": "omicron", "π": "pi", "ϖ": "pi",
    "ρ": "rho", "ϱ": "rho", "σ": "sigma", "ς": "sigma", "τ": "tau", "υ": "upsilon",
    "φ": "phi", "ϕ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
}  # fmt: skip
# A capital letter is "cap K": one syllable, so a formula with many capitals stays quick to hear.
CAP = "cap"


def in_full_words(said: str) -> str:
    """A reading as the review screen's own sentences name a letter ("capital X", not the
    "cap X" a voice says): for the words a person reads, not the ones a voice speaks."""
    return f"capital {said[len(CAP) + 1 :]}" if said.startswith(f"{CAP} ") else said


GREEK.update({ch.upper(): f"{CAP} {name}" for ch, name in list(GREEK.items()) if ch.upper() != ch})

SYMBOLS = {
    # operators
    "=": "equals", "+": "plus", "−": "minus", "-": "minus", "±": "plus or minus",
    "∓": "minus or plus", "×": "times", "·": "times", "⋅": "times", "∗": "star",
    "*": "times", "÷": "divided by", "/": "divided by", "∘": "composed with",
    "√": "square root", "!": "factorial", "%": "percent", "′": "prime", "″": "double prime",
    "‴": "triple prime", "°": "degrees", "∠": "angle", "∂": "partial", "∇": "nabla",
    "∞": "infinity", "ℏ": "h bar", "ℓ": "script l", "∅": "the empty set",
    "…": "dot dot dot", "⋯": "dot dot dot", "⋮": "vertical dots", "⋱": "diagonal dots",
    "&": "ampersand",
    # relations
    "<": "less than", ">": "greater than", "≤": "less than or equal to",
    "≥": "greater than or equal to", "⩽": "less than or equal to",
    "⩾": "greater than or equal to", "≠": "not equal to", "≈": "approximately equals",
    "≃": "approximately equals", "≅": "is congruent to", "∼": "is similar to",
    "≡": "is equivalent to", "∝": "proportional to", "≪": "much less than",
    "≫": "much greater than", "∈": "is an element of", "∉": "is not an element of",
    "⊂": "is a subset of", "⊆": "is a subset of or equal to", "⊃": "is a superset of",
    "⊥": "is perpendicular to", "∥": "is parallel to", "∣": "divides", "∴": "therefore",
    "∵": "because", "∀": "for all", "∃": "there exists", "¬": "not", "∧": "and",
    "∨": "or", "∪": "union", "∩": "intersection", "∖": "set minus", "⧵": "set minus",
    "\\": "backslash",
    "~": "tilde", "†": "dagger", "‡": "double dagger", "⊤": "top", "⊢": "proves",
    "⊨": "models", "≺": "precedes", "≻": "succeeds", "≼": "precedes or equals",
    "≽": "succeeds or equals", "∤": "does not divide", "∄": "there does not exist",
    "⊇": "is a superset of or equal to", "∋": "contains the member", "⟂": "is perpendicular to",
    "⊲": "is a normal subgroup of", "⊳": "contains as a normal subgroup",
    "⊴": "is a normal subgroup of or equal to", "⊵": "contains as a normal subgroup or equal to",
    "⊩": "forces",
    # arrows
    "→": "right arrow", "⟶": "right arrow", "←": "left arrow", "⟵": "left arrow",
    "↔": "left right arrow", "⟷": "left right arrow", "⇒": "implies", "⟹": "implies",
    "⇐": "is implied by",
    "⇔": "if and only if", "⟺": "if and only if", "↦": "maps to", "↑": "up arrow",
    "↓": "down arrow", "⇌": "equilibrium arrow",
    # fences and punctuation
    "(": "open parenthesis", ")": "close parenthesis", "[": "open bracket",
    "]": "close bracket", "{": "open brace", "}": "close brace", "|": "vertical bar",
    "‖": "double vertical bar", "│": "vertical bar", "║": "double vertical bar",
    "⟨": "open angle bracket", "⟩": "close angle bracket",
    "⌊": "open floor", "⌋": "close floor", "⌈": "open ceiling", "⌉": "close ceiling",
    ",": "comma", ";": "semicolon", ":": "colon", ".": "period",
    # large operators (as plain symbols; with limits they read as "the sum from ...")
    "∑": "sum", "∏": "product", "∐": "coproduct", "∫": "integral",
    "∬": "double integral", "∭": "triple integral", "∮": "contour integral",
    "⋃": "union", "⋂": "intersection",
}  # fmt: skip

FUNCTIONS = {
    "sin": "sine", "cos": "cosine", "tan": "tangent", "cot": "cotangent", "sec": "secant",
    "csc": "cosecant", "arcsin": "arc sine", "arccos": "arc cosine",
    "arctan": "arc tangent", "sinh": "hyperbolic sine", "cosh": "hyperbolic cosine",
    "tanh": "hyperbolic tangent", "log": "log", "lg": "log", "ln": "natural log",
    "exp": "exponential", "det": "determinant", "dim": "dimension", "ker": "kernel",
    "gcd": "greatest common divisor", "deg": "degree", "mod": "mod", "Pr": "probability",
}  # fmt: skip

# Operators that take limits ("the sum from ... to ... of").
LARGE_OPERATORS = {
    "∑": "sum", "∏": "product", "∐": "coproduct", "∫": "integral", "∬": "double integral",
    "∭": "triple integral", "∮": "contour integral", "⋃": "union", "⋂": "intersection",
}  # fmt: skip
LIMIT_OPERATORS = {
    "lim": "limit", "max": "maximum", "min": "minimum", "sup": "supremum",
    "inf": "infimum", "limsup": "limit superior", "liminf": "limit inferior",
}  # fmt: skip

# mover accents: character -> (template); "{}" is the base.
OVER_ACCENTS = {
    "→": "vector {}", "⃗": "vector {}", "^": "{} hat", "ˆ": "{} hat", "̂": "{} hat",
    "¯": "{} bar", "―": "{} bar", "‾": "{} bar", "̄": "{} bar", "_": "{} bar",
    "˙": "{} dot", "̇": "{} dot", "¨": "{} double dot", "̈": "{} double dot",
    "~": "{} tilde", "˜": "{} tilde", "̃": "{} tilde", "←": "{} with left arrow above",
    "↔": "{} with left right arrow above", "⏞": "{} with overbrace", "ˇ": "{} check",
}  # fmt: skip
UNDER_ACCENTS = {
    "_": "{} underlined", "―": "{} underlined", "‾": "{} underlined", "¯": "{} underlined",
    "⏟": "{} with underbrace", "→": "{} with right arrow below",
}  # fmt: skip

VARIANT_PREFIX = {
    "bold": "bold", "bold-italic": "bold", "bold-sans-serif": "bold",
    "sans-serif-bold-italic": "bold", "double-struck": "double-struck", "script": "script",
    "bold-script": "bold script", "fraktur": "fraktur", "bold-fraktur": "bold fraktur",
}  # fmt: skip

_TRAILING_PUNCT = ".,;"
_TOKENS = {"mi", "mn", "mo", "mtext", "ms"}
_RELATIONS = {"=", "<", ">", "≤", "≥", "≠", "≈", "≃", "≅", "≡", "∝", "→", "⇒", "⇔", "∼"}


# ------------------------------------------------------------------------ parsing


def _parse(mathml: str) -> ET.Element:
    """defusedxml parse, depth-capped, then the scorer's normalisation."""
    try:
        root = DET.fromstring(
            mathml.strip(), forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except (ET.ParseError, defusedxml.DefusedXmlException) as exc:
        raise SpeechError(f"MathML could not be parsed ({type(exc).__name__})") from None
    stack = [(root, 1)]
    while stack:
        elem, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise SpeechError("MathML nested too deeply")
        stack.extend((child, depth + 1) for child in elem)
    _restore_upright_symbols(root)
    _retag_operator_glyphs(root)
    _join_italic_words(root)
    return normalize(root)


def _drop_trailing_punctuation(node: ET.Element) -> None:
    """Sentence punctuation at the very end of the equation is not read."""
    while len(node):
        last = node[-1]
        if last.tag in {"math", "mrow", "mtable", "mtr", "mtd"}:
            _drop_trailing_punctuation(last)
            return
        if last.tag not in _TOKENS:
            return
        text = last.text or ""
        if text and all(ch in _TRAILING_PUNCT or ch.isspace() for ch in text):
            node.remove(last)
            continue
        if last.tag == "mtext" and text[-1:] in _TRAILING_PUNCT:
            last.text = text.rstrip(_TRAILING_PUNCT).rstrip()
        return


# ------------------------------------------------------------------------- tokens


SOLIDUS_OVERLAY = "\u0338"
# How a relation's words read once it is struck through. Anything not here gets "is not" before
# a phrase that ends in a preposition ("is not a subset of") and "not" before the rest.
_NEGATED_WORDS = {
    "equals": "is not equal to", "approximately equals": "is not approximately equal to",
    "precedes": "does not precede", "succeeds": "does not succeed",
    "precedes or equals": "does not precede or equal",
    "succeeds or equals": "does not succeed or equal",
    "proves": "does not prove", "models": "does not model", "implies": "does not imply",
    "maps to": "does not map to", "is implied by": "is not implied by",
    "if and only if": "is not equivalent to", "forces": "does not force",
    "less than or equal to": "is neither less than nor equal to",
    "greater than or equal to": "is neither greater than nor equal to",
    "is a subset of or equal to": "is neither a subset of nor equal to",
    "is a superset of or equal to": "is neither a superset of nor equal to",
    "contains the member": "does not contain the member",
    "contains as a normal subgroup": "does not contain as a normal subgroup",
    "contains as a normal subgroup or equal to":
        "does not contain as a normal subgroup or equal to",
    "approaches the limit": "does not approach the limit",
}  # fmt: skip
_ARROW_WORDS = frozenset(
    {"right arrow", "left arrow", "left right arrow", "up arrow", "down arrow"}
)


def _struck_base(text: str) -> str | None:
    """The relation under a negation slash: `≰` -> `≤`, `⟂` + U+0338 -> `⟂`, else None."""
    if len(text) == 2 and text[1] == SOLIDUS_OVERLAY:
        return text[0]
    if len(text) == 1:
        decomposed = unicodedata.normalize("NFD", text)
        if len(decomposed) == 2 and decomposed[1] == SOLIDUS_OVERLAY:
            return decomposed[0]
    return None


def _negated_words(text: str) -> str | None:
    """Words for a relation with a slash through it, built from the words of the relation, or
    None when `text` is not one."""
    base = _struck_base(text)
    if base is None:
        return None
    words = _char_words(base)
    if words in _ARROW_WORDS:
        return f"{words} with stroke"
    if words in _NEGATED_WORDS:
        return _NEGATED_WORDS[words]
    if words.startswith("is "):
        return f"is not {words[3:]}"
    return f"is not {words}" if words.split()[-1] in {"than", "to", "of"} else f"not {words}"


def _char_words(ch: str) -> str:
    if ch in GREEK:
        return GREEK[ch]
    if ch in SYMBOLS:
        return SYMBOLS[ch]
    negated = _negated_words(ch)
    if negated is not None:
        return negated
    if ch.isascii() and ch.isalpha():
        return f"{CAP} {ch}" if ch.isupper() else ch
    if ch.isascii():
        return ch
    name = unicodedata.name(ch, "")
    if not name:
        return ch
    # Letterlike symbols (U+211D double-struck R): "double-struck cap R".
    base = unicodedata.normalize("NFKC", ch)
    words = [CAP if w == "capital" else w for w in name.lower().split()]
    if len(base) == 1 and base.isascii() and base.isalpha() and words[-1] == base.lower():
        words[-1] = base
    return " ".join(words)


def _symbol_text(text: str) -> str | None:
    """Words for a token that is one known symbol (or Greek letter), else None."""
    if text in SYMBOLS:
        return SYMBOLS[text]
    if len(text) == 1 and text in GREEK:
        return GREEK[text]
    return None


# Course readings for the call in progress (`mathml_to_speech(readings=...)`): notation
# key -> the word read in place of the letter's style ("vector").
_READINGS: ContextVar[Mapping[str, str] | None] = ContextVar("speech_readings", default=None)


def _course_reading(text: str, variant: str | None) -> str | None:
    """The course's word for a one-letter token ("vector" for a bold d), if it has one."""
    readings = _READINGS.get()
    if not readings or len(text) != 1:
        return None
    key = canonical_from_parts(text, variant)
    return readings.get(key) if key is not None else None


def _speak_mi(node: ET.Element) -> list[str]:
    text = node.text or ""
    if not text:
        return []
    if node.get(_MARK):  # a "~" read as "is distributed as"
        return [node.get(_MARK, "")]
    variant = _course_reading(text, node.get("mathvariant")) or VARIANT_PREFIX.get(
        node.get("mathvariant", "")
    )
    if text in FUNCTIONS:
        words = FUNCTIONS[text]
    elif text in LIMIT_OPERATORS:
        words = LIMIT_OPERATORS[text]
    elif len(text) == 1:
        words = _char_words(text)
    else:
        symbol = _symbol_text(text)
        words = symbol if symbol is not None else text
    return [variant, words] if variant else [words]


def _speak_mo(node: ET.Element) -> list[str]:
    text = node.text or ""
    if not text:
        return []
    if node.get(_MARK):  # a reading the row gave it ("given", "dot"; see `_mark_row`)
        return [node.get(_MARK, "")]
    for table in (SYMBOLS, FUNCTIONS, LIMIT_OPERATORS):
        if text in table:
            return [table[text]]
    if len(text) == 1:
        return [_char_words(text)]
    return [_negated_words(text) or text]


def _speak_mtext(node: ET.Element) -> list[str]:
    """Text verbatim, except words that are exactly one maths symbol or Greek letter."""
    words = []
    for word in (node.text or "").split():
        symbol = _symbol_text(word)
        words.append(symbol if symbol is not None else word)
    return words


def _speak_mn(node: ET.Element) -> list[str]:
    return [node.text] if node.text else []


# --------------------------------------------------------------------- structures


def _text_of(node: ET.Element) -> str:
    return "".join(node.itertext()).strip()


_NAME_SYMBOLS = frozenset("∞∂∇∅ℏℓ")


def _is_plain_token(node: ET.Element) -> bool:
    r"""A letter, a number or a word. The converter leaves some relations and operators as `mi`
    (`\geqslant`, `\pm`): a sign is not a token a short form can run into."""
    text = _text_of(node)
    if node.tag == "mi" and not (text[:1].isalnum() or text[:1] in _NAME_SYMBOLS):
        return False
    return node.tag in {"mi", "mn", "mtext"} and text not in FUNCTIONS


def _starts_plain(node: ET.Element) -> bool:
    """A plain token, or a script on one (`b^2` after `a_a` can be taken for the rest of its
    subscript)."""
    while node.tag in {"msub", "msup", "msubsup"} and len(node):
        node = node[0]
    return _is_plain_token(node)


# Nodes a plain token follows directly in the call in progress (`_mark_followed`). A short
# fraction or subscript there says where it ends, or `\frac{1}{2}mv^2` and `\frac{1}{2m}v^2`
# would be said alike.
_FOLLOWED: ContextVar[frozenset[int]] = ContextVar("speech_followed", default=frozenset())


def _mark_followed(root: ET.Element) -> frozenset[int]:
    """The ids of the nodes a plain token follows directly: the next sibling is one, or the node
    ends a row (`mrow`) that one follows. Only a plain token can be taken for the rest of a short
    form: a short form holds no operators, so an operator or a bracket after it cannot be."""
    found: set[int] = set()
    stack = [(root, False)]
    while stack:
        node, followed = stack.pop()
        kids = list(node)
        transparent = node.tag in {"math", "mrow"}
        for k, kid in enumerate(kids):
            more = k + 1 < len(kids)
            after = _starts_plain(kids[k + 1]) if more else followed and transparent
            if after:
                found.add(id(kid))
            stack.append((kid, after))
    return frozenset(found)


# Fractions a plain token directly precedes in the call in progress: `a\\frac{b}{2}` takes the
# long form, or it would be said as `\\frac{ab}{2}` is.
_PRECEDED: ContextVar[frozenset[int]] = ContextVar("speech_preceded", default=frozenset())


def _mixed_number(prev: ET.Element, node: ET.Element) -> bool:
    """`2\\frac{1}{2}`: a number before a fraction of numbers; `\\frac{21}{2}` is one number."""
    return prev.tag == "mn" and node.tag == "mfrac" and len(node) == 2 and node[0].tag == "mn"


def _mark_preceded(root: ET.Element) -> frozenset[int]:
    """The ids of the nodes a plain token directly precedes (a first node in a row follows
    what the row follows)."""
    found: set[int] = set()
    stack: list[tuple[ET.Element, ET.Element | None]] = [(root, None)]
    while stack:
        node, before = stack.pop()
        transparent = node.tag in {"math", "mrow"}
        prev = before if transparent else None
        for kid in node:
            if prev is not None and _is_plain_token(prev) and not _mixed_number(prev, kid):
                found.add(id(kid))
            stack.append((kid, prev))
            prev = kid
    return frozenset(found)


def _is_simple(node: ET.Element) -> bool:
    """One or two plain tokens (no operators): safe to read without "end" markers."""
    if node.tag == "mrow":
        return 0 < len(node) <= 2 and all(_is_plain_token(child) for child in node)
    return _is_plain_token(node)


def _is_signed_token(node: ET.Element) -> bool:
    """A token, a minus/plus sign plus a token (`10^{-3}`), or a token plus a sign: the
    charge of an ion (`SO_4^{2-}`, `Fe^{3+}`)."""
    if node.tag == "mrow" and len(node) == 2 and node[0].tag == "mo":
        return _text_of(node[0]) in {"−", "-", "+"} and node[1].tag in _TOKENS
    if node.tag == "mrow" and len(node) == 2 and node[1].tag == "mo":
        return _text_of(node[1]) in {"−", "-", "+"} and node[0].tag in {"mn", "mi"}
    return node.tag in _TOKENS


def _speak_fraction(node: ET.Element) -> list[str]:
    if len(node) != 2:
        return _speak_children(node)
    num, den = node[0], node[1]
    if node.get("linethickness") == "0":
        return [*_speak(num), "choose", *_speak(den)]
    long_form = id(node) in _PRECEDED.get() and _is_plain_token(num)
    if _is_simple(num) and _is_simple(den) and not long_form:
        end = ["end fraction"] if id(node) in _FOLLOWED.get() else []
        return [*_speak(num), "over", *_speak(den), *end]
    return ["the fraction", *_speak(num), "over", *_speak(den), "end fraction"]


_INVERTIBLE = frozenset(
    {"sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan"}
    | {"sinh", "cosh", "tanh", "log", "lg", "ln", "exp"}
)
_BOLD_VARIANTS = frozenset({"bold", "bold-italic", "bold-sans-serif", "sans-serif-bold-italic"})


def _inverts(base: ET.Element, sup: ET.Element) -> bool:
    r"""`\sin^{-1}`: a named function to the power minus one is its inverse, not a power."""
    return (
        base.tag == "mi"
        and _text_of(base) in _INVERTIBLE
        and sup.tag == "mrow"
        and len(sup) == 2
        and _mo_text(sup[0]) in {"−", "-"}
        and sup[1].tag == "mn"
        and _text_of(sup[1]) == "1"
    )


def _transposable(base: ET.Element) -> bool:
    """A matrix or a vector by its print: a capital letter, a bold letter, or a bracketed
    expression with one of those (or a table) inside."""
    if base.tag == "mi":
        text = _text_of(base)
        bold = base.get("mathvariant") in _BOLD_VARIANTS
        return len(text) == 1 and text.isascii() and text.isalpha() and (text.isupper() or bold)
    if base.tag == "mrow" and len(base) > 1 and base[0].tag != "mo":
        return _transposable(base[-1])  # `\\boldsymbol{BB}^T` is printed as `BB^T`
    if base.tag == "mrow" and len(base) > 2:
        bracketed = _mo_text(base[0]) in {"(", "["} and _mo_text(base[-1]) in {")", "]"}
        return bracketed and any(n.tag == "mtable" or _transposable(n) for n in base.iter())
    return False


def _is_transpose(sup: ET.Element, base: ET.Element) -> bool:
    r"""`A^{T}` and `A^{\mathrm{T}}` (a plain or upright T alone over a matrix or a vector), and
    `A^\top` (the top sign, which over anything is a transpose)."""
    text = _text_of(sup)
    if text in {"⊤", "⊺"} and sup.tag in {"mi", "mo"}:
        return True
    plain_or_upright = sup.get("mathvariant") in {None, "normal"}
    return sup.tag == "mi" and text == "T" and plain_or_upright and _transposable(base)


def _power_words(
    sup: ET.Element, base: ET.Element | None = None, *, followed: bool = False
) -> list[str]:
    text = _text_of(sup)
    if sup.tag == "mn" and text == "2":
        return ["squared"]
    if sup.tag == "mn" and text == "3":
        return ["cubed"]
    if text in {"′", "″", "‴", "°", "∘"} and sup.tag in _TOKENS:
        return ["degrees"] if text in {"°", "∘"} else [SYMBOLS[text]]
    if base is not None and _is_transpose(sup, base):
        return ["transpose"]
    if sup.tag in {"mo", "mi"} and text in {"*", "∗", "†", "‡"}:
        return ["star" if text in {"*", "∗"} else SYMBOLS[text]]
    if base is not None and base.tag == "mn" and sup.tag == "mo" and text in {"+", "−", "-"}:
        return ["plus" if text == "+" else "minus"]  # a one-sided limit: 0 plus, 0 minus
    if _is_signed_token(sup) and not (followed and sup.tag == "mo"):  # `e^{-}x` is not `e^{-x}`
        return ["to the power", *_speak(sup)]
    return ["to the power", *_speak(sup), "end power"]


def _sub_words(sub: ET.Element, *, followed: bool = False) -> list[str]:
    """ "sub i", and "sub i end sub" when a plain token follows (or `x_i y` and `x_{iy}` are said
    alike); a long subscript always ends."""
    if _is_simple(sub) or _is_signed_token(sub):
        return ["sub", *_speak(sub), *(["end sub"] if followed else [])]
    return ["sub", *_speak(sub), "end sub"]


def _limit_words(node: ET.Element) -> list[str]:
    """Speech for a limit expression: an arrow reads as "approaches"."""
    words = _speak(node)
    return ["approaches" if w == "right arrow" else w for w in words]


def _operator_kind(base: ET.Element) -> tuple[str, str] | None:
    """("large", "sum") / ("limit", "limit") when `base` takes limits, else None."""
    text = _text_of(base)
    if base.tag not in {"mo", "mi"}:
        return None
    if text in LARGE_OPERATORS:
        return "large", LARGE_OPERATORS[text]
    if text in LIMIT_OPERATORS:
        return "limit", LIMIT_OPERATORS[text]
    return None


def _with_limits(
    base: ET.Element, lower: ET.Element | None, upper: ET.Element | None
) -> list[str] | None:
    kind = _operator_kind(base)
    if kind is None:
        return None
    family, name = kind
    if family == "limit":
        words = [f"the {name}"]
        if lower is not None:
            words += ["as" if name == "limit" else "over", *_limit_words(lower)]
        return [*words, "of"]
    words = [f"the {name}"]
    if lower is not None and upper is not None:
        words += ["from", *_speak(lower), "to", *_speak(upper)]
    elif lower is not None:
        words += ["over", *_speak(lower)]
    elif upper is not None:
        words += ["to", *_speak(upper)]
    return [*words, "of"]


def _no_base(base: ET.Element) -> bool:
    """A script with nothing to hang on: the scripts before a nuclide's symbol."""
    return (base.tag == "mrow" and len(base) == 0) or (base.tag == "mi" and not _text_of(base))


def _pre_script(word: str, script: ET.Element) -> list[str]:
    """A script before its base, read as "superscript 14" ("... end superscript" when long)."""
    if _is_simple(script) or _is_signed_token(script):
        return [word, *_speak(script)]
    return [word, *_speak(script), f"end {word}"]


def _speak_msub(node: ET.Element) -> list[str]:
    if len(node) != 2:
        return _speak_children(node)
    chemical = speech_chem.scripted_words(node, _speak)
    if chemical is not None:
        return chemical
    base, sub = node[0], node[1]
    if _no_base(base):
        return _pre_script("subscript", sub)
    limits = _with_limits(base, sub, None)
    if limits is not None:
        return limits
    return [*_speak(base), *_sub_words(sub, followed=id(node) in _FOLLOWED.get())]


def _speak_msup(node: ET.Element) -> list[str]:
    if len(node) != 2:
        return _speak_children(node)
    chemical = speech_chem.scripted_words(node, _speak)
    if chemical is not None:
        return chemical
    base, sup = node[0], node[1]
    if _no_base(base):
        return _pre_script("superscript", sup)
    limits = _with_limits(base, None, sup)
    if limits is not None:
        return limits
    if _inverts(base, sup):
        return ["inverse", FUNCTIONS[_text_of(base)]]
    return [*_speak(base), *_power_words(sup, base, followed=id(node) in _FOLLOWED.get())]


def _speak_msubsup(node: ET.Element) -> list[str]:
    if len(node) != 3:
        return _speak_children(node)
    chemical = speech_chem.scripted_words(node, _speak)
    if chemical is not None:
        return chemical
    base, sub, sup = node[0], node[1], node[2]
    if _no_base(base):  # a nuclide's mass number over its atomic number: {}^{14}_{6}C
        return [*_pre_script("superscript", sup), *_pre_script("subscript", sub)]
    limits = _with_limits(base, sub, sup)
    if limits is not None:
        return limits
    return [
        *_speak(base),
        *_sub_words(sub),
        *_power_words(sup, base, followed=id(node) in _FOLLOWED.get()),
    ]


def _speak_munder(node: ET.Element) -> list[str]:
    if len(node) != 2:
        return _speak_children(node)
    base, under = node[0], node[1]
    limits = _with_limits(base, under, None)
    if limits is not None:
        return limits
    template = UNDER_ACCENTS.get(_text_of(under)) if under.tag == "mo" else None
    if template:
        return template.format(" ".join(_speak(base))).split()
    return [*_speak(base), "with", *_speak(under), "below"]


def _speak_mover(node: ET.Element) -> list[str]:
    if len(node) != 2:
        return _speak_children(node)
    base, over = node[0], node[1]
    limits = _with_limits(base, None, over)
    if limits is not None:
        return limits
    template = OVER_ACCENTS.get(_text_of(over)) if over.tag == "mo" else None
    if template:
        return template.format(" ".join(_speak(base))).split()
    return [*_speak(base), "with", *_speak(over), "above"]


def _speak_munderover(node: ET.Element) -> list[str]:
    if len(node) != 3:
        return _speak_children(node)
    base, under, over = node[0], node[1], node[2]
    limits = _with_limits(base, under, over)
    if limits is not None:
        return limits
    return [*_speak(base), "with", *_speak(under), "below and", *_speak(over), "above"]


def _is_compound(node: ET.Element) -> bool:
    return len(node) > 1 or (len(node) == 1 and node[0].tag not in _TOKENS)


def _speak_msqrt(node: ET.Element) -> list[str]:
    words = ["the square root of", *_speak_children(node)]
    return [*words, "end root"] if _is_compound(node) else words


def _speak_mroot(node: ET.Element) -> list[str]:
    if len(node) != 2:
        return _speak_children(node)
    base, index = node[0], node[1]
    index_text = _text_of(index)
    if index_text == "2":
        words = ["the square root of"]
    elif index_text == "3":
        words = ["the cube root of"]
    else:
        words = ["the root of index", *_speak(index), "of"]
    words += _speak(base)
    return [*words, "end root"] if base.tag not in _TOKENS else words


def _speak_menclose(node: ET.Element) -> list[str]:
    notation = node.get("notation", "")
    if "strike" in notation:
        prefix = "crossed out"
    elif "box" in notation:
        prefix = "boxed"
    else:
        prefix = "enclosed"
    return [prefix, *_speak_children(node), "end enclosure"]


def _starts_with_relation(cell: ET.Element) -> bool:
    first = cell[0] if len(cell) else cell
    return first.tag in {"mo", "mtext"} and _text_of(first) in _RELATIONS


def _table_cells(node: ET.Element) -> list[list[ET.Element]]:
    """The non-empty cells of each row of an mtable (a row label is skipped)."""
    rows = [row for row in node if row.tag in {"mtr", "mlabeledtr"}]
    return [
        [
            cell
            for cell in (row[1:] if row.tag == "mlabeledtr" else list(row))
            if len(cell) or cell.text
        ]
        for row in rows
    ]


def _speak_mtable(node: ET.Element) -> list[str]:
    """Rows as "row N: ..." (only when there are several). Cells of an aligned equation
    (some cell starts with a relation) read as one line; matrix cells are comma-separated.
    """
    cells_by_row = _table_cells(node)
    aligned = any(_starts_with_relation(c) for cells in cells_by_row for c in cells)
    spoken_rows = []
    for cells in cells_by_row:
        # A row of half-reaction cells with an arrow in a cell of its own reads as one row.
        row = speech_chem.row_parts(cells, _speak)
        parts = row if row is not None else [" ".join(_speak(cell)) for cell in cells]
        spoken_rows.append((" " if aligned else ", ").join(p for p in parts if p))
    if len(spoken_rows) == 1:
        return [spoken_rows[0]] if spoken_rows[0] else []
    joined = "; ".join(f"row {i}: {text}" for i, text in enumerate(spoken_rows, 1))
    return [joined]


# ------------------------------------------------------------ matrices and cases

_MATRIX_FENCES = {"(": ")", "[": "]", "|": "|", "‖": "‖", "∣": "∣"}
_BARS = {"|", "‖", "∣"}
_CASE_WORDS = {"if", "for", "when", "otherwise", "else", "elsewhere", "where", "unless"}


def _mo_text(node: ET.Element) -> str | None:
    return _text_of(node) if node.tag == "mo" else None


def _speak_matrix(table: ET.Element, fence: str) -> list[str] | None:
    """ "the 2 by 2 matrix; row 1: a, b; row 2: c, d; end matrix" for a fenced table, or
    None when it is not a matrix (an aligned equation, or a single cell)."""
    cells_by_row = _table_cells(table)
    if not cells_by_row or any(_starts_with_relation(c) for r in cells_by_row for c in r):
        return None
    rows, columns = len(cells_by_row), max(len(r) for r in cells_by_row)
    if rows * columns < 2:
        return None
    kind = "determinant" if fence in _BARS else "matrix"
    if kind == "matrix" and columns == 1:
        kind = "column matrix"
    elif kind == "matrix" and rows == 1:
        kind = "row matrix"
    spoken = [
        ", ".join(p for p in (" ".join(_speak(cell)) for cell in cells) if p)
        for cells in cells_by_row
    ]
    body = "; ".join(f"row {i}: {text}" for i, text in enumerate(spoken, 1))
    end = "end determinant" if kind == "determinant" else "end matrix"
    return [f"the {rows} by {columns} {kind}; {body}; {end}"]


def _speak_cases(table: ET.Element) -> list[str] | None:
    """ "cases: case 1: x squared, if x greater than 0; case 2: 0, otherwise; end cases"
    for a table after an opening brace. A condition that does not start with a word like
    "if" or "otherwise" gets an "if"."""
    cases = []
    for index, cells in enumerate(_table_cells(table), 1):
        spoken = [text for text in (" ".join(_speak(cell)) for cell in cells) if text]
        if not spoken:
            continue
        value, condition = spoken[0], " ".join(spoken[1:])
        if condition:
            first = condition.split()[0].lower().rstrip(",.;:")
            value += (", " if first in _CASE_WORDS else ", if ") + condition
        cases.append(f"case {index}: {value}")
    return [f"cases: {'; '.join(cases)}; end cases"] if cases else None


def _fenced_table(kids: list[ET.Element], i: int) -> tuple[list[str], int] | None:
    """A matrix or cases at kids[i] (an opening fence, an mtable, then the closing fence
    for a matrix): the words and how many siblings they cover."""
    opener = _mo_text(kids[i])
    if opener is None or i + 1 >= len(kids) or kids[i + 1].tag != "mtable":
        return None
    table = kids[i + 1]
    closer = _mo_text(kids[i + 2]) if i + 2 < len(kids) else None
    if opener in _MATRIX_FENCES:
        if closer != _MATRIX_FENCES[opener]:
            return None
        words = _speak_matrix(table, opener)
        return (words, 3) if words else None
    if opener == "{" and closer != "}":
        words = _speak_cases(table)
        # An empty or "." closing fence belongs to the brace.
        return (words, 3 if closer in {"", "."} else 2) if words else None
    return None


# ------------------------------------------------------------- nuclides and units

_SCRIPTS = {"msub", "msup", "msubsup"}


def _is_empty(node: ET.Element) -> bool:
    return not _text_of(node) and all(_is_empty(child) for child in node)


def _letters(node: ET.Element) -> str | None:
    """The letters of a token or a row of tokens, else None."""
    if node.tag in {"mi", "mtext"}:
        return _text_of(node)
    if node.tag == "mrow" and len(node) and all(c.tag in {"mi", "mtext"} for c in node):
        return "".join(_text_of(c) for c in node)
    return None


def _number_text(node: ET.Element | None) -> str | None:
    """An integer printed as one mn, or a sign and an mn ("-1"); else None."""
    if node is None:
        return None
    if node.tag in {"mn", "mtext"}:
        return _text_of(node) or None
    if node.tag == "mrow" and len(node) == 2 and _mo_text(node[0]) in {"+", "-", "−"}:
        inner = _number_text(node[1])
        return f"{_text_of(node[0])}{inner}" if inner else None
    return None


def _script_numbers(node: ET.Element) -> tuple[str | None, str | None] | None:
    """(mass, atomic number) from the scripts of an empty-based msub/msup/msubsup."""
    if node.tag == "msubsup" and len(node) == 3:
        return _number_text(node[2]), _number_text(node[1])
    if node.tag == "msup" and len(node) == 2:
        return _number_text(node[1]), None
    if node.tag == "msub" and len(node) == 2:
        return None, _number_text(node[1])
    return None


def _nuclide(kids: list[ET.Element], i: int) -> tuple[list[str], int] | None:
    """A nuclide at kids[i]: scripts on an empty base, then the element's letters (one
    sibling "Fe" or "C", or two "H" "e"). The words and the siblings they cover."""
    node = kids[i]
    if node.tag not in _SCRIPTS or not len(node) or not _is_empty(node[0]):
        return None
    numbers = _script_numbers(node)
    if numbers is None:
        return None
    for used in (2, 1):
        siblings = kids[i + 1 : i + 1 + used]
        excited = bool(siblings) and _is_excited(siblings[-1])
        if excited:  # the star hangs on the last letters: `Ni^*`, `H` then `e^*`
            siblings = [*siblings[:-1], siblings[-1][0]]
        parts = [_letters(k) for k in siblings]
        if len(parts) < used or any(p is None for p in parts):
            continue
        words = nuclide_words("".join(p for p in parts if p), *numbers, excited=excited)
        if words is not None:
            return [words], used + 1
    return None


def _is_excited(node: ET.Element) -> bool:
    """`Ni^*`: letters with an asterisk as the only superscript."""
    return (
        node.tag == "msup"
        and len(node) == 2
        and speech_chem.is_star(node[1])
        and _letters(node[0]) is not None
    )


def _speak_multiscripts(node: ET.Element) -> list[str]:
    """`mmultiscripts` with prescripts on an element ("C" with 6 below and 14 above) reads
    as a nuclide; any other use is read child by child."""
    kids = list(node)
    tags = [k.tag for k in kids]
    if tags.count("mprescripts") == 1 and tags[-3:-2] == ["mprescripts"] and len(kids) == 4:
        base, sub, sup = _letters(kids[0]), _number_text(kids[2]), _number_text(kids[3])
        words = nuclide_words(base, sup, sub) if base else None
        if words is not None:
            return [words]
    return _speak_children(node)


_UNIT_CHARS = frozenset("/·⋅*()")


def _unit_string(node: ET.Element, *, first: bool) -> str | None:
    """`node` as a piece of a unit string ("kg", "/", "s^2"), or None if it cannot be one.
    Italic single letters are variables, so only upright letters and text count."""
    tag, text = node.tag, _text_of(node)
    if tag == "mi":
        upright = node.get("mathvariant") == "normal" or len(text) > 1
        return text if upright and text.replace("°", "").isalpha() else None
    if tag == "mtext":
        allowed = all(ch.isalpha() or ch in _UNIT_CHARS or ch in "° -−" for ch in text)
        return text if text and allowed else None
    if tag == "mo":
        return text if text in _UNIT_CHARS and not (first and text != "(") else None
    if tag == "msup" and len(node) == 2:
        base, power = _unit_string(node[0], first=first), _number_text(node[1])
        return f"{base}^{power}" if base and power and power.lstrip("+-−").isdigit() else None
    if tag == "mfrac" and len(node) == 2:
        top, bottom = _unit_string(node[0], first=True), _unit_string(node[1], first=True)
        return f"({top})/({bottom})" if top and bottom else None
    if tag == "mrow":
        parts = [_unit_string(child, first=first and k == 0) for k, child in enumerate(node)]
        return "".join(p for p in parts if p) if all(parts) and parts else None
    return None


def _after_number(prev: ET.Element) -> str | None:
    """The number just before a unit, as text, or None when `prev` is not a number."""
    if prev.tag == "mn":
        return _text_of(prev)
    if prev.tag in {"msup", "msubsup"} and len(prev) and prev[0].tag == "mn":
        return "10"  # 3 x 10^8 m/s reads in the plural
    return None


def _join_pieces(pieces: list[str]) -> str:
    """The pieces as one unit string. Two units side by side ("mol", "L^-1") are a product
    in a chemistry rate or molar quantity ("mol L^-1 s^-1", "J mol^-1 K^-1": a mole and a
    negative power); other side-by-side units stay as they were (not read as a unit)."""
    chemical = any(p.startswith(("mol", "mmol", "μmol", "µmol")) for p in pieces)
    if not chemical or not any("^-" in p or "^−" in p for p in pieces):
        return "".join(pieces)
    out = pieces[0]
    for piece in pieces[1:]:
        side_by_side = (out[-1].isalnum() or out[-1] == ")") and (
            piece[0].isalpha() or piece[0] == "("
        )
        out += ("*" if side_by_side else "") + piece
    return out


def _unit_run(kids: list[ET.Element], i: int, count: str) -> tuple[list[str], int] | None:
    """A unit at kids[i:] after the number `count`: the longest run of siblings that
    reads wholly as one unit ("kg", "·", "m", "/", "s^2"). The words and the siblings."""
    pieces: list[str] = []
    for k, node in enumerate(kids[i:]):
        piece = _unit_string(node, first=k == 0)
        if piece is None:
            break
        pieces.append(piece)
    for used in range(len(pieces), 0, -1):
        words = unit_words(_join_pieces(pieces[:used]), count)
        if words is not None:
            return [words], used
    return None


_DEGREE_SIGNS = {"°", "∘"}
_TEMPERATURE_SCALES = {"C", "F"}


def _degrees(kids: list[ET.Element], i: int) -> tuple[list[str], int] | None:
    """A temperature at kids[i]: a number with a degree superscript then the scale
    (`25^\\circ C`), or the degree sign after a number that was already read
    (`25 ^\\circ C`, `25 °C` as three tokens)."""
    node = kids[i]
    scale = _letters(kids[i + 1]) if i + 1 < len(kids) else None
    if scale not in _TEMPERATURE_SCALES:
        return None
    own_number = node.tag == "msup" and len(node) == 2 and node[0].tag == "mn"
    if own_number:
        count, sign = _text_of(node[0]), _text_of(node[1])
    else:
        count = _after_number(kids[i - 1]) if i > 0 else None
        sign = _text_of(node[0] if node.tag == "msup" and len(node) == 1 else node)
        if node.tag not in {"msup", "mi", "mo"}:
            return None
    unit = unit_words(f"°{scale}", count) if count is not None else None
    if unit is None or sign not in _DEGREE_SIGNS:
        return None
    return ([count or "", unit] if own_number else [unit]), 2


def _measured_unit(kids: list[ET.Element], i: int) -> tuple[list[str], int] | None:
    """In a row that is chemistry, a unit after a decimal number ("0.0592 V": V is also
    vanadium, C carbon, K potassium) is a unit when it is not the start of a formula: a
    species symbol, a count or a charge follows it. A whole number stays a coefficient
    ("2 K")."""
    count = _after_number(kids[i - 1]) if i > 0 else None
    if count is None or "." not in count:
        return None
    found = _unit_run(kids, i, count)
    if found is None or found[1] != 1:
        return None
    if len(speech_chem.formula_symbols(kids[i]) or []) > 1:
        return None  # "GeV" also parses as two element symbols: left to read as it is today
    following = kids[i + found[1]] if i + found[1] < len(kids) else None
    if following is not None and (
        following.tag in _SCRIPTS or speech_chem.starts_species(kids, i + found[1])
    ):
        return None
    return found


# ------------------------------------------------- a sign read for what it does in its row

_MARK = "speak"  # attribute a row sets on a sign: the words to say in place of its own
_OPENERS = {"(": ")", "[": "]", "{": "}", "⟨": "⟩"}
_CLOSERS = frozenset(_OPENERS.values())
_DOTS = frozenset({"·", "⋅", "∙"})
_VECTOR_ARROWS = frozenset({"→", "⃗"})


def _identifier(node: ET.Element) -> bool:
    """A letter or a name a bracket can follow like a call: P, E, p_X, Var."""
    base = node[0] if node.tag in _SCRIPTS and len(node) else node
    text = _text_of(base)
    return base.tag == "mi" and bool(text) and text[0].isalpha()


def _matching(kids: list[ET.Element], start: int) -> int | None:
    """The index of the closing bracket of the one at `start`, or None."""
    depth = 0
    for j in range(start, len(kids)):
        text = _mo_text(kids[j])
        if text in _OPENERS:
            depth += 1
        elif text in _CLOSERS:
            depth -= 1
            if depth == 0:
                return j
    return None


def _mark_bars(kids: list[ET.Element]) -> None:
    """A bar between the arguments of a call ("P(A \\mid B)", "E[X \\mid Y]") is "given", between
    set braces "such that", inside angle brackets "vertical bar". Elsewhere a `\\mid` stays
    "divides". A plain `|` is marked only when it is the one bar of its brackets (two are the
    absolute value)."""
    for i, kid in enumerate(kids):
        opener = _mo_text(kid)
        if opener not in _OPENERS or (end := _matching(kids, i)) is None:
            continue
        if opener in {"(", "["}:
            word = "given" if i > 0 and _identifier(kids[i - 1]) else None
        else:
            word = {"{": "such that", "⟨": "vertical bar"}[opener]
        if word is None:
            continue
        depth, bars = 0, []
        for inner in kids[i + 1 : end]:
            text = _mo_text(inner)
            if text in _OPENERS:
                depth += 1
            elif text in _CLOSERS:
                depth -= 1
            elif depth == 0 and text in {"∣", "|"}:
                bars.append(inner)
        # In "{n \mid 3 \mid n}" the first bar is "such that" and the second divides.
        for bar in bars[:1] if opener == "{" else bars:
            if _mo_text(bar) == "∣" or len(bars) == 1:
                bar.set(_MARK, word)


def _is_nabla(node: ET.Element) -> bool:
    return node.tag in {"mi", "mo"} and _text_of(node) == "∇"


def _vector_like(node: ET.Element) -> bool:
    """A vector by its print: a bold letter, a letter with a vector arrow, nabla; either under a
    script (`\\mathbf{E}_0`)."""
    base = node
    while base.tag in _SCRIPTS and len(base):
        base = base[0]
    if base.tag == "mover" and len(base) == 2:
        return _text_of(base[1]) in _VECTOR_ARROWS
    if base.tag == "mrow" and len(base):
        return all(_vector_like(child) for child in base)
    if base.tag == "mi" and base.get("mathvariant") in _BOLD_VARIANTS:
        return True
    return _is_nabla(base)


def _operand(kids: list[ET.Element], i: int, step: int) -> ET.Element | None:
    """The operand before (step -1) or after (+1) the sign at `i`: brackets are skipped on the
    way, and after the sign a unary minus and a differential `d` too (F . d r)."""
    j = i + step
    while 0 <= j < len(kids):
        text = _mo_text(kids[j])
        if text in _OPENERS or text in _CLOSERS or (step > 0 and text in {"−", "-"}):
            j += step
        else:
            break
    if not 0 <= j < len(kids):
        return None
    differential = kids[j].tag == "mi" and _text_of(kids[j]) in {"d", "δ"}
    if step > 0 and differential and j + 1 < len(kids) and _vector_like(kids[j + 1]):
        return kids[j + 1]
    return kids[j]


def _mark_products(kids: list[ET.Element]) -> None:
    """A centered dot or a cross between vectors (or after or before nabla) is "dot" or "cross";
    between numbers and scalars both stay "times". The vector test is the one `signs.py` uses on
    the text layer: a bold letter or a vector arrow on both sides."""
    for i, kid in enumerate(kids):
        text = _mo_text(kid)
        word = "dot" if text in _DOTS else "cross" if text == "×" else None
        if word is None:
            continue
        left, right = _operand(kids, i, -1), _operand(kids, i, 1)
        if left is None or right is None:
            continue
        if _is_nabla(left) or _is_nabla(right) or (_vector_like(left) and _vector_like(right)):
            kid.set(_MARK, word)


def _distribution_follows(kids: list[ET.Element], start: int) -> bool:
    """A name with an argument list after the sign: `N(0,1)`, `\\mathrm{Bin}(n,p)`, `U[0,1]`."""
    names, j = [], start
    while j < len(kids) and kids[j].tag in {"mi", "mtext", *_SCRIPTS}:
        node = kids[j]
        names.append(_text_of(node[0] if node.tag in _SCRIPTS and len(node) else node))
        j += 1
    word = "".join(names)
    named = bool(word) and (word[0].isupper() or len(word) > 1)
    return named and j < len(kids) and _mo_text(kids[j]) in {"(", "["}


def _mark_tilde(kids: list[ET.Element]) -> None:
    """`x \\sim N(0,1)` is "is distributed as", `a \\sim b` "is similar to", a tilde before a
    number "approximately"."""
    for i, kid in enumerate(kids):
        if kid.tag not in {"mi", "mo"} or _text_of(kid) not in {"~", "∼"}:
            continue
        if i == 0:
            word = "approximately"
        elif _distribution_follows(kids, i + 1):
            word = "is distributed as"
        else:
            word = "is similar to"
        kid.set(_MARK, word)


def _mark_row(kids: list[ET.Element]) -> None:
    _mark_bars(kids)
    _mark_products(kids)
    _mark_tilde(kids)


def _name_letter(node: ET.Element) -> bool:
    """One upright Latin letter: a letter of a name (`\\mathrm{Var}` is three of them)."""
    text = node.text or ""
    return (
        node.tag == "mi"
        and not len(node)
        and node.get("mathvariant") == "normal"
        and len(text) == 1
        and text.isascii()
        and text.isalpha()
    )


def _in_name(kids: list[ET.Element], i: int) -> bool:
    """Is kids[i] a letter in a run of two or more upright letters? Its case is part of how
    the name is spelled ("Var"), not a capital variable, so it adds no "cap"."""
    return _name_letter(kids[i]) and (
        (i > 0 and _name_letter(kids[i - 1])) or (i + 1 < len(kids) and _name_letter(kids[i + 1]))
    )


def _speak_children(node: ET.Element) -> list[str]:
    words: list[str] = []
    kids = list(node)
    _mark_row(kids)
    chemistry = speech_chem.is_chem_row(kids)
    i = 0
    while i < len(kids):
        found = _fenced_table(kids, i) or _nuclide(kids, i) or _degrees(kids, i)
        if found is None and chemistry:
            found = _measured_unit(kids, i)
        if found is None and chemistry:
            found = speech_chem.read_at(kids, i, _speak)
        if found is None and i > 0:
            count = _after_number(kids[i - 1])
            found = _unit_run(kids, i, count) if count is not None else None
        if found is None and _in_name(kids, i):
            found = [kids[i].text or ""], 1
        if found is None:
            words += _speak(kids[i])
            i += 1
        else:
            words += found[0]
            i += found[1]
    return words


_HANDLERS: dict[str, Callable[[ET.Element], list[str]]] = {
    "mi": _speak_mi,
    "mo": _speak_mo,
    "mn": _speak_mn,
    "mtext": _speak_mtext,
    "ms": _speak_mtext,
    "mfrac": _speak_fraction,
    "msub": _speak_msub,
    "msup": _speak_msup,
    "msubsup": _speak_msubsup,
    "munder": _speak_munder,
    "mover": _speak_mover,
    "munderover": _speak_munderover,
    "msqrt": _speak_msqrt,
    "mroot": _speak_mroot,
    "menclose": _speak_menclose,
    "mtable": _speak_mtable,
    "mmultiscripts": _speak_multiscripts,
}


def _speak(node: ET.Element) -> list[str]:
    return _HANDLERS.get(node.tag, _speak_children)(node)


_SPACE_BEFORE_PUNCT = re.compile(r"\s+([;:,])")


def _restore_upright_symbols(root: ET.Element) -> None:
    """A formula or reaction that carries `intent` has its element symbols as plain `mi` (the
    shape Speech Rule Engine reads without "normal", `pipeline.chem_mathml`); the chemistry
    reading tells a symbol from an italic variable by its upright style, so put it back."""
    if root.get("intent") not in ALLOWED_INTENTS:
        return
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "mi":
            continue
        text = node.text or ""
        if len(node) == 0 and node.get("mathvariant") is None and (text in ELEMENTS or text == "e"):
            node.set("mathvariant", "normal")


_OPERATOR_GLYPHS = frozenset("|‖∣∥⟨⟩±∓:…")


def _retag_operator_glyphs(root: ET.Element) -> None:
    r"""The converter writes `\vert`, `\Vert`, `\pm` and `\langle` as `mi` and `|`, `\|` as `mo`
    (gold LaTeX is converted without `mathml_shape`). One print is one reading: they are
    operators in the speech whichever way they arrive."""
    for node in root.iter():
        local = node.tag.rsplit("}", 1)[-1]
        if local == "mi" and len(node) == 0 and (node.text or "") in _OPERATOR_GLYPHS:
            node.tag = node.tag[: -len("mi")] + "mo"


def _join_italic_words(root: ET.Element) -> None:
    r"""`\mathit{diff}` arrives as one italic `mi` per letter, which would be spelled out ("d i f
    f"). Two or more of them in a row are the word. (A plain `diff` has no style on its letters and
    is still the product d, i, f, f.)"""

    def letter(node: ET.Element) -> bool:
        text = node.text or ""
        local = node.tag.rsplit("}", 1)[-1]
        italic = node.get("mathvariant") == "italic"
        return local == "mi" and italic and not len(node) and len(text) == 1 and text.isalpha()

    for parent in list(root.iter()):
        kids, rebuilt, i = list(parent), [], 0
        while i < len(kids):
            end = i
            while end < len(kids) and letter(kids[end]):
                end += 1
            if end - i >= 2:
                word = ET.Element(kids[i].tag, {"mathvariant": "italic"})
                word.text = "".join(k.text or "" for k in kids[i:end])
                rebuilt.append(word)
                i = end
            else:
                rebuilt.append(kids[i])
                i += 1
        if len(rebuilt) != len(kids):
            parent[:] = rebuilt


def course_readings(applied: Iterable[Any] | None) -> dict[str, str]:
    """The course's words for its styled letters ("vector" for a bold d), from the symbols a
    stored result says the conversion was handed (`DocumentResult.notation_applied`): what
    `mathml_to_speech(readings=...)` needs to say a stored equation as it was first said."""
    from readablestem.pipeline import notation_memory

    return notation_memory.readings(applied or ())


def reading_for(equation: EquationFix, readings: Mapping[str, str] | None = None) -> str:
    """The reading of a stored equation as the rules in force say it. A reading made by older
    rules (`EquationFix.speech_version`) is made again from its MathML, at no model cost, so
    the page, the EPUB and Word file, Listen and the review screen say the same words; one a
    person wrote (a negative version) and one the rules cannot read stay as they are."""
    version = equation.speech_version
    if version < 0 or version >= SPEECH_VERSION or not equation.mathml.strip():
        return equation.speech
    try:
        said = mathml_to_speech(equation.mathml, readings=readings)
    except Exception:
        # Not only `SpeechError`: this runs on stored documents each time one is shown, and a
        # fault in the rules on one equation must leave the page with the reading it has.
        return equation.speech
    return said or equation.speech


def refresh_readings(result: Any) -> None:
    """Make every stored reading in a `DocumentResult` that older rules wrote again, in place
    (`reading_for`, with the course's words from the result). A file is written again from a
    stored result through this, so the page, and the EPUB and Word file made from it, say what
    the rules in force say. A reading already current, and one a person wrote, is not touched."""
    readings = course_readings(result.notation_applied)
    for fix in result.fixes:
        equation = fix.equation
        if equation is None or not 0 <= equation.speech_version < SPEECH_VERSION:
            continue
        equation.speech = reading_for(equation, readings)
        equation.speech_version = SPEECH_VERSION


def mathml_to_speech(mathml: str, *, readings: Mapping[str, str] | None = None) -> str:
    """Plain-English reading of presentation MathML. Raises SpeechError on unreadable input.

    `readings` (notation key -> word, from the course notation) reads a styled letter by
    its meaning: {"\U0001d41d": "vector"} makes a bold d "vector d" instead of "bold d"."""
    root = _parse(mathml)
    _drop_trailing_punctuation(root)
    token = _READINGS.set(readings or None)
    followed = _FOLLOWED.set(_mark_followed(root))
    preceded = _PRECEDED.set(_mark_preceded(root))
    try:
        words = _speak(root)
    finally:
        _PRECEDED.reset(preceded)
        _FOLLOWED.reset(followed)
        _READINGS.reset(token)
    text = " ".join(word for word in words if word)
    return _SPACE_BEFORE_PUNCT.sub(r"\1", " ".join(text.split()))
