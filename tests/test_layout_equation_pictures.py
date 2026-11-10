"""Equations pasted as pictures (backlog T2-3): a small black-on-white picture of marks in or
beside a line of prose is an equation with no text layer, not a figure; a photograph, a
picture with a caption and a large drawing are still figures."""

from __future__ import annotations

import io

import pymupdf
from PIL import Image, ImageDraw

from readablestem.core.types import Element, Kind
from readablestem.pipeline import layout


def equation_png(width: int = 90, height: int = 60, *, pieces: int = 5) -> bytes:
    """Black strokes on a white ground, a few separate pieces: glyph-like."""
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    step = width // (pieces + 1)
    for i in range(pieces):
        x = step * (i + 1)
        draw.rectangle([x - 1, height // 4, x + 1, 3 * height // 4], fill=0)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def page_with(build) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    build(page)
    data = doc.tobytes()
    doc.close()
    return data


def text(page, y: float, words: str, x: float = 72.0) -> float:
    page.insert_text((x, y), words, fontsize=11, fontname="helv")
    return x + pymupdf.get_text_length(words, fontname="helv", fontsize=11)


def kinds(elements: list[Element]) -> list[Kind]:
    return [e.kind for e in elements]


def test_a_picture_too_small_to_be_a_figure_is_kept_in_a_line_of_text():
    # Under 12 pt wide: layout used to drop it as a bullet or spacer, silently.
    def build(page):
        end = text(page, 100, "Multiplying ")
        page.insert_image(
            pymupdf.Rect(end + 2, 90, end + 12, 108), stream=equation_png(30, 54, pieces=3)
        )
        text(page, 100, " by seven removes it.", x=end + 16)

    elements = layout.analyze(page_with(build)).elements
    assert kinds(elements) == [Kind.PARAGRAPH, Kind.EQUATION]
