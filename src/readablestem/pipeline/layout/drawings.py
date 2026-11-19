"""Vector drawings: the paths of a page, their straight lines, turns, boxes, radicals, rules and
axes, and which drawn marks belong to math next to them.
"""

from __future__ import annotations

import math
import re
from collections import deque
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

import pymupdf

from readablestem.core.types import BBox
from readablestem.pipeline import page_type
from readablestem.pipeline.layout.shared import _area, _Display, _Path, _Row, _RowIndex, _Straight

MIN_IMAGE_PT = 12.0  # ... and so are images drawn smaller than this on the page
FRAME_LINE_BAND = 0.05  # every point of a frame lies this share of its smaller side (at
RULE_THICKNESS = 1.6  # a fraction bar or table rule is at most this thick
MIN_BARS = 2  # a filled drawing of this many rectangles keeps them (`_Path.rects`) ...
MAX_BAR_ITEMS = 1000  # ... unless it has more items than this

NUMBER_REACH = 2.5  # x font size: furthest a number at the right edge stands from its nearest line

# "(2.4)", "[3]", "(A.1)", "(*)", "Eq. 2.4", "Eqn (5)": an equation number wherever it sits.
# A symbolic tag (`\tag{*}`) is one to three characters, the star and dagger signs among them.
EQUATION_NUMBER = re.compile(
    r"^(?:(?:Eq|Eqn|Equation)\.?\s*)?"
    r"(?:\(\s*[\dA-Za-z*\u2217\u2020\u2021]{1,3}(?:[.\-]\d{1,3})*[a-z]?\s*\)|\[\s*\d{1,3}(?:[.\-]\d{1,3})*[a-z]?\s*\])$"
    r"|^(?:Eq|Eqn|Equation)\.?\s*\d{1,3}(?:[.\-]\d{1,3})*[a-z]?$"
)


# "2.4" or "2.4a" without brackets (OpenStax, many Word templates): an equation number only
# when it sits on the same line as maths, well to its right.
BARE_EQUATION_NUMBER = re.compile(r"^\d{1,3}(?:\.\d{1,3}){1,2}[a-z]?$")


