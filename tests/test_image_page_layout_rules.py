"""Image pages: a line that holds only a formula is a display equation (docs/eval-handwriting.md).

On handwriting the page reader set 35 of 56 display equations as list items or paragraphs with
one formula run in them ("1." and an equation on a line of its own). The builder promotes such a
block to a display equation, with the marker as its number, so that the equation step reads the
crop of it and it is judged as a display equation.
"""

from __future__ import annotations

from image_page_support import block, math, png_of
from readablestem.core.types import Kind
from readablestem.pipeline import image_page
from readablestem.pipeline.image_page import ImagePageOutput

BOX = (0.0, 0.0, 612.0, 792.0)


def build(*blocks, png: bytes | None = None) -> image_page.Built:
    built = image_page.build(
        0, ImagePageOutput.model_validate({"blocks": list(blocks)}), box=BOX, png=png
    )
    assert built is not None
    return built


def kinds(built: image_page.Built) -> list[Kind]:
    return [e.kind for e in built.elements if e.display != "inline"][1:]  # not the original


def test_the_marker_is_left_out_of_the_promoted_equations_box():
    # a marker at x 100-128 and a formula at x 300-500 (pixels of a 600 px wide picture),
    # both inside the block's box (thousandths: 150 to 900)
    png = png_of([(100, 150, 128, 190), (300, 150, 500, 190)])
    built = build(
        block(
            "list_item",
            marker="1.",
            x0=150,
            y0=170,
            x1=900,
            y1=260,
            runs=[math("x^2")],
        ),
        png=png,
    )
    [equation] = [e for e in built.elements if e.kind is Kind.EQUATION]
    assert equation.bbox is not None
    assert equation.bbox[0] > 612 * 250 / 600  # starts at the formula, not at the marker
