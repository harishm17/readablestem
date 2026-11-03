# ruff: noqa: RUF001
# (the dotless i and j, and the increment sign TeX uses for Delta, are what is mapped here)
"""Two cross-checks of a transcription against the PDF's text layer that the symbol bag
cannot make: numbers as numbers, and capital against small letters.

`equation_step.text_layer_symbols_differ` compares letters, digits and Greek letters as a
case-folded multiset. That cannot see 36 written as 63, 9.81 as 98.1, 10^23 as 10^32 (the
same digits), or A for a, W/g for w/g, S for s (the same letters, case-folded). Both
slipped through in the review of the held-out run (A11, A12), and the same two checks were
added to the sister checker `stemcheck` after the same review of its own.

Both run only when the bags already agree, so they never repeat a symbol difference.

- `compare_numbers`: each number of the PDF text must be made of the MathML's numbers,
  each used once, joined in any order. A number is a run of digits with its decimal
  points inside it (`NUMBER`). The PDF's
  text glues a script to its base ("10^{23}" prints "1023", "v_0^2" prints "v20") and
  separates digits only by a space (a thin space in "1 000", or the numerator and the
  denominator of a flattened fraction), so a run of numbers split by spaces alone is one
  number there. So "v20" is v_0^2 (the numbers 0 and 2, in either order) and "1023" is
  10^23, but 36 against 63, 8.5 against 85 and 10^23 against 10^32 are differences. The
  search is bounded (`NUMBER_SEARCH_BUDGET`); past the bound the answer is "cannot tell",
  never a difference.
- `compare_case`: letters counted as (letter, is capital) on both sides; a letter whose
  capital and small counts differ is a difference. Small-caps fonts, which print small
  letters for capitals, are not recognised: the text layer has the capital letter, so
  nothing is flagged for them.

Two more things live here, for `EQUATION_GLYPH_CHECKS` (docs/steps.md, "Glyph checks"):
`extend_text_layer` finds the glyphs beside a span that a reading reaching past the span covers,
and `compare_letter_styles` compares each letter's style in the reading's LaTeX
(`latex_letter_styles`) with the style of its glyph (`layout.glyph_style`).

Pure functions of text and MathML; no model, no network. Ported from stemcheck
(checks/maths.py, `compare_numbers`, `compare_case`) and adapted to this module's
`_symbol_bag` conventions.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import defusedxml.ElementTree as DET

from readablestem.core.mathchars import base_char

# A number: a run of digits with its decimal points inside it. A point in front of the
# digits (".5") is not part of the number: in a text layer it is more often a stray full
# stop or operator dot that a fraction's flattened pieces put next to a digit (".42" in
# "1 -13 .42 + 4": 9 of the 10 false flags on 1,748 gold equations printed by Chromium),
# and the symbol check already sees a "0" added to or dropped from ".5".
NUMBER = r"\d+(?:\.\d+)*"
_NUMBER = re.compile(NUMBER)
_PDF_NUMBER = re.compile(NUMBER + r"(?:\s+\d+(?:\.\d+)*)*")
# Steps of the search that makes each PDF number from the MathML's numbers. A real pair
# takes tens of steps; past the budget the pair is "cannot tell", never a difference.
NUMBER_SEARCH_BUDGET = 20_000
_NUMBER_TOKENS = frozenset({"mi", "mn", "mo", "mtext", "ms"})
# Typesetting artefacts that are not letters: dotless i and j under an accent, TeX's
# capital Delta (the Adobe Glyph List maps "Delta" to U+2206 INCREMENT).
_DOTLESS = str.maketrans({"ı": "i", "ȷ": "j", "∆": "Δ"})


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


# ------------------------------------------------------------------------ numbers


@dataclass(frozen=True)
class NumberDiff:
    """The numbers one side cannot make of the other's. Both empty: the numbers agree."""

    pdf: tuple[str, ...]  # PDF numbers the MathML's numbers cannot make
    mathml: tuple[str, ...]  # MathML numbers left over

    @property
    def differ(self) -> bool:
        return bool(self.pdf or self.mathml)


