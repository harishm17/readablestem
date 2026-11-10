"""The omission check of the image page pass (ADR 0007, "Omission check"): ink the page
reader's blocks do not account for becomes a required decision on the page.

The pictures are generated: a page of "writing" is rows of short dark strokes with gaps, which
is what the grid of the check sees of handwriting and of print. No model is asked.
"""

from __future__ import annotations

import io
import random

import numpy as np
from PIL import Image, ImageDraw

from image_page_support import block
from readablestem.core.types import IMAGE_PAGE_OMISSION_REASON, Element, Kind, Status
from readablestem.pipeline import image_page
from readablestem.pipeline import image_page_omission as om
from readablestem.pipeline.image_page import ImagePageOutput

W, H = 1700, 2200  # a page at 200 dpi
BOX = (0.0, 0.0, 612.0, 792.0)
LEFT, RIGHT = 200, 1400


def writing(draw: ImageDraw.ImageDraw, y: int, x0: int = LEFT, x1: int = RIGHT, seed: int = 0):
    """One line of writing: strokes 10-28 px wide, 36-56 px tall, with word gaps."""
    rng = random.Random(seed + y)
    x = x0
    while x < x1:
        w = rng.randint(10, 28)
        h = rng.randint(36, 56)
        draw.rectangle((x, y + 56 - h, x + w, y + 56), fill=(25, 25, 30))
        x += w + (rng.randint(24, 40) if rng.random() < 0.18 else rng.randint(4, 9))


def page(lines: list[int], *, extra=None) -> Image.Image:
    image = Image.new("RGB", (W, H), (244, 242, 238))
    draw = ImageDraw.Draw(image)
    for y in lines:
        writing(draw, y)
    if extra:
        extra(draw)
    return image


def mask_of(image: Image.Image) -> np.ndarray:
    return image_page.Ink(png_bytes(image)).mask


def png_bytes(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def units(y: int, height: int = 70) -> tuple[int, int, int, int]:
    """A block's box (thousandths) round a line at pixel row y."""
    return (
        round(LEFT / W * 1000) - 5,
        round(y / H * 1000) - 5,
        round(RIGHT / W * 1000) + 5,
        round((y + height) / H * 1000) + 5,
    )


LINES = [260, 470, 680, 890, 1100]  # a header line and four display lines, pixel rows


# --------------------------------------------------------------------- the check itself


def test_four_lines_with_only_the_first_returned_are_one_unaccounted_region():
    regions = om.find(mask_of(page(LINES)), [units(LINES[0])])
    assert len(regions) == 1
    [region] = regions
    assert region.y0 <= round((LINES[1] + 30) / H * 1000)
    assert region.y1 >= round((LINES[4] + 56) / H * 1000)
    assert region.share > 0.6


def test_a_running_header_and_a_page_number_are_not_content():
    def extra(draw: ImageDraw.ImageDraw) -> None:
        writing(draw, 40, 200, 900)  # running header, top strip
        writing(draw, H - 90, 700, 1000)  # page number, bottom strip

    image = page(LINES, extra=extra)
    assert om.find(mask_of(image), [units(y) for y in LINES]) == []


# ----------------------------------------------------------------------- inside build()


def blocks_for(*rows: int) -> ImagePageOutput:
    return ImagePageOutput.model_validate(
        {
            "blocks": [
                block(
                    "equation",
                    latex="x = 1",
                    x0=units(y)[0],
                    y0=units(y)[1],
                    x1=units(y)[2],
                    y1=units(y)[3],
                )
                for y in rows
            ]
        }
    )


def built(rows: tuple[int, ...], image: Image.Image, **kw):
    out = image_page.build(0, blocks_for(*rows), box=BOX, png=png_bytes(image), **kw)
    assert out is not None
    return out


def omitted(result: image_page.Built) -> list[Element]:
    return [e for e in result.elements if IMAGE_PAGE_OMISSION_REASON in e.reasons]


def test_an_omission_is_a_required_figure_item_that_says_so_and_names_the_page():
    result = built((LINES[0],), page(LINES))
    [element] = omitted(result)
    fix = result.fixes[element.id]
    assert element.kind is Kind.FIGURE and element.page == 0
    x0, y0, x1, y1 = element.bbox
    assert 0 <= x0 < x1 <= 612 and y0 > 0.2 * 792 and y1 < 0.9 * 792  # the stretch, in points
    assert (fix.kind, fix.status, fix.review) == (Kind.FIGURE, Status.NEEDS_REVIEW, "required")
    assert [(c.name, c.code) for c in fix.failed_checks] == [
        ("image_page.omitted_region", "omitted_region")
    ]
    assert (
        result.record.omitted == 1 and result.record.required == 1
    )  # this item; the equation waits for its crop