def _page_paths(
    page: pymupdf.Page,
    drawings: list[dict] | None = None,
    seen: Sequence[tuple[bool, bool]] | None = None,
) -> list[_Path]:
    """Visible vector paths, classified. `get_cdrawings` (plain tuples) is several times
    faster than `get_drawings` on plots with thousands of markers; the same list is
    shared with the page type's ink analysis. Near-white paint counts only on something
    darker (`page_type.paint_visibility`: a dark slide's white diagram is seen, a white box
    on the white page is not)."""
    display = _Display.of(page)
    width_pt, height_pt = page.rect.width, page.rect.height
    frame = page.cropbox  # the drawings' own (unrotated) frame
    paths: list[_Path] = []
    lines: list[list[_Straight]] = []  # each path's horizontal and vertical lines
    drawings = drawings if drawings is not None else page.get_cdrawings()
    if page_type.too_complex(drawings):
        return []  # not read: the page keeps its image (`page_type.PAGE_MAX_PATHS`)
    if seen is None:
        seen = page_type.paint_visibility(drawings, page_type.image_boxes(page)[0])
    for source, drawing in enumerate(drawings):
        filled, stroked = seen[source]
        if not (stroked or filled):
            continue  # white boxes and invisible paths
        items = drawing.get("items") or []
        box = display.box(_drawing_bounds(drawing["rect"], items))
        if box[2] < 0 or box[3] < 0 or box[0] > width_pt or box[1] > height_pt:
            continue  # outside the CropBox: not visible on the page
        segments = []
        chords = []  # the ends of each curve
        curved = False
        straight = 0.0  # length of horizontal and vertical lines
        for item in items:
            if item[0] == "l":
                (px, py), (qx, qy) = display.point(*item[1]), display.point(*item[2])
                segments.append((px, py, qx, qy))
                if abs(px - qx) > 0.5 and abs(py - qy) > 0.5:
                    curved = True
                else:
                    straight += abs(px - qx) + abs(py - qy)
            elif item[0] == "c":
                curved = True
                chords.append((*display.point(*item[1]), *display.point(*item[4])))
        width, height = box[2] - box[0], box[3] - box[1]
        if curved and straight >= 0.6 * 2 * (width + height):
            curved = False  # a rounded rectangle (a callout box): its corners are not a plot
        if height <= RULE_THICKNESS and width >= 3:
            kind = "hrule"
        elif width <= RULE_THICKNESS and height >= 3:
            kind = "vrule"
        elif _radical_like(segments, box, items):
            kind = "radical"
        else:
            kind = "shape"
        border = (
            kind == "shape"
            and not filled
            and (_rectangle_outline(items, segments, box) or _border_like(items, display, box))
        )
        rules = kind == "shape" and page_type.paper_rules(drawing, frame.width, frame.height)
        open_stroke = stroked and not filled and not _encloses(items, drawing.get("closePath"))
        fill = filled and kind == "shape"
        path = _Path(box, kind, curved, fill, source, border, rules, open_stroke)
        path.fill_only = filled and not stroked
        strokes = [*segments, *chords]
        if strokes and len(strokes) <= MAX_EDGES and all(i[0] in ("l", "c") for i in items):
            path.edges = tuple(strokes)
        # What later tests read, only for the paths they look at (a page of handwriting
        # has tens of thousands of strokes, and the ink budget runs while they are read).
        if curved and open_stroke:
            path.ends = _subpath_ends(items, display)
            if kind == "shape":
                path.pen = page_type.pen_line(items, float(drawing.get("width") or 0.0))
                path.lines = _short_lines(items, segments)
        if open_stroke and kind == "shape" and max(width, height) >= MIN_IMAGE_PT:
            path.turns = _turns(items, segments)  # an open polyline (`_diagram_signs`)
        if curved and kind == "shape" and not open_stroke:  # a closed or filled hand mark
            path.colour = _rgb(drawing.get("color") if stroked else drawing.get("fill"))
            if fill:  # a ribbon tick has tens of points; a longer fill is no such mark
                path.fill_share = _fill_share(items, display, box) if len(items) <= 200 else 1.0
        if fill and kind == "shape" and not curved and MIN_BARS <= len(items) <= MAX_BAR_ITEMS:
            path.rects = _rects_of(items, display)
            # Thin boxes throughout (a Word table's borders, drawn as filled boxes) are rules
            # drawn as one path, like paper rules, not bars: no figure is made of them.
            path.rules = path.rules or (bool(path.rects) and all(map(_thin_box, path.rects)))
        paths.append(path)
        lines.append(_straight_lines(path, segments))
    _mark_axes(paths, lines)
    return paths


def _thin_box(box: BBox) -> bool:
    """Is the box as thin as a rule (`RULE_THICKNESS`) and long enough to be one?"""
    width, height = box[2] - box[0], box[3] - box[1]
    return (height <= RULE_THICKNESS and width >= 3) or (width <= RULE_THICKNESS and height >= 3)


def _rects_of(items: Sequence[Any], display: _Display) -> tuple[BBox, ...]:
    """The `re` and four-point `qu` items of a filled drawing as display boxes, or nothing
    unless it is rectangles throughout and there are at least `MIN_BARS` of them."""
    out: list[BBox] = []
    for item in items:
        if item[0] == "re":
            out.append(display.box(item[1]))
        elif item[0] == "qu":
            xs, ys = zip(*(display.point(*q) for q in item[1]), strict=True)
            out.append((min(xs), min(ys), max(xs), max(ys)))
        else:
            return ()
    return tuple(out) if len(out) >= MIN_BARS else ()


def _fill_share(items: Sequence[Any], display: _Display, box: BBox) -> float:
    """The share of `box` a filled path covers: its subpaths' areas by the shoelace over
    their points (a curve by its ends), at most 1. A highlight covers most of its box; a
    tick drawn as a filled ribbon little of it."""
    size = _area(box)
    if size <= 0:
        return 0.0
    total = 0.0
    ring: list[tuple[float, float]] = []

    def close() -> float:
        if len(ring) < 3:
            return 0.0
        return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in pairwise([*ring, ring[0]]))) / 2

    for item in items:
        if item[0] == "re":
            x0, y0, x1, y1 = item[1][0], item[1][1], item[1][2], item[1][3]
            total += abs(x1 - x0) * abs(y1 - y0)
            continue
        if item[0] == "qu":
            total += close()
            ring = [display.point(*q) for q in item[1]]
            total += close()
            ring = []
            continue
        start, end = display.point(*item[1]), display.point(*item[-1])
        if ring and (abs(ring[-1][0] - start[0]) > 0.01 or abs(ring[-1][1] - start[1]) > 0.01):
            total += close()
            ring = []
        if not ring:
            ring.append(start)
        ring.append(end)
    total += close()
    return min(1.0, total / size)