def pdf_numbers(text: str) -> tuple[str, ...]:
    """The PDF text's numbers in reading order (runs split by spaces alone are joined)."""
    folded = unicodedata.normalize("NFKC", text)
    return tuple("".join(m.group().split()) for m in _PDF_NUMBER.finditer(folded))


def mathml_numbers(mathml: str) -> tuple[str, ...]:
    """The numbers of the MathML's tokens in document order (base before script). A
    decimal point written as a token of its own between two numbers joins them
    (`<mn>3</mn><mo>.</mo><mn>0</mn>` is 3.0)."""
    root = DET.fromstring(mathml, forbid_dtd=True)
    leaves = [
        unicodedata.normalize("NFKC", elem.text or "").strip()
        for elem in root.iter()
        if _local(elem.tag) in _NUMBER_TOKENS
    ]
    leaves = [leaf for leaf in leaves if leaf]
    out: list[str] = []
    k = 0
    while k < len(leaves):
        leaf = leaves[k]
        point = leaf == "." and 0 < k < len(leaves) - 1
        if point and out and _NUMBER.fullmatch(leaves[k - 1]) and _NUMBER.fullmatch(leaves[k + 1]):
            out[-1] = f"{out[-1]}.{leaves[k + 1]}"
            k += 2
            continue
        out.extend(_NUMBER.findall(leaf))
        k += 1
    return tuple(out)


def _tilings(number: str, pool: Counter[str], budget: list[int]) -> Iterator[Counter[str]]:
    """Each multiset of numbers from `pool` whose concatenation, in some order, is
    `number` (the PDF glues a script to its base, in either order: "v20" is v_0^2)."""
    if not number:
        yield Counter()
        return
    for part in sorted(pool):
        if pool[part] <= 0 or not number.startswith(part):
            continue
        budget[0] -= 1
        if budget[0] < 0:
            return
        pool[part] -= 1
        for rest in _tilings(number[len(part) :], pool, budget):
            rest[part] += 1
            yield rest
        pool[part] += 1


def compare_numbers(pdf: Sequence[str], mathml: Sequence[str]) -> NumberDiff | None:
    """Do the PDF's numbers and the MathML's numbers agree? None: the search ran out of
    its budget (cannot tell, so no difference is reported)."""
    p, h = Counter(pdf), Counter(mathml)
    same = p & h
    p, h = p - same, h - same
    if not p and not h:
        return NumberDiff((), ())
    order = sorted(p.elements(), key=lambda s: (-len(s), s))
    budget = [NUMBER_SEARCH_BUDGET]

    def solve(i: int, pool: Counter[str]) -> bool:
        if i == len(order):
            return not +pool
        before = Counter(pool)
        for used in _tilings(order[i], pool, budget):
            if solve(i + 1, before - used):
                return True
            if budget[0] < 0:
                return False
        return False

    if solve(0, Counter(h)):
        return NumberDiff((), ())
    if budget[0] < 0:
        return None
    # Which ones to name: drop each PDF number some of the MathML's numbers still make.
    left_pdf: list[str] = []
    pool = Counter(h)
    for number in order:
        made = next(_tilings(number, Counter(pool), [NUMBER_SEARCH_BUDGET]), None)
        if made is None:
            left_pdf.append(number)
        else:
            pool -= made
    return NumberDiff(tuple(left_pdf), tuple(sorted(pool.elements())))


def numbers_differ(text_layer: str | None, mathml: str) -> NumberDiff | None:
    """The number comparison of a text layer and a transcription, for a pair whose letter
    and digit bags already agree (the caller checks). None: no text layer, or no number
    in it, or the search could not tell."""
    if not text_layer:
        return None
    pdf = pdf_numbers(text_layer)
    if not pdf:
        return None
    return compare_numbers(pdf, mathml_numbers(mathml))


# --------------------------------------------------------------------------- case


@dataclass(frozen=True)
class CaseRow:
    letter: str  # case-folded
    pdf_capital: int
    pdf_small: int
    mathml_capital: int
    mathml_small: int


