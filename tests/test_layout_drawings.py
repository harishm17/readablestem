"""Drawings that must end in an element or a kept page, and born-digital structure that
must add nothing (the drawings sweep of 2026-10-13, docs/layout.md "Figures" and "Small
drawings on a typed page"). Each case is rebuilt here small from the sweep's generator:
full-page plots with text inside their axes, plots and diagrams of straight lines with
labels inside, Fischer projections, bond lines, flat circuits, electron dots, hand marks
round an answer or over an equation, comb fields on an exam cover, a title slide's
decoration and icons that hold a glyph."""

from __future__ import annotations

import copy
import itertools
import math
import re
from html import unescape

import pymupdf
import pytest

from readablestem.config import Settings
from readablestem.core.types import Kind
from readablestem.models import OFFLINE_DEFAULTS
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import layout, page_type
from readablestem.pipeline.run import convert_pdf

LETTER = (612.0, 792.0)
SLIDE = (720.0, 405.0)
BLACK = (0.0, 0.0, 0.0)
BLUE = (0.1, 0.3, 0.8)
RED = (0.85, 0.05, 0.05)
GREY = (0.6, 0.6, 0.6)
PARAS = [
    "Displacement is the change in position of an object. It has a direction as well as a "
    "size, so it is a vector. Distance is how far the object travels along its path.",
    "Average velocity is displacement divided by the time it takes. Speed is the size of "
    "the velocity. A car that drives 30 m east in 3 s has an average velocity of 10 m/s.",
    "Acceleration is the rate of change of velocity. When the velocity of an object does "
    "not change, its acceleration is zero, even if it moves very fast.",
    "Current is the rate at which charge flows past a point. In a series circuit the same "
    "current flows through every component, and the voltages across them add up.",
]
NOTE = "The speed levels off at the terminal speed, when drag balances the weight of the ball."


# --------------------------------------------------------------------------- builders


def _page(doc: pymupdf.Document, size=LETTER) -> pymupdf.Page:
    return doc.new_page(width=size[0], height=size[1])


def _path(page, subpaths, *, colour=BLACK, width=1.0, fill=None, close=False, dashes=None):
    """One PDF path of `subpaths` (each a list of points joined by lines)."""
    shape = page.new_shape()
    for points in subpaths:
        for a, b in itertools.pairwise(points):
            shape.draw_line(a, b)
    shape.finish(color=colour, width=width, fill=fill, closePath=close, dashes=dashes)
    shape.commit()


def _lines(page, segments, **kw) -> None:
    for segment in segments:
        _path(page, [segment], **kw)


def _rect(page, r, *, colour=BLACK, width=1.0, fill=None) -> None:
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(r))
    shape.finish(color=colour, width=width, fill=fill)
    shape.commit()


def _label(page, at, text: str, size=9.0, font="helv") -> None:
    page.insert_text(at, text, fontsize=size, fontname=font)


def _running_text(page, top: float, n=2, start=0) -> float:
    """`n` paragraphs of body text from `top`; the y under them."""
    y = top
    for i in range(n):
        rect = pymupdf.Rect(72, y, 540, y + 120)
        left = page.insert_textbox(rect, PARAS[(start + i) % len(PARAS)], fontsize=11)
        y += 120 - left + 8
    return y


def _curve(f, x0: float, x1: float, n=80) -> list[tuple[float, float]]:
    return [(x0 + (x1 - x0) * i / n, f(x0 + (x1 - x0) * i / n)) for i in range(n + 1)]


# --------------------------------------------------------------------------- helpers


def _figures(out: layout.Layout, page: int = 0) -> list:
    return [
        e
        for e in out.elements
        if e.page == page
        and e.kind is Kind.FIGURE
        and page_type.HANDWRITING_REGION not in e.reasons
    ]


def _regions(out: layout.Layout, page: int = 0) -> list:
    return [e for e in out.elements if e.page == page and page_type.HANDWRITING_REGION in e.reasons]


def _holds(element, box, slack=2.0) -> bool:
    """`element`'s box holds all of `box`."""
    x0, y0, x1, y1 = element.bbox
    return (
        x0 - slack <= box[0] and y0 - slack <= box[1] and box[2] <= x1 + slack
        and box[3] <= y1 + slack
    )  # fmt: skip


def _text_of(out: layout.Layout, kinds=(Kind.HEADING, Kind.PARAGRAPH, Kind.LIST_ITEM)) -> str:
    return " ".join(e.text or "" for e in out.elements if e.kind in kinds)