def _rgb(paint: Sequence[float] | None) -> tuple[float, float, float]:
    """A drawing's colour as sRGB components 0..1 (grey and CMYK converted)."""
    if not paint:
        return (0.0, 0.0, 0.0)
    if len(paint) == 1:
        return (paint[0], paint[0], paint[0])
    if len(paint) == 4:
        c, m, y, k = paint
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return (paint[0], paint[1], paint[2])


def _turns(items: Sequence[Any], segments: list[tuple[float, float, float, float]]) -> bool:
    """The path has a curve, or lines in two directions at least 20 degrees apart: a
    polyline (a step, a zigzag, an arrowhead, a wire round a corner), not one straight
    line drawn in pieces."""
    if any(item[0] == "c" for item in items):
        return True
    first = None
    for px, py, qx, qy in segments:
        dx, dy = qx - px, qy - py
        length2 = dx * dx + dy * dy
        if length2 < 0.25:
            continue
        if first is None:
            first = (dx, dy, length2)
            continue
        cross = first[0] * dy - first[1] * dx
        if cross * cross > 0.117 * first[2] * length2:  # sin^2 of 20 degrees
            return True
    return False


SUBPATH_ENDS = 16  # at most this many subpath starts and ends are kept per path


def _short_lines(
    items: Sequence[Any], segments: Sequence[tuple[float, float, float, float]]
) -> tuple[tuple[float, float, float, float], ...]:
    """The lines (display space) of a short open stroke of straight lines, one after
    another, too few points for the page type's pen tests (a tick, a bond). A line drawn
    there and back (how MuPDF reports a closed single line) counts once. Empty for
    anything else. `_hand_strokes` decides which are a pen's."""
    if len(segments) != len(items):
        return ()  # curves or rectangles
    lines: list[tuple[float, float, float, float]] = []
    for seg in segments:
        if lines and seg == (lines[-1][2], lines[-1][3], lines[-1][0], lines[-1][1]):
            continue  # back along the line just drawn
        if lines and math.dist(seg[:2], lines[-1][2:]) > 0.05:
            return ()  # a second stroke
        lines.append(seg)
    return tuple(lines) if 0 < len(lines) < page_type.STROKE_MIN_POINTS - 1 else ()


def _subpath_ends(items: Sequence[Any], display: _Display) -> tuple[tuple[float, float], ...]:
    """The start and end of each subpath of a path of lines and curves (display space): a
    subpath starts where an item does not begin at the last one's end. At most
    SUBPATH_ENDS points."""
    found: list[tuple[float, float]] = []
    last = None
    for item in items:
        if item[0] not in ("l", "c"):
            continue
        first, end = item[1], item[-1]
        if last is None or abs(first[0] - last[0]) > 0.01 or abs(first[1] - last[1]) > 0.01:
            if last is not None:
                found.append(display.point(*last))
            found.append(display.point(*first))
            if len(found) >= SUBPATH_ENDS:
                return tuple(found)
        last = end
    if last is not None:
        found.append(display.point(*last))
    return tuple(found)


def _drawing_bounds(rect: Sequence[float], items: Sequence[Any]) -> tuple[float, ...]:
    """The box of every point of a drawing (unrotated frame). MuPDF's `rect` leaves out the
    start of a subpath that begins away from where the last one ended: the axes of a plot
    drawn as one path, "80 75 m 640 75 l 80 335 m 80 75 l S", read as the x-axis alone and
    the y-axis was lost (the second final verify, 2026-10-12). Curves count their control
    points, as `rect` does."""
    x0, y0, x1, y1 = rect[0], rect[1], rect[2], rect[3]
    for item in items:
        op = item[0]
        if op == "re":
            points = ((item[1][0], item[1][1]), (item[1][2], item[1][3]))
        elif op == "qu":
            points = item[1]
        else:  # "l" and "c": their points
            points = item[1:]
        for x, y in points:
            if x < x0:
                x0 = x
            elif x > x1:
                x1 = x
            if y < y0:
                y0 = y
            elif y > y1:
                y1 = y
    return x0, y0, x1, y1


