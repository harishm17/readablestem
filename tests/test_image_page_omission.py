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

from image_page_support import block, text
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


def test_a_page_whose_blocks_cover_every_line_has_no_unaccounted_region():
    assert om.find(mask_of(page(LINES)), [units(y) for y in LINES]) == []


def test_boxes_a_few_percent_off_their_lines_still_cover_them():
    # the reader's boxes sit off handwriting: here 4% of the page height too low
    off = [(a, b + 40, c, d + 40) for a, b, c, d in (units(y) for y in LINES)]
    assert om.find(mask_of(page(LINES)), off) == []


def test_boxes_a_little_wider_or_narrower_than_the_line_are_tolerated():
    cut = [(a + 15, b, c - 20, d) for a, b, c, d in (units(y) for y in LINES)]
    assert om.find(mask_of(page(LINES)), cut) == []


def test_one_line_next_to_a_block_is_not_flagged():
    # the cost of not trusting the boxes: a single dropped line inside the margin of error
    assert om.find(mask_of(page([260, 385])), [units(260)]) == []


def test_a_running_header_and_a_page_number_are_not_content():
    def extra(draw: ImageDraw.ImageDraw) -> None:
        writing(draw, 40, 200, 900)  # running header, top strip
        writing(draw, H - 90, 700, 1000)  # page number, bottom strip

    image = page(LINES, extra=extra)
    assert om.find(mask_of(image), [units(y) for y in LINES]) == []


def test_a_frame_drawn_round_the_text_is_not_content():
    def extra(draw: ImageDraw.ImageDraw) -> None:
        draw.rectangle((120, 150, 1580, 2000), outline=(40, 40, 40), width=4)

    image = page(LINES, extra=extra)
    assert om.find(mask_of(image), [units(y) for y in LINES]) == []


def test_a_rule_a_dark_scanner_bar_and_the_page_rim_are_not_content():
    def extra(draw: ImageDraw.ImageDraw) -> None:
        draw.line((150, 780, 1550, 784), fill=(30, 30, 30), width=3)  # a rule
        draw.rectangle((0, 0, 40, H), fill=(10, 10, 10))  # a scanner's bar
        draw.rectangle((0, 0, W, 12), fill=(10, 10, 10))  # a dark rim

    image = page(LINES, extra=extra)
    assert om.find(mask_of(image), [units(y) for y in LINES]) == []


def test_grain_and_specks_are_not_content():
    rng = np.random.default_rng(3)
    base = np.asarray(page(LINES), dtype=np.int16)
    noisy = np.clip(base + rng.normal(0, 7, base.shape), 0, 255).astype(np.uint8)
    specks = rng.integers(0, 255, (200, 2))
    for y, x in specks:
        noisy[int(y) * 11 % H : int(y) * 11 % H + 3, int(x) * 6 % W : int(x) * 6 % W + 3] = 40
    image = Image.fromarray(noisy)
    assert om.find(mask_of(image), [units(y) for y in LINES]) == []


def test_a_missing_middle_stretch_is_found_where_it_is():
    kept = [units(LINES[0]), units(LINES[4])]
    [region] = om.find(mask_of(page(LINES)), kept)
    assert region.y0 >= round(LINES[0] / H * 1000)
    assert region.y1 <= round((LINES[4] + 56) / H * 1000)


def test_no_boxes_or_a_tiny_picture_give_nothing():
    assert om.find(mask_of(page(LINES)), []) == []
    assert om.find(np.zeros((30, 30), dtype=bool), [(0, 0, 1000, 1000)]) == []
    assert om.find(np.zeros((H, W), dtype=bool), [(0, 0, 10, 10)]) == []


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


def test_a_page_read_completely_has_no_omission_item():
    result = built(tuple(LINES), page(LINES))
    assert omitted(result) == []
    assert result.record.omitted == 0 and result.record.required == 0


def test_the_check_can_be_turned_off():
    assert omitted(built((LINES[0],), page(LINES), check_omission=False)) == []


def test_without_a_picture_there_is_nothing_to_compare_and_the_page_is_unchanged():
    out = image_page.build(0, blocks_for(LINES[0]), box=BOX, png=None)
    assert out is not None and omitted(out) == []
    out = image_page.build(0, blocks_for(LINES[0]), box=BOX, png=b"not a picture")
    assert out is not None and omitted(out) == []


def test_a_block_without_a_usable_box_leaves_the_page_unjudged():
    unplaced = ImagePageOutput.model_validate(
        {"blocks": [block("paragraph", runs=[text("Hello.")], x0=0, y0=0, x1=0, y1=0)]}
    )
    out = image_page.build(0, unplaced, box=BOX, png=png_bytes(page(LINES)))
    assert out is not None and omitted(out) == []


def test_the_omission_item_sits_after_the_block_above_it_in_reading_order():
    result = built((LINES[0], LINES[4]), page(LINES))
    order = [
        e for e in sorted(result.elements, key=lambda e: e.order) if not image_page.is_original(e)
    ]
    kinds = [e.kind for e in order]
    assert kinds == [Kind.EQUATION, Kind.FIGURE, Kind.EQUATION]
    assert [e.order for e in result.elements] == sorted(e.order for e in result.elements)
    assert len({e.order for e in result.elements}) == len(result.elements)
