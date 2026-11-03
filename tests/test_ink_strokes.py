"""Vector ink to strokes (`pipeline.ink_strokes`).

Synthetic PDFs with known stroke paths in each way a notes app draws ink (stroked paths,
one filled shape per pen sample, filled outlines), on ruled, gridded and dotted paper,
in pastel ink; plus the two committed ink fixtures (tests/fixtures/handwriting). The
recovered strokes must match the drawn paths within a tolerance and in pen order.
"""

from __future__ import annotations

import itertools
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pymupdf
import pytest

from readablestem.pipeline import ink_strokes
from readablestem.pipeline.ink_strokes import Stroke, extract_ink, group_regions

HANDWRITING = Path(__file__).parent / "fixtures" / "handwriting"
PAGE = (300.0, 300.0)

Pts = list[tuple[float, float]]


# ----------------------------------------------------------------------------- truth


def _arc(cx, cy, r, a0, a1, n=39) -> Pts:
    return [
        (cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
         cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)))
        for i in range(n + 1)
    ]  # fmt: skip


def _line(p, q, n=30) -> Pts:
    return [(p[0] + (q[0] - p[0]) * i / n, p[1] + (q[1] - p[1]) * i / n) for i in range(n + 1)]


def _wave(x0, y0, x1, amp, n=60) -> Pts:
    return [
        (x0 + (x1 - x0) * i / n, y0 + amp * math.sin(2 * math.pi * 1.5 * i / n))
        for i in range(n + 1)
    ]


# Drawn in this order, which is not left to right or top to bottom.
TRUTH: list[Pts] = [
    _wave(150, 150, 250, 8),
    _line((40, 40), (48, 90)),
    _arc(120, 70, 18, 30, 320),
    _line((60, 160), (100, 200)),
    _wave(180, 40, 260, 10),
]
COLOURS = [(0.0, 0.44, 1.0), (0.05, 0.05, 0.08), (0.8, 0.1, 0.1), (0.1, 0.55, 0.2), (0.5, 0.2, 0.7)]


def _dense(poly: Pts, step: float = 0.4) -> np.ndarray:
    out = [poly[0]]
    for a, b in itertools.pairwise(poly):
        d = math.dist(a, b)
        for i in range(1, max(1, math.ceil(d / step)) + 1):
            t = i / max(1, math.ceil(d / step))
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return np.array(out)


def _dist_to_polyline(points: np.ndarray, poly: Pts) -> np.ndarray:
    """Distance of each point to the nearest segment of poly."""
    a = np.array(poly[:-1])[None]
    b = np.array(poly[1:])[None]
    p = points[:, None]
    ab = b - a
    t = np.clip(((p - a) * ab).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-12), 0, 1)
    return np.hypot(*(p - (a + t[..., None] * ab)).transpose(2, 0, 1)).min(axis=1)


def assert_matches(stroke: Stroke, truth: Pts, tol: float, end_tol: float | None = None) -> None:
    """Every recovered point lies on the true path, and the true path is covered; the
    stroke runs the same way."""
    rec = np.array(stroke.points)
    assert _dist_to_polyline(rec, truth).max() <= tol, "recovered points stray from the path"
    covered = _dist_to_polyline(_dense(truth), list(map(tuple, rec))) if len(rec) > 1 else None
    assert covered is not None
    assert covered.max() <= (end_tol or tol), "part of the path is missing"
    assert math.dist(rec[0], truth[0]) < math.dist(rec[0], truth[-1]), "wrong direction"


# ----------------------------------------------------------------------------- drawing