def _convert(pdf: bytes):
    client = FakeVisionClient(defaults=copy.deepcopy(OFFLINE_DEFAULTS))
    return convert_pdf(pdf, settings=Settings(_env_file=None), client=client).result  # type: ignore[call-arg]


def _html_text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


# --------------------------------------------------------------------------- full-page plots


# --------------------------------------------------------------------------- lines and boxes


def _arrow_head(tip, direction: float, size=6.0, spread=0.45):
    a1, a2 = direction + math.pi - spread, direction + math.pi + spread
    return (
        (tip[0] + size * math.cos(a1), tip[1] + size * math.sin(a1)),
        (tip[0] + size * math.cos(a2), tip[1] + size * math.sin(a2)),
    )


def _scatter(page, top: float) -> tuple[float, float, float, float]:
    """L axes as two lines, 24 filled 3 pt squares, a legend box with two entries."""
    x0, y0, x1, y1 = 120, top, 470, top + 200
    _path(page, [[(x0, y1), (x1, y1)]])
    _path(page, [[(x0, y1), (x0, y0)]])
    for i in range(24):
        x = x0 + 12 + i * (x1 - x0 - 24) / 23
        y = y1 - 15 - (i * 6.5) - 12 * math.sin(i * 1.7)
        _rect(page, (x - 1.5, y - 1.5, x + 1.5, y + 1.5), colour=BLUE, width=0, fill=BLUE)
    lx, ly = x0 + 30, y0 + 10
    _rect(page, (lx, ly, lx + 120, ly + 34), colour=GREY, width=0.5)
    _rect(page, (lx + 6, ly + 8, lx + 9, ly + 11), colour=BLUE, width=0, fill=BLUE)
    _rect(page, (lx + 6, ly + 22, lx + 9, ly + 25), colour=RED, width=0, fill=RED)
    _label(page, (lx + 16, ly + 13), "measured speed", 8)
    _label(page, (lx + 16, ly + 27), "predicted speed", 8)
    return x0, y0, x1, y1


