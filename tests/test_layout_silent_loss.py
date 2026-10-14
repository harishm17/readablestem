"""Nothing painted on a page is dropped without a decision (the silent-loss sweep of
2026-10-12, docs/layout.md "The safety net"): every drawing, picture or hand mark ends up
in an element (a figure, a table, an equation, a handwriting region) or its page keeps
its image with a decision. Each case is rebuilt here from the sweep's generator, small.
Controls check that born-digital structure (boxes round text, rules, a running footer, a
plain background) still adds nothing."""

from __future__ import annotations

import copy
import itertools
import math
import random
import re
from html import unescape
from pathlib import Path

import pymupdf
import pytest

from a11y_stem.config import Settings
from a11y_stem.core.types import Kind
from a11y_stem.models import OFFLINE_DEFAULTS
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline import layout, page_type
from a11y_stem.pipeline.run import convert_pdf
from a11y_stem.web.worker import result_outcome
from fixtures.handwriting.make_fixtures import (
    INK_BLACK,
    INK_BLUE,
    STAMP,
    Writer,
    rename_fonts,
    scan_png,
    text_layer,
)

HANDWRITING = Path(__file__).parent / "fixtures" / "handwriting"
LETTER = (612.0, 792.0)
A4 = (595.28, 841.89)
SLIDE = (720.0, 405.0)
PARAS = [
    "Displacement is the change in position of an object. It has a direction as well as a "
    "size, so it is a vector. Distance is how far the object travels along its path.",
    "Average velocity is displacement divided by the time it takes. Speed is the size of "
    "the velocity. A car that drives 30 m east in 3 s has an average velocity of 10 m/s.",
    "Acceleration is the rate of change of velocity. When the velocity of an object does "
    "not change, its acceleration is zero, even if it moves very fast.",
    "Momentum is mass times velocity. In a closed system the total momentum stays the same "
    "before and after a collision, whatever happens to the kinetic energy.",
]


# --------------------------------------------------------------------------- builders


def _page(doc: pymupdf.Document, size=LETTER) -> pymupdf.Page:
    return doc.new_page(width=size[0], height=size[1])


def _title(page: pymupdf.Page, text: str, at=(72, 72), size=18.0, colour=(0, 0, 0)) -> None:
    page.insert_text(at, text, fontsize=size, fontname="hebo", color=colour)


def _paragraphs(page: pymupdf.Page, top=100.0, n=3, x1=540.0) -> float:
    y = top
    for i in range(n):
        rect = pymupdf.Rect(72, y, x1, y + 200)
        left = page.insert_textbox(rect, PARAS[i % len(PARAS)], fontsize=11)
        y += 200 - left + 12
    return y


def _hand(page, x, base, strokes, *, style="polyline", colour=INK_BLACK, em=7.0, width=1.0,
          seed=1):  # fmt: skip
    """`strokes` pen strokes (one-stroke letters) as handwriting."""
    writer = Writer(page, seed, style, colour, width=width)
    rng = random.Random(seed)
    left, cx = strokes, x
    while left > 0:
        n = min(left, rng.randint(2, 5))
        cx = writer.word("".join(rng.choice("aelnosd") for _ in range(n)), cx, base, em, 0.15)
        cx += em * 1.2
        left -= n
    writer.commit()
    return writer.word_boxes


def _line(shape, p, q, colour=(0, 0, 0), width=1.0) -> None:
    shape.draw_line(p, q)
    shape.finish(color=colour, width=width)


def _tick(page, x, y, size=12.0, colour=(0.8, 0.1, 0.1)) -> None:
    shape = page.new_shape()
    shape.draw_polyline([(x, y + size * 0.55), (x + size * 0.35, y + size), (x + size, y)])
    shape.finish(color=colour, width=1.4, lineCap=1, lineJoin=1, closePath=False)
    shape.commit()


def _photo(w: int, h: int, seed: int = 5) -> pymupdf.Pixmap:
    rng = random.Random(seed)
    data = bytearray(w * h * 3)
    blobs = [(rng.uniform(0, w), rng.uniform(0, h), rng.uniform(w / 8, w / 3),
              [rng.randint(30, 220) for _ in range(3)]) for _ in range(6)]  # fmt: skip
    for y in range(h):
        for x in range(w):
            r, g, b = 120 + 80 * y // h, 150, 200 - 100 * x // w
            for bx, by, br, col in blobs:
                if (x - bx) ** 2 + (y - by) ** 2 < br * br:
                    r, g, b = col
            i = 3 * (y * w + x)
            data[i : i + 3] = bytes((r, g, b))
    return pymupdf.Pixmap(pymupdf.csRGB, w, h, bytes(data), False)


def _outlined(page, at, text: str, size=11.0) -> pymupdf.Rect:
    """`text` drawn as glyph outlines with no text layer (a MathJax SVG print)."""
    src = pymupdf.open()
    sp = src.new_page(width=400, height=60)
    sp.insert_text((5, 40), text, fontsize=size, fontname="tiit")
    width = pymupdf.get_text_length(text, fontname="tiit", fontsize=size)
    svg = pymupdf.open(stream=sp.get_svg_image(text_as_path=True).encode(), filetype="svg")
    pdf = pymupdf.open("pdf", svg.convert_to_pdf())
    clip = pymupdf.Rect(3, 40 - size, 7 + width, 40 + 0.35 * size)
    target = pymupdf.Rect(at[0], at[1] - size, at[0] + clip.width, at[1] - size + clip.height)
    page.show_pdf_page(target, pdf, 0, clip=clip)
    return target