def _template(page: pymupdf.Page, kind: str, colour=(0.36, 0.8, 1.0)) -> None:
    w, h = page.rect.width, page.rect.height
    shape = page.new_shape()
    if kind == "ruled":
        for y in range(20, int(h) - 10, 22):
            shape.draw_line((0, y), (w, y))
        shape.finish(color=colour, width=0.5)
        shape.draw_line((30, 0), (30, h))
        shape.finish(color=(1.0, 0.48, 0.62), width=0.5)
    elif kind == "grid":  # squared paper in short lines, none half the page across
        for y in range(10, int(h), 10):
            for x0 in (10, 150):
                shape.draw_line((x0, y), (x0 + 120, y))
                shape.finish(color=colour, width=0.3)
        for x in range(10, int(w), 10):
            for y0 in (10, 150):
                shape.draw_line((x, y0), (x, y0 + 120))
                shape.finish(color=colour, width=0.3)
    elif kind == "dots":
        for y in range(10, int(h), 12):
            for x in range(10, int(w), 12):
                shape.draw_circle((x, y), 0.6)
        shape.finish(fill=colour, color=None)
    elif kind == "background":
        shape.draw_rect(page.rect)
        shape.finish(fill=(0.98, 0.97, 0.9), color=None)
    shape.commit()


