"""Columns and reading order: the gutter between columns, full-width lines, section breaks and
the order rows are read in.
"""

from __future__ import annotations

import bisect
import statistics
from collections.abc import Iterable, Sequence
from typing import Protocol

from readablestem.core.types import BBox
from readablestem.pipeline.layout.furniture import _margin_zone
from readablestem.pipeline.layout.rows import _column_of
from readablestem.pipeline.layout.shared import _Row


class _Ordering(Protocol):
    """What stands in place of a gutter when the page is read by a region tree
    (`regions.RegionTree`): it orders the boxes itself."""

    def order(
        self,
        boxes: Sequence[BBox],
        body: float,
        context: Sequence[BBox] = (),
        *,
        blocks: bool = False,
    ) -> list[int]: ...


Gutter = tuple[float, float] | _Ordering | None

MIN_GUTTER = 8.0  # narrowest white gap between two text columns
MAX_GUTTER_CROSSINGS = 24  # lines that may cross a gutter (centred titles, captions)
MAX_HEAD_STOPS = 8  # runs of lines across the page that a page's columns are looked for under
# Under a head the thinner side of a gutter has at least this share of the lines of the thicker
# side. Measured on the corpus: the five pages whose columns lie under a head have 0.65 or more;
# a page of one column with a ruled step table (a few lines in the right cell, the steps' text
# in the left one) has 0.13 and 0.21, and was read column by column.
MIN_HEAD_COLUMN_BALANCE = 0.4


def _find_gutter(boxes: list[BBox], *, slide: bool = False) -> tuple[float, float] | None:
    """The white vertical gap between two text columns, or None for one column.

    Looks for an x-interval near the middle that (almost) no narrow line touches, with at
    least three lines on each side whose left edges line up (a real column has a margin)
    and whose rows are long enough to be prose (two table columns are not text columns).
    A couple of lines may cross it: a centred title or caption does not undo the columns.
    """
    if len(boxes) < (4 if slide else 6):
        return None
    left, right = min(b[0] for b in boxes), max(b[2] for b in boxes)
    width = right - left
    if width < 150:
        return None
    narrow = [b for b in boxes if b[2] - b[0] <= 0.55 * width]
    gutter = _gutter_of(narrow, left, width, slide)
    if gutter is not None or slide:
        return gutter
    # A first page: a title, the authors and their affiliations, an abstract across the page,
    # the columns under it. The centred lines of that head cross the gutter more often than a
    # column's own lines may, so the columns are looked for under the head: under each line
    # run of lines across the page, the top of the page first. Two columns hold about as many
    # lines as each other; a gap between a column of steps and a few lines beside it (a ruled
    # step table on a page of one column) is no gutter.
    stops: list[float] = []
    across: float | None = None
    wide = [b for b in boxes if b[2] - b[0] > 0.55 * width]
    for b in sorted(boxes, key=lambda b: b[1]):
        if b[2] - b[0] > 0.55 * width:
            across = b[3] if across is None else max(across, b[3])
        elif across is not None and b[1] >= across - 1.0:
            stops.append(across)
            across = None
    for top in stops[:MAX_HEAD_STOPS]:
        under = [b for b in narrow if b[1] >= top - 1.0]
        gutter = _gutter_of(under, left, width, slide)
        if gutter is not None:
            lefts = sum(b[2] <= gutter[0] + 0.5 for b in under)
            rights = sum(b[0] >= gutter[1] - 0.5 for b in under)
            if min(lefts, rights) < MIN_HEAD_COLUMN_BALANCE * max(lefts, rights):
                continue
            # Columns under a head run unbroken: a line across the page between the top and the
            # bottom of the two columns means these are not columns (an exam's questions, each
            # a line across with its options standing two by two under it).
            columns = [b for b in under if b[2] <= gutter[0] + 0.5 or b[0] >= gutter[1] - 0.5]
            first, last = min(b[1] for b in columns), max(b[3] for b in columns)
            if not any(w[1] < last and w[3] > first for w in wide):
                return gutter
    return None