def _boxes_chart(page, *, one_arrow: bool) -> pymupdf.Rect:
    """Boxes with labels joined by plain lines (no arrowheads): an org chart, or two
    boxes and one line."""
    shape = page.new_shape()
    labels = []
    if one_arrow:
        a, b = pymupdf.Rect(100, 165, 240, 215), pymupdf.Rect(360, 165, 500, 215)
        for box in (a, b):
            shape.draw_rect(box)
            shape.finish(color=(0, 0, 0), width=1.0)
        _line(shape, (240, 190), (360, 190))
        labels = [(a, "Measure the input"), (b, "Record the output")]
        area = pymupdf.Rect(100, 165, 500, 215)
    else:
        bw, root = 104, pymupdf.Rect(248, 165, 352, 195)
        kids = [pymupdf.Rect(100 + i * 148, 265, 204 + i * 148, 295) for i in range(3)]
        for box in [root, *kids]:
            shape.draw_rect(box)
            shape.finish(color=(0, 0, 0), width=1.0)
        _line(shape, (300, 195), (300, 230))
        _line(shape, (152, 230), (448, 230))
        for k in kids:
            _line(shape, (k.x0 + bw / 2, 230), (k.x0 + bw / 2, 265))
        labels = [(root, "Mechanics"), (kids[0], "Kinematics"), (kids[1], "Dynamics"),
                  (kids[2], "Statics")]  # fmt: skip
        area = pymupdf.Rect(100, 165, 500, 295)
    shape.commit()
    for box, text in labels:
        inner = pymupdf.Rect(box.x0 + 4, box.y0 + 8, box.x1 - 4, box.y1)
        page.insert_textbox(inner, text, fontsize=10, align=1)
    return area


def _step_plot(page, r: pymupdf.Rect) -> None:
    """Axes and a staircase, every piece its own horizontal or vertical line."""
    shape = page.new_shape()
    lines = [((r.x0, r.y1), (r.x1, r.y1)), ((r.x0, r.y1), (r.x0, r.y0))]
    y = r.y1 - 5
    for i in range(6):
        x0, x1 = r.x0 + i * r.width / 6, r.x0 + (i + 1) * r.width / 6
        y_next = r.y1 - (i + 1) * (r.height - 10) / 6
        lines += [((x0, y), (x1, y)), ((x1, y), (x1, y_next))]
        y = y_next
    for p, q in lines:
        _line(shape, p, q, colour=(0, 0, 0.6))
    shape.commit()


def _chart(page, r: pymupdf.Rect) -> None:
    shape = page.new_shape()
    _line(shape, (r.x0, r.y1), (r.x1, r.y1))
    _line(shape, (r.x0, r.y0), (r.x0, r.y1))
    pts = [
        (r.x0 + t * r.width / 60, r.y1 - 10 - (r.height - 30) * (t / 60) ** 2) for t in range(61)
    ]
    shape.draw_polyline(pts)
    shape.finish(color=(0.1, 0.3, 0.8), width=1.2, closePath=False)
    shape.commit()


# --------------------------------------------------------------------------- helpers


def _held(out: layout.Layout, page: int, box, kinds=(Kind.FIGURE,)) -> bool:
    """The middle of `box` is inside an element of one of `kinds` on `page`, or the page
    keeps its image."""
    if page in out.kept_pages:
        return True
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return any(
        e.page == page and e.kind in kinds and e.bbox[0] - 2 <= cx <= e.bbox[2] + 2
        and e.bbox[1] - 2 <= cy <= e.bbox[3] + 2
        for e in out.elements
    )  # fmt: skip


def _regions(out: layout.Layout, page: int = 0) -> list:
    return [e for e in out.elements if e.page == page and page_type.HANDWRITING_REGION in e.reasons]


def _figures(out: layout.Layout, page: int = 0) -> list:
    return [
        e
        for e in out.elements
        if e.page == page
        and e.kind is Kind.FIGURE
        and page_type.HANDWRITING_REGION not in e.reasons
    ]


def _text(out: layout.Layout) -> str:
    return " ".join(e.text or "" for e in out.elements)


def _convert(pdf: bytes):
    client = FakeVisionClient(defaults=copy.deepcopy(OFFLINE_DEFAULTS))
    return convert_pdf(pdf, settings=Settings(_env_file=None), client=client).result  # type: ignore[call-arg]


def _html_text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


# --------------------------------------------------------------------------- diagrams


@pytest.mark.parametrize("one_arrow", [False, True])
@pytest.mark.parametrize("framed", [False, True])
def test_boxes_joined_by_plain_lines_are_a_figure_not_boxes_round_text(one_arrow, framed):
    # An org chart with orthogonal connectors, and two boxes joined by one line: the box
    # labels had become loose paragraphs and the drawing nothing.
    doc = pymupdf.open()
    page = _page(doc)
    if framed:
        shape = page.new_shape()
        shape.draw_rect(pymupdf.Rect(30, 30, 582, 762))
        shape.finish(color=(0.2, 0.2, 0.5), width=2)
        shape.commit()
    _title(page, "Branches of mechanics")
    page.insert_textbox(pymupdf.Rect(72, 104, 540, 160), PARAS[0], fontsize=11)
    area = _boxes_chart(page, one_arrow=one_arrow)
    page.insert_textbox(pymupdf.Rect(72, 320, 540, 400), PARAS[1], fontsize=11)
    out = layout.analyze(doc.tobytes())
    (figure,) = _figures(out)
    assert figure.bbox[0] <= area.x0 + 1 and figure.bbox[2] >= area.x1 - 1
    assert figure.bbox[1] <= area.y0 + 1 and figure.bbox[3] >= area.y1 - 1
    labels = ["Measure the input"] if one_arrow else ["Mechanics", "Statics"]
    assert all(label in (figure.text or "") for label in labels)  # the labels go with it
    assert out.kept_pages == {}


