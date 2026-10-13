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

from a11y_stem.config import Settings
from a11y_stem.core.types import Kind
from a11y_stem.models import OFFLINE_DEFAULTS
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline import layout, page_type
from a11y_stem.pipeline.run import convert_pdf
from a11y_stem.web.worker import result_outcome

LETTER = (612.0, 792.0)
SLIDE = (720.0, 405.0)
BLACK = (0.0, 0.0, 0.0)
BLUE = (0.1, 0.3, 0.8)
RED = (0.85, 0.05, 0.05)
GREEN = (0.1, 0.55, 0.2)
GREY = (0.6, 0.6, 0.6)
ORANGE = (0.95, 0.55, 0.1)
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


def _slide_plot(page, *, title: bool, note: bool, x1=690.0, foot=385.0) -> None:
    """A slide's L axes as one path filling the slide and one curve rising from the
    origin, with a title and a two-line note inside the axes."""
    _path(page, [[(30, 20), (30, foot), (700, foot)]])
    rise = _curve(lambda x: foot - 300 * (1 - math.exp(-(x - 30) / 150)), 30, x1)
    _path(page, [rise], colour=RED, width=2)
    if title:
        _label(page, (200, 60), "Speed of a falling ball", 22, font="hebo")
    if note:
        page.insert_textbox(pymupdf.Rect(380, 250, 680, 300), NOTE, fontsize=12)


def _letter_plot(page, *, curves: int, frame: bool) -> None:
    """A letter page filled by a plot: L axes as two lines, curves from the origin."""
    if frame:
        _rect(page, (18, 18, 594, 774), colour=GREY, width=1.5)
    x0, y0, x1, y1 = 50, 40, 575, 745
    _path(page, [[(x0, y0), (x0, y1)]])
    _path(page, [[(x0, y1), (x1, y1)]])
    for k, colour in enumerate((BLUE, RED, GREEN)[:curves]):
        rise = _curve(lambda x, k=k: y1 - (200 + 120 * k) * (1 - math.exp(-(x - x0) / 120)), x0, x1)
        _path(page, [rise], colour=colour, width=1.5)


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


@pytest.mark.parametrize(
    "case", ["title and note", "note", "title, curve 75 % wide", "title, source line under"]
)
def test_a_slide_plot_with_text_inside_its_axes_is_a_figure(case):
    # L axes and one curve filling the slide, with a title or a note inside the axes: the
    # curve was set aside as a page-wide border piece, the axes alone made nothing, and the
    # job page said "Everything was converted" (the curve 75 % wide: kept as a page image).
    doc = pymupdf.open()
    page = _page(doc, SLIDE)
    if case == "title, source line under":
        _slide_plot(page, title=True, note=False, foot=375)
        _label(page, (30, 394), "Source: class data, autumn term, three trials averaged")
    else:
        _slide_plot(
            page,
            title=case != "note",
            note="note" in case,
            x1=560 if "75" in case else 690,
        )
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}
    (figure,) = _figures(out)
    assert _holds(figure, (30, 20, 700, 375))  # the axes and the curve
    text = _text_of(out)
    if case != "note":
        assert "Speed of a falling ball" in text  # read in the flow, not a label
    if "note" in case:
        assert "terminal speed" in text
    if "source" in case:
        assert "three trials averaged" in text