def _outline_bars(page, top: float) -> tuple[float, float, float, float]:
    """Five unfilled bars on a baseline, a value over each."""
    x0, base = 150, top + 190
    for i, h in enumerate([90, 150, 60, 170, 120]):
        x = x0 + i * 60
        _rect(page, (x, base - h, x + 36, base), width=1.2)
        _label(page, (x + 12, base - h - 4), str(h // 10))
    _path(page, [[(x0 - 10, base), (x0 + 300, base)]])
    return x0 - 10, base - 170, x0 + 300, base


def _circuit(page, top: float) -> tuple[float, float, float, float]:
    """A series circuit: the wires one path with gaps, a zigzag resistor, a battery of two
    plates, labels inside the loop."""
    x0, y0, x1, y1 = 180, top, 430, top + 160
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    wires = [[(x0, ym - 8), (x0, y0), (xm - 30, y0)],
             [(xm + 30, y0), (x1, y0), (x1, y1), (x0, y1), (x0, ym + 8)]]  # fmt: skip
    _path(page, wires, width=1.2)
    zigzag = [
        (xm - 30 + 5 * i, y0 + (0 if i in (0, 12) else (6 if i % 2 else -6))) for i in range(13)
    ]
    _path(page, [zigzag], width=1.2)
    _path(page, [[(x0 - 14, ym - 8), (x0 + 14, ym - 8)]], width=1.2)
    _path(page, [[(x0 - 7, ym + 8), (x0 + 7, ym + 8)]], width=3)
    _label(page, (xm - 30, y0 + 30), "R = 10 ohm", 10)
    _label(page, (x0 + 12, ym + 4), "12 V", 10)
    _label(page, (xm - 20, y1 - 12), "I = 1.2 A", 10)
    return x0 - 14, y0 - 6, x1, y1


def _flowchart(page, top: float, *, line_by_line: bool) -> tuple[float, float, float, float]:
    """Three boxes stacked with a step in each: filled boxes joined by arrows with open
    heads, or boxes drawn as four separate lines joined by plain lines."""
    x = 196
    steps = ["Measure the length", "Time ten swings", "Work out the period"]
    for k, step in enumerate(steps):
        y = top + k * 80
        if line_by_line:
            corners = [(x, y), (x + 220, y), (x + 220, y + 40), (x, y + 40), (x, y)]
            _lines(page, list(itertools.pairwise(corners)))
            if k < 2:
                _path(page, [[(x + 110, y + 40), (x + 110, y + 80)]])
        else:
            _rect(page, (x, y, x + 220, y + 40), colour=BLUE, fill=(0.85, 0.92, 1.0))
            if k < 2:
                tip = (x + 110, y + 79)
                h1, h2 = _arrow_head(tip, math.pi / 2)
                _path(page, [[(x + 110, y + 41), tip], [h1, tip, h2]], width=1.2)
        _label(page, (x + 20, y + 25), step, 11)
    return x, top, x + 220, top + 200


def _timing(page, top: float) -> tuple[float, float, float, float]:
    """Three digital signals, each one path, two dashed markers, labels inside."""
    x0 = 150
    for k, name in enumerate(["CLK", "DATA", "EN"]):
        lo = top + 60 + k * 50
        hi, level, points = lo - 20, lo, [(x0, lo)]
        for i in range(1, 10):
            x = x0 + i * 35 * (k + 1) // (2 if k == 2 else 1) + (k * 7 if k else 0)
            if x > x0 + 340:
                break
            points.append((x, level))
            level = hi if level == lo else lo
            points.append((x, level))
        points.append((x0 + 350, level))
        _path(page, [points], width=1.2)
        _label(page, (x0 - 40, lo - 6), name)
    for x in (x0 + 70, x0 + 140):
        _path(page, [[(x, top + 30), (x, top + 170)]], colour=GREY, dashes="[2 2] 0")
    _label(page, (x0 + 80, top + 85), "t setup", 8)
    _label(page, (x0 + 150, top + 135), "t hold", 8)
    return x0, top + 30, x0 + 350, top + 170


def _box_plot(page, top: float) -> tuple[float, float, float, float]:
    """One box plot: a box, a median, whiskers with caps, labels inside the box."""
    ym = top + 40
    _rect(page, (220, ym - 22, 400, ym + 22), width=1.2)
    _path(page, [[(300, ym - 22), (300, ym + 22)]], width=2)
    _path(page, [[(140, ym), (220, ym)]])
    _path(page, [[(400, ym), (500, ym)]])
    _path(page, [[(140, ym - 10), (140, ym + 10)]])
    _path(page, [[(500, ym - 10), (500, ym + 10)]])
    _label(page, (226, ym - 8), "Q1 = 31", 8)
    _label(page, (340, ym + 14), "Q3 = 55", 8)
    return 140, ym - 22, 500, ym + 22


DIAGRAMS = {
    "scatter plot with a legend": _scatter,
    "outlined bars on a baseline": _outline_bars,
    "circuit with labels inside its loop": _circuit,
    "flowchart of filled boxes and open arrows": lambda page, top: _flowchart(
        page, top, line_by_line=False
    ),
    "flowchart of boxes drawn line by line": lambda page, top: _flowchart(
        page, top, line_by_line=True
    ),
    "timing diagram": _timing,
    "box plot": _box_plot,
}


@pytest.mark.parametrize("case", list(DIAGRAMS))
def test_a_diagram_of_lines_and_boxes_with_labels_inside_is_a_figure(case):
    # Straight lines, boxes and marks with two or more text rows inside their box were
    # dropped as "a box round text" and the job page said "Everything was converted".
    doc = pymupdf.open()
    page = _page(doc)
    y = _running_text(page, 72, 2)
    area = DIAGRAMS[case](page, y + 30)
    _running_text(page, area[3] + 40, 1, start=2)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}
    (figure,) = _figures(out)
    assert _holds(figure, area)
    assert "It has a direction" in _text_of(out) and "Acceleration is" in _text_of(out)


def test_a_worked_example_box_with_a_grid_of_cell_borders_is_still_not_a_figure():
    # Controls: a worked example's frames round paragraphs, with a grid of cells drawn as
    # separate border pieces inside (an open chain of rules); and a box of four lines whose
    # corners do not quite meet round a shaded panel (its bottom-left corner looks like an
    # L of axes). Structure round text: no figure, no decision.
    doc = pymupdf.open()
    page = _page(doc)
    _rect(page, (51, 51, 561, 420), colour=GREY)
    _rect(page, (60, 96, 552, 410), colour=GREY)
    _label(page, (60, 80), "Example 4.7", 14, font="hebo")
    _running_text(page, 104, 1)
    for col in range(7):
        x = 77 + 37 * col
        for row in range(6):
            _path(page, [[(x, 165 + 20 * row), (x, 185 + 20 * row)]], width=0.7)
        if col < 6:
            _path(page, [[(x, 165), (x + 37, 165)]], width=0.7)
            _path(page, [[(x, 285), (x + 37, 285)]], width=0.7)
            for row in range(6):
                _label(page, (x + 6, 179 + 20 * row), f"({col + 1}, {row + 1})", 9)
    _running_text(page, 300, 2, start=1)
    page = _page(doc)
    _rect(page, (56.7, 64.5, 555.4, 335.3), colour=None, width=0, fill=(0.93, 0.95, 1.0))
    _path(page, [[(57, 63), (57, 336)]])
    _path(page, [[(555, 63), (555, 336)]])
    _path(page, [[(56, 65), (557, 65)]])
    _path(page, [[(56, 335), (557, 335)]])
    _running_text(page, 80, 3)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}
    assert _figures(out, 0) == [] and _figures(out, 1) == []


