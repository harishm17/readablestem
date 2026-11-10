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

from fixtures.handwriting.make_fixtures import (
    INK_BLACK,
    INK_BLUE,
    STAMP,
    Writer,
    rename_fonts,
    scan_png,
    text_layer,
)
from readablestem.config import Settings
from readablestem.core.types import Kind
from readablestem.models import OFFLINE_DEFAULTS
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import layout, page_type
from readablestem.pipeline.run import convert_pdf

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


# --------------------------------------------------------------------------- band, dark


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


# --------------------------------------------------------------------------- pictures


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


# --------------------------------------------------------------------------- end to end