@pytest.mark.parametrize("case", ["three curves in a frame", "two curves and a legend"])
def test_a_full_page_plot_with_a_title_and_notes_inside_keeps_its_axes_and_curves(case):
    # A letter page filled by a plot with a title and notes (or a legend) inside the axes:
    # the curves (page-wide) were set aside as a border, and the axes alone made nothing.
    doc = pymupdf.open()
    page = _page(doc)
    if case == "three curves in a frame":
        _letter_plot(page, curves=3, frame=True)
        _label(page, (150, 90), "Three charging curves", 20, font="hebo")
        page.insert_textbox(
            pymupdf.Rect(300, 560, 560, 640),
            "The blue curve has the smallest capacitor. The green curve has the largest one "
            "and charges the slowest of the three.",
            fontsize=11,
        )
    else:
        _letter_plot(page, curves=2, frame=False)
        _label(page, (160, 70), "Charging two capacitors", 18, font="hebo")
        _label(page, (420, 600), "C = 2 mF", 10)
        _label(page, (420, 615), "C = 5 mF", 10)
        _label(page, (40, 765), "Figure 4. Voltage across each capacitor against time.", 10)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}
    (figure,) = _figures(out)
    assert _holds(figure, (50, 40, 575, 745))
    text = _text_of(out)
    if case == "three curves in a frame":
        # The note's short last line is a line of the note, not a label of the figure.
        assert "Three charging curves" in text and "slowest of the three." in text
    else:
        assert "Charging two capacitors" in text
        assert "C = 2 mF" in (figure.text or "")  # the legend is the figure's


@pytest.mark.parametrize("case", ["title only", "axis title, no text inside"])
def test_a_slide_plot_with_a_title_or_axis_title_stays_a_figure(case):
    # Controls: converted before; the title alone on the slide is its heading now, not the
    # figure's label (the page's only text sets the body size, so size could not say so).
    doc = pymupdf.open()
    page = _page(doc, SLIDE)
    _slide_plot(page, title=case == "title only", note=False)
    if case != "title only":
        _label(page, (640, 400), "time (s)")
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}
    (figure,) = _figures(out)
    assert _holds(figure, (30, 20, 700, 385))
    if case == "title only":
        (heading,) = [e for e in out.elements if e.kind is Kind.HEADING]
        assert heading.text == "Speed of a falling ball"
        assert "Speed" not in (figure.text or "")


def test_a_title_slides_decoration_does_not_swallow_its_title():
    # An L-shaped accent along the left and bottom edges and a wave across the slide are
    # a template's decoration, not a plot (no ticks, labels or axis title, and the wave
    # does not start at the corner): the title and subtitle were the labels of one figure
    # of the whole slide, and the HTML had no slide text. No page is kept either.
    doc = pymupdf.open()
    page = _page(doc, SLIDE)
    _path(page, [[(20, 15), (20, 390), (700, 390)]], colour=ORANGE, width=3)
    wave = _curve(lambda x: 340 + 25 * math.sin((x - 20) / 70), 22, 700)
    _path(page, [wave], colour=(0.95, 0.75, 0.5), width=4)
    _label(page, (80, 160), "Kinematics in one dimension", 30, font="hebo")
    _label(page, (80, 200), "Week 3, lecture 2", 16)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}
    text = _text_of(out)
    assert "Kinematics in one dimension" in text and "Week 3, lecture 2" in text
    assert all("Kinematics" not in (f.text or "") for f in _figures(out))
    assert all(f.bbox[3] - f.bbox[1] < 100 for f in _figures(out))  # not the whole slide


def test_a_slide_plot_with_a_note_is_never_called_converted():
    doc = pymupdf.open()
    _slide_plot(_page(doc, SLIDE), title=False, note=True)
    result = _convert(doc.tobytes())
    figures = [e for e in result.elements if e.kind is Kind.FIGURE]
    assert len(figures) == 1 and _holds(figures[0], (30, 20, 700, 385))
    assert "terminal speed" in _html_text(result.html)
    assert result_outcome(result)[0] == "done"


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


def test_a_scatter_plot_with_a_legend_is_never_called_converted():
    doc = pymupdf.open()
    page = _page(doc)
    y = _running_text(page, 72, 2)
    _scatter(page, y + 30)
    result = _convert(doc.tobytes())
    (figure,) = [e for e in result.elements if e.kind is Kind.FIGURE]
    assert "measured speed" in (figure.text or "")  # the legend is the figure's
    assert "<img" in result.html


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