def test_a_box_or_panel_round_text_is_still_not_a_figure():
    # Controls: one framed box and a shaded panel round two paragraphs, two boxes side by
    # side with nothing joining them, and a line under a heading are structure, not
    # drawings: no figure, no decision.
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Worked example")
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(66, 96, 546, 250))
    shape.finish(color=(0, 0, 0), width=1.0)
    shape.draw_rect(pymupdf.Rect(66, 270, 546, 420))
    shape.finish(color=None, fill=(0.9, 0.95, 1.0))
    _line(shape, (72, 80), (540, 80))
    shape.draw_rect(pymupdf.Rect(66, 440, 300, 560))
    shape.finish(color=(0, 0, 0), width=1.0)
    shape.draw_rect(pymupdf.Rect(312, 440, 546, 560))
    shape.finish(color=(0, 0, 0), width=1.0)
    shape.commit()
    _paragraphs(page, top=104, n=2)
    _paragraphs(page, top=278, n=2)
    page.insert_textbox(pymupdf.Rect(72, 446, 294, 556), PARAS[2], fontsize=11)
    page.insert_textbox(pymupdf.Rect(318, 446, 540, 556), PARAS[3], fontsize=11)
    out = layout.analyze(doc.tobytes())
    assert _figures(out) == [] and _regions(out) == [] and out.kept_pages == {}


@pytest.mark.parametrize("little_text", [False, True])
def test_a_step_plot_drawn_in_separate_lines_is_a_figure(little_text):
    doc = pymupdf.open()
    page = _page(doc)
    if little_text:
        _title(page, "Energy levels")
    else:
        _title(page, "Quantised energy levels")
        _paragraphs(page, n=2)
    _step_plot(page, pymupdf.Rect(150, 211, 400, 371))
    out = layout.analyze(doc.tobytes())
    assert _held(out, 0, (150, 211, 400, 371))
    assert _figures(out) or out.kept_pages == {0: "unread_page"}


def test_rules_that_do_not_meet_end_to_end_are_still_not_a_drawing():
    # Controls: underlines, a rule under a heading, and a box drawn as four separate lines
    # round two paragraphs: no figure.
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Notes")
    shape = page.new_shape()
    for y in (300, 330, 360):
        _line(shape, (72, y), (400, y))
    _line(shape, (72, 80), (540, 80))
    for p, q in (((66, 96), (546, 96)), ((546, 96), (546, 260)), ((546, 260), (66, 260)),
                 ((66, 260), (66, 96))):  # fmt: skip
        _line(shape, p, q)
    shape.commit()
    _paragraphs(page, top=104, n=2)
    out = layout.analyze(doc.tobytes())
    assert _figures(out) == [] and out.kept_pages == {}


@pytest.mark.parametrize(
    ("text", "size", "fraction"),
    [("ω = dθ/dt = 2.4 rad/s", 11.0, False), ("E = m g h + m v2 / 2", 20.0, False),
     ("m g h", 16.0, True)],
)  # fmt: skip
def test_maths_drawn_as_outlines_between_paragraphs_is_never_dropped(text, size, fraction):
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Energy conservation")
    page.insert_textbox(pymupdf.Rect(72, 104, 540, 160), PARAS[0], fontsize=11)
    box = _outlined(page, (182, 176), text, size)
    if fraction:  # a bar and a denominator 8 pt under it
        shape = page.new_shape()
        _line(shape, (box.x0 - 4, box.y1 + 4), (box.x1 + 4, box.y1 + 4))
        shape.commit()
        box |= _outlined(page, (box.x0 + 12, box.y1 + 20), "2", size)
    page.insert_textbox(pymupdf.Rect(72, box.y1 + 30, 540, box.y1 + 90), PARAS[1], fontsize=11)
    out = layout.analyze(doc.tobytes())
    # A figure for the model, or, when most of its glyphs pass the pen-stroke tests (a
    # short fraction), a region kept as an image with a decision: never dropped.
    (element,) = [*_figures(out), *_regions(out)]
    assert _held(out, 0, box)
    assert element.bbox[0] <= box.x0 + 4 and element.bbox[2] >= box.x1 - 4
    assert out.kept_pages == {}


@pytest.mark.parametrize("what", ["warning", "benzene", "number line", "large ticks"])
def test_small_drawings_on_a_typed_page_are_never_dropped(what):
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Lab safety")
    y = _paragraphs(page, n=2)
    shape = page.new_shape()
    if what == "warning":
        x, s = 72, 16
        shape.draw_polyline([(x + s / 2, y), (x + s, y + s), (x, y + s)])
        shape.finish(color=(0.9, 0.6, 0), fill=(0.95, 0.75, 0.1), closePath=True)
        shape.draw_rect(pymupdf.Rect(x + s / 2 - 1, y + 5, x + s / 2 + 1, y + 11))
        shape.finish(fill=(0, 0, 0), color=None)
        box = (x, y, x + s, y + s)
    elif what == "benzene":
        cx, cy, r = 300, y + 12, 10
        pts = [(cx + r * math.cos(math.pi / 6 + k * math.pi / 3),
                cy + r * math.sin(math.pi / 6 + k * math.pi / 3)) for k in range(7)]  # fmt: skip
        shape.draw_polyline(pts)
        shape.finish(color=(0, 0, 0), width=0.8, closePath=True)
        shape.draw_circle((cx, cy), r * 0.55)
        shape.finish(color=(0, 0, 0), width=0.8)
        box = (cx - r, cy - r, cx + r, cy + r)
    elif what == "number line":
        x0, x1, ly = 100, 480, y + 10
        _line(shape, (x0, ly), (x1, ly))
        for i in range(11):
            _line(shape, (x0 + 10 + i * 36, ly - 3), (x0 + 10 + i * 36, ly + 3), width=0.8)
        for x, d in ((x1, 1), (x0, -1)):
            shape.draw_polyline([(x - 6 * d, ly - 3), (x, ly), (x - 6 * d, ly + 3)])
            shape.finish(color=(0, 0, 0), fill=(0, 0, 0), closePath=True)
        box = (x0, ly - 3, x1, ly + 3)
    else:
        shape.commit()
        _tick(page, 548, 100, size=32)
        _tick(page, 548, 150, size=32)
        box = (548, 100, 580, 132)
    shape.commit()
    _paragraphs(page, top=y + 40, n=1)
    out = layout.analyze(doc.tobytes())
    assert _held(out, 0, box)
    assert out.kept_pages == {}  # the page is read; the drawing is an element of its own


