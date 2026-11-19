"""The region tree: a page cut into regions before its lines are made (`REGION_TREE`).

The reader of `order.py` finds one gutter for the page and then merges the lines. This module
builds the tree first, from the lines' fragments (not yet merged), the page's raster pictures
and the boxes drawn round text, and merges baselines only inside one leaf, so a line never
runs across a column, a picture or a box. The page is read by the tree: `RegionTree` stands
where the gutter stood, and `order._reading_order` hands it the boxes to order.

How the tree is cut (docs/layout.md "The region tree"):

- Horizontal cuts. The items (fragments, pictures) are grouped in bands that overlap
  vertically; a run of bands that keep one set of white channels is one segment, and a band
  that does not (a line that spans the region, a blank band) starts the next.
- Vertical cuts. Inside a segment, a white channel at least `CHANNEL_PT` wide with items on
  both sides, each side with at least three lines (two on a slide) and a line of three words
  or more, cuts the segment in two, and each side is cut again, so a page has up to three
  columns. A column at most `NOTE_RATIO` of its neighbor's width and at most `NOTE_FILL`
  filled is a notes column: its blocks are read after the body block they stand beside.
- Pictures occupy space, so the text on both sides of a photo is not one row.

Nothing here is read with `REGION_TREE` off.
"""

from __future__ import annotations

import bisect
import itertools
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

import pymupdf

from readablestem.core.types import BBox
from readablestem.pipeline.layout import entries
from readablestem.pipeline.layout import figures as _figures
from readablestem.pipeline.layout.context import _Document
from readablestem.pipeline.layout.furniture import _box_zone, _margin_zone
from readablestem.pipeline.layout.order import (
    FULL_LINE,
    _ColumnExtent,
    _section_break,
    _top_down,
)
from readablestem.pipeline.layout.rows import _merge_baselines
from readablestem.pipeline.layout.shared import _Display, _Path, _Row

CHANNEL_PT = 7.0  # narrowest white channel that cuts a segment into columns
MAX_COLUMNS = 3
NOTE_RATIO = 0.35  # a notes column is at most this share of its neighbor's width...
NOTE_FILL = 0.6  # ...and filled at most this much of the height
WIDE_GAP = 2.5  # rule 6: line pitches of white that close a segment of side-by-side bands
BOX_MIN_WIDTH, BOX_MIN_HEIGHT, BOX_MAX_SHARE = 40.0, 18.0, 0.8  # rule 2
PICTURE_MIN_PT, PICTURE_MAX_SHARE = 24.0, 0.85
MAX_DEPTH = 10
MAX_BOX_CANDIDATES = 300  # a page of shaded shapes is a chart, not a page of boxes
FOOT_SIZE = 0.92  # rule 5: footnote type, as a share of the body size
FOOT_SHARE = 0.4  # ... at most this share of a column's lines

_WORD = re.compile(r"[A-Za-zÀ-ɏ]{2,}")
# a numbered question: "3. text", "Question 3) text", "Q3. text"
_QNUM = re.compile(r"^\s*(?:Question\s+|Problem\s+|Q)?\d{1,2}[.)]\s+\S")
# an option label: (A) A) (a) a) and "A." only in capitals
_OPT = re.compile(r"^\s*(?:\(([A-Ha-h])\)|([A-Ha-h])\)|([A-H])\.)\s")
# a cell label: (A) A. A) a) (iv) 1. 1) ___ 1.
_LABEL = re.compile(r"^\s*(?:_{2,}\s*)?\(?([A-Ha-h]|[ivx]{1,4}|\d{1,2})[.)]\s")
_BLANK = re.compile(r"^\s*_{2,}\s*\d{1,2}[.)]\s")
_NOTE_MARK = re.compile(r"^\s*([\d*\u2217\u2020\u2021\u00a7\u00b6]|\w+:)")
_ROMAN = {"ii": 1, "iii": 2, "iv": 3, "vi": 5, "vii": 6, "viii": 7}

Item = tuple[BBox, str]  # a box of the page and its text ("" for a picture)


@dataclass
class _Node:
    kind: str  # "leaf" | "stack" | "cols" | "grid"
    box: BBox
    children: list[_Node] = field(default_factory=list)
    cuts: list[float] = field(default_factory=list)  # stack: y of each cut
    channel: tuple[float, float] = (0.0, 0.0)  # cols: the white channel
    notes: int | None = None  # cols: which child is the notes column
    column: str = "L"  # the name rows of this leaf carry (`_Row.column`)
    boxes: list[tuple[BBox, _Node]] = field(default_factory=list)  # leaf: text boxes in it
    cells: list[BBox] = field(default_factory=list)  # grid: the cells in reading order


def _union(boxes: Sequence[BBox]) -> BBox:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _inside(inner: BBox, outer: BBox, tol: float = 1.0) -> bool:
    return (
        inner[0] >= outer[0] - tol
        and inner[1] >= outer[1] - tol
        and inner[2] <= outer[2] + tol
        and inner[3] <= outer[3] + tol
    )


def _centre_in(box: BBox, outer: BBox) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _free(spans: Sequence[tuple[float, float]], x0: float, x1: float) -> list[tuple[float, float]]:
    """The x-intervals of [x0, x1] that no span covers."""
    out: list[tuple[float, float]] = []
    cur = x0
    for a, b in sorted(spans):
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < x1:
        out.append((cur, x1))
    return out


def _intersect(
    a: Sequence[tuple[float, float]], b: Sequence[tuple[float, float]]
) -> list[tuple[float, float]]:
    out = []
    for a0, a1 in a:
        for b0, b1 in b:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi - lo >= CHANNEL_PT:
                out.append((lo, hi))
    return out


def _over_shorter(items: Sequence[Item], band: Sequence[int], y0: float, y1: float) -> bool:
    """Does the item from `y0` to `y1` overlap a shorter member of `band` by a fifth of that
    member's height?"""
    for j in band:
        top, bottom = items[j][0][1], items[j][0][3]
        if bottom - top < y1 - y0 and min(bottom, y1) - y0 > 0.2 * (bottom - top):
            return True
    return False