AXIS_MIN_PT = 48.0  # pt: each of a plot's two axes is at least this long
AXIS_CLOSE_PT = 4.5  # pt: a long line's end this close to an axis's far end closes a box
TICK_MAX_PT = 16.0  # pt: a tick mark across or on a ruler's line is at most this long
RULER_TICKS = 3  # tick marks at different places make a line a ruler (a frame's corner marks are 2)
STRAIGHT_MIN_PT = 3.0  # pt: shorter horizontal and vertical lines are not looked at
AXES_MAX_LINES = 20_000  # a page with more straight lines (a grid, hatching) is not looked at
PATH_MAX_LINES = 200  # nor are the lines of a path with more (a grid drawn as one path)


def _straight_lines(
    path: _Path, segments: list[tuple[float, float, float, float]]
) -> list[_Straight]:
    """The horizontal and vertical lines of a path at least STRAIGHT_MIN_PT long; a rule
    drawn as a thin filled rectangle is its middle line. A line drawn there and back (as
    MuPDF reports a closed one-segment path) is one line."""
    if not segments and path.kind in ("hrule", "vrule"):
        x0, y0, x1, y1 = path.bbox
        if path.kind == "hrule":
            return [_Straight(True, (y0 + y1) / 2, x0, x1)]
        return [_Straight(False, (x0 + x1) / 2, y0, y1)]
    found: dict[tuple[bool, int, int, int], _Straight] = {}
    for px, py, qx, qy in segments:
        if abs(py - qy) <= 0.5 and abs(px - qx) >= STRAIGHT_MIN_PT:
            line = _Straight(True, (py + qy) / 2, min(px, qx), max(px, qx))
        elif abs(px - qx) <= 0.5 and abs(py - qy) >= STRAIGHT_MIN_PT:
            line = _Straight(False, (px + qx) / 2, min(py, qy), max(py, qy))
        else:
            continue
        found.setdefault((line.horizontal, round(line.c), round(line.lo), round(line.hi)), line)
        if len(found) > PATH_MAX_LINES:
            return []
    return list(found.values())