# --------------------------------------------------------------------------- hand marks


@pytest.mark.parametrize(
    "marks",
    ["tick", "ticks and cross", "straight strokes", "mixed pen widths", "one stroke",
     "two strokes over text", "signature"],
)  # fmt: skip
def test_hand_marks_on_a_typed_page_are_cut_out_as_handwriting(marks):
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Quiz 1, question 2")
    end = _paragraphs(page, n=3)
    boxes: list = []
    if marks == "tick":
        _tick(page, 548, 105)
        boxes = [(548, 105, 560, 117)]
    elif marks == "ticks and cross":
        _tick(page, 548, 105)
        _tick(page, 548, 150)
        shape = page.new_shape()
        _line(shape, (548, 200), (556, 210), colour=(0.8, 0.1, 0.1), width=1.2)
        _line(shape, (556, 200), (549, 209), colour=(0.8, 0.1, 0.1), width=1.2)
        shape.commit()
        boxes = [(548, 105, 560, 117), (548, 150, 560, 162), (548, 200, 556, 210)]
    elif marks == "straight strokes":
        rng = random.Random(3)
        shape = page.new_shape()
        for _ in range(5):
            p = (rng.uniform(546, 566), rng.uniform(120, 140))
            q = (rng.uniform(546, 566), rng.uniform(120, 140))
            _line(shape, p, q, colour=INK_BLACK, width=1.2)
        shape.commit()
        boxes = [(546, 120, 566, 140)]
    elif marks == "mixed pen widths":
        a = _hand(page, 90, end + 30, 3, style="ribbon", colour=INK_BLUE, width=0.5, seed=31)
        b = _hand(page, 200, end + 30, 3, style="ribbon", colour=INK_BLUE, width=2.6, seed=32)
        boxes = [a[0], b[0]]
    elif marks == "one stroke":
        boxes = _hand(page, 20, 300, 1, seed=1)
    elif marks == "two strokes over text":
        boxes = _hand(page, 200, 118, 2, seed=2)
    else:  # one stroke, too long for the stroke tests, under the text
        shape = page.new_shape()
        y = end + 20
        pts = [(120 + 1.5 * i, y + 6 * math.sin(i / 4) - 3 * math.sin(i / 9)) for i in range(100)]
        shape.draw_polyline(pts)
        shape.finish(color=INK_BLUE, width=1.2, lineCap=1, closePath=False)
        shape.commit()
        boxes = [(120, y - 9, 268, y + 9)]
    out = layout.analyze(doc.tobytes())
    assert out.page_types[0].kind == "born_digital"
    assert out.kept_pages == {}
    regions = _regions(out)
    assert regions, "the marks became no handwriting region"
    for box in boxes:
        assert _held(out, 0, box, kinds=(Kind.FIGURE,))
    assert "It has a direction" in _text(out)  # the typed text is read whole


def test_handwriting_over_a_display_equation_is_cut_out():
    # The strokes lie over the equation's box: before, the equation "held" them, and a
    # model that transcribed the equation would have dropped the note with no decision.
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Kinematic equations")
    y = _paragraphs(page, n=1)
    page.insert_text((220, y + 30), "v = u + a t", fontsize=14, fontname="tiit")
    _paragraphs(page, top=y + 60, n=1)
    boxes = _hand(page, 230, y + 30, 5, em=7, seed=51)
    out = layout.analyze(doc.tobytes())
    assert out.page_types[0].kind == "born_digital"
    (region,) = _regions(out)
    assert all(_held(out, 0, b) for b in boxes)
    assert region.bbox[1] <= y + 30 <= region.bbox[3]


# --------------------------------------------------------------------------- band, dark


@pytest.mark.parametrize("band", ["top", "bottom"])
def test_a_note_in_the_top_or_bottom_band_under_a_title_keeps_its_page(band):
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Quiz 3", at=(72, 120))
    _hand(page, 400 if band == "top" else 300, 35 if band == "top" else 780, 4, seed=81)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {0: "unread_page"}


def test_a_pen_mark_in_the_footer_of_an_otherwise_blank_page_keeps_it():
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Chapter 2 summary")
    _paragraphs(page, n=2)
    _hand(_page(doc), 300, 778, 4, em=3, seed=88)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {1: "unread_page"}


def test_a_logo_repeated_in_the_footer_of_title_slides_keeps_no_page():
    # Control: a small vector logo in the footer band of every slide, slides with a short
    # title only. It repeats, so it is the template's, not a note.
    doc = pymupdf.open()
    for n in range(4):
        page = _page(doc, SLIDE)
        _title(page, f"Part {n + 1}", at=(40, 200), size=28)
        shape = page.new_shape()
        shape.draw_circle((690, 390), 6)
        shape.finish(color=(0.2, 0.3, 0.6), fill=(0.2, 0.3, 0.6))
        shape.draw_polyline([(672, 384), (678, 396), (684, 384)])
        shape.finish(color=(0.2, 0.3, 0.6), width=1.2, closePath=False)
        shape.commit()
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}


