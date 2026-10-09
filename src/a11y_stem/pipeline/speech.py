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
- text is read verbatim, except words that are a single maths symbol ("=" -> "equals").
"""

# This module is a table of maths symbols; characters that look like Latin letters
# (Greek nu, the multiplication sign, the tilde operator) are the point, not a mistake.
# ruff: noqa: RUF001

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from contextvars import ContextVar

import defusedxml
import defusedxml.ElementTree as DET

from a11y_stem.core.notation import canonical_from_parts
from a11y_stem.eval.equations import normalize

MAX_DEPTH = 64


class SpeechError(ValueError):
    """The input is not MathML that can be read. The message never contains the input."""


GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ϵ": "epsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ϑ": "theta", "ι": "iota", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "ο": "omicron", "π": "pi", "ϖ": "pi",
    "ρ": "rho", "ϱ": "rho", "σ": "sigma", "ς": "sigma", "τ": "tau", "υ": "upsilon",
    "φ": "phi", "ϕ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
}  # fmt: skip
GREEK.update(
    {ch.upper(): f"capital {name}" for ch, name in list(GREEK.items()) if ch.upper() != ch}
)

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
    "∨": "or", "∪": "union", "∩": "intersection",
    # arrows
    "→": "right arrow", "⟶": "right arrow", "←": "left arrow", "⟵": "left arrow",
    "↔": "left right arrow", "⇒": "implies", "⟹": "implies", "⇐": "is implied by",
    "⇔": "if and only if", "⟺": "if and only if", "↦": "maps to", "↑": "up arrow",
    "↓": "down arrow", "⇌": "equilibrium arrow",
    # fences and punctuation
    "(": "open parenthesis", ")": "close parenthesis", "[": "open bracket",
    "]": "close bracket", "{": "open brace", "}": "close brace", "|": "vertical bar",
    "‖": "double vertical bar", "⟨": "open angle bracket", "⟩": "close angle bracket",
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


def _char_words(ch: str) -> str:
    if ch in GREEK:
        return GREEK[ch]
    if ch in SYMBOLS:
        return SYMBOLS[ch]
    if ch.isascii() and ch.isalpha():
        return f"capital {ch}" if ch.isupper() else ch
    if ch.isascii():
        return ch
    name = unicodedata.name(ch, "")
    if not name:
        return ch
    # Letterlike symbols (U+211D double-struck R): "double-struck capital R".
    base = unicodedata.normalize("NFKC", ch)
    words = name.lower().split()
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
    for table in (SYMBOLS, FUNCTIONS, LIMIT_OPERATORS):
        if text in table:
            return [table[text]]
    if len(text) == 1:
        return [_char_words(text)]
    return [text]


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


def _is_plain_token(node: ET.Element) -> bool:
    return node.tag in {"mi", "mn", "mtext"} and _text_of(node) not in FUNCTIONS


def _is_simple(node: ET.Element) -> bool:
    """One or two plain tokens (no operators): safe to read without "end" markers."""
    if node.tag == "mrow":
        return 0 < len(node) <= 2 and all(_is_plain_token(child) for child in node)
    return _is_plain_token(node)


def _is_signed_token(node: ET.Element) -> bool:
    """A token, or a minus/plus sign plus a token (`10^{-3}`)."""
    if node.tag == "mrow" and len(node) == 2 and node[0].tag == "mo":
        return _text_of(node[0]) in {"−", "-", "+"} and node[1].tag in _TOKENS
    return node.tag in _TOKENS


def _speak_fraction(node: ET.Element) -> list[str]:
    if len(node) != 2:
        return _speak_children(node)
    num, den = node[0], node[1]
    if node.get("linethickness") == "0":
        return [*_speak(num), "choose", *_speak(den)]
    if _is_simple(num) and _is_simple(den):
        return [*_speak(num), "over", *_speak(den)]
    return ["the fraction", *_speak(num), "over", *_speak(den), "end fraction"]


def _power_words(sup: ET.Element) -> list[str]:
    text = _text_of(sup)
    if sup.tag == "mn" and text == "2":
        return ["squared"]
    if sup.tag == "mn" and text == "3":
        return ["cubed"]
    if text in {"′", "″", "‴", "°", "∘"} and sup.tag in _TOKENS:
        return ["degrees"] if text in {"°", "∘"} else [SYMBOLS[text]]
    if _is_signed_token(sup):
        return ["to the power", *_speak(sup)]
    return ["to the power", *_speak(sup), "end power"]


def _sub_words(sub: ET.Element) -> list[str]:
    if _is_simple(sub) or _is_signed_token(sub):
        return ["sub", *_speak(sub)]
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


def _speak_msub(node: ET.Element) -> list[str]:
    if len(node) != 2:
        return _speak_children(node)
    base, sub = node[0], node[1]
    limits = _with_limits(base, sub, None)
    return limits if limits is not None else [*_speak(base), *_sub_words(sub)]


def _speak_msup(node: ET.Element) -> list[str]:
    if len(node) != 2:
        return _speak_children(node)
    base, sup = node[0], node[1]
    limits = _with_limits(base, None, sup)
    return limits if limits is not None else [*_speak(base), *_power_words(sup)]


def _speak_msubsup(node: ET.Element) -> list[str]:
    if len(node) != 3:
        return _speak_children(node)
    base, sub, sup = node[0], node[1], node[2]
    limits = _with_limits(base, sub, sup)
    if limits is not None:
        return limits
    return [*_speak(base), *_sub_words(sub), *_power_words(sup)]


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


def _speak_mtable(node: ET.Element) -> list[str]:
    """Rows as "row N: ..." (only when there are several). Cells of an aligned equation
    (some cell starts with a relation) read as one line; matrix cells are comma-separated.
    """
    rows = [row for row in node if row.tag in {"mtr", "mlabeledtr"}]
    cells_by_row = [
        [
            cell
            for cell in (row[1:] if row.tag == "mlabeledtr" else list(row))
            if len(cell) or cell.text
        ]
        for row in rows
    ]
    aligned = any(_starts_with_relation(c) for cells in cells_by_row for c in cells)
    spoken_rows = []
    for cells in cells_by_row:
        parts = [" ".join(_speak(cell)) for cell in cells]
        spoken_rows.append((" " if aligned else ", ").join(p for p in parts if p))
    if len(spoken_rows) == 1:
        return [spoken_rows[0]] if spoken_rows[0] else []
    joined = "; ".join(f"row {i}: {text}" for i, text in enumerate(spoken_rows, 1))
    return [joined]


def _speak_children(node: ET.Element) -> list[str]:
    words: list[str] = []
    for child in node:
        words += _speak(child)
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
}


def _speak(node: ET.Element) -> list[str]:
    return _HANDLERS.get(node.tag, _speak_children)(node)


_SPACE_BEFORE_PUNCT = re.compile(r"\s+([;:,])")


def mathml_to_speech(mathml: str, *, readings: Mapping[str, str] | None = None) -> str:
    """Plain-English reading of presentation MathML. Raises SpeechError on unreadable input.

    `readings` (notation key -> word, from the course notation) reads a styled letter by
    its meaning: {"\U0001d41d": "vector"} makes a bold d "vector d" instead of "bold d"."""
    root = _parse(mathml)
    _drop_trailing_punctuation(root)
    token = _READINGS.set(readings or None)
    try:
        words = _speak(root)
    finally:
        _READINGS.reset(token)
    text = " ".join(word for word in words if word)
    return _SPACE_BEFORE_PUNCT.sub(r"\1", " ".join(text.split()))