# --------------------------------------------------------------------------- formulas, circuits


def _fischer(page, cx: float, top: float) -> tuple[float, float, float, float]:
    """A Fischer projection: a vertical line crossed by two horizontal ones, each a path,
    with the groups at their ends."""
    _path(page, [[(cx, top), (cx, top + 100)]], width=1.2)
    _label(page, (cx - 12, top - 6), "CHO", 10)
    for k in range(2):
        y = top + 30 + 40 * k
        _path(page, [[(cx - 30, y), (cx + 30, y)]], width=1.2)
        _label(page, (cx - 44, y + 4), "H", 10)
        _label(page, (cx + 34, y + 4), "OH", 10)
    _label(page, (cx - 22, top + 114), "CH2OH", 10)
    return cx - 30, top, cx + 30, top + 100


def _ethane(page, ym: float) -> tuple[float, float, float, float]:
    """Ethane: atom letters joined by short horizontal and vertical bond lines."""
    for x, atom in zip([200, 240, 280, 320], ["H", "C", "C", "H"], strict=True):
        _label(page, (x - 3.5, ym + 4), atom, 12)
    bonds = [[(206, ym), (232, ym)], [(246, ym), (272, ym)], [(286, ym), (312, ym)]]
    for x in (240, 280):
        _label(page, (x - 3.5, ym - 26), "H", 12)
        _label(page, (x - 3.5, ym + 34), "H", 12)
        bonds += [[(x, ym - 22), (x, ym - 8)], [(x, ym + 8), (x, ym + 22)]]
    _lines(page, bonds, width=1.2)
    return 206, ym - 22, 312, ym + 22


def _flat_circuit(page, y: float) -> tuple[float, float, float, float]:
    """A one-line circuit under 24 pt tall: wires, a battery's two plates, two resistor
    boxes; straight lines and boxes only."""
    _lines(page, [[(150, y), (200, y)], [(206, y), (240, y)], [(290, y), (330, y)],
                  [(380, y), (440, y)]], width=1.2)  # fmt: skip
    _path(page, [[(200, y - 9), (200, y + 9)]], width=1.2)
    _path(page, [[(206, y - 5), (206, y + 5)]], width=3)
    _rect(page, (240, y - 6, 290, y + 6), width=1.2)
    _rect(page, (330, y - 6, 380, y + 6), width=1.2)
    return 150, y - 9, 440, y + 9


def _lewis_water(page, ym: float) -> tuple[float, float, float, float]:
    """H O H with the oxygen's eight electrons as dots round it."""
    for x, atom in ((250, "H"), (290, "O"), (330, "H")):
        _label(page, (x, ym + 5), atom, 14)
    for dx, dy in [(-2, -14), (6, -14), (-2, 13), (6, 13), (-12, -2), (-12, 4), (18, -2),
                   (18, 4)]:  # fmt: skip
        shape = page.new_shape()
        shape.draw_circle((294 + dx, ym + dy - 4), 1.4)
        shape.finish(color=BLACK, fill=BLACK, width=0)
        shape.commit()
    return 281, ym - 19, 313, ym + 10


FORMULAS = {
    "Fischer projections": lambda page, y: _fischer(page, 220, y + 40),
    "ethane's bond lines": lambda page, y: _ethane(page, y + 70),
    "a one-line circuit of boxes and wires": lambda page, y: _flat_circuit(page, y + 20),
    "a Lewis structure's electron dots": lambda page, y: _lewis_water(page, y + 50),
}