def _bands(items: Sequence[Item]) -> list[list[int]]:
    """Indexes of `items` grouped in bands: items that overlap vertically (all but a fifth of
    their height) are one band. An item that stands over a shorter one's whole height, but
    starts a little below it (a picture beside the first line of a column), overlaps it by a
    fifth of the shorter one's height."""
    order = sorted(range(len(items)), key=lambda i: items[i][0][1])
    bands: list[list[int]] = []
    bottom = 0.0
    for i in order:
        y0, y1 = items[i][0][1], items[i][0][3]
        shrink = 0.2 * (y1 - y0)
        if bands and (y0 + shrink < bottom or _over_shorter(items, bands[-1], y0, y1)):
            bands[-1].append(i)
            bottom = max(bottom, y1 - shrink)
        else:
            bands.append([i])
            bottom = y1 - shrink
    return bands


def _prose(items: Sequence[Item], idx: Sequence[int]) -> bool:
    return any(len(_WORD.findall(items[i][1])) >= 3 for i in idx)


def _interior(
    channels: Sequence[tuple[float, float]], items: Sequence[Item], idx: Sequence[int]
) -> list[tuple[tuple[float, float], list[int], list[int]]]:
    """The channels with items on both sides: (channel, left items, right items)."""
    out = []
    for a, b in channels:
        left = [i for i in idx if items[i][0][2] <= a + 0.5]
        right = [i for i in idx if items[i][0][0] >= b - 0.5]
        if left and right:
            out.append(((a, b), left, right))
    return out