def _choices(page, y: float) -> None:
    _label(page, (72, y), "3. A car covers 120 m in 10 s. What is its average speed?", 11)
    for x, option in zip(
        (90, 200, 310, 420), ("(a) 4 m/s", "(b) 8 m/s", "(c) 12 m/s", "(d) 16 m/s"), strict=True
    ):
        _label(page, (x, y + 24), option, 11)


def _oval(page, r, *, colour, width=1.5) -> None:
    shape = page.new_shape()
    shape.draw_oval(pymupdf.Rect(r))
    shape.finish(color=colour, width=width)
    shape.commit()


MARKS = ["answer circled in red", "tick over a display equation", "filled tick", "ribbon tick"]


@pytest.mark.parametrize("case", MARKS)
def test_a_hand_mark_round_an_answer_or_over_an_equation_is_cut_out(case):
    # A closed red ellipse round an answer, a tick over a display equation (held by the
    # equation, whose transcription drops it), a tick drawn as a filled outline: lost, with
    # "Everything was converted".
    doc = pymupdf.open()
    page = _page(doc)
    y = _running_text(page, 72, 2)
    if case == "tick over a display equation":
        page.insert_text(
            (220, y + 40), "F = m \u00d7 a = 2 \u00d7 9.8 = 19.6", fontsize=13, fontname="tiro"
        )
        _path(page, [[(300, y + 36), (307.7, y + 44.8), (322, y + 22.8)]], colour=RED, width=1.8)
        mark = (300, y + 22.8, 322, y + 44.8)
    else:
        _choices(page, y + 30)
        if case == "answer circled in red":
            _oval(page, (304, y + 40, 372, y + 60), colour=RED)
            mark = (304, y + 40, 372, y + 60)
        elif case == "filled tick":
            points = [(320, y + 50), (326, y + 58), (342, y + 34), (344, y + 36), (327, y + 62),
                      (318, y + 52)]  # fmt: skip
            _path(page, [points], colour=RED, width=0, fill=RED, close=True)
            mark = (318, y + 34, 344, y + 62)
        else:
            spine = [(318 + t * 8, y + 50 + t * 9) for t in [i / 10 for i in range(11)]]
            spine += [(326 + t * 18, y + 59 - t * 26) for t in [i / 10 for i in range(1, 11)]]
            ribbon = [(x - 1.2, yy) for x, yy in spine] + [
                (x + 1.2, yy) for x, yy in reversed(spine)
            ]
            _path(page, [ribbon], colour=RED, width=0, fill=RED, close=True)
            mark = (316.8, y + 33, 345.2, y + 59)
    _running_text(page, y + 90, 1, start=2)
    out = layout.analyze(doc.tobytes())
    assert out.page_types[0].kind == "born_digital"
    assert out.kept_pages == {}
    (region,) = _regions(out)
    assert _holds(region, mark)
    text = " ".join(e.text or "" for e in out.elements)
    assert "12 m/s" in text or "19.6" in text  # the typed text under the mark stays read


def test_marks_set_with_the_text_are_still_part_of_it():
    # Controls: a circle round a step number and a box round a word in the text's colour,
    # a red underline, and a yellow highlight with rounded ends behind a line (filled, in
    # another colour, taller than the line): set with the text, not hand marks.
    doc = pymupdf.open()
    page = _page(doc)
    y = _running_text(page, 72, 1)
    _label(page, (90, y + 30), "1", 11)
    _oval(page, (85, y + 18, 101, y + 34), colour=BLACK, width=0.8)
    _label(page, (110, y + 30), "Measure the length of the pendulum with a metre rule.", 11)
    _label(page, (72, y + 60), "The answer is the average speed, not the final one.", 11)
    _rect(page, (134, y + 50, 168, y + 63), width=0.6)
    _path(page, [[(250, y + 62), (330, y + 62)]], colour=RED, width=0.8)
    _label(page, (72, y + 90), "Write the units of every quantity you work out.", 11)
    page.add_highlight_annot(pymupdf.Rect(70, y + 77, 330, y + 97)).update()
    _running_text(page, y + 110, 1, start=2)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}
    assert _regions(out) == [] and _figures(out) == []


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
