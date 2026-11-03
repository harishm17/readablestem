"""Vector ink to ordered pen strokes: what a notes app drew on a PDF page, as point lists.

Why this exists: iPad and other notes apps export handwriting as vector paths, so the page
still holds the pen's trace. `page_type` already tells such a page apart (it decides
`handwritten`); this module reads the trace itself, so a later step can hand a recogniser
strokes (or a sharp re-render of just the ink) instead of a pixel crop. Nothing calls it
from the pipeline yet. Pure PyMuPDF and numpy; no model, no network, deterministic.

Three ways of drawing ink are read (docs/layout.md "Vector ink and strokes"):

- `path`: a stroked polyline or Bezier path with a pen width (one path per stroke, or one
  short segment per path object, which are chained). The path is the stroke.
- `samples`: one small filled shape per pen sample (GoodNotes). Consecutive, touching
  shapes in paint order of one colour are one stroke; the shape centres are its points.
- `outline`: a filled outline of the whole stroke, several to a path object (PencilKit).
  The two long edges are paired and averaged: the centre-line is the stroke.

Page furniture is dropped before anything is grouped: page-wide rules, evenly spaced
parallel lines (ruled and squared paper), dashed lines, exactly axis-aligned frames and
cells, dot grids, and filled backgrounds. Colour is never a reason to drop a stroke, so
pastel ink survives a pastel template. The result keeps paint order (the pen order), the
colour and opacity, and groups strokes into lines and regions by proximity.

Limits (measured on synthetic ink only; the first real exports are for the probe to
settle): the stroke direction of an outline is a guess (the outline's first point is taken
as the start), fast pen samples that do not touch split a stroke, crossing short ticks
may join, there is no time or pressure, and a rule drawn by hand across more than half the
page is taken for paper.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pymupdf

from readablestem.pipeline.page_type import (
    INK_MAX_ITEMS,
    PAGE_MAX_PATHS,
    InkBudget,
)

BBox = tuple[float, float, float, float]
Point = tuple[float, float]

# ---------------------------------------------------------------------------- limits

# The caps and the time budget are the page-type analysis's (one definition): a page with
# more drawings or path items, or an analysis that outruns the budget, is not read.
MAX_STROKES = 20_000  # strokes before grouping; more is not handwriting (or is hostile)
GRID_MAX_CELLS = 256  # a box over more grid cells than this is compared with every other
GRID_MAX_BIG = 2000  # ... and a page with more such boxes than this is not grouped
GRID_MAX_CELL = 1000  # boxes in one grid cell: real ink never piles this deep

FLATTEN_STEP = 1.5  # pt: a Bezier is cut into pieces about this long
POINT_EPS = 0.05  # pt: points closer than this are one point
JOIN_TOL = 0.3  # pt: a segment that starts this close to where the last ended continues it

# Filled shapes. A "short" shape is compact (its perimeter is at most SHORT_PERIMETER x its
# longest side: a disc is 3.1, a capsule or a tick 2 to 3) and small.
SHORT_MAX = 12.0  # pt
SHORT_PERIMETER = 3.6
RIBBON_MAX_RATIO = 0.3  # mean width 2A/P over longest side; a pen outline is ~0.1, a disc 0.5
RIBBON_MIN_POINTS = 8  # below this a long thin shape is a rectangle: its mid-line is the stroke
TICK_RATIO = 0.15  # a short shape this thin (width over length) is a tick, never a pen sample
RUN_MIN = 3  # touching short shapes in a row that make a sampled stroke (fewer: single marks)
RUN_GAP = 0.35  # touching: bbox gap at most this x the smaller shape, and never under 0.3 pt

# Furniture.
RULE_SHARE = 0.5  # an axis-aligned line this share of the page across is paper
AXIS_TOL = 0.4  # pt: "exactly horizontal or vertical"
GRID_MIN_LINES = 4  # parallel evenly spaced lines
GRID_MIN_LEN = 20.0  # pt
GRID_SPACING_TOL = 0.03
FRAME_SHARE = 0.3  # an exact axis-aligned rectangle this share of the page is a frame
CELL_MIN = 4  # equal exact rectangles are table or grid cells
LATTICE_FILL = 0.6  # a lattice of points has this share of its x-by-y positions occupied
LATTICE_MIN = 12
LATTICE_GRID = 0.3  # pt: coordinates are compared at this resolution

# Highlighter: wide and see-through. Kept, but outside the writing.
HIGHLIGHT_WIDTH = 5.0
HIGHLIGHT_OPACITY = 0.85

# Grouping, in units of s, the median stroke size.
LINE_GAP = 4.0  # horizontal gap that still joins two strokes in a line
LINE_OVERLAP = 0.3  # share of the shorter stroke's height that must overlap vertically
MIN_HEIGHT = 0.3  # a flat stroke (a bar) counts this tall
REGION_GAP = 0.5  # vertical gap that still joins two lines into one region
MIN_SIZE = 3.0  # pt: floor for s


# ---------------------------------------------------------------------------- result


@dataclass(frozen=True)
class Stroke:
    """One pen stroke: points in display space (page points, y down), in drawing order."""

    points: tuple[Point, ...]
    colour: str  # "#rrggbb"
    opacity: float
    width: float  # pen width in points
    order: int  # paint order on the page; strokes sort by it
    source: str  # "path", "samples" or "outline"

    @property
    def bbox(self) -> BBox:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def highlight(self) -> bool:
        return self.width >= HIGHLIGHT_WIDTH and self.opacity <= HIGHLIGHT_OPACITY


@dataclass(frozen=True)
class InkRegion:
    """A block of writing: strokes close together (an expression, a line, a few lines)."""

    bbox: BBox
    strokes: tuple[Stroke, ...]  # pen order
    lines: tuple[tuple[int, ...], ...]  # indices into strokes: rows top to bottom, left to right

    def normalised(self, size: float = 1000.0) -> list[list[Point]]:
        """Each stroke's points with the region's top left at (0, 0), scaled evenly so its
        longer side is `size`. One list per stroke, in pen order."""
        k, x0, y0 = self._scale(size)
        return [[((x - x0) * k, (y - y0) * k) for x, y in s.points] for s in self.strokes]

    def _scale(self, size: float) -> tuple[float, float, float]:
        x0, y0, x1, y1 = self.bbox
        return size / max(x1 - x0, y1 - y0, 1.0), x0, y0

    def to_inkml(self, size: float = 1000.0) -> str:
        """InkML (W3C) for the region: one trace per stroke in pen order, coordinates as in
        `normalised`, one brush per colour."""
        brushes: dict[tuple[str, float], str] = {}
        for s in self.strokes:
            brushes.setdefault((s.colour, s.opacity), f"b{len(brushes)}")
        out = [
            '<ink xmlns="http://www.w3.org/2003/InkML"><definitions><context><inkSource>'
            '<traceFormat><channel name="X" type="decimal"/><channel name="Y" type="decimal"/>'
            "</traceFormat></inkSource></context>"
        ]
        for (colour, opacity), ident in brushes.items():
            out.append(
                f'<brush xml:id="{ident}"><brushProperty name="color" value="{colour}"/>'
                f'<brushProperty name="transparency" value="{round((1 - opacity) * 255)}"/>'
                "</brush>"
            )
        out.append("</definitions><traceGroup>")
        for s, pts in zip(self.strokes, self.normalised(size), strict=True):
            data = ", ".join(f"{x:.2f} {y:.2f}" for x, y in pts)
            out.append(f'<trace brushRef="#{brushes[(s.colour, s.opacity)]}">{data}</trace>')
        out.append("</traceGroup></ink>")
        return "".join(out)


@dataclass
class PageInk:
    strokes: list[Stroke] = field(default_factory=list)  # writing and drawing, pen order
    highlights: list[Stroke] = field(default_factory=list)  # wide see-through strokes
    regions: list[InkRegion] = field(default_factory=list)  # reading order
    dropped: dict[str, int] = field(default_factory=dict)  # furniture removed, by reason
    analysed: bool = True  # False: the page was over the caps and nothing was read


# ---------------------------------------------------------------------------- reading


@dataclass
class _Prim:
    """A subpath that may be ink, in paint order."""

    kind: str  # "stroked", "short" or "outline"
    pts: list[Point]
    colour: str
    opacity: float
    width: float
    order: int
    drawing: int
    closed: bool = False
    size: float = 0.0  # short: longest side
    elong: float = 1.0  # short: mean width over longest side (small: a tick, not a dot)
    outline_ok: bool = False  # short: can be read as a thin outline


class InkOverrun(Exception):
    """The time budget or a cap ran out: the page is not read (`extract_ink` says so with
    `analysed` False; `group_regions` called alone raises this)."""


def _tick(budget: InkBudget | None) -> None:
    if budget is not None and not budget.left():
        raise InkOverrun


def extract_ink(
    page: pymupdf.Page,
    drawings: Sequence[dict[str, Any]] | None = None,
    *,
    budget: InkBudget | None = None,
    max_paths: int = PAGE_MAX_PATHS,
    max_items: int = INK_MAX_ITEMS,
    max_strokes: int = MAX_STROKES,
) -> PageInk:
    """Strokes, regions and what was dropped for one page. `drawings` is
    `page.get_cdrawings()` when the caller has it already. `budget` is the document's
    ink-analysis budget (a fresh `InkBudget` when not given), checked in every phase and
    loop; over a cap or the budget the result is empty with `analysed` False."""
    if budget is None:
        budget = InkBudget()
    if drawings is None:
        drawings = page.get_cdrawings()
    result = PageInk()
    if len(drawings) > max_paths or sum(len(d.get("items") or ()) for d in drawings) > max_items:
        result.analysed = False
        return result
    try:
        dropped: Counter[str] = Counter()
        prims = _primitives(page, drawings, dropped, budget)
        prims = _drop_furniture(prims, page.rect, dropped, budget)
        strokes = _strokes(prims, dropped, budget)
        strokes = _on_page(strokes, page.rect, dropped)
        if len(strokes) > max_strokes:
            raise InkOverrun
        result.dropped = dict(dropped)
        result.strokes = [s for s in strokes if not s.highlight]
        result.highlights = [s for s in strokes if s.highlight]
        result.regions = group_regions(result.strokes, budget=budget, frame=tuple(page.rect))
    except InkOverrun:
        return PageInk(analysed=False)
    return result


def _on_page(strokes: list[Stroke], page: pymupdf.Rect, dropped: Counter) -> list[Stroke]:
    """Strokes with any part on the page. (A path object that holds many strokes can reach
    off the page; the strokes that lie wholly outside it are not writing on it.)"""
    out = []
    for st in strokes:
        x0, y0, x1, y1 = st.bbox
        if x1 < page.x0 or x0 > page.x1 or y1 < page.y0 or y0 > page.y1:
            dropped["off_page"] += 1
        else:
            out.append(st)
    return out


def _colour(value: Sequence[float] | None) -> str:
    if not value:
        return "#000000"
    v = [min(1.0, max(0.0, float(c))) for c in value]
    if len(v) == 1:
        v = [v[0]] * 3
    elif len(v) == 4:  # CMYK
        v = [(1 - v[i]) * (1 - v[3]) for i in range(3)]
    return "#" + "".join(f"{round(c * 255):02x}" for c in v[:3])


def _bezier(p0: Point, p1: Point, p2: Point, p3: Point) -> list[Point]:
    reach = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
    n = min(16, max(2, math.ceil(reach / FLATTEN_STEP)))
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))  # fmt: skip
    return out


def _subpaths(items: Sequence[Any]) -> list[tuple[list[Point], bool]]:
    """Flattened subpaths of one drawing, in order, and whether each returns to its start.
    The pen jumps (a new moveto) wherever a segment does not start where the last ended;
    rectangles and quads are subpaths of their own."""
    out: list[tuple[list[Point], bool]] = []
    cur: list[Point] = []
    last: Point | None = None

    def flush() -> None:
        nonlocal cur
        if cur:
            closed = len(cur) > 2 and math.dist(cur[0], cur[-1]) <= 0.1
            out.append((cur, closed))
        cur = []

    for it in items:
        op = it[0]
        if op == "re":
            flush()
            r = it[1]
            out.append(
                ([(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3]), (r[0], r[1])], True)
            )
            last = None
        elif op == "qu":
            flush()
            q = it[1]
            ul, ur, ll, lr = (tuple(q[i]) for i in range(4))
            out.append(([ul, ur, lr, ll, ul], True))
            last = None
        elif op in ("l", "c"):
            start = (it[1][0], it[1][1])
            if last is None or math.dist(start, last) > 0.05:
                flush()
                cur = [start]
            if op == "l":
                end = (it[2][0], it[2][1])
                cur.append(end)
            else:
                p0, p1, p2, p3 = ((p[0], p[1]) for p in it[1:5])
                cur.extend(_bezier(p0, p1, p2, p3))
                end = cur[-1]
            last = end
        else:
            flush()
            last = None
    flush()
    return out


def _dedupe(pts: list[Point]) -> list[Point]:
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) > POINT_EPS or abs(p[1] - out[-1][1]) > POINT_EPS:
            out.append(p)
    return out


def _bbox(pts: Sequence[Point]) -> BBox:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _shape(pts: Sequence[Point]) -> tuple[float, float, float]:
    """Longest bbox side, perimeter and area of a closed polygon."""
    x0, y0, x1, y1 = _bbox(pts)
    perimeter = area2 = 0.0
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        perimeter += math.dist(a, b)
        area2 += a[0] * b[1] - b[0] * a[1]
    return max(x1 - x0, y1 - y0), perimeter, abs(area2) / 2


def _axis_aligned(pts: Sequence[Point]) -> str | None:
    x0, y0, x1, y1 = _bbox(pts)
    if y1 - y0 <= AXIS_TOL and x1 - x0 > AXIS_TOL:
        return "h"
    if x1 - x0 <= AXIS_TOL and y1 - y0 > AXIS_TOL:
        return "v"
    return None


def _exact_rect(pts: Sequence[Point]) -> bool:
    """Four or five points that are the corners of an axis-aligned rectangle."""
    if not 4 <= len(pts) <= 5:
        return False
    xs = sorted({round(p[0] / AXIS_TOL) for p in pts})
    ys = sorted({round(p[1] / AXIS_TOL) for p in pts})
    return len(xs) <= 2 and len(ys) <= 2


def _primitives(
    page: pymupdf.Page,
    drawings: Sequence[dict[str, Any]],
    dropped: Counter,
    budget: InkBudget | None = None,
) -> list[_Prim]:
    """Every subpath that might be ink, in paint order, in display space."""
    rotated = page.rotation % 360 != 0
    m = page.rotation_matrix
    frame = page.rect
    out: list[_Prim] = []
    order = 0
    for di, d in enumerate(drawings):
        if not di & 255:
            _tick(budget)
        kind = d.get("type")
        if kind not in ("f", "s", "fs"):
            continue
        fill = d.get("fill") if kind in ("f", "fs") else None
        stroke = d.get("color") if kind in ("s", "fs") else None
        opacity = d.get("fill_opacity", 1.0) if fill is not None else d.get("stroke_opacity", 1.0)
        if fill is None and stroke is None:
            continue
        if opacity is not None and opacity <= 0.0:
            dropped["hidden"] += 1
            continue
        opacity = 1.0 if opacity is None else float(opacity)
        rx0, ry0, rx1, ry1 = d["rect"]
        if rotated:
            rx0, ry0, rx1, ry1 = pymupdf.Rect(rx0, ry0, rx1, ry1) * m
        if rx1 < frame.x0 or rx0 > frame.x1 or ry1 < frame.y0 or ry0 > frame.y1:
            dropped["off_page"] += 1
            continue
        dashed = d.get("dashes") not in (None, "", "[] 0", "[] 0.0")
        width = float(d.get("width") or 0.0)
        for pts, closed in _subpaths(d.get("items") or ()):
            if rotated:
                pts = [(p.x, p.y) for p in (pymupdf.Point(q) * m for q in pts)]
            pts = _dedupe(pts)
            order += 1
            if fill is not None:
                prim = _filled(pts, _colour(fill), opacity, order, di, frame, dropped)
            else:
                prim = _Prim("stroked", pts, _colour(stroke), opacity, width, order, di, closed)
                if dashed and _axis_aligned(pts):
                    dropped["dashed"] += 1
                    continue
            if prim is not None:
                out.append(prim)
    return out


def _filled(
    pts: list[Point],
    colour: str,
    opacity: float,
    order: int,
    di: int,
    frame: pymupdf.Rect,
    dropped: Counter,
) -> _Prim | None:
    """Classify a filled subpath: a short shape (a pen sample or tick), a whole-stroke
    outline, or something that is not ink."""
    if len(pts) < 3:
        return None
    longest, perimeter, area = _shape(pts)
    if perimeter <= 0 or longest <= 0:
        return None
    mean_width = 2 * area / perimeter
    elong = mean_width / longest
    if _exact_rect(pts) and longest >= 0.25 * min(frame.width, frame.height):
        dropped["background"] += 1  # rules drawn as filled bars, shaded areas, paper
        return None
    if longest <= SHORT_MAX and perimeter <= SHORT_PERIMETER * longest:
        return _Prim("short", pts, colour, opacity, mean_width, order, di, True, longest, elong,
                     elong <= RIBBON_MAX_RATIO)  # fmt: skip
    if elong <= RIBBON_MAX_RATIO and (len(pts) >= RIBBON_MIN_POINTS or _exact_rect(pts)):
        return _Prim("outline", pts, colour, opacity, mean_width, order, di, True, longest, elong)
    dropped["blob"] += 1  # a disc, a region, a shaded shape: not a pen outline
    return None


# ---------------------------------------------------------------------------- furniture


def _lattice(points: Sequence[Point]) -> bool:
    """Points on a regular lattice (a dot grid): most of the x-by-y positions are taken. Ink
    never repeats its coordinates like that."""
    n = len(points)
    if n < LATTICE_MIN:
        return False
    ux = len({round(p[0] / LATTICE_GRID) for p in points})
    uy = len({round(p[1] / LATTICE_GRID) for p in points})
    return ux >= 3 and uy >= 3 and n >= LATTICE_FILL * ux * uy


def _drop_furniture(
    prims: list[_Prim], page: pymupdf.Rect, dropped: Counter, budget: InkBudget | None = None
) -> list[_Prim]:
    _tick(budget)
    gone: set[int] = set()
    lines: dict[tuple[str, str, float], list[tuple[float, float, int]]] = {}
    rects: dict[tuple[float, float], list[int]] = {}
    for i, p in enumerate(prims):
        if p.kind != "stroked":
            continue
        orient = _axis_aligned(p.pts)
        x0, y0, x1, y1 = _bbox(p.pts)
        if orient:
            length = (x1 - x0) if orient == "h" else (y1 - y0)
            across = page.width if orient == "h" else page.height
            if length >= RULE_SHARE * across:
                gone.add(i)
                dropped["rule"] += 1
            elif length >= GRID_MIN_LEN:
                coord = (y0 + y1) / 2 if orient == "h" else (x0 + x1) / 2
                lines.setdefault((orient, p.colour, round(p.width, 1)), []).append(
                    (coord, length, i)
                )
        elif p.closed and _exact_rect(p.pts):
            w, h = x1 - x0, y1 - y0
            if w >= FRAME_SHARE * page.width or h >= FRAME_SHARE * page.height:
                gone.add(i)
                dropped["frame"] += 1
            else:
                rects.setdefault((round(w, 0), round(h, 0)), []).append(i)
    for members in rects.values():
        if len(members) >= CELL_MIN:
            gone.update(members)
            dropped["grid"] += len(members)
    for group in lines.values():
        by_coord: dict[int, list[int]] = {}  # lines on one coordinate (two halves of a row)
        for coord, _length, i in group:
            by_coord.setdefault(round(coord / AXIS_TOL), []).append(i)
        coords = sorted(by_coord)
        run = [coords[0]]
        for cur in coords[1:]:
            spacing = (cur - run[-1]) * AXIS_TOL
            ok = spacing > AXIS_TOL
            if ok and len(run) >= 2:
                step = (run[-1] - run[-2]) * AXIS_TOL
                ok = abs(spacing - step) <= max(2 * AXIS_TOL, GRID_SPACING_TOL * step)
            if ok:
                run.append(cur)
                continue
            _mark_run([i for c in run for i in by_coord[c]], len(run), gone, dropped)
            run = [cur]
        _mark_run([i for c in run for i in by_coord[c]], len(run), gone, dropped)
    _tick(budget)
    # dot grids: short shapes on a lattice, by drawing and by colour and size
    by_key: dict[tuple, list[int]] = {}
    for i, p in enumerate(prims):
        if p.kind == "short" and i not in gone:
            by_key.setdefault(("d", p.drawing), []).append(i)
            by_key.setdefault(("c", p.colour, round(p.size, 1)), []).append(i)
    for members in by_key.values():
        live = [i for i in members if i not in gone]
        if _lattice([_centre(prims[i].pts) for i in live]):
            gone.update(live)
            dropped["dot_grid"] += len(live)
    return [p for i, p in enumerate(prims) if i not in gone]


def _mark_run(members: list[int], rows: int, gone: set[int], dropped: Counter) -> None:
    if rows >= GRID_MIN_LINES:
        gone.update(members)
        dropped["grid"] += len(members)


# ---------------------------------------------------------------------------- strokes


def _centre(pts: Sequence[Point]) -> Point:
    x0, y0, x1, y1 = _bbox(pts)
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def _touch(a: _Prim, b: _Prim) -> bool:
    ax0, ay0, ax1, ay1 = _bbox(a.pts)
    bx0, by0, bx1, by1 = _bbox(b.pts)
    gap = max(JOIN_TOL, RUN_GAP * min(a.size, b.size))
    return ax0 - gap <= bx1 and bx0 - gap <= ax1 and ay0 - gap <= by1 and by0 - gap <= ay1


def _strokes(prims: list[_Prim], dropped: Counter, budget: InkBudget | None = None) -> list[Stroke]:
    out: list[Stroke] = []
    i = 0
    n = len(prims)
    while i < n:
        _tick(budget)  # an outline costs about 3 ms
        p = prims[i]
        if p.kind == "stroked":
            # Xournal++ style: one segment per path object; chain the ones that continue.
            pts = list(p.pts)
            j = i + 1
            while (
                j < n
                and prims[j].kind == "stroked"
                and len(prims[j].pts) == 2
                and (prims[j].colour, prims[j].opacity, prims[j].width)
                == (p.colour, p.opacity, p.width)
                and math.dist(prims[j].pts[0], pts[-1]) <= JOIN_TOL
            ):
                pts.append(prims[j].pts[1])
                j += 1
            out.append(Stroke(tuple(pts), p.colour, p.opacity, p.width, p.order, "path"))
            i = j
        elif p.kind == "short":
            run = [p]
            j = i + 1
            while (
                j < n
                and p.elong > TICK_RATIO
                and prims[j].kind == "short"
                and prims[j].elong > TICK_RATIO
                and (prims[j].colour, prims[j].opacity) == (p.colour, p.opacity)
                and _touch(run[-1], prims[j])
            ):
                run.append(prims[j])
                j += 1
            if len(run) >= RUN_MIN:
                pts = _dedupe([_centre(q.pts) for q in run])
                width = float(np.median([q.width for q in run]))
                out.append(Stroke(tuple(pts), p.colour, p.opacity, width, p.order, "samples"))
            else:
                for q in run:
                    out.append(_single(q))
            i = j
        else:
            s = _outline(p)
            if s is not None:
                out.append(s)
            else:
                dropped["unreadable_outline"] += 1
            i += 1
    return out


def _single(p: _Prim) -> Stroke:
    """A short shape on its own: a thin one is a tick, anything else a dot."""
    if p.outline_ok:
        s = _outline(p)
        if s is not None:
            return s
    return Stroke((_centre(p.pts),), p.colour, p.opacity, p.size, p.order, "samples")


def _resample(pts: np.ndarray, n: int, closed: bool) -> np.ndarray:
    """n points evenly along a polyline (or a closed loop) by arc length."""
    loop = np.vstack([pts, pts[:1]]) if closed else pts
    seg = np.hypot(*np.diff(loop, axis=0).T)
    t = np.concatenate([[0.0], np.cumsum(seg)])
    if t[-1] <= 0:
        return np.repeat(pts[:1], n, axis=0)
    u = np.linspace(0.0, t[-1], n, endpoint=not closed)
    return np.column_stack([np.interp(u, t, loop[:, 0]), np.interp(u, t, loop[:, 1])])


def _outline(p: _Prim) -> Stroke | None:
    """Centre-line of a filled pen outline. The outline runs along one edge and back the
    other; the two caps are where the loop folds. The cap is the point whose mirror pairs
    (k steps on one side, k on the other) stay closest, and the other cap is half the loop
    round. The paired edges are averaged."""
    pts = np.array(p.pts[:-1] if math.dist(p.pts[0], p.pts[-1]) <= 0.1 else p.pts, dtype=float)
    if len(pts) < 3:
        return None
    if len(pts) < RIBBON_MIN_POINTS:  # a bar: its mid-line along the long axis
        mid = pts.mean(axis=0)
        vt = np.linalg.svd(pts - mid)[2]
        axis = vt[0]
        proj = (pts - mid) @ axis
        # keep the loop's own direction: from the end nearer the first point
        ends = [mid + axis * proj.min(), mid + axis * proj.max()]
        if math.dist(ends[1], pts[0]) < math.dist(ends[0], pts[0]):
            ends.reverse()
        centre = np.array(ends)
        width = float(np.ptp((pts - mid) @ vt[1]))
    else:
        per = float(np.hypot(*(np.roll(pts, -1, axis=0) - pts).T).sum())
        n = int(min(300, max(16, 2 * round(per / 1.2))))
        loop = _resample(pts, n, closed=True)  # loop[0] is the outline's first point
        m = n // 2
        k = np.arange(m + 1)
        start = np.arange(n)[:, None]
        ahead, behind = loop[(start + k) % n], loop[(start - k) % n]
        a = int(np.argmin(np.hypot(*(ahead - behind).transpose(2, 0, 1)).mean(axis=1)))
        best: tuple[float, np.ndarray, float] | None = None
        for delta in range(-2, 3):
            span = m + delta
            if span < 2 or n - span < 2:
                continue
            side_a = loop[(a + np.arange(span + 1)) % n]
            side_b = loop[(a - np.arange(n - span + 1)) % n]
            count = max(span, n - span) + 1
            ra, rb = _resample(side_a, count, False), _resample(side_b, count, False)
            gap = float(np.hypot(*(ra - rb).T).mean())
            if best is None or gap < best[0]:
                best = (gap, (ra + rb) / 2, float(np.median(np.hypot(*(ra - rb).T))))
        if best is None:
            return None
        centre, width = best[1], best[2]
        # direction: the cap nearer the outline's first point starts the stroke
        other = (a + m) % n
        if min(other, n - other) < min(a, n - a):
            centre = centre[::-1]
    return Stroke(
        tuple(_dedupe([(float(x), float(y)) for x, y in centre])),
        p.colour,
        p.opacity,
        max(width, 0.0),
        p.order,
        "outline",
    )


# ---------------------------------------------------------------------------- grouping


class _Sets:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def _union_near(
    boxes: Sequence[BBox],
    pad_x: float,
    pad_y: float,
    cell: float,
    sets: _Sets,
    budget: InkBudget | None,
    test: Any = None,
) -> None:
    """Union every pair of boxes that intersect once either is grown by (pad_x, pad_y) and
    pass `test`. A grid hash: each box sits in the cells it covers, and looks only in the
    cells its grown box covers. A box over GRID_MAX_CELLS cells is compared with all boxes
    (more than GRID_MAX_BIG of them is an overrun). Pairs are taken once, j after i.."""
    cx, cy = max(cell, pad_x, 1e-6), max(cell, pad_y, 1e-6)
    grid: dict[tuple[int, int], list[int]] = {}
    big: list[int] = []

    for i, (x0, y0, x1, y1) in enumerate(boxes):
        nx = int(x1 // cx - x0 // cx) + 1
        ny = int(y1 // cy - y0 // cy) + 1
        if nx * ny > GRID_MAX_CELLS:
            big.append(i)
            continue
        for gx in range(int(x0 // cx), int(x1 // cx) + 1):
            for gy in range(int(y0 // cy), int(y1 // cy) + 1):
                grid.setdefault((gx, gy), []).append(i)
        if not i & 1023:
            _tick(budget)
    if any(len(v) > GRID_MAX_CELL for v in grid.values()) or len(big) > GRID_MAX_BIG:
        raise InkOverrun
    big_set = set(big)
    work = 0
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        ex0, ey0, ex1, ey1 = x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y
        nx = int(ex1 // cx - ex0 // cx) + 1
        ny = int(ey1 // cy - ey0 // cy) + 1
        if i in big_set or nx * ny > GRID_MAX_CELLS:
            candidates: Any = range(len(boxes))  # a wide box: compare with all
        else:
            found: set[int] = set(big_set)
            for gx in range(int(ex0 // cx), int(ex1 // cx) + 1):
                for gy in range(int(ey0 // cy), int(ey1 // cy) + 1):
                    found.update(grid.get((gx, gy), ()))
            candidates = found
        for j in candidates:
            work += 1
            if not work & 4095:
                _tick(budget)
            if j <= i:
                continue
            b = boxes[j]
            near = ex0 <= b[2] and b[0] <= ex1 and ey0 <= b[3] and b[1] <= ey1
            if near and sets.find(i) != sets.find(j) and (test is None or test(i, j)):
                sets.union(i, j)


def group_regions(
    strokes: Sequence[Stroke],
    *,
    budget: InkBudget | None = None,
    frame: BBox | None = None,
) -> list[InkRegion]:
    """Lines by proximity (strokes that overlap vertically and sit within a few stroke sizes
    of each other horizontally), then regions (lines that stack closely and overlap
    horizontally: a fraction, a matrix, a multi-line equation). Sizes are relative to the
    median stroke, so small and large writing group alike. Regions come in reading order.
    Neighbours are found with a grid hash, so the cost is near linear in the strokes.
    A region's box is clipped to `frame` (the page) when given."""
    if not strokes:
        return []
    boxes = [s.bbox for s in strokes]
    size = max(MIN_SIZE, float(np.median([max(b[2] - b[0], b[3] - b[1]) for b in boxes])))
    flat = MIN_HEIGHT * size
    spans = []
    for b in boxes:
        h = b[3] - b[1]
        mid = (b[1] + b[3]) / 2
        half = max(h, flat) / 2
        spans.append((mid - half, mid + half))
    lines = _Sets(len(boxes))

    def same_line(i: int, j: int) -> bool:
        overlap = min(spans[i][1], spans[j][1]) - max(spans[i][0], spans[j][0])
        shorter = min(spans[i][1] - spans[i][0], spans[j][1] - spans[j][0])
        return overlap >= LINE_OVERLAP * shorter

    line_boxes = [(b[0], sp[0], b[2], sp[1]) for b, sp in zip(boxes, spans, strict=True)]
    _union_near(line_boxes, LINE_GAP * size, 0.0, size, lines, budget, same_line)
    rows: dict[int, list[int]] = {}
    for i in range(len(boxes)):
        rows.setdefault(lines.find(i), []).append(i)
    row_list = list(rows.values())
    row_boxes = [_union([boxes[i] for i in r]) for r in row_list]
    stacks = _Sets(len(row_list))
    gap = REGION_GAP * size
    _union_near(row_boxes, size, gap, size, stacks, budget)
    groups: dict[int, list[int]] = {}
    for r in range(len(row_list)):
        groups.setdefault(stacks.find(r), []).append(r)
    regions = []
    for members in groups.values():
        idx = sorted(i for r in members for i in row_list[r])  # strokes arrive in pen order
        member_strokes = tuple(strokes[i] for i in idx)
        position = {i: k for k, i in enumerate(idx)}
        row_order = sorted(
            members, key=lambda r: (row_boxes[r][1] + row_boxes[r][3], row_boxes[r][0])
        )
        region_lines = tuple(
            tuple(position[i] for i in sorted(row_list[r], key=lambda i: boxes[i][0]))
            for r in row_order
        )
        box = _union([boxes[i] for i in idx])
        if frame is not None:
            box = (
                max(box[0], frame[0]),
                max(box[1], frame[1]),
                min(box[2], frame[2]),
                min(box[3], frame[3]),
            )
        regions.append(InkRegion(box, member_strokes, region_lines))
    regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    return regions


def _union(boxes: Sequence[BBox]) -> BBox:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