def test_white_drawings_on_a_dark_slide_are_seen():
    doc = pymupdf.open()
    page = _page(doc, SLIDE)
    shape = page.new_shape()
    shape.draw_rect(page.rect)
    shape.finish(fill=(0.05, 0.08, 0.2), color=None)
    shape.commit()
    _title(page, "Forces on a block on a slope", at=(40, 50), size=26, colour=(1, 1, 1))
    page.insert_textbox(pymupdf.Rect(40, 80, 330, 200), PARAS[0], fontsize=12, color=(1, 1, 1))
    shape = page.new_shape()
    shape.draw_polyline([(380, 330), (680, 330), (680, 150), (380, 330)])
    shape.finish(color=(1, 1, 1), width=2, closePath=True)
    shape.draw_rect(pymupdf.Rect(520, 205, 570, 245))
    shape.finish(color=(1, 1, 1), width=2)
    shape.commit()
    out = layout.analyze(doc.tobytes())
    assert _held(out, 0, (380, 150, 680, 330))
    assert _figures(out) and out.kept_pages == {}
    # White paint on the white page is still nothing.
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Notes")
    _paragraphs(page, n=2)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(100, 400, 400, 600))
    shape.finish(color=(1, 1, 1), fill=(1, 1, 1))
    shape.commit()
    assert _figures(layout.analyze(doc.tobytes())) == []


def test_white_handwriting_on_a_dark_slide_keeps_its_page():
    doc = pymupdf.open()
    page = _page(doc, SLIDE)
    shape = page.new_shape()
    shape.draw_rect(page.rect)
    shape.finish(fill=(0.1, 0.1, 0.1), color=None)
    shape.commit()
    _title(page, "Chalkboard notes", at=(40, 50), size=26, colour=(1, 1, 1))
    _hand(page, 60, 140, 8, colour=(1, 1, 1), em=12, width=1.6, seed=71)
    out = layout.analyze(doc.tobytes())
    assert out.page_types[0].ink.strokes >= 1  # the page type sees the white ink too
    assert out.kept_pages == {0: "unread_page"}


# --------------------------------------------------------------------------- pictures


@pytest.mark.parametrize("case", ["title on photo", "text box on photo", "caption on photo"])
def test_a_picture_with_text_on_it_is_a_figure_and_the_text_is_read(case):
    doc = pymupdf.open()
    if case == "caption on photo":
        page = _page(doc)
        _title(page, "Field notes")
        page.insert_textbox(pymupdf.Rect(72, 104, 540, 160), PARAS[0], fontsize=11)
        rect = pymupdf.Rect(75, 166, 537, 466)
        page.insert_image(rect, pixmap=_photo(200, 130))
        page.insert_textbox(pymupdf.Rect(86, 412, 500, 460), "The river bank after the flood. "
                            "The water line is the dark band on the left, about two metres "
                            "above the path we walked along.", fontsize=11)  # fmt: skip
        said = "The river bank after the flood"
    else:
        page = _page(doc, SLIDE)
        if case == "title on photo":
            rect = pymupdf.Rect(300, 20, 700, 300)
            page.insert_image(rect, pixmap=_photo(160, 110))
            page.insert_text((310, 60), "Pendulum lab", fontsize=30, fontname="hebo",
                             color=(1, 1, 1))  # fmt: skip
            page.insert_textbox(pymupdf.Rect(30, 90, 290, 300), PARAS[1], fontsize=13)
            said = "Pendulum lab"
        else:
            _title(page, "Results of the drop test", at=(40, 50), size=24)
            rect = pymupdf.Rect(200, 86, 620, 374)
            page.insert_image(rect, pixmap=_photo(160, 110))
            page.insert_textbox(pymupdf.Rect(210, 322, 600, 370), "The egg in the foam box "
                                "survived a drop of three metres; the egg in the paper box "
                                "broke at one metre.", fontsize=13)  # fmt: skip
            said = "survived a drop of three metres"
    out = layout.analyze(doc.tobytes())
    assert _held(out, 0, rect)
    assert said in " ".join(e.text or "" for e in out.elements if e.kind is not Kind.FIGURE)


def test_a_full_page_picture_under_a_line_of_text_keeps_its_page():
    # A scan with a long visible stamp and no OCR layer (the open "d1" case), a slide on
    # a full-bleed photo with a title and a paragraph, and a typed page under a
    # transparent picture of hand-drawn circles: the visible text is read and the page
    # keeps its image with a decision.
    doc = pymupdf.open()
    jpeg, _ = scan_png(*A4)
    scan = _page(doc, A4)
    scan.insert_image(scan.rect, stream=jpeg)
    scan.insert_text((36, 820), STAMP, fontsize=7)
    slide = _page(doc, SLIDE)
    slide.insert_image(slide.rect, pixmap=_photo(160, 90))
    _title(slide, "Field trip: the hydro dam", at=(40, 60), size=28)
    slide.insert_textbox(pymupdf.Rect(40, 90, 500, 200), PARAS[2], fontsize=14)
    typed = _page(doc, A4)
    _title(typed, "Marked essay")
    _paragraphs(typed, n=4)
    w, h = 298, 421
    over = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), True)
    alpha = bytearray(w * h)
    for cx, cy, r in ((100, 80, 20), (200, 160, 15)):
        for t in range(0, 360, 2):
            x = int(cx + r * math.cos(math.radians(t)))
            y = int(cy + 0.6 * r * math.sin(math.radians(t)))
            over.set_rect(pymupdf.IRect(x, y, x + 2, y + 2), (220, 30, 30, 255))
            for dy in range(2):
                alpha[(y + dy) * w + x : (y + dy) * w + x + 2] = b"\xff\xff"
    over.set_alpha(bytes(alpha))
    typed.insert_image(typed.rect, pixmap=over, overlay=True)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {0: "unread_page", 1: "unread_page", 2: "unread_page"}
    assert "Downloaded from library" in _text(out)
    assert "Field trip" in _text(out) and "It has a direction" in _text(out)


