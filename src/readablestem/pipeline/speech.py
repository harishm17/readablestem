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

from readablestem.core.notation import canonical_from_parts
from readablestem.eval.equations import normalize
from readablestem.pipeline import speech_chem
from readablestem.pipeline.speech_nuclides import nuclide_words
from readablestem.pipeline.speech_units import unit_words

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
    return limits if limits is not None else [*_speak(base), *_sub_words(sub)]


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
    return limits if limits is not None else [*_speak(base), *_power_words(sup)]


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
        parts = [" ".join(_speak(cell)) for cell in cells]
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
        parts = [_letters(k) for k in kids[i + 1 : i + 1 + used]]
        if len(parts) < used or any(p is None for p in parts):
            continue
        words = nuclide_words("".join(p for p in parts if p), *numbers)
        if words is not None:
            return [words], used + 1
    return None


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
        words = unit_words("".join(pieces[:used]), count)
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


def _speak_children(node: ET.Element) -> list[str]:
    words: list[str] = []
    kids = list(node)
    chemistry = speech_chem.is_chem_row(kids)
    i = 0
    while i < len(kids):
        found = _fenced_table(kids, i) or _nuclide(kids, i) or _degrees(kids, i)
        if found is None and chemistry:
            found = speech_chem.read_at(kids, i, _speak)
        if found is None and i > 0:
            count = _after_number(kids[i - 1])
            found = _unit_run(kids, i, count) if count is not None else None
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