class _Build:
    """The state of building one page's tree."""

    def __init__(self, items: list[Item], slide: bool, pitch: float, rects: Sequence[BBox]) -> None:
        self.items = items
        self.slide = slide
        self.pitch = pitch
        self.rects = list(rects)
        self.used: set[BBox] = set()
        self.weight: dict[int, int] = {}  # a text box's item -> the lines it holds
        self.boxes = 0

    # -- horizontal cuts: bands in segments, segments in a stack --------------------------

    def build(self, idx: list[int], column: str, depth: int = 0) -> _Node:
        items = self.items
        box = _union([items[i][0] for i in idx]) if idx else (0.0, 0.0, 0.0, 0.0)
        if len(idx) < 2 or depth > MAX_DEPTH:
            return _Node("leaf", box, column=column)
        local: list[tuple[BBox, _Node, int]] = []
        idx, local = self._text_boxes(idx, column, depth)
        if len(idx) < 2:
            leaf = _Node("leaf", box, column=column)
            leaf.boxes = [(r, n) for r, n, _ in local]
            return leaf
        sub = [items[i] for i in idx]
        x0, x1 = box[0], box[2]
        segments = self._segments(sub, x0, x1)
        children: list[_Node] = []
        cuts: list[float] = []
        for k, (members, chans) in enumerate(segments):
            real = [idx[i] for i in members]
            children.append(self._columns(real, chans, column, depth))
            if k:
                prev = [idx[j] for j in segments[k - 1][0]]
                cuts.append(
                    (max(items[i][0][3] for i in prev) + min(items[i][0][1] for i in real)) / 2
                )
        node = children[0] if len(children) == 1 else _Node("stack", box, children, cuts)
        for r, n, _ in local:  # this level's text boxes go to the leaf that holds them
            _leaf_of(node, r, descend=False).boxes.append((r, n))
        return node

    def _segments(
        self, sub: list[Item], x0: float, x1: float
    ) -> list[tuple[list[int], list[tuple[float, float]]]]:
        """Bands in segments: (items, the channels the segment's bands share)."""
        segments: list[tuple[list[int], list[tuple[float, float]]]] = []
        seg_bottom = 0.0
        for band in _bands(sub):
            free = _free([(sub[i][0][0], sub[i][0][2]) for i in band], x0, x1)
            top = min(sub[i][0][1] for i in band)
            if segments:
                members, chans = segments[-1]
                both = _intersect(chans, free)
                inside = _interior(chans, sub, members)
                opens = not inside and _interior(free, sub, band)
                side_by_side = bool(_interior(free, sub, band))
                # rule 6: only before a band that is itself side by side (the next row of slides
                # or panels); a main column going on below a sidebar stays in its segment
                wide_gap = top - seg_bottom >= WIDE_GAP * self.pitch and side_by_side
                # rule 1: one-column bands continue a one-column segment
                flat = not inside and not side_by_side
                if (both or flat) and not opens and not wide_gap:
                    segments[-1] = (members + band, both)
                    seg_bottom = max(seg_bottom, max(sub[i][0][3] for i in band))
                    continue
            segments.append((list(band), free))
            seg_bottom = max(sub[i][0][3] for i in band)
        return segments

    # -- rule 2: boxes drawn round text ---------------------------------------------------

    def _text_boxes(
        self, idx: list[int], column: str, depth: int
    ) -> tuple[list[int], list[tuple[BBox, _Node, int]]]:
        """The outermost drawn rectangles that hold two or more of `idx`'s lines become one
        item each, cut and ordered inside themselves."""
        items = self.items
        rects = [r for r in self.rects if r not in self.used]
        if not rects:
            return idx, []
        held = [(r, [i for i in idx if _inside(items[i][0], r, 2.0)]) for r in rects]
        held = [(r, m) for r, m in held if 2 <= len(m) < len(idx)]
        top = [
            (r, m)
            for r, m in held
            if not any(o is not r and _inside(r, o) and len(mo) > len(m) for o, mo in held)
        ]
        taken: set[int] = set()
        local: list[tuple[BBox, _Node, int]] = []
        for r, m in sorted(top, key=lambda t: -len(t[1])):
            m = [i for i in m if i not in taken]
            if len(m) < 2:
                continue
            taken.update(m)
            self.used.add(r)
            self.boxes += 1
            node = self.build(m, f"{column}#{self.boxes}", depth + 1)
            items.append((r, " ".join(items[i][1] for i in m)))
            local.append((r, node, len(items) - 1))
            self.weight[len(items) - 1] = len(m)
        return [i for i in idx if i not in taken] + [j for _, _, j in local], local

    # -- vertical cuts: columns, grids, notes ---------------------------------------------

    def _columns(
        self,
        idx: list[int],
        chans: list[tuple[float, float]],
        column: str,
        depth: int,
    ) -> _Node:
        items = self.items
        box = _union([items[i][0] for i in idx])
        inner = _interior(chans, items, idx)
        if not inner:
            return _Node("leaf", box, column=column)
        if len(inner) == 1:
            (a, b), left, right = inner[0]
            grid = self._grid(idx, left, right, box, column)
            if isinstance(grid, _Node):
                return grid
            if grid == "cols":  # each column a run of labels of its own
                return _Node(
                    "cols",
                    box,
                    [
                        self.build(left, _side(column, "L"), depth + 1),
                        self.build(right, _side(column, "R"), depth + 1),
                    ],
                    channel=(a, b),
                )
        need = 2 if self.slide else 3
        # a channel counts when text stands beside text across it (the numbers of a margin,
        # the gaps a formula leaves in a line are no columns), and only then are they counted
        good = [
            c
            for c in inner
            if self._beside(c[1], c[2]) >= need and _prose(items, c[1]) and _prose(items, c[2])
        ]
        if not good or len(good) > MAX_COLUMNS - 1:
            return _Node("leaf", box, column=column)
        (a, b), left, right = max(good, key=lambda g: g[0][1] - g[0][0])
        lnode = self.build(left, _side(column, "L"), depth + 1)
        rnode = self.build(right, _side(column, "R"), depth + 1)
        node = _Node("cols", box, [lnode, rnode], channel=(a, b))
        widths = (lnode.box[2] - lnode.box[0], rnode.box[2] - rnode.box[0])
        height = box[3] - box[1]
        for k, side in enumerate((left, right)):
            filled = sum(items[i][0][3] - items[i][0][1] for i in side)
            if widths[k] <= NOTE_RATIO * widths[1 - k] and filled <= NOTE_FILL * height:
                node.notes = k
        return node

    def _rows(self, side: Sequence[int]) -> list[tuple[float, float, int]]:
        """A column's lines: (top, bottom, how many lines it holds) of each band of its
        items, so that the pieces a formula cuts one line into are one line."""
        items = self.items
        return [
            (
                min(items[side[k]][0][1] for k in band),
                max(items[side[k]][0][3] for k in band),
                max(self.weight.get(side[k], 1) for k in band),
            )
            for band in _bands([items[i] for i in side])
        ]

    def _beside(self, left: Sequence[int], right: Sequence[int]) -> int:
        """The lines (the fewer of the two columns') that have a line of the other column
        at their height."""
        lrows, rrows = self._rows(left), self._rows(right)

        def facing(
            rows: Sequence[tuple[float, float, int]], others: Sequence[tuple[float, float, int]]
        ) -> int:
            return sum(n for a, b, n in rows if any(min(b, d) > max(a, c) for c, d, _ in others))

        return min(facing(lrows, rrows), facing(rrows, lrows))

    def _grid(
        self, idx: list[int], left: list[int], right: list[int], box: BBox, column: str
    ) -> _Node | str | None:
        """Rule 8. A label grid ("grid", read in label order), or "cols" when each column
        holds a run of labels of its own (terms 1. 2. 3. beside definitions A. B. C.)."""
        items = self.items
        lc, rc = _cells(items, left), _cells(items, right)
        if len(lc) > 12 or len(rc) > 12:
            return None
        if any(lab is None for lab, _ in lc + rc):
            return None
        labels = [lab for lab, _ in lc + rc if lab is not None]
        if len({lab[0] for lab in labels}) != 1:
            return self._grid_above(idx, left, right, box, column, lc + rc) or "cols"
        vals = sorted(v for _, v in labels)
        if vals != list(range(vals[0], vals[0] + len(vals))):
            return None
        cells = sorted(lc + rc, key=lambda c: c[0][1] if c[0] else 0)
        node = _Node("grid", _union([items[i][0] for i in idx]), column=column)
        node.cells = [_union([items[i][0] for i in m]) for _, m in cells]
        return node

    def _grid_above(
        self,
        idx: list[int],
        left: list[int],
        right: list[int],
        box: BBox,
        column: str,
        cells: list[tuple[tuple[str, int] | None, list[int]]],
    ) -> _Node | None:
        """Rule 8, a grid with the next question under it. Options (A) (C) beside (B) (D) and the
        number of the next question ("5.") stand in the same two columns, as no blank band
        parts them: the labels of the cells below the first kind of label are cut off, and the
        cells above are the grid, a stack of the grid and what is under it."""
        items = self.items
        first = min(cells, key=lambda c: min(items[i][0][1] for i in c[1]))
        kind = first[0][0] if first[0] else ""
        foreign = [c for c in cells if c[0] is not None and c[0][0] != kind]
        top = min(items[i][0][1] for c in foreign for i in c[1])
        centre = {i: (items[i][0][1] + items[i][0][3]) / 2 for i in idx}
        above = [i for i in idx if centre[i] < top]
        below = [i for i in idx if centre[i] >= top]
        if not above or not below:
            return None
        grid = self._grid(
            above, [i for i in left if i in above], [i for i in right if i in above], box, column
        )
        if not isinstance(grid, _Node):
            return None
        cut = (max(items[i][0][3] for i in above) + top) / 2
        return _Node("stack", box, [grid, self.build(below, column, 1)], [cut], column=column)


def _side(column: str, side: str) -> str:
    """The name of a column: the page's first two are "L" and "R", a column of a column
    "LL", "LR" ...; what stands in no column is "S" (a page with one column: "L")."""
    return side if column == "S" else column + side


def _label(text: str) -> tuple[str, int] | None:
    m = _LABEL.match(text)
    if not m:
        return None
    s = m.group(1)
    if s.isdigit():
        return ("n", int(s) - 1)
    if re.fullmatch(r"[ivx]{2,4}", s):
        return ("r", _ROMAN.get(s, 0))
    return ("U" if s.isupper() else "l", ord(s.lower()) - 97)


