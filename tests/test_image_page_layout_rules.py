"""Image pages: a line that holds only a formula is a display equation (docs/eval-handwriting.md).

On handwriting the page reader set 35 of 56 display equations as list items or paragraphs with
one formula run in them ("1." and an equation on a line of its own). The builder promotes such a
block to a display equation, with the marker as its number, so that the equation step reads the
crop of it and it is judged as a display equation.
"""

from __future__ import annotations

from image_page_support import block, math, png_of, text
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


def test_a_list_item_holding_only_a_formula_is_a_display_equation_numbered_by_its_marker():
    built = build(
        block("list_item", marker="(b)", level=1, confidence="medium", runs=[math("x^2 = 4")])
    )
    [equation] = [e for e in built.elements if e.kind is Kind.EQUATION]
    assert equation.display == "block" and equation.equation_label == "(b)"
    assert equation.page_latex == "x^2 = 4" and equation.page_status == "span"
    assert equation.image_confidence == "medium"
    assert not [e for e in built.elements if e.kind is Kind.LIST_ITEM]
    assert equation.id not in built.fixes  # the equation step decides it, from the crop
    assert built.record.by_kind == {"equation": 1}


def test_a_paragraph_holding_only_a_formula_is_a_display_equation_without_a_number():
    built = build(block("paragraph", runs=[math("E = mc^2")]))
    [equation] = [e for e in built.elements if e.kind is Kind.EQUATION]
    assert equation.display == "block" and equation.equation_label is None
    assert not [e for e in built.elements if e.kind is Kind.PARAGRAPH]


def test_a_full_stop_after_the_formula_does_not_stop_the_promotion():
    built = build(block("paragraph", runs=[math("a+b=c"), text(" .")]))
    assert [e.display for e in built.elements if e.kind is Kind.EQUATION] == ["block"]


def test_words_in_the_block_keep_it_a_paragraph_with_an_inline_formula():
    built = build(
        block("list_item", marker="1.", runs=[text("Solve "), math("x^2 = 4")]),
        block("paragraph", runs=[math("x"), text(" is the speed")]),
    )
    assert [e.display for e in built.elements if e.kind is Kind.EQUATION] == ["inline", "inline"]
    assert len([e for e in built.elements if e.kind in (Kind.LIST_ITEM, Kind.PARAGRAPH)]) == 2


def test_two_formulas_and_no_words_are_left_as_they_were():
    built = build(block("paragraph", runs=[math("x = 1"), math("y = 2")]))
    assert [e.display for e in built.elements if e.kind is Kind.EQUATION] == ["inline", "inline"]


def test_a_formula_that_does_not_validate_is_not_promoted():
    built = build(block("list_item", marker="1.", runs=[math("\\frac{a}{")]))
    assert not [e for e in built.elements if e.display == "block"]
    assert [e for e in built.elements if e.kind is Kind.LIST_ITEM]


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