def case_bag(text: str) -> Counter[tuple[str, bool]]:
    """Letters as (case-folded letter, is capital), counted. Modifier letters (accents a
    LaTeX hat leaves in a text layer) are not letters; digits have no case."""
    out: Counter[tuple[str, bool]] = Counter()
    for ch in unicodedata.normalize("NFKC", text).translate(_DOTLESS):
        if ch.isalpha() and unicodedata.category(ch) != "Lm":
            capital = ch.isupper()
            for folded in ch.casefold():
                out[(folded, capital)] += 1
    return out


def compare_case(text_layer: str | None, mathml: str) -> tuple[CaseRow, ...]:
    """Letters whose capital and small counts differ between the text layer and the
    MathML (empty: the case agrees, or there is no text layer). Meant for a pair whose
    case-folded bags already agree (the caller checks)."""
    if not text_layer:
        return ()
    root = DET.fromstring(mathml, forbid_dtd=True)
    pdf = case_bag(text_layer)
    got = case_bag(" ".join(root.itertext()))
    rows = []
    for letter in sorted({f for f, _ in pdf} | {f for f, _ in got}):
        counts = (pdf[(letter, True)], pdf[(letter, False)])
        found = (got[(letter, True)], got[(letter, False)])
        if counts != found:
            rows.append(CaseRow(letter, counts[0], counts[1], found[0], found[1]))
    return tuple(rows)


# ------------------------------------------------------------------------ extent

# The glyphs `extend_text_layer` may take in on either side of the span, nearest first.
EXTENT_REACH = 40


def symbol_bag(text: str) -> Counter[str]:
    """Letters, digits and Greek letters of a text, case-folded and counted: what the symbol
    check compares (`equation_step.text_layer_symbols_differ`). Modifier letters (the accent a
    LaTeX hat leaves in a text layer) are accents, not symbols."""
    folded = unicodedata.normalize("NFKC", text).translate(_DOTLESS).casefold()
    return Counter(ch for ch in folded if ch.isalnum() and unicodedata.category(ch) != "Lm")


def mathml_bag(mathml: str) -> Counter[str]:
    """`symbol_bag` of a MathML's text."""
    root = DET.fromstring(mathml, forbid_dtd=True)
    return symbol_bag(" ".join(root.itertext()))


Glyph = tuple[str, str | None]  # (character as the text layer writes it, letter style)


@dataclass(frozen=True)
class Extent:
    """The glyphs a reading covers when it reaches past the span the text layer holds."""

    text: str  # the text layer of the whole extent, in reading order
    glyphs: tuple[Glyph, ...]  # every glyph in it, in reading order
    before: int  # glyphs taken in left of the span
    after: int  # glyphs taken in right of it


def _prefix_bags(glyphs: Sequence[Glyph]) -> list[Counter[str]]:
    bags: list[Counter[str]] = [Counter()]
    for char, _style in glyphs[:EXTENT_REACH]:
        bags.append(bags[-1] + symbol_bag(char))
    return bags


def _key(bag: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((k, v) for k, v in bag.items() if v > 0))