def _cells(
    items: Sequence[Item], side: Sequence[int]
) -> list[tuple[tuple[str, int] | None, list[int]]]:
    """A column's lines in cells: a labeled line and the unlabeled lines under it."""
    out: list[tuple[tuple[str, int] | None, list[int]]] = []
    for i in sorted(side, key=lambda i: items[i][0][1]):
        lab = _label(items[i][1])
        if lab is not None or not out:
            out.append((lab, [i]))
        else:
            out[-1][1].append(i)
    return out


# -- finding a box's leaf, and the order inside the tree ---------------------------------------


def _leaf_of(node: _Node, box: BBox, *, descend: bool = True) -> _Node:
    """The leaf (or grid) whose place `box` is in. `descend`: into the text box that holds it."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    while True:
        if node.kind == "leaf":
            if descend:
                for r, inner in node.boxes:
                    if _centre_in(box, r):
                        node = inner
                        break
                else:
                    return node
                continue
            return node
        if node.kind == "stack":
            node = node.children[bisect.bisect_right(node.cuts, cy)]
        elif node.kind == "grid":
            return node
        else:
            node = node.children[0 if cx < sum(node.channel) / 2 else 1]


def _nearest_cell(cells: Sequence[BBox], box: BBox) -> int:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2

    def distance(k: int) -> float:
        c = cells[k]
        dx = max(c[0] - cx, 0.0, cx - c[2])
        dy = max(c[1] - cy, 0.0, cy - c[3])
        return dx * dx + dy * dy

    return min(range(len(cells)), key=distance)


@dataclass
class RegionTree:
    """What `_prepare_rows` makes in place of a gutter: the page's tree, its footnote zones
    and the text of its lines (`order._Ordering`)."""

    root: _Node
    pitch: float
    foot: list[BBox]
    lines: list[Item]
    columns: bool  # the page has two or more columns somewhere
    _tops: list[float] = field(init=False)

    def __post_init__(self) -> None:
        self.lines.sort(key=lambda it: (it[0][1] + it[0][3]) / 2)
        self._tops = [(b[1] + b[3]) / 2 for b, _ in self.lines]

    def text_of(self, box: BBox) -> str:
        """The text of the first fragment inside a row's or block's box."""
        frame = (box[0] - 1, box[1] - 1, box[2] + 1, box[3] + 1)
        lo = bisect.bisect_left(self._tops, frame[1])
        hi = bisect.bisect_right(self._tops, frame[3])
        inside = [it for it in self.lines[lo:hi] if _centre_in(it[0], frame)]
        if not inside:
            return ""
        return min(inside, key=lambda it: (round(it[0][1] / 3), it[0][0]))[1]

    def column_of(self, box: BBox) -> str:
        return _leaf_of(self.root, box).column

    def order(
        self,
        boxes: Sequence[BBox],
        body: float,
        context: Sequence[BBox] = (),
        *,
        blocks: bool = False,
    ) -> list[int]:
        """Indices of `boxes` in reading order: footnote zones last, the rest by the tree, and
        option grids in label order (rule 8)."""
        boxes = list(boxes)
        members = list(range(len(boxes)))
        zone_of: dict[int, int] = {}
        for i in members:
            for k, zone in enumerate(self.foot):
                if _centre_in(boxes[i], zone):
                    zone_of[i] = k
                    break
        members = [i for i in members if i not in zone_of]
        tail = [
            i
            for k in range(len(self.foot))
            for i in _top_down(boxes, [j for j in zone_of if zone_of[j] == k])
        ]
        walker = _Walk(self, boxes, body, list(context), blocks)
        seq = walker.order(self.root, members) + tail
        return _numbered_runs(_options(seq, boxes, self), boxes, self)


class _Walk:
    """Ordering the members of one tree: the boxes, the body size and what stands in the
    columns without being ordered (figure captions)."""

    def __init__(
        self, tree: RegionTree, boxes: list[BBox], body: float, context: list[BBox], blocks: bool
    ) -> None:
        self.tree, self.boxes, self.body, self.context, self.blocks = (
            tree,
            boxes,
            body,
            context,
            blocks,
        )

    def order(self, node: _Node, members: list[int]) -> list[int]:
        if not members:
            return []
        boxes = self.boxes
        if node.kind == "leaf":
            return self._leaf(node, members)
        if node.kind == "stack":
            parts: list[list[int]] = [[] for _ in node.children]
            for i in members:
                cy = (boxes[i][1] + boxes[i][3]) / 2
                parts[bisect.bisect_right(node.cuts, cy)].append(i)
            return [j for c, p in zip(node.children, parts, strict=True) for j in self.order(c, p)]
        if node.kind == "grid":
            per: list[list[int]] = [[] for _ in node.cells]
            for i in members:
                per[_nearest_cell(node.cells, boxes[i])].append(i)
            return [j for p in per for j in _top_down(boxes, p)]
        return self._columns(node, members)

    def _leaf(self, node: _Node, members: list[int]) -> list[int]:
        boxes = self.boxes
        if not node.boxes:
            return _top_down(boxes, members)
        # a text box is read where it stands, as one item, with its inside in its own order
        groups: dict[int, list[int]] = {}
        loose: list[int] = []
        for i in members:
            for k, (r, _) in enumerate(node.boxes):
                if _centre_in(boxes[i], r):
                    groups.setdefault(k, []).append(i)
                    break
            else:
                loose.append(i)
        proxies = list(boxes) + [self._place(node, k, groups, loose) for k in groups]
        pmap = {len(boxes) + n: k for n, k in enumerate(groups)}
        out: list[int] = []
        for i in _top_down(proxies, loose + list(pmap)):
            if i in pmap:
                k = pmap[i]
                out.extend(self.order(node.boxes[k][1], groups[k]))
            else:
                out.append(i)
        return out

    def _place(self, node: _Node, k: int, groups: dict[int, list[int]], loose: list[int]) -> BBox:
        """Where a text box is read. A box with text beside it (a sidebar the text wraps
        around) is read at the first block break below its bottom edge, so that a paragraph
        running past it is not cut in two."""
        boxes = self.boxes
        r = node.boxes[k][0]
        beside = any(
            boxes[i][1] < r[3]
            and boxes[i][3] > r[1]
            and (boxes[i][2] <= r[0] or boxes[i][0] >= r[2])
            for i in loose
        )
        if not beside:
            return r
        flow = [
            boxes[i] for i in loose if not (boxes[i][0] >= r[0] - 1 and boxes[i][2] <= r[2] + 1)
        ]
        flow += [node.boxes[j][0] for j in groups if node.boxes[j][0] != r]
        prev_bottom: float | None = None
        for b in sorted(flow, key=lambda b: b[1]):
            top = b[1]
            if (
                top >= r[3] - 1
                and prev_bottom is not None
                and top - prev_bottom >= 0.6 * self.tree.pitch
            ):
                return (r[0], top - 0.6, r[2], top - 0.5)
            prev_bottom = b[3] if prev_bottom is None else max(prev_bottom, b[3])
        return (r[0], 1e6, r[2], 1e6 + 1)

    def _columns(self, node: _Node, members: list[int]) -> list[int]:
        boxes = self.boxes
        a, b = node.channel
        split = (a + b) / 2
        tol = 2.0
        left: list[int] = []
        right: list[int] = []
        span: list[int] = []
        for i in members:
            x0, _, x1, _ = boxes[i]
            if x1 <= b + tol and (x0 + x1) / 2 < split:
                left.append(i)
            elif x0 >= a - tol and (x0 + x1) / 2 >= split:
                right.append(i)
            else:
                span.append(i)
        if left and right:
            self._section_breaks(node, left, right, span, split)
            self._heads_of_spans(left, right, span)
        span.sort(key=lambda i: (boxes[i][1], boxes[i][0]))
        starts = [boxes[i][1] for i in span]
        groups: list[tuple[list[int], list[int]]] = [([], []) for _ in range(len(span) + 1)]
        for side, its in ((0, left), (1, right)):
            for i in its:
                groups[bisect.bisect_right(starts, boxes[i][1])][side].append(i)
        out: list[int] = []
        for g, (ls, rs) in enumerate(groups):
            lo = self.order(node.children[0], ls)
            ro = self.order(node.children[1], rs)
            if node.notes is not None and self.blocks:
                main, notes = (ro, lo) if node.notes == 0 else (lo, ro)
                out.extend(_interleave(boxes, main, notes))
            elif node.notes is not None:
                out.extend((ro + lo) if node.notes == 0 else (lo + ro))
            else:
                out.extend(lo + ro)
            if g < len(span):
                out.append(span[g])
        return out

    def _section_breaks(
        self, node: _Node, left: list[int], right: list[int], span: list[int], split: float
    ) -> None:
        """Rule 3. The repository's test for a heading at a break across both columns, applied
        inside every column pair; figure captions still fill their column."""
        boxes, body = self.boxes, self.body
        inside = [
            c for c in self.context if node.box[1] - 1 <= (c[1] + c[3]) / 2 <= node.box[3] + 1
        ]
        extent = (
            _ColumnExtent(
                [boxes[i] for i in left] + [c for c in inside if (c[0] + c[2]) / 2 < split], body
            ),
            _ColumnExtent(
                [boxes[i] for i in right] + [c for c in inside if (c[0] + c[2]) / 2 >= split],
                body,
            ),
        )
        for side, own in ((0, left), (1, right)):
            for i in list(own):
                if _heading_break(boxes[i], extent[side], extent[1 - side], body):
                    own.remove(i)
                    span.append(i)

    def _heads_of_spans(self, left: list[int], right: list[int], span: list[int]) -> None:
        """Rule 3. A short item directly above a spanning item, with nothing beside it in the
        other column, opens that spanning part ("Part E. Short answer" over its question)."""
        boxes, body = self.boxes, self.body
        for own, other in ((left, right), (right, left)):
            for i in list(own):
                bx = boxes[i]
                below = [s for s in span if 0 <= boxes[s][1] - bx[3] <= 2.0 * body]
                if not below:
                    continue
                if any(boxes[o][3] > bx[1] - 0.25 * body for o in other):
                    continue  # the other column goes on beside or below it
                if any(bx[3] < boxes[o][1] < min(boxes[s][1] for s in below) for o in own):
                    continue
                own.remove(i)
                span.append(i)


def _heading_break(box: BBox, own: _ColumnExtent, other: _ColumnExtent, body: float) -> bool:
    """`order._section_break`, except that an item taller than 2.5 body lines is not a heading
    and one line of large type (a heading) may be as wide as its column ("CONCEPTUAL
    QUESTIONS" across a whole column)."""
    height = box[3] - box[1]
    if height > 2.5 * body:
        return False  # a figure or a paragraph, not a heading
    if 1.3 * body <= height <= 2.2 * body and box[2] - box[0] >= FULL_LINE * own.width:
        box = (box[0], box[1], box[0] + 0.5 * own.width, box[3])
    return _section_break(box, own, other, body)


def _interleave(boxes: Sequence[BBox], main: list[int], notes: list[int]) -> list[int]:
    """The notes column's blocks each after the last body block that starts above them."""
    out = list(main)
    for n in sorted(notes, key=lambda i: boxes[i][1]):
        top = boxes[n][1]
        at = 0
        for k, i in enumerate(out):
            if i in notes:
                continue
            if boxes[i][1] <= top + 1:
                at = k + 1
        while at < len(out) and out[at] in notes:
            at += 1
        out.insert(at, n)
    return out


def _option(text: str) -> tuple[str, int] | None:
    m = _OPT.match(text)
    if not m:
        return None
    s = next(g for g in m.groups() if g)
    return ("U" if s.isupper() else "l", ord(s.lower()) - 97)


def _options(seq: list[int], boxes: Sequence[BBox], tree: RegionTree) -> list[int]:
    """Rule 8. Side-by-side options read in label order: (A) (B) / (C) (D) and (A) (C) / (B) (D)
    grids both come out A B C D, each with its wrapped lines."""
    if not any(_option(tree.text_of(boxes[i])) for i in seq):
        return seq
    texts = {i: tree.text_of(boxes[i]) for i in seq}
    lab = {i: _option(texts[i]) for i in seq}
    pitch = tree.pitch
    out: list[int] = []
    k = 0
    while k < len(seq):
        if lab[seq[k]] is None:
            out.append(seq[k])
            k += 1
            continue
        win: list[int] = []
        j = k
        while j < len(seq):
            i = seq[j]
            if lab[i] is not None or (
                not _QNUM.match(texts[i])
                and any(
                    lab[w] is not None
                    and boxes[w][0] - 2 <= boxes[i][0] < boxes[w][2]
                    and -0.3 * pitch <= boxes[i][1] - boxes[w][3] <= 1.2 * pitch
                    for w in win
                )
            ):
                win.append(i)
            else:
                break
            j += 1
        labeled = [i for i in win if lab[i] is not None]
        named = {i: label for i in labeled if (label := lab[i]) is not None}
        vals = [label[1] for label in named.values()]
        kinds = {label[0] for label in named.values()}
        xs = {round(boxes[i][0] / 12) for i in labeled}
        if (
            len(labeled) >= 2
            and len(kinds) == 1
            and len(xs) >= 2
            and vals != sorted(vals)
            and sorted(vals) == list(range(min(vals), min(vals) + len(vals)))
        ):
            conts: dict[int, list[int]] = {w: [] for w in labeled}
            for i in win:
                if lab[i] is None:
                    above = [
                        w
                        for w in labeled
                        if boxes[w][0] - 2 <= boxes[i][0] < boxes[w][2]
                        and boxes[w][1] < boxes[i][1]
                    ]
                    conts[max(above, key=lambda w: boxes[w][1])].append(i)
            for w in sorted(labeled, key=lambda w: named[w][1]):
                out.append(w)
                out.extend(sorted(conts[w], key=lambda i: boxes[i][1]))
        else:
            out.extend(win)
        k = j
    return out


# -- the boxes drawn round text (rule 2) -------------------------------------------------------


def _boxes_of(
    paths: Sequence[_Path], page: tuple[float, float], items: Sequence[Item]
) -> list[BBox]:
    """Drawn rectangles, filled or stroked, at least 40 by 18 pt and under 0.8 of the page,
    that hold two or more lines with no line crossing the border. Touching pieces of one fill
    (a frame of strips and a rectangle per entry) are joined."""
    width, height = page
    area = width * height
    shapes = [p for p in paths if p.kind == "shape" and not p.curved and (p.filled or p.frame)]
    found: list[BBox] = []
    for p in shapes:
        w, h = p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]
        if w >= BOX_MIN_WIDTH and h >= BOX_MIN_HEIGHT and w * h <= BOX_MAX_SHARE * area:
            found.append(p.bbox)
    groups: list[list[BBox]] = []
    for p in shapes:
        if not p.filled or (p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1]) > BOX_MAX_SHARE * area:
            continue
        box = p.bbox
        hit = [
            g
            for g in groups
            if any(
                box[0] <= q[2] + 1
                and q[0] <= box[2] + 1
                and box[1] <= q[3] + 1
                and q[1] <= box[3] + 1
                for q in g
            )
        ]
        merged = [box] + [q for g in hit for q in g]
        groups = [g for g in groups if g not in hit] + [merged]
    for g in groups:
        if len(g) >= 3:
            u = _union(g)
            if u[2] - u[0] >= BOX_MIN_WIDTH and u[3] - u[1] >= BOX_MIN_HEIGHT:
                found.append(u)
    # one rectangle drawn twice (a fill and a border) is one box
    unique: list[BBox] = []
    for r in sorted(found, key=lambda r: -(r[2] - r[0]) * (r[3] - r[1]))[:MAX_BOX_CANDIDATES]:
        if any(all(abs(r[k] - u[k]) < 3 for k in range(4)) for u in unique):
            continue
        unique.append(r)
    keep = []
    for r in unique:
        within = crossing = 0
        for b, t in items:
            if not t:
                continue
            if b[2] <= r[0] or b[0] >= r[2] or b[3] <= r[1] or b[1] >= r[3]:
                continue
            if _inside(b, r, 2.0):
                within += 1
            else:
                crossing += 1
        if within >= 2 and crossing == 0:
            keep.append(r)
    return keep