def _gutter_of(
    narrow: list[BBox], left: float, width: float, slide: bool
) -> tuple[float, float] | None:
    """The gutter of these narrow lines: the gap that fewest of them cross, with the columns'
    lines on both sides (`_gutter_with`)."""
    events = sorted([(b[0], 1) for b in narrow] + [(b[2], -1) for b in narrow])
    # Strictest first: with crossings allowed, the gap would also take in the ragged ends
    # of the longest left-column lines. A gutter crossed by more than MAX_GUTTER_CROSSINGS
    # lines is no gutter (and the cap keeps a page of thousands of rows linear).
    for allowed in range(min(max(2, len(narrow) // 10), MAX_GUTTER_CROSSINGS) + 1):
        gutter = _gutter_with(narrow, events, allowed, left, width, slide)
        if gutter is not None:
            return gutter
    return None


def _gutter_with(
    narrow: list[BBox],
    events: list[tuple[float, int]],
    allowed: int,
    left: float,
    width: float,
    slide: bool = False,
) -> tuple[float, float] | None:
    best: tuple[float, float] | None = None
    for gap0, gap1 in _sparse_intervals(events, allowed):
        middle = (gap0 + gap1) / 2
        if gap1 - gap0 < MIN_GUTTER or not left + 0.25 * width <= middle <= left + 0.75 * width:
            continue
        if best is None or gap1 - gap0 > best[1] - best[0]:
            best = (gap0, gap1)
    if best is None:
        return None
    lefts = [b for b in narrow if b[2] <= best[0] + 0.5]
    rights = [b for b in narrow if b[0] >= best[1] - 0.5]
    # A slide's column has two or three short lines (a heading and a bullet), whose left edges
    # line up two by two (a bullet's text, its continuation); a page's has three.
    wanted = 2 if slide else 3
    for side in (lefts, rights):
        if len(side) < wanted:
            return None
        if slide:
            aligned = max(sum(abs(b[0] - a[0]) <= 2.5 for b in side) for a in side)
        else:
            edge = min(b[0] for b in side)
            aligned = sum(b[0] <= edge + 2.5 for b in side)
        if aligned < wanted:
            return None
        if statistics.median(_line_widths(side)) < (0.15 if slide else 0.3) * width:
            return None
    return best


def _words_both_sides(rows: Sequence[_Row], gutter: tuple[float, float], height: float) -> bool:
    """Each side of `gutter` has a row with a word of three letters or more."""
    split = (gutter[0] + gutter[1]) / 2
    has = {"L": False, "R": False}
    for row in rows:
        if row.vertical or _margin_zone(row, height) is not None:
            continue
        if any(w.isalpha() and len(w) >= 3 for w in row.clean.split()):
            has["L" if (row.x0 + row.x1) / 2 < split else "R"] = True
    return has["L"] and has["R"]


def _sparse_intervals(events: list[tuple[float, int]], allowed: int) -> list[tuple[float, float]]:
    """Maximal x-intervals covered by at most `allowed` boxes, from sorted box edges
    ((x0, +1) and (x1, -1) per box)."""
    intervals: list[tuple[float, float]] = []
    depth, start = 0, None
    for x, step in events:
        depth += step
        if depth <= allowed and start is None:
            start = x
        elif depth > allowed and start is not None:
            if x > start:
                intervals.append((start, x))
            start = None
    return intervals


def _line_widths(boxes: list[BBox]) -> list[float]:
    """Widths of visual lines: boxes that overlap vertically by half the smaller height
    are one line. TeX splits a line with inline maths into pieces whose bottom edges
    differ (subscripts, fractions); counted apart, they made a column of full lines look
    like a column of short ones."""
    lines: list[list[float]] = []  # [x0, y0, x1, y1] per line, top to bottom
    for x0, y0, x1, y1 in sorted(boxes, key=lambda b: (b[1] + b[3]) / 2):
        if lines:
            line = lines[-1]
            overlap = min(y1, line[3]) - max(y0, line[1])
            if overlap >= 0.5 * min(y1 - y0, line[3] - line[1]):
                line[0], line[2] = min(line[0], x0), max(line[2], x1)
                line[1], line[3] = min(line[1], y0), max(line[3], y1)
                continue
        lines.append([x0, y0, x1, y1])
    return [x1 - x0 for x0, _, x1, _ in lines]


def _reading_order(
    boxes: Sequence[BBox],
    gutter: Gutter,
    body: float,
    context: Sequence[BBox] = (),
    *,
    blocks: bool = False,
) -> list[int]:
    """Indices of `boxes` in reading order.

    Two columns: left column, then right column, segment by segment, where full-width
    items close a segment: items crossing the gutter, and narrow items at a section break
    across both columns (see `_section_break`). `context` boxes (e.g. figures while
    ordering text) shape the column extents but are not returned. A region tree in place of
    the gutter orders the boxes itself; `blocks` says they are blocks, not rows.
    """
    if gutter is not None and not isinstance(gutter, tuple):
        return gutter.order(boxes, body, context, blocks=blocks)
    if gutter is None:
        return _top_down(boxes, range(len(boxes)))
    everything = list(boxes) + list(context)
    columns = [_column_of(b, gutter) for b in everything]
    sides = {
        side: _ColumnExtent(
            [b for b, c in zip(everything, columns, strict=True) if c == side], body
        )
        for side in ("L", "R")
    }
    for i, (box, column) in enumerate(zip(everything, columns, strict=True)):
        if column != "S" and _section_break(
            box, sides[column], sides["R" if column == "L" else "L"], body
        ):
            columns[i] = "S"
    spanning = sorted(
        (i for i in range(len(boxes)) if columns[i] == "S"),
        key=lambda i: (boxes[i][1], boxes[i][0]),
    )
    starts = [boxes[i][1] for i in spanning]
    segments: dict[tuple[int, str], list[int]] = {}
    for i in range(len(boxes)):
        if columns[i] != "S":
            segment = bisect.bisect_right(starts, boxes[i][1])
            segments.setdefault((segment, columns[i]), []).append(i)
    order: list[int] = []
    for segment in range(len(spanning) + 1):
        for side in ("L", "R"):
            order.extend(_top_down(boxes, segments.get((segment, side), [])))
        if segment < len(spanning):
            order.append(spanning[segment])
    return order


class _ColumnExtent:
    """Where one column has content: bridged bands, plus item tops and bottoms."""

    def __init__(self, boxes: list[BBox], body: float) -> None:
        self.bands = _bands(boxes, 2.0 * body)
        self.band_tops = [y0 for y0, _ in self.bands]
        # The column's text width: a line as wide as that is a line of a paragraph.
        self.width = max((b[2] for b in boxes), default=0.0) - min(
            (b[0] for b in boxes), default=0.0
        )
        self.tops = sorted(b[1] for b in boxes)
        self.bottoms = sorted(b[3] for b in boxes)

    def overlaps(self, y0: float, y1: float) -> bool:
        k = bisect.bisect_left(self.band_tops, y1) - 1  # last band starting above y1
        return k >= 0 and self.bands[k][1] > y0

    def first_top_from(self, y: float) -> float | None:
        k = bisect.bisect_left(self.tops, y)
        return self.tops[k] if k < len(self.tops) else None

    def last_bottom_to(self, y: float) -> float | None:
        k = bisect.bisect_right(self.bottoms, y)
        return self.bottoms[k - 1] if k else None


FULL_LINE = 0.9  # share of its column's width at which a single item is a full line of text


def _section_break(box: BBox, own: _ColumnExtent, other: _ColumnExtent, body: float) -> bool:
    """Is this narrow item really full-width: a heading at a break across both columns?

    The other column must have nothing beside it but more content below, the item must
    stand apart from its own column's content above (a block gap, not the next line of a
    paragraph), and both columns must resume below it at about the same height. A
    heading inside one column, beside a large gap in the other (space around an equation
    or before a Word "Heading 1"), fails the last test and stays in its column.
    """
    if other.overlaps(box[1], box[3]):
        return False
    if box[2] - box[0] >= FULL_LINE * own.width:
        return False  # a line of a paragraph, as wide as its column; a heading is shorter
    other_below = other.first_top_from(box[3])
    if other_below is None:
        return False
    above = own.last_bottom_to(box[1] + 0.25 * body)
    if above is not None and box[1] - above < 0.8 * body:
        return False  # the next line of a paragraph, not a block on its own
    own_below = own.first_top_from(box[3] - 0.25 * body)
    return own_below is not None and other_below >= own_below - 0.5 * body


def _bands(boxes: list[BBox], bridge: float) -> list[tuple[float, float]]:
    """Vertical extents of one column, closing gaps smaller than `bridge`."""
    bands: list[list[float]] = []
    for _, y0, _, y1 in sorted(boxes, key=lambda b: b[1]):
        if bands and y0 <= bands[-1][1] + bridge:
            bands[-1][1] = max(bands[-1][1], y1)
        else:
            bands.append([y0, y1])
    return [(a, b) for a, b in bands]


def _top_down(boxes: Sequence[BBox], members: Iterable[int]) -> list[int]:
    """Top to bottom; items overlapping vertically by half their height go left to right.

    Only items of comparable height share a line: a tall box (a panel or figure with text
    inside or beside it) would otherwise pull everything it spans into one "line" and
    order it by x instead of top to bottom.
    """
    ordered = sorted(members, key=lambda i: (boxes[i][1], boxes[i][0]))
    lines: list[list[int]] = []
    for i in ordered:
        if lines:
            anchor = boxes[lines[-1][0]]
            box = boxes[i]
            overlap = min(anchor[3], box[3]) - max(anchor[1], box[1])
            smaller = min(anchor[3] - anchor[1], box[3] - box[1])
            larger = max(anchor[3] - anchor[1], box[3] - box[1])
            if smaller > 0 and overlap >= 0.5 * smaller and larger <= 3 * smaller:
                lines[-1].append(i)
                continue
        lines.append([i])
    return [i for line in lines for i in sorted(line, key=lambda i: boxes[i][0])]