def extend_text_layer(
    span_text: str | None,
    mathml: str,
    span: Sequence[Glyph],
    before: Sequence[Glyph],
    after: Sequence[Glyph],
) -> Extent | None:
    """The glyphs a reading covers when it reaches past its span, or None.

    A reading that goes on past the text layer's span (the span ends where the characters do,
    the formula where the author's maths does: `T = 2\\pi` boxed, `\\sqrt{m/k}` right after it
    on the line) is compared with the glyphs it covers, not with the span. The span's letters
    and digits are taken as given; the reading is covered when the glyphs next to it, on either
    side and nearest first, make up exactly the letters and digits it has beyond the span. The
    least extent that does is the one (`before` is taken in left of the span, `after` right of
    it). None when the reading already agrees with the span, has less than the span has, or is
    made by no extent: then the span stands and the difference is a difference.
    """
    if not span_text:
        return None
    have, want = symbol_bag(span_text), mathml_bag(mathml)
    if have == want or have - want:
        return None
    missing = want - have
    left, right = _prefix_bags(before), _prefix_bags(after)
    nearest: dict[tuple[tuple[str, int], ...], int] = {}
    for j, bag in enumerate(right):
        nearest.setdefault(_key(bag), j)
    best: tuple[int, int] | None = None
    for i, bag in enumerate(left):
        if bag - missing:  # the left side alone has more than is missing
            break
        j = nearest.get(_key(missing - bag))
        if j is not None and (best is None or i + j < sum(best)):
            best = (i, j)
    if best is None:
        return None
    i, j = best
    taken_left, taken_right = tuple(before[:i]), tuple(after[:j])
    pieces = [c for c, _ in reversed(taken_left)] + [span_text] + [c for c, _ in taken_right]
    return Extent(
        " ".join("".join(pieces).split()),
        (*reversed(taken_left), *span, *taken_right),
        i,
        j,
    )


# ------------------------------------------------------------------------- styles

ITALIC, UPRIGHT, BOLD, BOLD_ITALIC = "italic", "upright", "bold", "bold-italic"
# LaTeX commands that set the style of the letters they apply to: the group that follows them
# (`\mathrm{d}`), or the single letter (`\mathbf v`). A style the glyph side cannot tell from
# a font name (sans-serif, monospace) is None: those letters are left out of the comparison.
_STYLE_COMMANDS: dict[str, str | None] = {
    **dict.fromkeys(("mathrm", "textrm", "text", "mbox", "textup", "operatorname"), UPRIGHT),
    **dict.fromkeys(("mathbf", "textbf"), BOLD),
    **dict.fromkeys(("boldsymbol", "bm", "pmb"), BOLD_ITALIC),
    **dict.fromkeys(("mathit", "textit", "mathnormal"), ITALIC),
    "mathbb": "double-struck",
    **dict.fromkeys(("mathcal", "mathscr"), "script"),
    "mathfrak": "fraktur",
    **dict.fromkeys(("mathsf", "textsf", "mathtt", "texttt"), None),
}
# Old-style switches: the rest of the enclosing group is in the style.
_STYLE_SWITCHES: dict[str, str | None] = {
    "rm": UPRIGHT,
    "bf": BOLD,
    "it": ITALIC,
    "cal": "script",
    "sf": None,
    "tt": None,
}
# A command and its argument that print no letters of the formula.
_NO_LETTERS = frozenset(
    (
        *("begin", "end", "label", "tag", "ref", "eqref", "cite", "color", "textcolor"),
        *("hspace", "vspace", "phantom", "hphantom", "vphantom", "href", "url"),
    )
)
# Functions TeX sets upright: their letters are upright whatever precedes them.
_FUNCTIONS = frozenset(
    (
        *("sin", "cos", "tan", "cot", "sec", "csc", "sinh", "cosh", "tanh", "coth"),
        *("arcsin", "arccos", "arctan", "log", "ln", "lg", "exp", "lim", "limsup", "liminf"),
        *("max", "min", "sup", "inf", "det", "dim", "ker", "deg", "gcd", "arg", "hom", "Pr"),
    )
)
_COMMAND = re.compile(r"\\([A-Za-z]+)\*?")


def _group_end(latex: str, start: int) -> int:
    """The index of the brace that closes the one at `start` (the end of the text when none)."""
    depth = 0
    k = start
    while k < len(latex):
        char = latex[k]
        if char == "\\":
            k += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return k
        k += 1
    return len(latex)


