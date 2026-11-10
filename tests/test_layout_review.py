"""Regression tests for defects found in review of layout.analyze (2026-10-08).

Each test rebuilds the reviewer's reproduction as a small generated PDF and pins the fixed
behaviour: numbered-line headings, equation numbers, two-column reading order, Symbol-font
bullets, decoration in the margins, watermarks, text over images and panels, fake inline
markers, MuPDF's own output, and the cost of fragment resolution.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline.layout import (
    analyze,
    release,
)

BODY = 11.0
PITCH = 15.0


def write(page: pymupdf.Page, x: float, y: float, rows: list[str], **kw) -> float:
    """Insert one text row per string; return the baseline after the last row."""
    size = kw.pop("fontsize", BODY)
    pitch = kw.pop("pitch", PITCH * size / BODY)
    for row in rows:
        page.insert_text((x, y), row, fontsize=size, **kw)
        y += pitch
    return y


def one_page(fill, width: float = 612, height: float = 792) -> bytes:
    doc = pymupdf.open()
    fill(doc.new_page(width=width, height=height))
    return doc.tobytes()


def html_page(html: str) -> bytes:
    return one_page(lambda page: page.insert_htmlbox(pymupdf.Rect(72, 72, 540, 300), html))


def texts(elements: list[Element], kind: Kind | None = None) -> list[str]:
    return [e.text or "" for e in elements if kind is None or e.kind == kind]


def red_pixmap(width: int = 64, height: int = 48) -> pymupdf.Pixmap:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.set_rect(pixmap.irect, (200, 40, 40))
    return pixmap


@pytest.fixture(autouse=True)
def empty_cache():
    release()
    yield
    release()


# ---------------------------------------------------------------- numbered-line headings


def table_page(page: pymupdf.Page) -> None:
    write(page, 72, 100, ["The table lists the distance of the cart at each second of the run."])
    columns = (200, 260, 320, 380)
    page.draw_line((190, 128), (420, 128), width=0.8)
    for x, head in zip(columns, ("Time", "Dist", "Speed", "Acc"), strict=True):
        page.insert_text((x, 142), head, fontsize=BODY)
    page.draw_line((190, 147), (420, 147), width=0.5)
    values = [("0", "0.0", "0.0", "2.0"), ("1", "1.0", "2.0", "2.0"), ("2", "4.0", "4.0", "2.0")]
    for r, row in enumerate(values):
        for x, value in zip(columns, row, strict=True):
            page.insert_text((x, 162 + 15 * r), value, fontsize=BODY)
    page.draw_line((190, 197), (420, 197), width=0.8)
    # A ruled "label | value" table whose values start with a number.
    for y in (240, 260, 280, 300):
        page.draw_line((72, y), (400, y), width=0.5)
    for x in (72, 236, 400):
        page.draw_line((x, 240), (x, 300), width=0.5)
    cells = (("Mass", "2 kg"), ("Speed", "3 m/s"), ("Distance", "12 Metres"))
    for y, (name, value) in zip((254, 274, 294), cells, strict=True):
        write(page, 78, y, [name])
        write(page, 242, y, [value])


# ---------------------------------------------------------------- equation numbers


def test_a_bare_equation_number_halfway_down_a_fraction_belongs_to_it():
    """OpenStax: a fraction alone on its line, with "2.4" level with the bar."""

    def fill(page):
        write(page, 72, 100, ["The slope of the graph can also be written as a ratio of changes:"])
        page.insert_text((282, 130), "Δd", fontsize=BODY, fontname="symb")
        page.draw_line((280, 133), (295, 133), width=0.6)
        page.insert_text((282, 142), "Δt", fontsize=BODY, fontname="symb")
        page.insert_text((318, 136), "2.4", fontsize=BODY)
        write(page, 72, 170, ["This relationship was how we defined average velocity before."])

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.EQUATION, Kind.PARAGRAPH]
    equation = elements[1]
    assert equation.equation_label == "2.4" and "2.4" not in (equation.text or "")
    assert "2 lines merged" in equation.reasons
    assert equation.bbox[1] <= 125 and equation.bbox[3] >= 140 and equation.bbox[2] < 310


# ---------------------------------------------------------------- two-column reading order


# ---------------------------------------------------------------- bullets


# ---------------------------------------------------------------- decoration


# ---------------------------------------------------------------- text over images and panels


# ---------------------------------------------------------------- inline markers


DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


# ---------------------------------------------------------------- MuPDF output


# ---------------------------------------------------------------- margins


# ---------------------------------------------------------------- cost