# -- footnote zones (rule 5) -------------------------------------------------------------------


def _footnote_zones(root: _Node, frags: Sequence[_Row], body_size: float) -> list[BBox]:
    """Boxes of footnote zones: the run of small type at the foot of a column of a column pair,
    at most 0.4 of the column's lines, that stands apart from the lines above it or starts with
    a mark or a number. Read at the end of the page."""
    zones: list[BBox] = []

    def walk(n: _Node) -> None:
        if n.kind == "cols":
            for left in n.children:
                lf = sorted(
                    (f for f in frags if _centre_in(f.bbox, left.box)), key=lambda f: f.bbox[1]
                )
                # lines below the end of the other column are a footer, not the column's foot
                floor = max((c.box[3] for c in n.children if c is not left), default=None)
                if floor is not None:
                    lf = [f for f in lf if f.bbox[1] <= floor + 2]
                run: list[_Row] = []
                for f in reversed(lf):
                    if f.size <= FOOT_SIZE * body_size:
                        run.append(f)
                    else:
                        break
                if run and len(run) < len(lf) and len(run) <= FOOT_SHARE * len(lf):
                    top = min(f.bbox[1] for f in run)
                    above = max((f.bbox[3] for f in lf if f.bbox[3] <= top + 0.5), default=None)
                    first = min(run, key=lambda f: f.bbox[1])
                    if _NOTE_MARK.match(first.clean or "") or (
                        above is not None and top - above > 0.6 * first.size
                    ):
                        zones.append(_union([f.bbox for f in run]))
        for c in n.children:
            walk(c)
        if n.kind == "leaf":
            for _, inner in n.boxes:
                walk(inner)

    walk(root)
    return zones