def test_a_plain_full_page_background_keeps_no_page():
    # Control: a slide template's one-colour background picture and a smooth gradient
    # under typed text are not content.
    doc = pymupdf.open()
    flat = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 36), False)
    flat.set_rect(flat.irect, (235, 240, 250))
    gradient = pymupdf.Pixmap(
        pymupdf.csRGB, 64, 36,
        bytes(v for y in range(36) for x in range(64) for v in (200 + y, 210 + y, 230 + y // 2)),
        False,
    )  # fmt: skip
    for picture in (flat, gradient):
        page = _page(doc, SLIDE)
        page.insert_image(page.rect, pixmap=picture)
        _title(page, "Kinematics in one dimension", at=(40, 60), size=24)
        page.insert_textbox(pymupdf.Rect(40, 90, 680, 300), PARAS[0], fontsize=14)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {} and _figures(out, 0) == _figures(out, 1) == []


@pytest.mark.parametrize("case", ["qr", "maths at 1x"])
def test_a_small_pixel_picture_drawn_large_is_a_figure(case):
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Online simulation")
    page.insert_textbox(pymupdf.Rect(72, 100, 540, 140), PARAS[0], fontsize=11)
    if case == "qr":
        rng = random.Random(9)
        modules = bytes(rng.choice((0, 255)) for _ in range(441))
        pix = pymupdf.Pixmap(pymupdf.csGRAY, 21, 21, modules, False)
        rect = pymupdf.Rect(72, 144, 144, 216)
    else:
        src = pymupdf.open()
        sp = src.new_page(width=80, height=16)
        sp.insert_text((2, 12), "\u03c4 = I \u03b1", fontsize=11, fontname="tiit")
        pix = sp.get_pixmap()
        rect = pymupdf.Rect(73, 144, 153, 160)
    page.insert_image(rect, pixmap=pix)
    _paragraphs(page, top=240, n=1)
    out = layout.analyze(doc.tobytes())
    # A picture of maths is held as an equation (T2-3), a QR code as a figure: not dropped.
    kinds = (Kind.FIGURE,) if case == "qr" else (Kind.FIGURE, Kind.EQUATION)
    assert _held(out, 0, rect, kinds) and out.kept_pages == {}


def test_a_spacer_or_a_one_colour_image_is_still_decoration():
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Notes")
    _paragraphs(page, n=2)
    spacer = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1, 1), True)
    spacer.set_alpha(b"\x00")
    page.insert_image(pymupdf.Rect(72, 400, 300, 430), pixmap=spacer)
    block = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
    block.set_rect(block.irect, (230, 230, 230))
    page.insert_image(pymupdf.Rect(72, 450, 540, 470), pixmap=block)
    out = layout.analyze(doc.tobytes())
    assert _figures(out) == [] and out.kept_pages == {}


# --------------------------------------------------------------------------- margins


def test_chart_tick_labels_in_the_bottom_band_are_read():
    doc = pymupdf.open()
    page = _page(doc, SLIDE)
    _title(page, "Distance fallen against time", at=(40, 46), size=16)
    page.insert_textbox(pymupdf.Rect(40, 60, 680, 80), "Measured with the light gates, five "
                        "drops each, on the long air track.", fontsize=12)  # fmt: skip
    _chart(page, pymupdf.Rect(80, 100, 660, 378))
    for i, label in enumerate(("125", "250", "375", "500", "625")):
        page.insert_text((74 + 145 * i, 396), label, fontsize=10)
    out = layout.analyze(doc.tobytes())
    text = _text(out)
    assert all(label in text for label in ("125", "250", "375", "500", "625"))


def test_a_repeated_axis_title_beside_each_chart_of_a_deck_is_read():
    doc = pymupdf.open()
    for n in range(3):
        page = _page(doc, SLIDE)
        _title(page, f"Trial {n + 1}: speed of the cart", at=(40, 46), size=20)
        page.insert_textbox(pymupdf.Rect(40, 58, 680, 80), "Measured with the light gates on "
                            "the air track, five runs each.", fontsize=10)  # fmt: skip
        _chart(page, pymupdf.Rect(110, 100, 660, 330))
        page.insert_text((80, 260), "Speed in metres per second", fontsize=12, rotate=90)
        for i, label in enumerate(("0", "2", "4", "6", "8")):
            page.insert_text((104 + 137 * i, 345), label, fontsize=10)
    out = layout.analyze(doc.tobytes())
    for n in range(3):
        text = " ".join(e.text or "" for e in out.elements if e.page == n)
        assert "Speed in metres per second" in text


