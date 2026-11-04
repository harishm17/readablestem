"""Where an inline formula starts and ends, read from the glyphs on the page (T11).

`layout._inline_runs` cuts the maths out of a line of prose. Its edges decide whether a formula
drops a symbol it should have kept (`v_{2j}^{\\rho}` read as `v`, `\\rho_{m,q}(x)` as `q(x)`) or
swallows a word it should have left (`h = 1.0 x 10^7 m`). These rules look at what the page
shows at an edge, not at what the characters spell: the baseline and size of a glyph (a comma
set as a subscript is part of the index), the gap between two glyphs (a space of no width
between a radical and its radicand), the face of a letter (an upright word glued to an opening
bracket is a function name). Each rule is a pure function of the row's characters, so it can be
tested on a hand-made row; `layout` calls them from `_inline_runs` and `_trim_run`.

Every rule only moves an edge of a run that was already found as maths: none makes a stretch of
text a formula, so no text is lost (the paragraph keeps whatever stays outside a run), and a rule
that is unsure leaves the edge where it was.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class Glyph(Protocol):
    """What the rules read of a character of a row (`layout._Char`)."""

    @property
    def c(self) -> str: ...

    @property
    def x0(self) -> float: ...

    @property
    def x1(self) -> float: ...

    @property
    def size(self) -> float: ...

    @property
    def blank(self) -> bool: ...


# Punctuation that an index holds: the comma of `\rho_{m,q}`, the colon of `g_{1:T}`, the
# semicolon of `\gamma_{\alpha;\lambda}`. In a text font, which is why the maths class misses it.
SCRIPT_PUNCTUATION = frozenset(",;:")
# A gap narrower than this (x font size) between two glyphs is no space at all: the text layer
# puts a blank there when the page draws a glyph wider than its box (a radical over its radicand).
NO_GAP = 0.1


def _neighbour(chars: Sequence[Glyph], i: int, step: int) -> int | None:
    k = i + step
    while 0 <= k < len(chars):
        if not chars[k].blank:
            return k
        k += step
    return None


def script_punctuation(chars: Sequence[Glyph], script: Sequence[bool], i: int) -> bool:
    """Is the glyph at `i` a comma, colon or semicolon inside a sub- or superscript?

    It is one when the glyph is raised or lowered (`script`) and the glyph before it and the one
    after it, blanks aside, are raised or lowered too: `m,q` of `\\rho_{m,q}`. A comma at the
    end of an index (`x_i,`), or a comma at the baseline, ends the formula as before.
    """
    if chars[i].c not in SCRIPT_PUNCTUATION or not script[i]:
        return False
    before, after = _neighbour(chars, i, -1), _neighbour(chars, i, 1)
    return (
        before is not None
        and after is not None
        and script[before]
        and script[after]
        and chars[before].c not in SCRIPT_PUNCTUATION
    )


def touching(chars: Sequence[Glyph], i: int) -> bool:
    """Do the glyphs on either side of the blank at `i` touch, gap under `NO_GAP` of the size?

    A blank like that is no word space and no thin space either: `_between_formulas` and
    `_word_space_between_operands` read it as the end of an operand, and cut a radical from its
    radicand ("O(\\sqrt T)" read as "O(\\sqrt").
    """
    before, after = _neighbour(chars, i, -1), _neighbour(chars, i, 1)
    if before is None or after is None:
        return False
    gap = chars[after].x0 - chars[before].x1
    return gap < NO_GAP * max(chars[before].size, chars[after].size)


# Names of operators and functions that papers set upright (`\operatorname{Cov}`, `\mathrm{supp}`,
# `\Pr`) and that the layout's list of maths words (`layout._MATH_WORDS`) does not hold. The text
# layer cannot tell an upright name from a word of the prose (both are in the text font), so the
# rule never guesses: only these exact spellings count, and an acronym such as ARFIMA(1, d, 1) or
# a word of the sentence is left alone. A name counts when it touches the bracket of its argument.
OPERATOR_NAMES = frozenset(
    {
        "Pr", "Cov", "Corr", "Var", "Std", "Exp", "supp", "vec", "vech", "Vec", "Tr", "Rank",
        "Diag", "diag", "Unif", "Bern", "Bin", "Poi", "Cat", "KL", "argmax", "argmin", "softmax",
        "sign", "span", "proj", "dist", "dom", "Re", "Im", "Hess", "Jac", "ReLU", "erfc", "atan",
        "atan2", "expit", "logit", "Ker", "Ran",
    }
)  # fmt: skip


def function_name(chars: Sequence[Glyph], start: int, end: int) -> bool:
    """Is the word `chars[start:end]` one of `OPERATOR_NAMES` with an opening parenthesis after
    it and no gap (`NO_GAP` of the size)? "Cov(X)" is; "function(s)", "ARFIMA(1, d, 1)" and
    "Mg(OH)2" are not."""
    if end >= len(chars) or "".join(c.c for c in chars[start:end]) not in OPERATOR_NAMES:
        return False
    opening, last = chars[end], chars[end - 1]
    return opening.c == "(" and opening.x0 - last.x1 < NO_GAP * max(opening.size, last.size)


UNIT_LETTERS = 6  # most letters of the unit an author sets after a number ("mol", "kg m", "Hz")


def trailing_unit(
    chars: Sequence[Glyph],
    start: int,
    end: int,
    *,
    upright: Sequence[bool],
    script: Sequence[bool],
) -> int | None:
    """Where the formula `chars[start:end]` ends when its tail is the unit of a number, or None.

    A unit is upright letters of the text font, each factor with the raised or lowered figures of
    its exponent, after a digit: "24h", "5 m", "6.6 x 10^-11 m^3 kg^-1". Authors set it outside
    the dollar signs. `upright[k]` says glyph k is a letter in upright type (no italic, no maths
    font) and not a script; a letter after a variable ("x m") has no digit before it and is no
    unit. The index returned is just after the digit.
    """
    k = end
    letters = 0
    while True:
        while k > start and chars[k - 1].blank:
            k -= 1
        exponent = k
        while exponent > start and script[exponent - 1] and not chars[exponent - 1].c.isalpha():
            exponent -= 1
        base = exponent
        while base > start and upright[base - 1]:
            base -= 1
        letters += exponent - base
        if base == exponent or letters > UNIT_LETTERS:
            return None  # no unit letters here, or too many for a unit
        before = base
        while before > start and chars[before - 1].blank:
            before -= 1
        if before == start:
            return None
        if not chars[before - 1].c.isdigit():
            k = before  # another factor of the unit ("kg" before "m s^-1")
            continue
        # A digit: the number the unit follows, or the exponent of an earlier factor ("m3 kg").
        figures = before
        while figures > start and script[figures - 1] and not chars[figures - 1].c.isalpha():
            figures -= 1
        if figures < before and figures > start and upright[figures - 1]:
            k = before
            continue
        if figures < before and not (figures > start and chars[figures - 1].c.isdigit()):
            return None  # raised figures of no number ("15O", "226Ra": a nuclide) or an index
        return before


def percent_of_number(chars: Sequence[Glyph], i: int) -> bool:
    """Is the glyph at `i` a percent sign set right after a digit ("5%")? In a formula it is the
    unit of the number, `\\phi = 5\\%`; a run that ends before it drops the sign."""
    return chars[i].c == "%" and i > 0 and chars[i - 1].c.isdigit()


# ---------------------------------------------------------------- short formulas the runs miss

RELATION_REASON = "relation symbol before a number"
# The relations a TeX author sets in a maths font in front of a number: approximately, at least,
# at most, much more or less than, less than, more than.
_NUMBER_RELATIONS = frozenset("\u2248\u223c\u2264\u2265\u226a\u226b<>")
_AFTER_NUMBER = frozenset(",.;:)]")


def relation_runs(
    chars: Sequence[Glyph], maths: Sequence[bool], script: Sequence[bool]
) -> list[tuple[int, int, str]]:
    """A relation glyph in a maths font and the number after it (`\\approx 0.14`, `\\ge 2`,
    `\\geq 90\\%`), when the glyph before the relation is no maths: the formula starts at the
    relation, and the run finder drops a leading relation as the tail of a formula that a line
    break cut. A relation after a digit or another maths glyph is part of a longer formula."""
    found: list[tuple[int, int, str]] = []
    for i, ch in enumerate(chars):
        if ch.c not in _NUMBER_RELATIONS or not maths[i] or script[i]:
            continue
        before = next((k for k in range(i - 1, -1, -1) if not chars[k].blank), None)
        if before is not None and (maths[before] or chars[before].c.isdigit()):
            continue
        k = i + 1
        while k < len(chars) and chars[k].blank:
            k += 1
        j = k
        while j < len(chars) and (chars[j].c.isdigit() or chars[j].c == "."):
            j += 1
        number = "".join(c.c for c in chars[k:j])
        if not number or not number[0].isdigit() or number.endswith(".") or number.count(".") > 1:
            continue
        if j < len(chars) and chars[j].c == "%":
            j += 1  # `\geq 90\%`
        after = chars[j].c if j < len(chars) else " "
        if not (after.isspace() or after in _AFTER_NUMBER):
            continue
        found.append((i, j, RELATION_REASON))
    return found