# -- the page ----------------------------------------------------------------------------------


def _pictures(page: pymupdf.Page, height: float) -> list[Item]:
    """The page's raster pictures that take room: 24 pt or more each way, not the page itself."""
    display = _Display.of(page)
    out: list[Item] = []
    for info in _figures._image_infos(page):
        box = display.box(info["bbox"])
        w, h = box[2] - box[0], box[3] - box[1]
        if (
            w >= PICTURE_MIN_PT
            and h >= PICTURE_MIN_PT
            and w * h < PICTURE_MAX_SHARE * page.rect.width * height
            and _box_zone(box, height) is None
        ):
            out.append((box, ""))
    return out


def _takes_room(box: BBox, width: float, height: float) -> bool:
    w, h = box[2] - box[0], box[3] - box[1]
    return (
        w >= PICTURE_MIN_PT and h >= PICTURE_MIN_PT and w * h < PICTURE_MAX_SHARE * width * height
    )


def _tree_of(
    page: pymupdf.Page,
    kept: Sequence[_Row],
    paths: Sequence[_Path],
    tables: Sequence[BBox],
    document: _Document,
) -> RegionTree:
    """The region tree of a page from the fragments that are kept and the tables found on it
    (their cells are no fragments of the page, but the table takes its room like a picture)."""
    height = page.rect.height
    body = [f for f in kept if not f.vertical and _margin_zone(f, height) is None]
    items: list[Item] = [(f.bbox, f.clean) for f in body]
    heights = [f.bbox[3] - f.bbox[1] for f in body if f.clean.strip()]
    pitch = statistics.median(heights) if heights else 10.0
    sizes = [f.size for f in body if len(f.clean.split()) >= 4]
    body_size = statistics.median(sizes) if sizes else 10.0
    items += _pictures(page, height)
    items += [(t, "") for t in tables if _takes_room(t, page.rect.width, height)]
    rects = _boxes_of(paths, (page.rect.width, height), items)
    build = _Build(items, document.slide_deck, pitch, rects)
    root = build.build(list(range(len(items))), "S")
    lines = [(f.bbox, f.clean) for f in kept if not f.vertical]
    foot = _footnote_zones(root, body, body_size)
    columns = _has_columns(root)
    if not columns:  # one column: the name the rows had with no gutter
        _rename(root, "S", "L")
    return RegionTree(root, pitch, foot, lines, columns)