@pytest.mark.parametrize("case", list(FORMULAS))
def test_straight_or_dotted_drawings_between_paragraphs_are_kept(case):
    # Rules that cross with groups at their ends, short bonds from one letter to another,
    # boxes joined by wires, dots round a letter: dropped as rules, boxes or dots alone
    # (or held by the row of letters), with "Everything was converted".
    doc = pymupdf.open()
    page = _page(doc)
    y = _running_text(page, 72, 1, start=3)
    area = FORMULAS[case](page, y)
    _running_text(page, area[3] + 40, 1, start=1)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}
    (figure,) = _figures(out)
    assert _holds(figure, area)
    if case == "Fischer projections":
        assert {"CHO", "OH", "H", "CH2OH"} <= set((figure.text or "").split())


def test_blanks_to_fill_in_and_dot_leaders_are_still_not_drawings():
    # Controls: a blank on the baseline between two words, a fraction bar with its
    # numerator and denominator, dots on the baseline between a title and its page
    # number: part of the text, no figure.
    doc = pymupdf.open()
    page = _page(doc)
    _running_text(page, 72, 1)
    _label(page, (72, 160), "The speed of the cart is", 11)
    _path(page, [[(200, 161), (260, 161)]], width=0.6)
    _label(page, (264, 160), "m/s.", 11)
    _label(page, (72, 200), "Kinematics", 11)
    for i in range(30):
        shape = page.new_shape()
        shape.draw_circle((140 + 8 * i, 199), 0.6)
        shape.finish(color=BLACK, fill=BLACK, width=0)
        shape.commit()
    _label(page, (384, 200), "12", 11)
    _label(page, (72, 240), "so the time is", 11)
    _label(page, (152, 234), "12", 9)
    _path(page, [[(150, 237), (164, 237)]], width=0.5)
    _label(page, (153, 245), "4", 9)
    _label(page, (168, 240), "s in all.", 11)
    _running_text(page, 280, 1, start=1)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}
    assert _figures(out) == [] and _regions(out) == []


# --------------------------------------------------------------------------- hand marks


# --------------------------------------------------------------------------- answer fields


@pytest.mark.parametrize("labelled", [False, True])
def test_comb_fields_on_an_exam_cover_are_no_drawing_but_a_labelled_scale_is(labelled):
    # A line with ticks rising from it a letter cell apart is an answer field to write a
    # candidate number or a name in (two figures and two decisions before); with numbers
    # under its ticks it is a scale, a drawing.
    doc = pymupdf.open()
    page = _page(doc)
    _label(page, (72, 80), "Physics 101 Midterm Examination", 18, font="hebo")
    _label(page, (72, 130), "Candidate number", 11)
    _lines(
        page,
        [[(200, 134), (360, 134)]] + [[(200 + 20 * k, 134), (200 + 20 * k, 122)] for k in range(9)],
    )
    _label(page, (72, 170), "Family name", 11)
    _lines(
        page,
        [[(200, 174), (500, 174)]]
        + [[(200 + 20 * k, 174), (200 + 20 * k, 162)] for k in range(16)],
    )
    if labelled:
        for k in range(9):
            _label(page, (197 + 20 * k, 145), str(k), 8)
    _running_text(page, 220, 2)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}
    if labelled:
        assert any(_holds(figure, (200, 122, 360, 134)) for figure in _figures(out))
    else:
        assert _figures(out) == [] and _regions(out) == []
        assert "Candidate number" in _text_of(out)


def test_a_full_page_plot_with_axes_drawn_as_dashes_keeps_its_axes():
    # Axes drawn as separate 4 pt dashes along the left and bottom edges, one curve and a
    # title: the dashes were set aside as a frame's pieces and the curve made a figure of
    # its own, so the axes were lost (the page image kept them before the curve across
    # the middle of the page stopped being a border piece).
    doc = pymupdf.open()
    page = _page(doc)
    _lines(page, [[(x, 770), (x + 4, 770)] for x in range(30, 588, 7)])
    _lines(page, [[(30, y), (30, y + 4)] for y in range(22, 766, 7)])
    _path(page, [_curve(lambda x: 760 - 0.0021 * (x - 30) ** 2, 30, 580)], colour=BLUE, width=1.5)
    _label(page, (200, 60), "Projectile height", 20, font="hebo")
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}
    (figure,) = _figures(out)
    assert _holds(figure, (30, 22, 587, 770))
    assert "Projectile height" in _text_of(out)
