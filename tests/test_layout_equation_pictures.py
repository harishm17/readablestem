"""Equations pasted as pictures (backlog T2-3): a small black-on-white picture of marks in or
beside a line of prose is an equation with no text layer, not a figure; a photograph, a
picture with a caption and a large drawing are still figures."""

from __future__ import annotations

import io

import numpy as np
import pymupdf
from PIL import Image, ImageDraw

from readablestem.core.types import Element, Kind
from readablestem.pipeline import equation_images, layout


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


def photo_png(side: int = 80) -> bytes:
    noise = np.random.default_rng(7).integers(0, 255, (side, side, 3), dtype=np.uint8)
    out = io.BytesIO()
    Image.fromarray(noise, "RGB").save(out, format="PNG")
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


def test_a_picture_in_a_sentence_is_an_inline_equation_without_text():
    def build(page):
        end = text(page, 100, "The mean of the equal parts is ")
        page.insert_image(pymupdf.Rect(end + 2, 88, end + 18, 108), stream=equation_png(48, 60))
        text(page, 100, " in all.", x=end + 22)

    elements = layout.analyze(page_with(build)).elements
    assert kinds(elements) == [Kind.PARAGRAPH, Kind.EQUATION]
    paragraph, picture = elements
    assert paragraph.text is not None and paragraph.text.startswith(
        "The mean of the equal parts is "
    )
    assert paragraph.text.endswith("in all.") and "⟦p1-e1⟧" in paragraph.text
    assert picture.display == "inline" and picture.parent_id == paragraph.id
    assert picture.text is None and equation_images.REASON in picture.reasons


def test_a_picture_on_its_own_line_is_a_display_equation_with_its_number():
    def build(page):
        text(page, 100, "Expanding along the first row gives:")
        page.insert_image(pymupdf.Rect(150, 112, 300, 160), stream=equation_png(450, 144, pieces=7))
        text(page, 140, "(2.1)", x=500)

    elements = layout.analyze(page_with(build)).elements
    assert kinds(elements) == [Kind.PARAGRAPH, Kind.EQUATION]
    picture = elements[1]
    assert picture.display == "block" and picture.text is None
    assert picture.equation_label == "(2.1)"
    assert equation_images.REASON in picture.reasons


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


def test_a_photograph_a_large_drawing_and_a_captioned_picture_stay_figures():
    def build(page):
        page.insert_image(pymupdf.Rect(72, 80, 152, 160), stream=photo_png())
        text(page, 180, "A paragraph of ordinary prose follows the photograph here.")
        page.insert_image(pymupdf.Rect(72, 200, 372, 420), stream=equation_png(900, 660, pieces=9))
        page.insert_image(pymupdf.Rect(72, 460, 222, 508), stream=equation_png(450, 144, pieces=7))
        text(page, 530, "Figure 2.1 The apparatus used in the experiment.")

    elements = layout.analyze(page_with(build)).elements
    assert [e.kind for e in elements if e.kind is not Kind.PARAGRAPH] == [Kind.FIGURE] * 3
    assert not any(e.kind is Kind.EQUATION for e in elements)


def test_looks_like_equation_needs_ink_on_white_and_no_frame():
    ink = equation_images.Profile(colour=0.0, ink=0.08, white=0.9, components=6, frame=False)
    assert equation_images.looks_like_equation(ink)
    assert not equation_images.looks_like_equation(
        equation_images.Profile(0.5, 0.3, 0.1, 40, False)
    )
    assert not equation_images.looks_like_equation(equation_images.Profile(0.0, 0.08, 0.9, 6, True))
    assert not equation_images.looks_like_equation(equation_images.Profile(0.0, 0.0, 1.0, 0, False))
    assert equation_images.size_fits(150, 48, 11) and not equation_images.size_fits(300, 300, 11)