def _rename(node: _Node, old: str, new: str) -> None:
    if node.column.startswith(old):
        node.column = new + node.column[len(old) :]
    for child in node.children:
        _rename(child, old, new)
    for _, inner in node.boxes:
        _rename(inner, old, new)


def _has_columns(node: _Node) -> bool:
    return (
        node.kind == "cols"
        or any(_has_columns(c) for c in node.children)
        or any(_has_columns(inner) for _, inner in node.boxes)
    )


def prepare(
    page: pymupdf.Page,
    kept: list[_Row],
    dropped: list[_Row],
    rules: Sequence[_Path],
    *,
    tables: Sequence[BBox] = (),
    document: _Document,
) -> tuple[list[_Row], RegionTree, list[_Row]]:
    """The rows of a page from the region tree: fragments grouped by the leaf they stand in,
    baselines merged inside a leaf only. Returns the rows, the tree (in place of the gutter)
    and the dropped rows."""
    tree = _tree_of(page, kept, rules, tables, document)
    groups: dict[tuple[int, int], list[_Row]] = {}
    for f in kept:
        if f.vertical:
            continue
        leaf = _leaf_of(tree.root, f.bbox)
        cell = _nearest_cell(leaf.cells, f.bbox) if leaf.kind == "grid" else 0
        groups.setdefault((id(leaf), cell), []).append(f)
    rows: list[_Row] = []
    for frags in groups.values():
        rows.extend(_merge_baselines(frags, None, rules))
    vertical = [f for f in kept if f.vertical]
    if vertical:
        rows.extend(_merge_baselines(vertical, None, rules))
    index = entries.INDEX_PAGE if _is_index(kept, page.rect.height) else ""
    for row in rows:
        if not row.vertical:
            row.column = tree.column_of(row.bbox) + index
    return rows, tree, _merge_baselines(dropped, None)


# -- table proposals (WS15.2) -----------------------------------------------------------------

REFUSED_PROSE = "prose on both sides of a gutter"
REFUSED_ALIGNMENT = "rows do not line up across a gutter"
REFUSED_MATCHING = "two labeled lists side by side"
MIN_ALIGNMENT = 0.6  # a proposal that crosses a gutter needs at least this share of cells in line
MATCHING_CELLS = 3  # a labeled list of at least this many cells


