"""What a thin horizontal rule beside a row of text is (SW36): one answer for every caller.

A rule just under, through or beside the glyphs of one row can be a strike-through, an
underline, a blank to fill in (a gap with a rule under it, or a rule after the last word) or
only a rule: a table's or a border's. Layout reads the blank (`layout._mark_blanks`), the text
pass reads the strike-through (`text_pass.struck_glyphs`), and both ask `classify`, so one
rule has one meaning: a line that crosses the middle of the glyphs it spans is never a blank,
and a gap with nothing above it is never an underline.

The test, in the order it is asked:

1. **Strike.** The rule crosses most of the glyphs it spans, between `STRIKE_LOW` and
   `STRIKE_HIGH` of their height, and is no thicker than `STRIKE_MAX_SIZE_SHARE` of their
   size (a filled bar behind text, a highlight, is not a line through it).
2. **Near a baseline.** Otherwise only glyphs whose baseline is `BELOW_BASELINE_PT` or less
   above the rule (and `ABOVE_BASELINE_PT` below it) count as its row; a row at least
   `NEIGHBOUR_EM` of its size away from either end of the rule is no row of it. A rule with
   no such row is a rule.
3. **Underline, blank, rule.** Glyphs of the row over the rule's span: when they cover at
   least `UNDERLINE_COVER` of it, the rule is an underline; some over it but less, a rule
   (a heading's border runs on past the heading); none over it, and `BLANK_MIN_PT` or longer,
   a blank.

Everything is in display points, the frame of `Element.bbox`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

BBox = tuple[float, float, float, float]

STRIKE_LOW, STRIKE_HIGH = 0.25, 0.65  # of a glyph's height, measured up from its foot
STRIKE_MAX_SIZE_SHARE = 0.12  # of the text's size
STRIKE_MIN_CROSSED = 0.5  # of the glyphs the line touches
STRIKE_X_SLACK = 0.5  # pt: a glyph whose middle is this close to a line's end is under it

BLANK_MIN_PT = 12.0  # a blank is at least this wide
BELOW_BASELINE_PT = 4.0  # a rule this far under the baseline or less is the row's
ABOVE_BASELINE_PT = 1.0  # ... or this far over it (a rule drawn at the baseline)
NEIGHBOUR_EM = 2.0  # the nearest glyph of the row is within this x its size of the rule
UNDERLINE_COVER = 0.5  # share of the rule the glyphs over it cover, for an underline

RuleKind = Literal["strike", "underline", "blank", "rule"]


@dataclass(frozen=True, slots=True)
class Ink:
    """A printed glyph as the classifier sees it. `size` is the font size (0: not known)."""

    bbox: BBox
    size: float
    baseline: float

    @property
    def text_size(self) -> float:
        return self.size or (self.bbox[3] - self.bbox[1])


@dataclass(frozen=True, slots=True)
class Verdict:
    """`kind`, and for a strike or an underline the indexes of the glyphs it is under."""

    kind: RuleKind
    ink: tuple[int, ...] = ()


def _centre(ink: Ink) -> float:
    return (ink.bbox[0] + ink.bbox[2]) / 2


def _middle(ink: Ink) -> float:
    return (ink.bbox[1] + ink.bbox[3]) / 2


def _struck(rule: BBox, ink: Sequence[Ink], under: list[int]) -> tuple[int, ...]:
    y0, y1 = rule[1], rule[3]
    y, thickness = (y0 + y1) / 2, y1 - y0
    touched = [i for i in under if ink[i].bbox[1] <= y <= ink[i].bbox[3]]
    crossed = []
    for i in touched:
        top, foot = ink[i].bbox[1], ink[i].bbox[3]
        if foot - top <= 0 or thickness > STRIKE_MAX_SIZE_SHARE * ink[i].text_size:
            continue
        if STRIKE_LOW <= (foot - y) / (foot - top) <= STRIKE_HIGH:
            crossed.append(i)
    if not crossed or len(crossed) < STRIKE_MIN_CROSSED * len(touched):
        return ()
    top = min(ink[i].bbox[1] for i in crossed)
    foot = max(ink[i].bbox[3] for i in crossed)
    # Every glyph of that row under the line is struck, a comma or a full stop as much as
    # a letter.
    return tuple(i for i in under if top <= _middle(ink[i]) <= foot)


def classify(rule: BBox, ink: Sequence[Ink]) -> Verdict:
    """What the horizontal `rule` is, given the glyphs `ink` on the page around it (the
    row's, or the whole page's: only those it touches or stands beside count)."""
    x0, y0, x1, y1 = rule
    under = [
        i for i, g in enumerate(ink) if x0 - STRIKE_X_SLACK <= _centre(g) <= x1 + STRIKE_X_SLACK
    ]
    if struck := _struck(rule, ink, under):
        return Verdict("strike", struck)
    y = (y0 + y1) / 2
    row = [
        i for i, g in enumerate(ink) if -ABOVE_BASELINE_PT <= y - g.baseline <= BELOW_BASELINE_PT
    ]
    near = [
        i
        for i in row
        if max(x0 - ink[i].bbox[2], ink[i].bbox[0] - x1, 0.0) <= NEIGHBOUR_EM * ink[i].text_size
    ]
    if not near:
        return Verdict("rule")
    over = [i for i in near if i in under]
    if not over:
        return Verdict("blank" if x1 - x0 >= BLANK_MIN_PT else "rule")
    covered = sum(ink[i].bbox[2] - ink[i].bbox[0] for i in over)
    if covered >= UNDERLINE_COVER * (x1 - x0):
        return Verdict("underline", tuple(over))
    return Verdict("rule")
