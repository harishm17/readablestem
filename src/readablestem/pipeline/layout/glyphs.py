"""Characters: `_Char` (one glyph with its font, size and style), the font classes and styles
read from font names, character classes, and the text cleaning every later stage relies on.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, replace
from functools import lru_cache

from readablestem.core.mathchars import base_char, char_variant, styled_char
from readablestem.core.types import BBox
from readablestem.pipeline.parse import GREEK, MATH_FONT, is_math_alphanumeric

# Glyphs that start list items. Word draws bullets in the Symbol font (as U+2022; its
# private-use U+F0B7 and the Wingdings bullets are mapped to these by `font_codes`), and Symbol
# is a maths font, so without this every bullet would be an inline equation.
# Beamer draws its triangles with the AMS symbol font (U+25B6), also a maths font.
_BULLET_GLYPHS = frozenset(
    "\u2022\u25e6\u25aa\u25ab\u25a0\u25a1\u25cf\u25cb\u2023\u2043\u27a2\u2713\u2714\u25ba\u25b8"
    "\u25b6\u25b9\u25bb\u27a4\u25c6\u25c7\u2666\u2756"
)


# Applied to every piece of text taken from the document: invisible characters go; the
# inline-marker brackets (core.types.INLINE_MARKER) become look-alike white square
# brackets, so text in the PDF cannot fake a marker (layout is the only producer of
# markers). Private-use codes of symbol fonts are mapped by `font_codes`, by font name.
_TEXT_MAP: dict[int, str | None] = {
    **dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff")),
    ord("\u27e6"): "\u301a",
    ord("\u27e7"): "\u301b",
}


# Character classes for maths detection. Symbols that only occur in maths (signs such as
# the minus are in _SIGNS: in a text font they also print dashes and option flags):
_STRONG_MATH = frozenset(
    "\u2211\u220f\u222b\u222e\u221a\u2202\u2207\u221e"  # sum prod int oint sqrt partial nabla inf
    "\u2206"  # increment: TeX's text-font capital Delta (OT1 cmr) extracts as this
    "\u2264\u2265\u2260\u2248\u2261\u221d"  # <= >= != approx equiv proportional
    "\u2192\u2190\u2194\u21d2\u21d4"  # arrows
    "\u22c5\u00d7\u00f7"  # cdot times div
    "\u2208\u2209\u2282\u2286\u2283\u2287\u222a\u2229\u2200\u2203"  # sets and quantifiers
    "\u2032\u2033\u2220\u22a5\u2225\u27e8\u27e9"  # primes angle perp parallel brackets
)


# "+-*/=<>^_|~", and the middle dot a body font draws for a product (Chromium prints
# "7 · 6 · 5" with U+00B7 in the text font, TeX writes it U+22C5 in a maths font): an
# operator, so a run of maths goes on through it instead of being cut there.
_OPERATORS = frozenset("+-*/=<>^_|~\u00b7\u2219")
_SIGNS = frozenset("\u2212\u00b1\u2213")  # minus, plus-minus, minus-plus
_BRACKETS = frozenset("()[]{}")

_SCRIPT_DIGITS = frozenset(
    "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"  # superscript 0-9
    "\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089"  # subscript 0-9
)


# TeX's bold fonts: CMBX (bold extended), CMB, CMMIB (bold maths italic, \boldsymbol and
# \bm) and CMBSY (bold symbols); EC/cm-super's SFBX.
_BOLD_FONT = re.compile(
    r"bold|black|heavy|semibold|demi|cmbx|cmb\d|cmmib|cmbsy|sfbx|-medi$|\bbd\b|,bd|-bd",
    re.IGNORECASE,
)


_ITALIC_FONT = re.compile(r"italic|oblique|cmti|cmsl|sfti|-it$|,it|ital", re.IGNORECASE)

# Bold italic: `\boldsymbol` and `\bm` (CMMIB, newtx's bmi, Nimbus MediItal, ...), against `\mathbf`
# (CMBX, upright). The text layer gives both as a bold letter; the equation step restores it.
_BOLD_ITALIC_FONT = re.compile(
    r"cmmib|bmi\d*$|bold-?ital|bold-?obl|medi-?ital|bdit|mt2bmi[ts]", re.IGNORECASE
)


# Regular-weight text and maths fonts known to have a bold face that PDFs name as bold
# (CMBX, Times-Bold, DejaVuSerif-Bold, ...), or OpenType maths fonts that write bold as
# Unicode maths bold. A letter drawn in one of them, not bold, is known to be plain, so
# the equation step may treat a bold reading of it as a real difference. Fonts that
# cannot show a letter's style in the text layer (MSBM double-struck, EUSM and RSFS
# script, EUFM fraktur, CMSY calligraphic, Symbol) are deliberately not in the list.
_REGULAR_FACE = re.compile(
    r"^(?:cmr\d|cmmi\d|cmti\d|cmsl\d|cmss\d|cmssi\d|cmu|lmroman|lmmathitalic|lmsans|"
    r"latinmodern|sfrm|sfti|sfss|txr|txmi|pxr|pxmi|times|nimbus|liberation|dejavu|arial|"
    r"helvetica|calibri|cambria|carlito|caladea|georgia|palatino|bookantiqua|garamond|"
    r"charter|verdana|tahoma|segoe|noto(?:serif|sans)|sourceserif|sourcesans|opensans|"
    r"roboto|lato|stix|xits|texgyre|linlibertine|libertinus|minion|myriad|utopia)",
    re.IGNORECASE,
)


_MONO_FONT = re.compile(r"mono|courier|consolas|menlo|inconsolata|cmtt|sftt", re.IGNORECASE)

# TeX fonts whose letters are a style the text layer drops: MSBM's R is \mathbb{R}, the
# reals, but its text layer says "R". (pattern, style, capitals only): MSBM, bbm and
# dsrom draw double-struck capitals; CMSY's capitals are \mathcal and EUSM, EUSB and RSFS
# (\mathscr) draw script capitals; EUFM and EUFB are fraktur in both cases.
_LETTER_STYLE_FONTS = (
    (re.compile(r"^(?:msbm\d|bbm\d|bbmss|dsrom|dsss)", re.IGNORECASE), "double-struck", True),
    (re.compile(r"^(?:cmsy\d|cmbsy\d|eusm\d|eusb\d|rsfs)", re.IGNORECASE), "script", True),
    (re.compile(r"^(?:eufm\d|eufb\d)", re.IGNORECASE), "fraktur", False),
)


_BOLD_FLAG = 16
_ITALIC_FLAG = 2
NOTE_MARK_FLAG = 1 << 21  # `_Char.flags` of a footnote's mark (`page._mark_notes`): never maths
_MONO_FLAG = 8

# The letter styles `glyph_style` names (the equation step compares them with the reading's).
STYLE_ITALIC = "italic"
STYLE_UPRIGHT = "upright"
STYLE_BOLD = "bold"
STYLE_BOLD_ITALIC = "bold-italic"

# Math-italic faces that no font-name rule above calls italic: CMMI, Latin Modern and TeX
# Gyre math italics, newtx and pxfonts' math italic.
_MATH_ITALIC_FONT = re.compile(
    r"^(?:cmmi\d|lmmi\d|lmmathitalic|txmi|pxmi|newtxmi|mathit|stixmathital|stixgeneral-?ital|"
    r"mt2mi[tsf])",
    re.IGNORECASE,
)


_CODE_POINT_STYLE = {
    "italic": STYLE_ITALIC,
    "bold": STYLE_BOLD,
    "bold-italic": STYLE_BOLD_ITALIC,
    "script": "script",
    "bold-script": "script",
    "double-struck": "double-struck",
    "fraktur": "fraktur",
    "bold-fraktur": "fraktur",
}


# Maths symbol and extension fonts: their capitals are calligraphic (CMSY, Latin Modern Symbols)
# and their flags say italic whatever the letter is, so a letter in one says nothing but script
# (`_LETTER_STYLE_FONTS`, which names the ones the text layer can show).
_MATH_SYMBOL_FONT = re.compile(
    r"^(?:lmmathsymbols|lmmathextension|cmsy|cmbsy|cmex|msam|msbm|txsy|txex|pxsy|pxex|"
    r"symbol|stixmathsymbols|stixsizeone|mnsy|rsfs|eusm|eusb|eufm|eufb|mt2sy|mt2ex)",
    re.IGNORECASE,
)


def glyph_style(c: _Char) -> str | None:
    """The style of one printed letter, or None for anything but an ASCII letter and for a
    letter whose font says nothing: italic (CMMI, an italic face, a Unicode math italic
    letter), upright (a regular face: CMR, Times), bold (CMBX), bold-italic (CMMIB, what
    `\\bm` and `\\boldsymbol` print), script (CMSY and EUSM capitals), double-struck (MSBM) or
    fraktur. The fonts `_Char.styled` writes into `Element.text`, and those it leaves plain."""
    base = base_char(c.c)
    if not (len(base) == 1 and base.isascii() and base.isalpha()):
        return None
    if (variant := char_variant(c.c)) is not None:
        return _CODE_POINT_STYLE.get(variant)
    style = _letter_style(c.font)
    if style is not None and (base.isupper() or not style[1]):
        return style[0]
    face = c.font.split("+", 1)[-1].replace(" ", "")
    if _MATH_SYMBOL_FONT.match(face):
        return None
    if _bold_italic(c.font) or (c.bold and c.italic):
        return STYLE_BOLD_ITALIC
    if c.bold:
        return STYLE_BOLD
    if c.italic or _MATH_ITALIC_FONT.match(face):
        return STYLE_ITALIC
    if c.mono:
        return None
    return STYLE_UPRIGHT if _REGULAR_FACE.match(face) else None


@dataclass(frozen=True, slots=True)
class _Char:
    c: str
    x0: float
    y0: float
    x1: float
    y1: float
    baseline: float
    size: float
    font: str
    flags: int
    colour: int = 0  # the span's sRGB colour (0xRRGGBB)
    # A glyph of an inline stacked fraction (numerator or denominator on a thin bar inside
    # a line of text): evidence that its run is maths (`_lift_stacked_fractions`).
    stacked: bool = False
    # A letter of a Greek word: two or more letters of the Greek script in a text font
    # (`mark_greek_words`). Prose, not the symbols of a formula.
    greek_word: bool = False
    # A small capital: a capital letter set smaller than its line's text but on its baseline
    # (`mark_small_capitals`). Text, not a script; a lower-case letter of the source when a
    # full-size capital leads the word.
    small_cap: bool = False

    @property
    def blank(self) -> bool:
        return self.c.isspace()

    @property
    def bold(self) -> bool:
        return bool(self.flags & _BOLD_FLAG) or _font_traits(self.font)[0]

    @property
    def italic(self) -> bool:
        return bool(self.flags & _ITALIC_FLAG) or _font_traits(self.font)[1]

    @property
    def math(self) -> bool:
        """Maths whatever the context: maths font, math-italic letter, Greek, symbol.

        A minus or plus-minus sign in a text font is not enough on its own: groff prints
        command-line options as "\u2212d", and dashes come out as minus signs too. List
        bullets are never maths, even in the Symbol font Word draws them in.
        """
        c = self.c
        if self.blank or c in _BULLET_GLYPHS:
            return False
        if _font_traits(self.font)[2]:
            return True
        return (
            is_math_alphanumeric(c)
            or c in _STRONG_MATH
            or (bool(GREEK.match(c)) and not self.greek_word)
        )

    @property
    def mono(self) -> bool:
        return bool(self.flags & _MONO_FLAG) or _font_traits(self.font)[3]

    @property
    def styled(self) -> str:
        """The character with its style kept as a Unicode math letter (bold d -> U+1D41D;
        MSBM's R -> U+211D, the reals), as Chromium-printed PDFs already store it.
        Equation text layers use this so the equation step can check that a bold vector
        stayed bold and that the reals stayed the reals."""
        style = _letter_style(self.font) if self.c.isascii() and self.c.isalpha() else None
        if style is not None and (self.c.isupper() or not style[1]):
            return styled_char(self.c, style[0])
        if self.c.isascii() and self.c.isalpha() and _bold_italic(self.font):
            return styled_char(self.c, "bold-italic")
        return styled_char(self.c, "bold") if self.bold else self.c

    @property
    def known_regular(self) -> bool:
        """A letter known to be plain: not bold, in a font with a bold face, and not a
        styled maths code point (a maths bold or double-struck letter says its own style)."""
        if not self.c.isalpha() or self.bold or char_variant(self.c) not in (None, "italic"):
            return False
        return bool(_REGULAR_FACE.match(self.font.split("+", 1)[-1].replace(" ", "")))


@lru_cache(maxsize=1024)
def face_traits(font: str, flags: int) -> tuple[bool, bool, bool]:
    """(bold, italic, monospaced) of a face for the runs of emphasis (`text_pass.style_runs`),
    by the tests `_Char` makes of a glyph (the font's flags, then its name). A letter set in a
    maths font is a variable or a symbol, not emphasis (the equation path owns it), so such a
    face has none."""
    if _font_traits(font)[2]:
        return False, False, False
    face = _Char(" ", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, font, flags)
    return face.bold, face.italic, face.mono


def _greek_letter(c: str) -> bool:
    """A letter of the Greek script, accented ones included (U+0370 to U+03FF, U+1F00 to U+1FFF)."""
    return (0x370 <= ord(c[0]) <= 0x3FF or 0x1F00 <= ord(c[0]) <= 0x1FFF) and c.isalpha()


def mark_greek_words(chars: list[_Char]) -> list[_Char]:
    """`chars` with `greek_word` set on every letter of a Greek word: a run of two or more
    letters of the Greek script in a text font is prose. A lone Greek letter, and one in a
    maths font, stay maths (`_Char.math`), except the article "\u03b7" or "\u03bf" (and
    capitals) with one blank between it and a Greek word: it is a word of the sentence,
    not a variable. Two letters are a word only on a line that holds a Greek word of three
    or more: alone in a line of another language they are a product of two symbols (a
    change of angle typed as capital delta and theta). A combining accent belongs to its
    letter."""
    out = list(chars)
    n = len(chars)

    def text_greek(k: int) -> bool:
        return _greek_letter(chars[k].c) and not _font_traits(chars[k].font)[2]

    spans: list[tuple[int, int]] = []  # [start, end) of each run of Greek letters and accents
    i = 0
    while i < n:
        if not text_greek(i):
            i += 1
            continue
        j = i + 1
        while j < n and (text_greek(j) or unicodedata.category(chars[j].c[0]) == "Mn"):
            j += 1
        spans.append((i, j))
        i = j

    def letters(span: tuple[int, int]) -> list[int]:
        return [k for k in range(*span) if _greek_letter(chars[k].c)]

    long_word = any(len(letters(span)) >= 3 for span in spans)
    for at, span in enumerate(spans):
        run = letters(span)
        if len(run) == 1 and chars[run[0]].c in _GREEK_ARTICLES:
            next_word = (
                at + 1 < len(spans)
                and spans[at + 1][0] == span[1] + 1
                and chars[span[1]].blank
                and len(letters(spans[at + 1])) >= 2
            )
            last_word = (
                at > 0
                and spans[at - 1][1] == span[0] - 1
                and chars[span[0] - 1].blank
                and len(letters(spans[at - 1])) >= 2
            )
            if not (next_word or last_word):
                continue
        elif len(run) < 2 or (len(run) == 2 and not long_word):
            continue
        for k in run:
            out[k] = replace(chars[k], greek_word=True)
    return out


_GREEK_ARTICLES = frozenset("\u03b7\u03bf\u0397\u039f")  # eta, omicron: "the"


@lru_cache(maxsize=1024)
def _bold_italic(font: str) -> bool:
    return bool(_BOLD_ITALIC_FONT.search(font.split("+", 1)[-1]))


@lru_cache(maxsize=1024)
def _letter_style(font: str) -> tuple[str, bool] | None:
    """(style, capitals only) when the font draws its letters in a style the text layer
    drops (`_LETTER_STYLE_FONTS`), else None."""
    name = font.split("+", 1)[-1]
    for pattern, style, capitals in _LETTER_STYLE_FONTS:
        if pattern.match(name):
            return style, capitals
    return None


@lru_cache(maxsize=1024)
def _font_family(font: str) -> str:
    """The typeface of a font name, without subset tag, weight, style or design size:
    "ABCDEF+LiberationSans-Bold" and "LiberationSans" are "liberationsans", "CMBX12" is
    "cmbx". Bold and regular faces of one family compare equal; Sans and Serif do not."""
    name = font.split("+", 1)[-1].lower()
    return re.split(r"[-,]", name, maxsplit=1)[0].rstrip("0123456789")


@lru_cache(maxsize=1024)
def _font_traits(font: str) -> tuple[bool, bool, bool, bool]:
    """(bold, italic, maths, monospaced) from a font name like "ABCDEF+DejaVuSerif-Bold"."""
    name = font.split("+", 1)[-1]
    return (
        bool(_BOLD_FONT.search(name)),
        bool(_ITALIC_FONT.search(name)),
        bool(MATH_FONT.search(name)),
        bool(_MONO_FONT.search(name)),
    )


def _regular_letters(chars: Iterable[_Char]) -> str | None:
    """`Element.regular_letters` of an equation: its letters known to be plain."""
    letters = sorted({base_char(c.c) for c in chars if c.known_regular})
    return "".join(letters) or None


def _char_class(ch: _Char) -> str:
    """W space, C code (monospaced), M maths, D digit, O operator, B bracket, L letter,
    P other."""
    c = ch.c
    if ch.blank:
        return "W"
    if ch.flags & NOTE_MARK_FLAG:
        return "P"
    if ch.math:
        return "M"
    if ch.mono:
        return "C"  # code listings are full of "x = 5"; never maths
    if c.isdigit() or c in _SCRIPT_DIGITS:
        return "D"
    if c in _OPERATORS or c in _SIGNS:
        return "O"
    if c in _BRACKETS:
        return "B"
    if c.isalpha() and c not in _ACCENTS:  # a TeX hat (U+02C6) is a "modifier letter"
        return "L"
    return "P"


def _union(boxes: Iterable[BBox]) -> BBox:
    boxes = list(boxes)
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _rounded(box: BBox) -> BBox:
    return (round(box[0], 2), round(box[1], 2), round(box[2], 2), round(box[3], 2))


def clean_text(text: str) -> str:
    """`_clean` for other modules (the image page pass cleans a model's text the same way)."""
    return _clean(text)


def _clean(text: str) -> str:
    """Whitespace collapsed, soft hyphens/zero-width/control characters removed, marker
    brackets neutralised (`_TEXT_MAP`), TeX's negation slash joined to its relation."""
    text = text.translate(_TEXT_MAP)
    text = "".join(c for c in text if c.isspace() or unicodedata.category(c) != "Cc")
    if "\u0338" in text:
        text = _negate_relations(text)
    return " ".join(text.split())


# TeX writes \neq as a zero-width slash (U+0338, CMSY's "negationslash") drawn over "=".
_NEGATED = {
    "=": "\u2260",
    "<": "\u226e",
    ">": "\u226f",
    "\u2264": "\u2270",
    "\u2265": "\u2271",
    "\u2208": "\u2209",
    "\u2261": "\u2262",
    "\u223c": "\u2241",
    "\u2282": "\u2284",
    "\u2286": "\u2288",
}


_NEGATION = re.compile(
    "\\s*\u0338\\s*([" + "".join(_NEGATED) + "])|([" + "".join(_NEGATED) + "])\\s*\u0338"
)


def _negate_relations(text: str) -> str:
    """ "A\u0338 = 0" -> "A \u2260 0"; a slash with no relation next to it is dropped."""
    text = _NEGATION.sub(lambda m: " " + _NEGATED[m.group(1) or m.group(2)], text)
    return text.replace("\u0338", "")


# Spacing accents TeX sets as separate glyphs above a letter (hat, tilde, bar, dot,
# check, breve): inside an inline formula they do not end it.
_ACCENTS = frozenset("\u02c6\u02dc\u00af\u02d9\u00a8\u02c7\u02d8")
