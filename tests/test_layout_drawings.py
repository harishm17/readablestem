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