def test_an_axis_title_repeated_in_the_band_of_each_slide_is_read():
    # Each slide of a deck has its chart, its tick labels and, in the footer band 60 pt
    # under them, its x-axis title: the same text at the same place on every slide.
    doc = pymupdf.open()
    for n in range(3):
        page = _page(doc, SLIDE)
        _title(page, f"Trial {n + 1}: distance fallen", at=(40, 46), size=16)
        _chart(page, pymupdf.Rect(80, 100, 660, 300))
        for i in range(5):
            page.insert_text((74 + 145 * i, 320), str(i), fontsize=10)
        page.insert_text((80, 396), "Time (s)", fontsize=12)
    out = layout.analyze(doc.tobytes())
    for n in range(3):
        assert "Time (s)" in " ".join(e.text or "" for e in out.elements if e.page == n)


@pytest.mark.parametrize("chart_on_every_page", [False, True])
def test_a_running_footer_and_page_numbers_are_still_dropped(chart_on_every_page):
    # Controls: a short running footer and a page number, close under a chart on some
    # pages, are the template's when the footer also runs under pages of text; a page
    # number alone is a page number whatever is above it.
    doc = pymupdf.open()
    for n in range(3):
        page = _page(doc)
        _title(page, f"Lab {n + 1}")
        _paragraphs(page, n=2)
        if chart_on_every_page or n == 1:
            _chart(page, pymupdf.Rect(100, 300, 500, 740))
        else:
            _paragraphs(page, top=300, n=4)
        page.insert_text((72, 770), "PHYS 101 lab notes", fontsize=9)
        page.insert_text((300, 770), str(n + 1), fontsize=9)
    out = layout.analyze(doc.tobytes())
    if not chart_on_every_page:
        assert "PHYS 101 lab notes" not in _text(out)
    assert not any(e.text in ("1", "2", "3") for e in out.elements)


# --------------------------------------------------------------------------- page types


def _hidden_layer(page, rect, text: str, font: str) -> None:
    words = [(pymupdf.Rect(rect[0], rect[1], rect[0] + 300, rect[1] + 11), text)]
    text_layer(page, words, "mode3")
    rename_fonts(page.parent, page, font, "cour")


def test_a_hidden_layer_over_a_vector_diagram_with_no_picture_is_not_a_scan():
    # A slide whose state diagram has an OCR layer over it, written by Acrobat: no picture
    # of a page at all, so never a scan (the upload was refused as "scanned_ocr").
    doc = pymupdf.open()
    page = _page(doc, SLIDE)
    _title(page, "Circuit for lab 2", at=(40, 50), size=24)
    shape = page.new_shape()
    centres = [(120 + 480 * f, 225) for f in (0.15, 0.5, 0.85)]
    for c in centres:
        shape.draw_circle(c, 48)
        shape.finish(color=(0, 0, 0), width=1.0)
    for a, b in itertools.pairwise(centres):
        _line(shape, (a[0] + 48, a[1]), (b[0] - 48, b[1]))
    shape.commit()
    _hidden_layer(page, (150, 200), "q0 q1 q2 start accept reject state", "ArialMT")
    with pymupdf.open(stream=doc.tobytes()) as copy_:
        assert page_type.document_refusal(copy_) is None
    out = layout.analyze(doc.tobytes())
    assert out.page_types[0].kind == "vector_no_text"
    assert _held(out, 0, (120, 177, 600, 273))


def test_strokes_under_a_hidden_layer_and_a_title_are_handwriting_not_a_scan():
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Lecture 8")
    boxes = _hand(page, 90, 160, 6, em=9, seed=85)
    rect = boxes[0]
    _hidden_layer(page, (rect.x0, rect.y0), "velocity equals distance over time", "GlyphLessFont")
    out = layout.analyze(doc.tobytes())
    if out.page_types[0].ink.strokes >= 3:
        assert out.page_types[0].kind == "handwritten_ocr"
    assert out.page_types[0].kind not in page_type.SCAN_KINDS
    assert out.kept_pages  # kept as an image either way


# --------------------------------------------------------------------------- axes and rulers
# The second final verify (2026-10-12), on committed fixtures
# (tests/fixtures/handwriting/make_fixtures.py, `build_edge_cases`).


def _fixture(name: str) -> bytes:
    return (HANDWRITING / name).read_bytes()


@pytest.mark.parametrize(
    ("page", "y_axis_x", "top", "x_axis_y"),
    [(0, 60, 40, 737), (1, 60, 40, 737), (2, 40, 30, 755), (3, 40, 30, 755)],
)
def test_a_full_page_plot_keeps_its_axes_and_their_labels(page, y_axis_x, top, x_axis_y):
    # A y-axis near the page's edge and nearly as tall as the page was taken for a side of
    # a frame round the page, the x-axis was then dropped as a lone rule, and the tick
    # labels (in the footer band) and the axis title (in the header band) with them: the
    # figure was the curve alone. Pages: a frame round the page; no frame; axes filling
    # the page as two lines; as one L-shaped path.
    out = layout.analyze(_fixture("full_page_plots.pdf"))
    assert page not in out.kept_pages
    (figure,) = _figures(out, page)
    x0, y0, _, y1 = figure.bbox
    assert x0 <= y_axis_x and y0 <= top + 1 and y1 >= x_axis_y  # both axes are in it
    assert {"v", "0", "1", "2", "3", "4"} <= set((figure.text or "").split())
    assert [e.kind for e in out.elements if e.page == page] == [Kind.FIGURE]


def test_axes_drawn_as_one_path_of_separate_lines_stay_in_the_plot():
    # "80 75 m 640 75 l 80 335 m 80 75 l S": MuPDF's box of the path leaves out the start
    # of the second line, so the axes read as a horizontal rule and the y-axis was lost.
    with pymupdf.open(stream=_fixture("open_axes.pdf")) as doc:
        axes = layout._page_paths(doc[0])[0]
    assert axes.bbox == (80.0, 70.0, 640.0, 330.0)
    assert axes.kind == "shape" and not axes.frame  # two sides of a box are not a frame
    out = layout.analyze(_fixture("open_axes.pdf"))
    assert out.kept_pages == {}
    for page in (0, 1):
        (figure,) = _figures(out, page)
        x0, y0, x1, y1 = figure.bbox
        assert x0 <= 80 and y0 <= 70 and y1 >= 330 and x1 >= 640
    assert all(f" {n} " in f" {_text(out)} " for n in ("0", "2", "4", "6", "8", "10"))