def _mark_axes(paths: list[_Path], lines: list[list[_Straight]]) -> None:
    """Flag the paths that hold a plot's axes (`_Path.axis`) or a ruler (`_Path.ruler`),
    whether each line is its own path or one path holds them all.

    Axes: a horizontal and a vertical line, each at least AXIS_MIN_PT long, meeting end to
    end at a corner (JOIN_PT), with nothing else at that corner or at their other ends: an
    L. A frame drawn line by line meets itself at every corner; a U or a staircase has
    more than two lines. A ruler: a line at least AXIS_MIN_PT long that RULER_TICKS or more
    short perpendicular lines (TICK_MAX_PT, at most a quarter of it) cross or end on, at
    different places along it, where a tick that ends on it has its other end free (a
    table's row has its column rules end on the rules above and below: not ticks). A
    y-axis near the page's edge and as tall as the page read as a side of a frame, and a
    timeline across the top of a page as a header rule (the second final verify,
    2026-10-12): an axis or a ruler is never page furniture (`_page_furniture`)."""
    unique: dict[tuple[bool, int, int, int], tuple[_Straight, list[int]]] = {}
    for owner, own in enumerate(lines):
        for line in own:
            key = (line.horizontal, round(line.c), round(line.lo), round(line.hi))
            unique.setdefault(key, (line, []))[1].append(owner)
    flat = list(unique.values())
    if not flat or len(flat) > AXES_MAX_LINES:
        return
    ends: dict[tuple[int, int], list[int]] = {}
    for k, (line, _) in enumerate(flat):
        for x, y in line.ends():
            ends.setdefault((int(x // JOIN_PT), int(y // JOIN_PT)), []).append(k)

    def meeting(k: int, point: tuple[float, float]) -> set[int]:
        """The other lines with an end within JOIN_PT of `point`."""
        x, y = point
        bx, by = int(x // JOIN_PT), int(y // JOIN_PT)
        found = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for m in ends.get((bx + dx, by + dy), ()):
                    if m != k and any(
                        abs(ex - x) <= JOIN_PT and abs(ey - y) <= JOIN_PT
                        for ex, ey in flat[m][0].ends()
                    ):
                        found.add(m)
        return found

    def flag(k: int, attribute: str) -> None:
        for owner in flat[k][1]:
            setattr(paths[owner], attribute, True)

    long = [k for k, (line, _) in enumerate(flat) if line.hi - line.lo >= AXIS_MIN_PT]

    def closed_by(point: tuple[float, float], horizontal: bool) -> bool:
        """A long line of that orientation has an end within AXIS_CLOSE_PT of `point`: the
        L is part of a box whose corners do not quite meet (a report's box of four lines,
        2026-10-13), not axes."""
        x, y = point
        return any(
            flat[j][0].horizontal == horizontal
            and any(
                abs(ex - x) <= AXIS_CLOSE_PT and abs(ey - y) <= AXIS_CLOSE_PT
                for ex, ey in flat[j][0].ends()
            )
            for j in long
        )

    for k in long:
        line = flat[k][0]
        if not line.horizontal:
            continue
        for corner, other in (line.ends(), line.ends()[::-1]):
            met = meeting(k, corner)
            if len(met) != 1:
                continue
            (m,) = met
            upright = flat[m][0]
            if upright.horizontal or upright.hi - upright.lo < AXIS_MIN_PT or meeting(k, other):
                continue
            near, far = upright.ends()
            if abs(near[0] - corner[0]) > JOIN_PT or abs(near[1] - corner[1]) > JOIN_PT:
                near, far = far, near
            if closed_by(other, False) or closed_by(far, True):
                continue
            if meeting(m, near) == {k} and not meeting(m, far):
                flag(k, "axis")
                flag(m, "axis")
                for owner in {*flat[k][1], *flat[m][1]}:
                    paths[owner].axes += ((line, upright),)
    # Rulers: short lines by orientation, in bands of TICK_MAX_PT of their middle.
    short: dict[tuple[bool, int], list[int]] = {}
    for k, (line, _) in enumerate(flat):
        if line.hi - line.lo <= TICK_MAX_PT:
            middle = (line.lo + line.hi) / 2
            short.setdefault((line.horizontal, int(middle // TICK_MAX_PT)), []).append(k)
    across: dict[tuple[bool, int], list[int]] = {}  # every line, by orientation and place
    for k, (line, _) in enumerate(flat):
        across.setdefault((line.horizontal, int(line.c // JOIN_PT)), []).append(k)

    def on_parallel(point: tuple[float, float], horizontal: bool, skip: int) -> bool:
        """`point` lies on a line of that orientation other than `skip`."""
        x, y = point
        c, t = (y, x) if horizontal else (x, y)
        for b in (int(c // JOIN_PT) - 1, int(c // JOIN_PT), int(c // JOIN_PT) + 1):
            for m in across.get((horizontal, b), ()):
                other = flat[m][0]
                if (
                    m != skip
                    and abs(other.c - c) <= JOIN_PT
                    and other.lo - JOIN_PT <= t <= other.hi + JOIN_PT
                ):
                    return True
        return False

    for k in long:
        line = flat[k][0]
        length = line.hi - line.lo
        band = int(line.c // TICK_MAX_PT)
        ticks: list[int] = []
        places: set[int] = set()
        sides: set[int] = set()  # which side of the line each tick stands on (0: across it)
        for b in (band - 1, band, band + 1):
            for t in short.get((not line.horizontal, b), ()):
                tick = flat[t][0]
                if tick.hi - tick.lo > length / 4:
                    continue
                if not line.lo - JOIN_PT <= tick.c <= line.hi + JOIN_PT:
                    continue  # beyond the line's ends
                if tick.lo > line.c + JOIN_PT or tick.hi < line.c - JOIN_PT:
                    continue  # does not reach it
                if not (tick.lo < line.c - 1 and tick.hi > line.c + 1):  # ends on it
                    free = tick.hi if abs(tick.lo - line.c) <= JOIN_PT else tick.lo
                    point = (tick.c, free) if line.horizontal else (free, tick.c)
                    if on_parallel(point, line.horizontal, k):
                        continue  # a column rule between two rules, not a tick
                    sides.add(1 if free > line.c else -1)
                else:
                    sides.add(0)
                ticks.append(t)
                places.add(round(tick.c))
        if len(places) >= RULER_TICKS:
            flag(k, "ruler")
            for t in ticks:
                flag(t, "ruler")
            if len(sides) == 1 and 0 not in sides and _cells(sorted(places), ticks, flat):
                flag(k, "comb")
                for t in ticks:
                    flag(t, "comb")


COMB_CELL_PT = (8.0, 30.0)  # pt: an answer field's letter cells are this wide
COMB_EVEN = 1.3  # its widest cell is at most this x its narrowest


def _cells(places: list[int], ticks: list[int], flat: list[tuple[_Straight, list[int]]]) -> bool:
    """Ticks at even places a letter cell apart (COMB_CELL_PT, COMB_EVEN), all as long:
    the cells of an answer field to write a name or a number in, one letter each."""
    gaps = [b - a for a, b in pairwise(places)]
    lengths = [flat[t][0].hi - flat[t][0].lo for t in ticks]
    return (
        COMB_CELL_PT[0] <= min(gaps)
        and max(gaps) <= COMB_CELL_PT[1]
        and max(gaps) <= COMB_EVEN * min(gaps)
        and max(lengths) <= COMB_EVEN * min(lengths)
    )


def _encloses(items: Sequence[Any], close_path: object) -> bool:
    """The path closes round an area (a box, a polygon, a ring): not a pen mark's open
    stroke. A line that goes back on itself (MuPDF reports a closed one-segment path as
    there and back) encloses nothing."""
    if any(item[0] in ("re", "qu") for item in items):
        return True
    points = [q for item in items if item[0] in ("l", "c") for q in item[1:]]
    if len(points) < 3:
        return False
    first, last = points[0], points[-1]
    if not close_path and (abs(first[0] - last[0]) >= 0.3 or abs(first[1] - last[1]) >= 0.3):
        return False
    area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in pairwise([*points, points[0]]))
    return abs(area2) > 1.0  # pt^2, twice the area


def _border_like(items: Sequence[Any], display: _Display, box: BBox) -> bool:
    """The path runs along the edges of its own box only, all four of them: the shape of a
    frame, single or double, with square, rounded, ticked or curled corners, drawn as one
    path. Each line or curve stays near one edge (all its points within the band of that
    edge; a rounded corner is near both of its edges), and each rectangle or quad lies
    along all four. A plot's grid, axes or curves drawn in the same path cross the inside;
    a plot's two axes drawn as one L-shaped path run along two edges only (2026-10-12: a
    full-page plot's axes were set aside as a frame)."""
    x0, y0, x1, y1 = box
    band = max(MIN_IMAGE_PT, FRAME_LINE_BAND * min(x1 - x0, y1 - y0))
    if min(x1 - x0, y1 - y0) <= 4 * band:
        return False  # too small or too thin to tell its edges from its inside
    edges = [
        lambda q: q[0] - x0,
        lambda q: x1 - q[0],
        lambda q: q[1] - y0,
        lambda q: y1 - q[1],
    ]
    along = [False] * 4
    for item in items:
        if item[0] in ("re", "qu"):
            corners = (
                [(item[1][0], item[1][1]), (item[1][2], item[1][3])]
                if item[0] == "re"
                else list(item[1])
            )
            points = [display.point(*c) for c in corners]
            xs, ys = [q[0] for q in points], [q[1] for q in points]
            if max(min(xs) - x0, x1 - max(xs), min(ys) - y0, y1 - max(ys)) > band:
                return False
            along = [True] * 4
            continue
        points = [display.point(*q) for q in item[1:]]
        near = [all(distance(q) <= band for q in points) for distance in edges]
        if not any(near):
            return False
        along = [a or n for a, n in zip(along, near, strict=True)]
    return all(along)


def _rectangle_outline(
    items: Sequence[Any], segments: list[tuple[float, float, float, float]], box: BBox
) -> bool:
    """One upright rectangle and nothing else: a single "re" item, or four or five
    horizontal and vertical lines along the edges of the path's box (a page frame drawn
    line by line, closed or not)."""
    if len(items) == 1 and items[0][0] == "re":
        return True
    if len(segments) != len(items) or not 4 <= len(segments) <= 5:
        return False
    x0, y0, x1, y1 = box
    for px, py, qx, qy in segments:
        on_x = abs(px - qx) <= 0.5 and min(abs(px - x0), abs(px - x1)) <= 0.5
        on_y = abs(py - qy) <= 0.5 and min(abs(py - y0), abs(py - y1)) <= 0.5
        if not (on_x or on_y):
            return False
    return True


def _radical_like(
    segments: list[tuple[float, float, float, float]], box: BBox, items: Sequence[object]
) -> bool:
    """A stroked square-root sign: lines only, a steep diagonal and a bar along the top."""
    if len(segments) < 2 or len(segments) != len(items) or box[3] - box[1] > 60:
        return False
    top = box[1]
    bar = any(
        abs(y0 - y1) <= 0.6 and abs(x1 - x0) >= 4 and abs(y0 - top) <= 1.0
        for x0, y0, x1, y1 in segments
    )
    steep = any(
        abs(y1 - y0) >= 4 and abs(y1 - y0) >= 2 * abs(x1 - x0) > 0.5 for x0, y0, x1, y1 in segments
    )
    return bar and steep


def _rule_rows(rule: _Path, index: _RowIndex) -> list[_Row]:
    """Maths rows touching a rule: above/below a fraction bar or under a radical."""
    x0, y0, x1, y1 = rule.bbox
    near = []
    reach = 0.8 * index.largest  # the furthest a touching row can be (see below)
    for row in index.near(y0 - reach, y1 + reach):
        if row.kind not in ("math", "frag") or row.absorbed:
            continue
        # Half of the shorter one: a numerator sits on its bar, a bar sits over a radicand.
        overlap = min(row.x1, x1 + 1) - max(row.x0, x0 - 1)
        if overlap < 0.5 * max(1.0, min(row.x1 - row.x0, x1 - x0)) and not (
            rule.kind == "radical" and overlap > 0
        ):
            continue
        distance = max(row.y0 - y1, y0 - row.y1)
        if -row.size <= distance <= 0.8 * row.size:
            near.append(row)
    return near


def _resolve_fragments(rows: list[_Row], rules: list[_Path]) -> list[_Path]:
    """Decide which "frag" rows are maths; return the rules that belong to equations.

    A fragment is maths when it sits on a fraction bar/radical that also touches another
    maths row (or fragments on both sides of a bar), or when it is vertically adjacent to
    a maths row (exponent rows, limits, an equation number on the same line). Others
    become text again (a lone "12 m/s." line is prose).

    Attachment spreads breadth-first from the maths rows through a vertical index, so a
    page full of short numeric rows costs about n log n, not a pass per row.
    """
    mathish = [r for r in rows if r.kind in ("math", "frag") and not r.absorbed]
    index = _RowIndex(mathish)
    attached = {id(r) for r in mathish if r.kind == "math"}
    used: list[_Path] = []
    for rule in rules:
        near = _rule_rows(rule, index)
        if not near:
            continue
        widest = max(r.x1 - r.x0 for r in near)
        if rule.bbox[2] - rule.bbox[0] > 1.5 * widest + 2 * max(r.size for r in near):
            continue  # a table rule or separator next to a short maths-looking cell
        middle = (rule.bbox[1] + rule.bbox[3]) / 2
        above = any(r.y1 <= middle + 0.5 for r in near)
        below = any(r.y0 >= middle - 0.5 for r in near)
        if any(r.kind == "math" for r in near) or (above and below) or rule.kind == "radical":
            used.append(rule)
            attached.update(id(r) for r in near)
    queue = deque(r for r in mathish if id(r) in attached)
    crossing: dict[int, bool] = {}  # may a number go to maths of the other column? (cached)

    def may_cross(row: _Row) -> bool:
        if id(row) not in crossing:
            number = EQUATION_NUMBER.match(row.clean) or BARE_EQUATION_NUMBER.match(row.clean)
            crossing[id(row)] = not (number and _column_content_near(row, rows))
        return crossing[id(row)]

    reach = 0.6 * index.largest  # _math_neighbours' widest vertical gap
    while queue:
        source = queue.popleft()
        for row in index.near(source.y0 - reach, source.y1 + reach):
            if id(row) not in attached and _math_neighbours(
                row, source, cross=row.kind != "frag" or may_cross(row)
            ):
                attached.add(id(row))
                queue.append(row)
    far = NUMBER_REACH * index.largest
    for row in mathish:
        if row.kind == "frag" and id(row) not in attached:
            for other in index.near(row.y0 - far, row.y1 + far):
                if (
                    other.kind == "math"
                    and (row.column == other.column or (row.x0 >= other.x1 and may_cross(row)))
                    and _number_beside(row, other.bbox, max(row.size, other.size), far=True)
                ):
                    row.far_number = True
                    attached.add(id(row))
                    break
    for row in mathish:
        if row.kind == "frag":
            row.kind = "math" if id(row) in attached else "text"
    return used


# How far to the right of a stray opening brace the first equation of its system may start.
STRAY_BRACE_REACH = 2.0


def _math_neighbours(row: _Row, other: _Row, *, cross: bool = True) -> bool:
    """Does fragment `row` belong with maths row `other`? Vertically adjacent and
    overlapping in x, or inside its box (the second row of a matrix, beside the tall bracket
    the first row starts with), or an equation number beside it (`_number_beside`), or an
    opening brace level with `other` and a little to its left (the brace of a system,
    `_stray_brace_cut`)."""
    size = max(row.size, other.size)
    if other.x0 <= (row.x0 + row.x1) / 2 <= other.x1 and other.y0 <= row.y0 and row.y1 <= other.y1:
        return True
    if (
        row.ink
        and all(c.c in _OPENING_BRACES for c in row.ink)
        and row.column == other.column
        and 0 <= other.x0 - row.x1 <= STRAY_BRACE_REACH * size
        and other.y0 - size <= (row.y0 + row.y1) / 2 <= other.y1 + size
    ):
        return True
    gap_y = max(row.y0 - other.y1, other.y0 - row.y1)
    gap_x = max(row.x0 - other.x1, other.x0 - row.x1)
    if gap_y <= 0.6 * size and gap_x <= 1.0 * size and gap_y > -0.5 * min(row.height, other.height):
        return True
    if not (row.column == other.column or (cross and row.x0 >= other.x1)):
        return False
    return _number_beside(row, other.bbox, size)


def _column_content_near(label: _Row, rows: Sequence[_Row]) -> bool:
    """Is there text of the number's own column within two lines of it?

    On a page with two columns there, the number closes that column's content (a matrix, a
    drawn bracket), not the maths of the other column.
    """
    reach = 2 * label.size
    return any(
        r is not label
        and r.column == label.column
        and r.kind == "text"
        and r.y0 < label.y1 + reach
        and label.y0 - reach < r.y1
        and not (EQUATION_NUMBER.match(r.clean) or BARE_EQUATION_NUMBER.match(r.clean))
        for r in rows
    )


def _number_column(row: _Row, column: str, x1: float) -> bool:
    """May the equation number `row` belong to maths of `column` ending at `x1`? The same
    column, or any column when the number stands to the right of the maths: on a page with
    a two-column section, the number at the right edge of an equation in the left column is
    in the right column."""
    return row.column == column or row.x0 >= x1


def _number_beside(row: _Row, box: BBox, size: float, *, far: bool = False) -> bool:
    """Is `row` an equation number for the maths in `box`?

    Its vertical centre must be within half a font size of the maths: numbers sit on the
    baseline of a one-line equation, or halfway down a fraction (between numerator and
    denominator, on neither line). "(2.4)" or "Eq. 2.4" may sit on either side; a bare
    "2.4" counts only to the right of the maths, with a gap. With `far`, a bracketed number
    at the right edge may stand up to `NUMBER_REACH` font sizes off the maths: it is
    centered on a group of lines (or on an aligned row with wide spacing) and the nearest
    line is its equation.
    """
    text = row.clean
    bare = bool(BARE_EQUATION_NUMBER.match(text))
    if not bare and not EQUATION_NUMBER.match(text):
        return False
    centre = (row.y0 + row.y1) / 2
    right = row.x0 - box[2] >= 1.0 * size
    reach = NUMBER_REACH if far and right and not bare else 0.5
    if not box[1] - reach * size <= centre <= box[3] + reach * size:
        return False
    return not bare or right


# Signs that continue the line above when a maths line starts with them ("= 250 m/s" under
# "v = ...", "+ c" under a long sum).
# A system's opening brace: the glyph, or the pieces TeX builds a tall one from (CMEX), the
# Unicode piece characters `glyph_names` reads from the font. Adobe's private-use codes for the
# pieces never reach here: a glyph still undecoded is a gap mark (`glyph_names.mark_gaps`).
_OPENING_BRACES = frozenset("{\u23a7\u23a8\u23a9\u23aa")
JOIN_PT = 1.5  # pt: line ends this close meet (a corner of a staircase, a connector's end)
MAX_EDGES = 48  # a path with more lines than this stands for itself by its box (`_Path.edges`)