def _ribbon(centre: Pts, width: float) -> Pts:
    dense = [tuple(p) for p in _dense(centre, 1.5)]
    left, right = [], []
    for i, (x, y) in enumerate(dense):
        a, b = dense[max(0, i - 1)], dense[min(len(dense) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length * width / 2, dx / length * width / 2
        left.append((x + nx, y + ny))
        right.append((x - nx, y - ny))
    return left + right[::-1]


def draw_ink(
    page: pymupdf.Page,
    style: str,
    strokes: list[Pts] = TRUTH,
    colours=COLOURS,
    width: float = 1.4,
) -> None:
    """Draw each stroke in order in one of the ways note apps write ink."""
    shape = page.new_shape()
    packed = style == "samples_packed"
    for stroke, colour in zip(strokes, colours, strict=False):
        if style == "polyline":
            shape.draw_polyline([tuple(p) for p in _dense(stroke, 0.9)])
            shape.finish(color=colour, width=width, lineCap=1, lineJoin=1)
        elif style == "bezier":  # one cubic piece per three true points
            pts = stroke
            for i in range(0, len(pts) - 3, 3):
                p0, p3 = pts[i], pts[i + 3]
                # Catmull-Rom tangents give a curve through every third point
                a, b = pts[max(0, i - 3)], pts[min(len(pts) - 1, i + 6)]
                c1 = (p0[0] + (pts[i + 3][0] - a[0]) / 6, p0[1] + (pts[i + 3][1] - a[1]) / 6)
                c2 = (p3[0] - (b[0] - pts[i][0]) / 6, p3[1] - (b[1] - pts[i][1]) / 6)
                shape.draw_bezier(p0, c1, c2, p3)
            shape.finish(color=colour, width=width, lineCap=1, closePath=False)
        elif style == "segments":  # Xournal++: a path object per segment
            dense = [tuple(p) for p in _dense(stroke, 0.8)]
            for a, b in itertools.pairwise(dense):
                shape.draw_line(a, b)
                shape.finish(color=colour, width=width, lineCap=1, closePath=False)
        elif style.startswith("samples"):  # a filled disc per pen sample
            for x, y in _dense(stroke, 1.1):
                shape.draw_circle((float(x), float(y)), width * 0.6)
                if not packed:
                    shape.finish(fill=colour, color=None)
            if packed:
                shape.finish(fill=colour, color=None)
        elif style.startswith("outline"):
            shape.draw_polyline(_ribbon(stroke, width))
            shape.finish(fill=colour, color=None, closePath=True)  # packed: one path each too
        else:
            raise AssertionError(style)
    shape.commit()


def make_page(
    style: str | None, *, template: str | None = None, template_colour=(0.36, 0.8, 1.0), **kw
) -> pymupdf.Page:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE[0], height=PAGE[1])
    if template:
        _template(page, template, template_colour)
    if style:
        draw_ink(page, style, **kw)
    return page


# ----------------------------------------------------------------------------- styles

STYLES = [
    ("polyline", 0.5, None),
    ("bezier", 0.8, None),
    ("segments", 0.5, None),
    ("samples", 0.3, 1.5),
    ("samples_packed", 0.3, 1.5),
    ("outline", 0.9, 1.7),
    ("outline_packed", 0.9, 1.7),
]


@pytest.mark.parametrize(("style", "tol", "end_tol"), STYLES)
def test_each_drawing_style_recovers_the_strokes_in_pen_order(style, tol, end_tol):
    ink = extract_ink(make_page(style))
    assert len(ink.strokes) == len(TRUTH), style
    assert [s.order for s in ink.strokes] == sorted(s.order for s in ink.strokes)
    for stroke, truth in zip(ink.strokes, TRUTH, strict=True):
        assert_matches(stroke, truth, tol, end_tol)
    expected = {"samples": "samples", "outline": "outline"}.get(style.split("_")[0], "path")
    assert {s.source for s in ink.strokes} == {expected}


@pytest.mark.parametrize(("style", "_tol", "_end"), STYLES)
def test_colour_and_width_follow_each_stroke(style, _tol, _end):
    ink = extract_ink(make_page(style))
    want = ["#" + "".join(f"{round(c * 255):02x}" for c in col) for col in COLOURS]
    assert [s.colour for s in ink.strokes] == want
    for s in ink.strokes:
        assert 0.7 <= s.width <= 2.0, (style, s.width)
        assert s.opacity == 1.0


def test_pen_order_is_paint_order_not_position():
    ink = extract_ink(make_page("polyline"))
    firsts = [s.points[0] for s in ink.strokes]
    assert firsts != sorted(firsts), "the fixture must draw out of reading order"
    assert math.dist(firsts[0], TRUTH[0][0]) < 1.0


# ----------------------------------------------------------------------------- furniture


@pytest.mark.parametrize("style", ["polyline", "samples", "outline"])
@pytest.mark.parametrize("template", ["ruled", "grid", "dots", "background"])
def test_paper_templates_are_dropped_and_the_ink_kept(style, template):
    ink = extract_ink(make_page(style, template=template))
    assert len(ink.strokes) == len(TRUTH), (style, template, ink.dropped)
    assert sum(ink.dropped.values()) > 0
    for stroke, truth in zip(ink.strokes, TRUTH, strict=True):
        assert_matches(stroke, truth, 1.0, 1.7)


def test_ruled_paper_alone_has_no_strokes():
    ink = extract_ink(make_page(None, template="ruled"))
    assert ink.strokes == []
    assert ink.dropped["rule"] >= 12


def test_square_grid_lines_shorter_than_the_page_are_dropped_by_their_spacing():
    ink = extract_ink(make_page(None, template="grid"))
    assert ink.strokes == [] and ink.dropped["grid"] > 40


@pytest.mark.parametrize("style", ["polyline", "samples", "outline"])
def test_pastel_ink_on_a_pastel_template_is_kept(style):
    pastel = (0.8, 0.8, 1.0)
    ink = extract_ink(
        make_page(style, template="ruled", template_colour=pastel, colours=[pastel] * 5)
    )
    assert len(ink.strokes) == len(TRUTH)
    assert {s.colour for s in ink.strokes} == {"#ccccff"}
    assert ink.regions


def test_handwritten_rule_shorter_than_half_the_page_is_ink():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    shape = page.new_shape()
    shape.draw_line((40, 100), (150, 100.0))  # an underline: 110 of 300
    shape.finish(color=(0, 0, 0), width=1)
    shape.commit()
    ink = extract_ink(page)
    assert len(ink.strokes) == 1 and not ink.dropped


def test_dashed_lines_are_furniture():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    shape = page.new_shape()
    shape.draw_line((40, 100), (150, 100))
    shape.finish(color=(0, 0, 0), width=1, dashes="[3] 0")
    shape.commit()
    assert extract_ink(page).dropped == {"dashed": 1}


def test_hidden_ink_is_not_read():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    shape = page.new_shape()
    shape.draw_polyline(TRUTH[2])
    shape.finish(color=(0, 0, 0), width=1, stroke_opacity=0)
    shape.commit()
    ink = extract_ink(page)
    assert ink.strokes == [] and ink.dropped == {"hidden": 1}


# ----------------------------------------------------------------------------- the rest


def test_highlighter_is_kept_apart_from_the_writing():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    shape = page.new_shape()
    shape.draw_polyline(_line((40, 60), (180, 66), 20))
    shape.finish(color=(1, 0.9, 0.2), width=9, stroke_opacity=0.4)
    shape.draw_polyline(TRUTH[2])
    shape.finish(color=(0, 0, 0), width=1)
    shape.commit()
    ink = extract_ink(page)
    assert len(ink.highlights) == 1 and ink.highlights[0].opacity == pytest.approx(0.4)
    assert len(ink.strokes) == 1 and len(ink.regions) == 1


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_pages_give_display_coordinates(rotation):
    page = make_page("polyline")
    page.set_rotation(rotation)
    ink = extract_ink(page)
    assert len(ink.strokes) == len(TRUTH)
    mapped = [[tuple(pymupdf.Point(p) * page.rotation_matrix) for p in t] for t in TRUTH]
    for stroke, truth in zip(ink.strokes, mapped, strict=True):
        assert_matches(stroke, truth, 0.6)
    for s in ink.strokes:
        x0, _y0, x1, y1 = s.bbox
        assert page.rect.x0 <= x0 and x1 <= page.rect.x1 and y1 <= page.rect.y1


def test_pages_over_the_caps_are_not_read():
    page = make_page("polyline")
    assert not extract_ink(page, max_paths=2).analysed
    assert not extract_ink(page, max_items=10).analysed


def test_a_short_tick_and_a_dot_are_strokes_and_two_ticks_stay_apart():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    shape = page.new_shape()
    for y in (100.0, 105.0):  # the bars of an "=", as thin filled outlines
        shape.draw_polyline(_ribbon(_line((50, y), (58, y + 0.5), 6), 1.0))
        shape.finish(fill=(0, 0, 0), color=None, closePath=True)
    shape.draw_circle((80, 100), 1.0)  # a full stop
    shape.finish(fill=(0, 0, 0), color=None)
    shape.commit()
    ink = extract_ink(page)
    assert [len(s.points) > 1 for s in ink.strokes] == [True, True, False]
    assert math.dist(ink.strokes[2].points[0], (80, 100)) < 0.2


# ----------------------------------------------------------------------------- grouping


def _word(x: float, y: float, n: int = 4, size: float = 8.0, gap: float = 3.0) -> list[Stroke]:
    out = []
    for i in range(n):
        x0 = x + i * (size * 0.6 + gap)
        out.append(
            Stroke(
                ((x0, y), (x0 + size * 0.2, y + size), (x0 + size * 0.6, y + size * 0.5)),
                "#000000",
                1.0,
                1.0,
                len(out),
                "path",
            )
        )
    return out


def _renumber(strokes: list[Stroke]) -> list[Stroke]:
    return [
        Stroke(s.points, s.colour, s.opacity, s.width, i, s.source) for i, s in enumerate(strokes)
    ]


def test_lines_group_by_proximity_and_far_apart_blocks_are_regions():
    strokes = _renumber(
        _word(40, 40) + _word(100, 41) + _word(40, 100) + _word(200, 200)  # two lines, one lone
    )
    regions = group_regions(strokes)
    assert [len(r.strokes) for r in regions] == [8, 4, 4]
    assert [len(line) for line in regions[0].lines] == [8]
    assert regions[0].bbox[1] < regions[1].bbox[1] < regions[2].bbox[1]


def test_closely_stacked_lines_make_one_region_with_lines_in_order():
    strokes = _renumber(_word(40, 40) + _word(40, 52) + _word(40, 64))
    (region,) = group_regions(strokes)
    assert len(region.lines) == 3
    tops = [min(region.strokes[i].bbox[1] for i in line) for line in region.lines]
    assert tops == sorted(tops)


def test_a_fraction_is_one_region_even_though_its_bar_is_flat():
    bar = Stroke(((40.0, 50.0), (80.0, 50.0)), "#000000", 1.0, 1.0, 0, "path")
    top = _word(48, 38, n=2)
    bottom = _word(48, 54, n=2)
    (region,) = group_regions(_renumber([bar, *top, *bottom]))
    assert len(region.strokes) == 5


def test_strokes_stay_in_pen_order_inside_a_region():
    strokes = _renumber(_word(40, 40))
    shuffled = [strokes[2], strokes[0], strokes[3], strokes[1]]
    (region,) = group_regions(shuffled)
    assert [s.order for s in region.strokes] == [2, 0, 3, 1]  # as given (pen order)
    assert [region.strokes[i].order for i in region.lines[0]] == [0, 1, 2, 3]  # left to right


# ----------------------------------------------------------------------------- output


def test_normalised_points_are_per_region_and_keep_the_aspect_ratio():
    (region,) = group_regions(extract_ink(make_page("polyline")).strokes[2:3])
    norm = region.normalised(1000.0)
    flat = np.array([p for s in norm for p in s])
    assert flat.min(axis=0).tolist() == pytest.approx([0.0, 0.0], abs=1e-6)
    assert flat.max() == pytest.approx(1000.0)
    x0, y0, x1, y1 = region.bbox
    assert flat[:, 0].max() / flat[:, 1].max() == pytest.approx((x1 - x0) / (y1 - y0), rel=1e-6)


def test_inkml_is_well_formed_with_a_trace_per_stroke_in_order():
    ink = extract_ink(make_page("outline"))
    xml = "".join(r.to_inkml() for r in ink.regions[:1])
    root = ET.fromstring(xml)
    ns = {"i": "http://www.w3.org/2003/InkML"}
    traces = root.findall(".//i:trace", ns)
    region = ink.regions[0]
    assert len(traces) == len(region.strokes)
    first = [tuple(map(float, p.split())) for p in (traces[0].text or "").split(",")]
    assert len(first) == len(region.strokes[0].points)
    brushes = {
        b.get("{http://www.w3.org/XML/1998/namespace}id") for b in root.findall(".//i:brush", ns)
    }
    assert {t.get("brushRef")[1:] for t in traces} <= brushes  # type: ignore[index]
    assert all(
        0 <= float(v) <= 1000 for t in traces for p in (t.text or "").split(",") for v in p.split()
    )


# ----------------------------------------------------------------------------- fixtures


def _fixture_ink(name: str) -> ink_strokes.PageInk:
    with pymupdf.open(HANDWRITING / name) as doc:
        return extract_ink(doc[0])


def test_ios_notes_outlines_become_centre_lines_in_the_ink_area():
    ink = _fixture_ink("ios_notes.pdf")
    # 4 lines of words written with filled outlines on ruled paper (ruled lines dropped)
    assert len(ink.strokes) > 100
    assert {s.source for s in ink.strokes} == {"outline"}
    assert {s.colour for s in ink.strokes} == {"#0070ff"}
    assert ink.dropped["rule"] >= 25
    area = (78.0, 66.0, 405.0, 168.0)
    for s in ink.strokes:
        x0, y0, x1, y1 = s.bbox
        assert area[0] <= x0 and x1 <= area[2] and area[1] <= y0 and y1 <= area[3], s.bbox
        assert len(s.points) >= 2
        assert 0.5 <= s.width <= 2.0
    assert [s.order for s in ink.strokes] == sorted(s.order for s in ink.strokes)
    assert sum(len(r.lines) for r in ink.regions) >= 4
    assert sum(len(r.strokes) for r in ink.regions) == len(ink.strokes)


def test_ink_no_layer_polylines_are_the_strokes():
    ink = _fixture_ink("ink_no_layer.pdf")
    with pymupdf.open(HANDWRITING / "ink_no_layer.pdf") as doc:
        drawn = len(doc[0].get_cdrawings())
    assert len(ink.strokes) + 2 == drawn  # the ruled-lines path and the margin line
    assert {s.source for s in ink.strokes} == {"path"}
    assert {s.colour for s in ink.strokes} == {"#0d0d14"}
    assert ink.dropped["rule"] >= 25
    assert sum(len(r.lines) for r in ink.regions) >= 5


def test_fixture_strokes_match_the_pdf_drawing_for_the_polylines():
    """ink_no_layer is one polyline per stroke: the recovered points are the PDF's own."""
    with pymupdf.open(HANDWRITING / "ink_no_layer.pdf") as doc:
        page = doc[0]
        paths = [d for d in page.get_cdrawings() if abs(d["width"] - 0.9) < 0.01]
    ink = _fixture_ink("ink_no_layer.pdf")
    assert len(ink.strokes) == len(paths)
    for s, d in zip(ink.strokes, paths, strict=True):
        assert s.points[0] == pytest.approx(tuple(d["items"][0][1]), abs=0.06)
        assert s.points[-1] == pytest.approx(tuple(d["items"][-1][2]), abs=0.06)


def test_ios_notes_centre_lines_follow_the_strokes_the_fixture_was_written_from():
    """Rebuild ios_notes.pdf from its seed, record the pen paths the writer was given, and
    compare: tapered, wobbling outlines packed many to a path object."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("make_fixtures", HANDWRITING / "make_fixtures.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["make_fixtures"] = mod
    spec.loader.exec_module(mod)
    truth: list[Pts] = []
    original = mod.Writer.stroke

    def record(self, centre):
        if len(centre) >= 2:
            truth.append(list(centre))
        original(self, centre)

    mod.Writer.stroke = record
    try:
        doc = pymupdf.open()
        mod.ios_page(doc, 101)
    finally:
        mod.Writer.stroke = original
        sys.modules.pop("make_fixtures", None)
    ink = extract_ink(doc[0])
    assert len(ink.strokes) == len(truth)
    pairs = list(zip(ink.strokes, truth, strict=True))
    off = [float(_dist_to_polyline(np.array(s.points), t).max()) for s, t in pairs]
    gap = [
        float(_dist_to_polyline(np.array(t), list(s.points)).max())
        for s, t in zip(ink.strokes, truth, strict=True)
    ]
    assert float(np.median(off)) < 0.7 and max(off) < 1.6
    assert float(np.median(gap)) < 1.3 and max(gap) < 2.6
    forward = sum(
        math.dist(s.points[0], t[0]) < math.dist(s.points[0], t[-1])
        for s, t in zip(ink.strokes, truth, strict=True)
    )
    assert forward >= 0.9 * len(truth)  # the misses are closed loops, whose start is a guess


# ----------------------------------------------------------------------------- limits


def _marks(points: list[tuple[float, float]]) -> list[Stroke]:
    return [
        Stroke(((x, y), (x + 3, y + 5), (x + 6, y + 2)), "#000000", 1.0, 1.0, i, "path")
        for i, (x, y) in enumerate(points)
    ]


@pytest.mark.parametrize("shape", ["column", "row", "random"])
def test_grouping_50000_strokes_is_fast_whatever_their_layout(shape):
    import random
    import time

    n = 50_000
    rng = random.Random(7)
    if shape == "column":
        pts = [(100.0, 4.0 * i) for i in range(n)]
    elif shape == "row":
        pts = [(8.0 * i, 100.0) for i in range(n)]
    else:
        pts = [(rng.uniform(0, 5000), rng.uniform(0, 5000)) for _ in range(n)]
    start = time.monotonic()
    regions = group_regions(_marks(pts), budget=ink_strokes.InkBudget(60.0))
    elapsed = time.monotonic() - start
    assert elapsed < 8.0, (shape, elapsed)
    assert sum(len(r.strokes) for r in regions) == n


def test_a_pile_of_strokes_is_an_overrun_not_a_stall():
    import random
    import time

    rng = random.Random(7)
    pts = [(100.0 + rng.uniform(0, 1), 100.0 + rng.uniform(0, 1)) for _ in range(50_000)]
    start = time.monotonic()
    with pytest.raises(ink_strokes.InkOverrun):
        group_regions(_marks(pts), budget=ink_strokes.InkBudget(60.0))
    assert time.monotonic() - start < 5.0


def test_grouping_equals_the_brute_force_partition_on_random_ink():
    import random

    rng = random.Random(3)
    strokes = _marks([(rng.uniform(0, 300), rng.uniform(0, 120)) for _ in range(400)])
    boxes = [s.bbox for s in strokes]
    size = max(
        ink_strokes.MIN_SIZE, float(np.median([max(b[2] - b[0], b[3] - b[1]) for b in boxes]))
    )
    flat = ink_strokes.MIN_HEIGHT * size
    spans = [
        (
            (b[1] + b[3]) / 2 - max(b[3] - b[1], flat) / 2,
            (b[1] + b[3]) / 2 + max(b[3] - b[1], flat) / 2,
        )
        for b in boxes
    ]
    sets = ink_strokes._Sets(len(boxes))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            overlap = min(spans[i][1], spans[j][1]) - max(spans[i][0], spans[j][0])
            shorter = min(spans[i][1] - spans[i][0], spans[j][1] - spans[j][0])
            gap_ok = (
                boxes[i][0] - ink_strokes.LINE_GAP * size <= boxes[j][2]
                and boxes[j][0] - ink_strokes.LINE_GAP * size <= boxes[i][2]
            )
            if gap_ok and overlap >= ink_strokes.LINE_OVERLAP * shorter:
                sets.union(i, j)
    rows: dict[int, list[int]] = {}
    for i in range(len(boxes)):
        rows.setdefault(sets.find(i), []).append(i)
    row_list = list(rows.values())
    rb = [ink_strokes._union([boxes[i] for i in r]) for r in row_list]
    stacks = ink_strokes._Sets(len(row_list))
    gap = ink_strokes.REGION_GAP * size
    for a in range(len(rb)):
        for b in range(a + 1, len(rb)):
            if (
                rb[a][0] - size <= rb[b][2]
                and rb[b][0] - size <= rb[a][2]
                and rb[a][1] - gap <= rb[b][3]
                and rb[b][1] - gap <= rb[a][3]
            ):
                stacks.union(a, b)
    want: dict[int, set[int]] = {}
    for r, members in enumerate(row_list):
        want.setdefault(stacks.find(r), set()).update(strokes[i].order for i in members)
    got = [{s.order for s in r.strokes} for r in group_regions(strokes)]
    assert sorted(map(sorted, got)) == sorted(map(sorted, want.values()))


def test_an_exhausted_budget_returns_an_unanalysed_empty_page():
    ink = extract_ink(make_page("outline"), budget=ink_strokes.InkBudget(0))
    assert not ink.analysed and not ink.strokes and not ink.regions


def test_more_strokes_than_the_cap_are_not_grouped():
    ink = extract_ink(make_page("polyline"), max_strokes=3)
    assert not ink.analysed and not ink.regions


def test_the_caps_are_the_page_type_ones():
    from readablestem.pipeline import page_type

    assert ink_strokes.INK_MAX_ITEMS is page_type.INK_MAX_ITEMS
    assert ink_strokes.PAGE_MAX_PATHS is page_type.PAGE_MAX_PATHS
    assert ink_strokes.InkBudget is page_type.InkBudget


def test_strokes_wholly_off_the_page_are_dropped_and_do_not_stretch_regions():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    shape = page.new_shape()
    shape.draw_polyline(TRUTH[2])
    shape.draw_polyline([(x + 1000.0, y) for x, y in TRUTH[2]])  # same path object, off the page
    shape.finish(color=(0, 0, 0), width=1)
    shape.commit()
    ink = extract_ink(page)
    assert len(ink.strokes) == 1 and ink.dropped == {"off_page": 1}
    (region,) = ink.regions
    assert region.bbox[2] <= page.rect.x1


def test_a_stroke_leaving_the_page_clips_its_region_box_to_the_page():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    shape = page.new_shape()
    shape.draw_polyline([(250.0 + 2 * i, 100.0 + (i % 5)) for i in range(60)])
    shape.finish(color=(0, 0, 0), width=1)
    shape.commit()
    (region,) = extract_ink(page).regions
    assert region.bbox[2] == pytest.approx(300.0)