@pytest.mark.parametrize("page", [0, 1])
def test_a_timeline_across_the_top_of_a_page_is_a_drawing(page):
    # A rule within the top 15 % of the page with tick marks across it: dropped as a header
    # rule (or as rules alone), with nothing to say so. Its ticks make it a drawing. The
    # rule and ticks are one path on page 1 and separate paths on page 2.
    out = layout.analyze(_fixture("timelines.pdf"))
    assert page not in out.kept_pages
    (figure,) = _figures(out, page)
    assert figure.bbox[0] <= 31 and figure.bbox[2] >= 581
    assert figure.bbox[1] <= 85 and figure.bbox[3] >= 95  # the ticks too
    assert "Repeat question 2" in _text(out)
    assert "1900" in _text(out)


def test_a_timeline_under_a_short_title_keeps_its_page():
    # Less than a line of text: no figure is made of a drawing this flat, so the safety net
    # must count the ruler's lines (before, rules never counted unless joined end to end).
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Timeline")
    shape = page.new_shape()
    _line(shape, (30, 120), (582, 120), width=1.2)
    for i in range(7):
        _line(shape, (30 + i * 92, 114), (30 + i * 92, 126), width=1.2)
    shape.commit()
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {0: page_type.UNREAD_PAGE} or _held(out, 0, (30, 114, 582, 126))


def test_a_running_header_over_a_rule_across_the_page_adds_nothing():
    out = layout.analyze(_fixture("timelines.pdf"))
    assert 2 not in out.kept_pages
    assert _figures(out, 2) == [] and _regions(out, 2) == []


@pytest.mark.parametrize("style", ["lines", "lines and header"])
def test_a_frame_of_four_lines_and_rules_with_end_marks_are_still_furniture(style):
    # Controls: a frame round the page drawn as four separate lines meets itself at every
    # corner (no open corner: not axes), and a rule with a mark at each end only (two, not
    # three: not a ruler) is a rule.
    doc = pymupdf.open()
    page = _page(doc)
    shape = page.new_shape()
    for a, b in itertools.pairwise([(24, 24), (588, 24), (588, 768), (24, 768), (24, 24)]):
        _line(shape, a, b, width=1.5)
    if style == "lines and header":
        _line(shape, (36, 60), (576, 60), width=0.8)
        _line(shape, (36, 56), (36, 64), width=0.8)
        _line(shape, (576, 56), (576, 64), width=0.8)
    shape.commit()
    page.insert_text((72, 52), "PHYS 101 Mechanics, week 3", fontsize=9)
    _title(page, "Kinematics", at=(72, 100))
    _paragraphs(page, top=120, n=3)
    out = layout.analyze(doc.tobytes())
    assert out.kept_pages == {}
    assert _figures(out) == [] and _regions(out) == []


@pytest.mark.parametrize(
    ("page", "box"),
    [(0, (250, 106, 270, 124)), (1, (250, 102, 264, 118)), (2, (160, 110, 182, 128))],
)
def test_a_hand_tick_cross_or_slash_over_a_line_of_typed_text_is_cut_out(page, box):
    # One or two pen strokes too simple for the stroke test, drawn over typed text: taken
    # as part of the line they lie on (like an underline), they were lost. Open strokes
    # that are no radical sign and reach out of their line are pen marks.
    out = layout.analyze(_fixture("marks_over_text.pdf"))
    assert out.page_types[page].kind == "born_digital"
    assert page not in out.kept_pages
    (region,) = _regions(out, page)
    assert _held(out, page, box) and region.bbox[0] <= box[0] and region.bbox[2] >= box[2]
    text = " ".join(e.text or "" for e in out.elements if e.page == page)
    assert "How high does it go" in text or "It has a direction" in text  # read whole


def test_an_underline_a_box_or_a_cancel_stroke_in_a_line_is_still_part_of_the_text():
    out = layout.analyze(_fixture("marks_over_text.pdf"))
    assert 3 not in out.kept_pages
    assert _regions(out, 3) == [] and _figures(out, 3) == []


# --------------------------------------------------------------------------- end to end


def test_nothing_on_these_pages_is_ever_called_converted():
    doc = pymupdf.open()
    page = _page(doc)
    _title(page, "Branches of mechanics")
    page.insert_textbox(pymupdf.Rect(72, 104, 540, 160), PARAS[0], fontsize=11)
    _boxes_chart(page, one_arrow=False)
    page.insert_textbox(pymupdf.Rect(72, 320, 540, 400), PARAS[1], fontsize=11)
    page = _page(doc)
    _title(page, "Quiz 1, question 2")
    _paragraphs(page, n=2)
    _tick(page, 548, 105)
    result = _convert(doc.tobytes())
    assert result_outcome(result) == ("done", "pages_not_converted")
    figures = [e for e in result.elements if e.kind is Kind.FIGURE]
    assert [e.page for e in figures] == [0, 1]
    assert "Mechanics" in (figures[0].text or "")  # the chart's labels go with it
    fixes = {f.element_id: f for f in result.fixes}
    assert fixes[figures[1].id].reason == "handwriting_on_page"
    assert "It has a direction" in _html_text(result.html)
