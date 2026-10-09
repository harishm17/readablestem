"""Notation keys: "bold d", "\\mathbf{d}" and the styled letter U+1D41D are one symbol.

An instructor writes a course's notation the way they think of it ("bold d is a vector",
"R with double strike is the reals"). The equation step meets the same symbol as LaTeX
(`\\mathbf{d}`), as MathML (`<mi mathvariant="bold">d</mi>`) or as a styled letter in the
PDF's text layer. `canonical_symbol` turns any of these spellings into one key, which
`NotationEntry.canonical` stores and the notation builder matches on:

- a letter (Latin or Greek) or a digit in a style is the styled Unicode character
  (`core.mathchars.styled_char`): bold d -> U+1D41D, double-struck R -> U+211D (the
  reals), script L -> U+2112. Italic is the default style of a maths letter, so it
  counts as plain: a bold italic omega and a bold omega are one key;
- a plain letter is itself ("e" for Euler's number); Greek symbol variants fold to the
  letter (U+03D5 phi symbol -> U+03C6 phi, \\varepsilon -> epsilon);
- an arrow or a hat over it is a combining mark after it: \\vec{v} -> "v" U+20D7,
  \\hat{x} -> "x" U+0302 (NFC, so a letter with a precomposed form uses it);
- a few operator symbols are themselves: nabla, partial, h-bar, script l, infinity,
  empty set.

Anything else (two symbols, an unknown command or word, a style Unicode has no letter
for, such as a double-struck omega) is None: "not understood", so the course page can ask
the instructor to write it another way instead of storing a key nothing will match.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from a11y_stem.core.mathchars import base_char, char_variant, styled_char

MAX_SYMBOL_CHARS = 64  # NotationEntry.symbol and .canonical, and the database columns

# Accents, by name, and the combining character each becomes in a key.
ACCENTS = {"vec": "\u20d7", "hat": "\u0302"}  # COMBINING RIGHT ARROW ABOVE, CIRCUMFLEX
_ACCENT_OF_MARK = {mark: name for name, mark in ACCENTS.items()}

# Symbols that are keys on their own (never styled or accented here).
SYMBOLS = {
    "nabla": "\u2207",
    "partial": "\u2202",
    "hbar": "\u210f",
    "ell": "\u2113",
    "infty": "\u221e",
    "infinity": "\u221e",
    "emptyset": "\u2205",
    "varnothing": "\u2205",
}
_SYMBOL_CHARS = frozenset(SYMBOLS.values())

# Style traits -> the mathvariant of the key. Italic is the default for a maths letter, so
# it adds nothing; any combination not listed (bold double-struck, script fraktur) has no
# Unicode letters and is not understood.
_VARIANT_OF_TRAITS: dict[frozenset[str], str | None] = {
    frozenset(): None,
    frozenset({"bold"}): "bold",
    frozenset({"double-struck"}): "double-struck",
    frozenset({"script"}): "script",
    frozenset({"bold", "script"}): "bold-script",
    frozenset({"fraktur"}): "fraktur",
    frozenset({"bold", "fraktur"}): "bold-fraktur",
    frozenset({"sans-serif"}): "sans-serif",
    frozenset({"bold", "sans-serif"}): "bold-sans-serif",
    frozenset({"monospace"}): "monospace",
}
# A styled character's own mathvariant -> its traits (italic dropped).
_TRAITS_OF_VARIANT: dict[str, frozenset[str]] = {
    "bold": frozenset({"bold"}),
    "italic": frozenset(),
    "bold-italic": frozenset({"bold"}),
    "double-struck": frozenset({"double-struck"}),
    "script": frozenset({"script"}),
    "bold-script": frozenset({"bold", "script"}),
    "fraktur": frozenset({"fraktur"}),
    "bold-fraktur": frozenset({"bold", "fraktur"}),
    "sans-serif": frozenset({"sans-serif"}),
    "bold-sans-serif": frozenset({"bold", "sans-serif"}),
    "sans-serif-italic": frozenset({"sans-serif"}),
    "sans-serif-bold-italic": frozenset({"bold", "sans-serif"}),
    "monospace": frozenset({"monospace"}),
}

# LaTeX commands: styles (and plain wrappers, which add nothing), accents, Greek letters.
_LATEX_STYLES: dict[str, frozenset[str]] = {
    "mathbf": frozenset({"bold"}),
    "boldsymbol": frozenset({"bold"}),
    "bm": frozenset({"bold"}),
    "pmb": frozenset({"bold"}),
    "mathbb": frozenset({"double-struck"}),
    "mathcal": frozenset({"script"}),
    "mathscr": frozenset({"script"}),
    "mathfrak": frozenset({"fraktur"}),
    "mathsf": frozenset({"sans-serif"}),
    "mathtt": frozenset({"monospace"}),
    "mathrm": frozenset(),
    "mathit": frozenset(),
    "mathnormal": frozenset(),
}
_LATEX_ACCENTS = {"vec": "vec", "overrightarrow": "vec", "hat": "hat"}
_GREEK = frozenset(
    {
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota",
        "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi", "rho", "sigma", "tau",
        "upsilon", "phi", "chi", "psi", "omega",
    }
)  # fmt: skip
# LaTeX's variant letters are the same letter for a notation key.
_GREEK_VARIANTS = {
    "varepsilon": "epsilon",
    "vartheta": "theta",
    "varpi": "pi",
    "varrho": "rho",
    "varsigma": "sigma",
    "varphi": "phi",
    "varkappa": "kappa",
}

# Words, after lower-casing and turning hyphens into spaces; longest phrases first.
_STYLE_WORDS: dict[str, frozenset[str]] = {
    "blackboard bold": frozenset({"double-struck"}),
    "double struck": frozenset({"double-struck"}),
    "double strike": frozenset({"double-struck"}),
    "doublestruck": frozenset({"double-struck"}),
    "blackboard": frozenset({"double-struck"}),
    "bb": frozenset({"double-struck"}),
    "sans serif": frozenset({"sans-serif"}),
    "sans": frozenset({"sans-serif"}),
    "bold face": frozenset({"bold"}),
    "boldface": frozenset({"bold"}),
    "bold": frozenset({"bold"}),
    "bf": frozenset({"bold"}),
    "script": frozenset({"script"}),
    "calligraphic": frozenset({"script"}),
    "cal": frozenset({"script"}),
    "curly": frozenset({"script"}),
    "fraktur": frozenset({"fraktur"}),
    "gothic": frozenset({"fraktur"}),
    "blackletter": frozenset({"fraktur"}),
    "monospace": frozenset({"monospace"}),
    "typewriter": frozenset({"monospace"}),
    "italic": frozenset(),
    "upright": frozenset(),
    "plain": frozenset(),
    "roman": frozenset(),
}
_ACCENT_WORDS = {"vec": "vec", "arrow": "vec", "hat": "hat"}
_CASE_WORDS = {"capital": True, "uppercase": True, "upper": True, "small": False,
               "lowercase": False, "lower": False}  # fmt: skip
_FILLER_WORDS = frozenset(
    {"an", "the", "letter", "symbol", "with", "in", "on", "over", "above", "top", "of", "it", "its"}
)
_WORD_SPLIT = re.compile(r"[\s\-]+")
_H_BAR = re.compile(r"\bh[\s\-]bar\b", re.IGNORECASE)
_LATEX_TOKEN = re.compile(r"\\[A-Za-z]+|\\.|[{}]|\S")


@dataclass(frozen=True)
class _Symbol:
    base: str  # one character: a Latin or Greek letter, a digit, or one of SYMBOLS
    traits: frozenset[str] = frozenset()
    accent: str | None = None  # a key of ACCENTS


def canonical_symbol(text: str) -> str | None:
    """The notation key for one symbol written in words, LaTeX or Unicode; None when it
    is not one symbol this module understands (see the module docstring)."""
    if not isinstance(text, str):
        return None
    raw = unicodedata.normalize("NFC", text).strip()
    while len(raw) >= 2 and raw.startswith("$") and raw.endswith("$"):
        raw = raw[1:-1].strip()  # "$\mathbf{d}$" as pasted from a LaTeX source
    if not raw or len(raw) > MAX_SYMBOL_CHARS:
        return None
    symbol = _from_latex(raw) if raw.startswith("\\") else None
    if symbol is None:
        symbol = _from_unicode(raw)
    if symbol is None:
        symbol = _from_words(raw)
    return None if symbol is None else _key(symbol)


def canonical_from_parts(
    base: str, variant: str | None = None, accent: str | None = None
) -> str | None:
    """The key for a symbol already taken apart, e.g. from MathML: `base` is the token's
    text (one character, styled or not), `variant` its mathvariant, `accent` "vec" or
    "hat" for an arrow or hat above it. None when it is not one symbol."""
    symbol = _from_unicode(unicodedata.normalize("NFC", base.strip()))
    if symbol is None:
        return None
    if variant and variant != "normal":
        traits = _TRAITS_OF_VARIANT.get(variant)
        if traits is None:
            return None
        symbol = _styled(symbol, traits)
    if accent is not None:
        symbol = _accented(symbol, accent)
    return None if symbol is None else _key(symbol)


# --------------------------------------------------------------------------- building


def _styled(symbol: _Symbol, traits: frozenset[str]) -> _Symbol:
    return _Symbol(symbol.base, symbol.traits | traits, symbol.accent)


def _accented(symbol: _Symbol, accent: str) -> _Symbol | None:
    if accent not in ACCENTS or (symbol.accent is not None and symbol.accent != accent):
        return None
    return _Symbol(symbol.base, symbol.traits, accent)


def _key(symbol: _Symbol) -> str | None:
    if symbol.traits not in _VARIANT_OF_TRAITS:
        return None
    variant = _VARIANT_OF_TRAITS[symbol.traits]
    base = symbol.base
    if base in _SYMBOL_CHARS:
        if variant is not None or symbol.accent is not None:
            return None  # a bold nabla, an arrow over infinity: not a notation we key
        return base
    if base.isascii() and base.isdigit() and variant is None:
        return None  # a plain digit is not notation
    letter = base if variant is None else styled_char(base, variant)
    if variant is not None and letter == base:
        return None  # Unicode has no such styled character (a double-struck omega)
    mark = ACCENTS[symbol.accent] if symbol.accent else ""
    key = unicodedata.normalize("NFC", letter + mark)
    return key if len(key) <= MAX_SYMBOL_CHARS else None


def _letter(ch: str) -> str | None:
    """`ch` as a plain base: an ASCII letter or digit, or a Greek letter (symbol variants
    and the micro sign folded to the letter); None for anything else."""
    if ch.isascii():
        return ch if ch.isalnum() else None
    folded = unicodedata.normalize("NFKC", ch)
    name = unicodedata.name(folded, "") if len(folded) == 1 else ""
    if name.startswith(("GREEK SMALL LETTER ", "GREEK CAPITAL LETTER ")):
        return folded
    return None


# --------------------------------------------------------------------------- spellings


def _from_unicode(text: str) -> _Symbol | None:
    """One character, styled or not, optionally followed by one accent mark."""
    chars = unicodedata.normalize("NFD", text)
    if not chars:
        return None
    first, marks = chars[0], chars[1:]
    if len(marks) > 1 or (marks and marks not in _ACCENT_OF_MARK):
        return None
    accent = _ACCENT_OF_MARK.get(marks) if marks else None
    if first in _SYMBOL_CHARS:
        return _Symbol(first, accent=accent)
    variant = char_variant(first)
    if variant is not None:
        base = _letter(base_char(first))
        if base is None:
            return None
        return _Symbol(base, _TRAITS_OF_VARIANT[variant], accent)
    base = _letter(first)
    return None if base is None else _Symbol(base, accent=accent)


def _from_latex(text: str) -> _Symbol | None:
    tokens = _LATEX_TOKEN.findall(text)
    parsed = _latex_expr(tokens, 0)
    if parsed is None:
        return None
    symbol, end = parsed
    return symbol if end == len(tokens) else None


def _latex_expr(tokens: list[str], i: int) -> tuple[_Symbol, int] | None:
    """One LaTeX symbol from tokens[i]: `{...}`, a style or accent command and its
    argument, a Greek letter or symbol command, or one character."""
    if i >= len(tokens):
        return None
    token = tokens[i]
    if token == "{":
        inner = _latex_expr(tokens, i + 1)
        if inner is None or inner[1] >= len(tokens) or tokens[inner[1]] != "}":
            return None
        return inner[0], inner[1] + 1
    if token.startswith("\\") and len(token) > 1 and token[1:].isalpha():
        name = token[1:]
        if name in _LATEX_STYLES or name in _LATEX_ACCENTS:
            argument = _latex_expr(tokens, i + 1)
            if argument is None:
                return None
            inner, end = argument
            if name in _LATEX_STYLES:
                styled = _styled(inner, _LATEX_STYLES[name])
            else:
                styled = _accented(inner, _LATEX_ACCENTS[name])
            return None if styled is None else (styled, end)
        base = _greek(name) or SYMBOLS.get(name)
        return None if base is None else (_Symbol(base), i + 1)
    if token in "{}" or token.startswith("\\"):
        return None
    symbol = _from_unicode(token)
    return None if symbol is None else (symbol, i + 1)


def _greek(name: str) -> str | None:
    """ "omega" -> U+03C9, "Omega" -> U+03A9 (LaTeX names, also in words)."""
    lower = _GREEK_VARIANTS.get(name.lower(), name.lower())
    if lower not in _GREEK:
        return None
    unicode_name = "LAMDA" if lower == "lambda" else lower.upper()  # Unicode's spelling
    case = "CAPITAL" if name[0].isupper() else "SMALL"
    try:
        return unicodedata.lookup(f"GREEK {case} LETTER {unicode_name}")
    except KeyError:
        return None


def _from_words(text: str) -> _Symbol | None:
    """ "bold d", "double-struck R", "R with double strike", "v with an arrow",
    "capital omega in bold": style words, accent words and filler around one symbol."""
    words = [w for w in _WORD_SPLIT.split(_H_BAR.sub("hbar", text)) if w]
    traits: set[str] = set()
    accents: set[str] = set()
    capital: bool | None = None
    candidates: list[str] = []
    i = 0
    while i < len(words):
        pair = " ".join(words[i : i + 2]).lower() if i + 1 < len(words) else ""
        lower = words[i].lower()
        if pair in _STYLE_WORDS:
            traits |= _STYLE_WORDS[pair]
            i += 2
            continue
        if lower in _STYLE_WORDS:
            traits |= _STYLE_WORDS[lower]
        elif lower in _ACCENT_WORDS:
            accents.add(_ACCENT_WORDS[lower])
        elif lower in _CASE_WORDS:
            capital = _CASE_WORDS[lower]
        elif lower not in _FILLER_WORDS:
            candidates.append(words[i])
        i += 1
    # "a" is an article next to another symbol ("R with a double strike") and the letter
    # a when it is the only candidate ("a with a hat").
    if len(candidates) > 1:
        others = [c for c in candidates if c != "a"]
        candidates = others or ["a"]
    if len(candidates) != 1 or len(accents) > 1:
        return None
    symbol = _word_symbol(candidates[0])
    if symbol is None:
        return None
    if capital is not None and symbol.base.isalpha():
        letter = symbol.base.upper() if capital else symbol.base.lower()
        symbol = _Symbol(letter, symbol.traits, symbol.accent)
    symbol = _styled(symbol, frozenset(traits))
    return _accented(symbol, accents.pop()) if accents else symbol


def _word_symbol(word: str) -> _Symbol | None:
    """One word that names a symbol: a character ("d", a styled letter, a symbol), a
    Greek letter's name ("omega", "Omega") or a symbol's name ("nabla")."""
    if len(unicodedata.normalize("NFD", word)) <= 2:
        symbol = _from_unicode(word)
        if symbol is not None:
            return symbol
    greek = _greek(word)
    if greek is not None:
        return _Symbol(greek)
    name = word.lower()
    return _Symbol(SYMBOLS[name]) if name in SYMBOLS else None