def latex_letter_styles(latex: str) -> Counter[tuple[str, str]]:
    """The ASCII letters of a LaTeX formula, each with the style it is set in: (letter, style)
    counted. A bare letter is italic; `\\mathrm{...}`, `\\text{...}` and the names of functions
    (`\\sin`, `\\lim`) are upright; `\\mathbf` is bold, `\\boldsymbol` and `\\bm` bold italic,
    `\\mathbb` double-struck, `\\mathcal` and `\\mathscr` script, `\\mathfrak` fraktur. Read from
    the LaTeX because the MathML drops what the glyph says: latex2mathml writes `\\mathrm{d}` as
    a plain `<mi>d</mi>`. Letters set in a style the glyphs cannot tell are left out."""
    out: Counter[tuple[str, str]] = Counter()

    def walk(text: str, style: str | None) -> None:
        i = 0
        while i < len(text):
            char = text[i]
            if char == "\\":
                found = _COMMAND.match(text, i)
                if found is None:
                    i += 2  # an escaped symbol: \{ \, \\
                    continue
                name, i = found.group(1), found.end()
                if name in _STYLE_SWITCHES:
                    style = _STYLE_SWITCHES[name]
                elif name in _STYLE_COMMANDS or name in _NO_LETTERS:
                    while i < len(text) and text[i] == " ":
                        i += 1
                    if i < len(text) and text[i] == "[" and name in _NO_LETTERS:
                        i = text.find("]", i) + 1 or len(text)
                    if i < len(text) and text[i] == "{":
                        end = _group_end(text, i)
                        if name in _STYLE_COMMANDS:
                            walk(text[i + 1 : end], _STYLE_COMMANDS[name])
                        arg_is_name = name == "begin" and text[i + 1 : end].startswith("array")
                        i = end + 1
                        if arg_is_name and text[i : i + 1] == "{":
                            i = _group_end(text, i) + 1  # the column specification
                    elif i < len(text) and name in _STYLE_COMMANDS and text[i].isalpha():
                        if text[i].isascii() and (named := _STYLE_COMMANDS[name]):
                            out[(text[i], named)] += 1
                        i += 1
                elif name in _FUNCTIONS:
                    out.update((letter, UPRIGHT) for letter in name)
            elif char == "{":
                end = _group_end(text, i)
                walk(text[i + 1 : end], style)
                i = end + 1
            else:
                if char.isascii() and char.isalpha() and style:
                    out[(char, style)] += 1
                i += 1

    walk(latex, ITALIC)
    return out


@dataclass(frozen=True)
class StyleRow:
    """A letter whose styles in the PDF and in the reading do not match."""

    letter: str
    pdf: tuple[tuple[str, int], ...]  # (style, count) the glyphs show
    reading: tuple[tuple[str, int], ...]  # (style, count) the reading has


def compare_letter_styles(glyphs: Sequence[Glyph], latex: str) -> tuple[StyleRow, ...]:
    """Letters whose style differs between the glyphs of the PDF and the reading's LaTeX:
    italic against upright (`d` for `\\mathrm{d}`), bold against bold italic (`\\mathbf` for
    `\\boldsymbol`), script, double-struck or fraktur against plain. Each letter's glyphs are
    paired with the reading's letters of the same style first; what is left of either side
    differs only if a glyph of a known style meets a reading letter of another. A glyph whose
    font says no style (a symbol font, an unknown face) may stand for any style, so it makes
    no difference of its own. Empty: the styles agree, or nothing is known."""
    known: dict[str, Counter[str]] = {}
    unknown: Counter[str] = Counter()
    for char, style in glyphs:
        letter = base_char(char)
        if not (len(letter) == 1 and letter.isascii() and letter.isalpha()):
            continue
        if style is None:
            unknown[letter] += 1
        else:
            known.setdefault(letter, Counter())[style] += 1
    reading: dict[str, Counter[str]] = {}
    for (letter, style), n in latex_letter_styles(latex).items():
        reading.setdefault(letter, Counter())[style] += n
    rows = []
    for letter in sorted(known.keys() & reading.keys()):
        pdf, got = known[letter], reading[letter]
        leftover_pdf = sum((pdf - got).values())
        leftover_reading = sum((got - pdf).values())
        # A glyph of unknown style may be the reading's spare letter; the rest must pair up.
        if min(leftover_pdf, max(0, leftover_reading - unknown[letter])) > 0:
            rows.append(StyleRow(letter, tuple(sorted(pdf.items())), tuple(sorted(got.items()))))
    return tuple(rows)