def _between(node: _Node, lines: list[Item], rest: Sequence[BBox]) -> list[list[list[Item]]]:
    """The gutters of the tree that `lines` (the cells of a table proposal) stand across: for
    each, the lines on each of its sides. A horizontal cut is no gutter (a table has rows), and
    a gutter counts only where the page has lines of its own on two sides (`rest`: the lines that
    are not the proposal's): a table whose own columns are cut is no gutter of the page, though
    text below it may stand under one of its columns."""
    if not lines:
        return []
    found: list[list[list[Item]]] = []
    if node.kind == "stack":
        parts: dict[int, list[Item]] = {}
        for it in lines:
            parts.setdefault(bisect.bisect_right(node.cuts, (it[0][1] + it[0][3]) / 2), []).append(
                it
            )
        for k, part in parts.items():
            found += _between(node.children[k], part, rest)
        return found
    if node.kind == "grid":
        cells: dict[int, list[Item]] = {}
        for it in lines:
            cells.setdefault(_nearest_cell(node.cells, it[0]), []).append(it)
        if len(cells) >= 2 and sum(_others(c, rest) for c in node.cells) >= 2:
            found.append(list(cells.values()))
        return found
    if node.kind == "cols":
        middle = sum(node.channel) / 2
        sides = [
            [it for it in lines if (it[0][0] + it[0][2]) / 2 < middle],
            [it for it in lines if (it[0][0] + it[0][2]) / 2 >= middle],
        ]
        if all(sides) and all(_others(c.box, rest) for c in node.children):
            found.append(sides)
        for child, side in zip(node.children, sides, strict=True):
            found += _between(child, side, rest)
        return found
    inside: dict[int, list[Item]] = {}
    outside: list[Item] = []
    for it in lines:
        for k, (rect, _) in enumerate(node.boxes):
            if _centre_in(it[0], rect):
                inside.setdefault(k, []).append(it)
                break
        else:
            outside.append(it)
    groups = ([outside] if outside else []) + list(inside.values())
    standing = sum(_others(node.boxes[k][0], rest) for k in inside)
    if outside:  # the page's own lines in the leaf itself, outside its boxes
        standing += any(_centre_in(b, node.box) and not _in_a_box(b, node) for b in rest)
    if len(groups) >= 2 and standing >= 2:
        found.append(groups)
    for k, part in inside.items():
        found += _between(node.boxes[k][1], part, rest)
    return found


def _in_a_box(box: BBox, leaf: _Node) -> bool:
    return any(_centre_in(box, rect) for rect, _ in leaf.boxes)


def _others(box: BBox, rest: Sequence[BBox]) -> bool:
    """Does a line that is not the proposal's stand in `box`?"""
    return any(_centre_in(b, box) for b in rest)


def _matching(sides: Sequence[list[Item]]) -> bool:
    """Every side is a list of labeled cells (1. 2. 3., A. B. C.), as the tree reads an exercise
    that matches terms with definitions (rule 8)."""
    for side in sides:
        cells = _cells(side, range(len(side)))
        if len(cells) < MATCHING_CELLS or any(lab is None for lab, _ in cells):
            return False
        if len({lab[0] for lab, _ in cells if lab is not None}) != 1:
            return False
    return True


def table_refusal(
    tree: RegionTree, lines: Sequence[Item], box: BBox, alignment: float
) -> str | None:
    """Why a table proposal is not accepted, as the reason (`REFUSED_*`), or None. The proposal
    is made on the raw page, before the page has columns; here the page has its tree. A proposal
    in one region is accepted, and so is a ruled table that spans the columns on purpose (it is
    a cut of its own: nothing else of the page stands beside it). One that stands across a
    gutter is refused only when it has prose on both sides of it, or its rows do not line up
    (`MIN_ALIGNMENT`), or its sides are two labeled lists (an exercise). `lines` are the lines
    the proposal takes (cells and caption) and `box` is the box of its cells."""
    frame = (box[0] - 1, box[1] - 1, box[2] + 1, box[3] + 1)
    cells = [it for it in lines if _centre_in(it[0], frame)]
    own = {b for b, _ in lines}
    rest = [b for b, _ in tree.lines if b not in own]
    across = _between(tree.root, cells, rest)
    if not across:
        return None
    if any(sum(_prose(side, range(len(side))) for side in sides) >= 2 for sides in across):
        return REFUSED_PROSE
    if alignment < MIN_ALIGNMENT:
        return REFUSED_ALIGNMENT
    if any(_matching(sides) for sides in across):
        return REFUSED_MATCHING
    return None


# -- rule 12: numbered runs --------------------------------------------------------------------

_NUMBER_LABEL = re.compile(r"^\s*\(?(\d{1,3})[.)]?(?:\s|$)")
NUMBER_RUN_MIN = 6


def _numbered_runs(seq: list[int], boxes: Sequence[BBox], tree: RegionTree) -> list[int]:
    """Rule 12. Where a run of six or more numbered labels is read out of sequence, the
    column-by-column reading is used if it makes the run consecutive."""

    def number(text: str) -> int | None:
        m = _NUMBER_LABEL.match(text)
        return int(m.group(1)) if m and len(text.strip()) <= 40 else None

    nums = [number(tree.text_of(boxes[i])) for i in seq]
    out: list[int] = []
    k = 0
    while k < len(seq):
        if nums[k] is None:
            out.append(seq[k])
            k += 1
            continue
        j = k
        while j < len(seq) and nums[j] is not None:
            j += 1
        run, ns = seq[k:j], nums[k:j]
        if len(run) >= NUMBER_RUN_MIN and any(
            a is None or b is None or b != a + 1 for a, b in itertools.pairwise(ns)
        ):
            columns: list[list[int]] = []  # left edges that agree to a text-line height
            for i in sorted(run, key=lambda i: boxes[i][0]):
                if columns and boxes[i][0] - boxes[columns[-1][-1]][0] <= tree.pitch:
                    columns[-1].append(i)
                else:
                    columns.append([i])
            by_column = [i for c in columns for i in sorted(c, key=lambda i: boxes[i][1])]
            seen = [nums[k + run.index(i)] for i in by_column]
            if len(columns) >= 2 and all(
                b is not None and a is not None and b == a + 1 for a, b in itertools.pairwise(seen)
            ):
                run = by_column
        out.extend(run)
        k = j
    return out


# -- rule 9: index pages ---------------------------------------------------------------------

INDEX_MIN_LINES = 20
INDEX_MAX_WORDS = 7


def _is_index(fragments: Sequence[_Row], height: float) -> bool:
    """Is the page an index: most of its lines end in a page number and are short?"""
    lines = [
        f
        for f in fragments
        if not f.vertical and _margin_zone(f, height) is None and f.clean.strip()
    ]
    if len(lines) < INDEX_MIN_LINES:
        return False
    ends = sum(
        1
        for f in lines
        if entries.PAGE_NUMBER_END.search(f.clean) and re.search(r"[A-Za-z]", f.clean)
    )
    words = statistics.median(len(f.clean.split()) for f in lines)
    return ends >= 0.5 * len(lines) and words <= INDEX_MAX_WORDS
