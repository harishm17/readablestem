r"""`\ce{...}` (mhchem) on every LaTeX path (`equation_step.read_latex`): the review screen's
edit box (equation and table cell), the image-page reader and its second reading, the table
cells' HTML, and the page-math spans. A `\ce` inside the subset is written out as plain LaTeX
and read like any other; one outside it is refused with the same category on every path
(`unsupported_ce`), never guessed and never converted to something else."""

from __future__ import annotations

from typing import Any

from image_page_support import block, math, text
from readablestem.core.types import (
    Element,
    EquationFix,
    Fix,
    Kind,
    Status,
)
from readablestem.pipeline import image_page, validate
from readablestem.web import edit_check
from test_image_page import build, by_kind

WATER = r"\text{H}_2\text{O}"
BAD = r"\ce{N2 <=>> NO}"  # an unequal equilibrium arrow: outside the subset


def equation_case(text_: str = "H2O") -> tuple[Element, Fix]:
    element = Element(
        id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(0, 0, 10, 10), order=1,
        text=text_, font_size=10, display="inline",
    )  # fmt: skip
    fix = Fix(
        element_id="p1-e1",
        kind=Kind.EQUATION,
        status=Status.NEEDS_REVIEW,
        equation=EquationFix(
            latex="x", mathml=validate.safe_latex_to_mathml("x", "inline"), speech="x"
        ),
    )
    return element, fix


# ------------------------------------------------------------------------- the one entry


# ----------------------------------------------------------------------- the review edit


def test_a_ce_edit_outside_the_subset_is_refused_in_words():
    element, fix = equation_case()
    found = edit_check.check_equation_edit(element, fix, BAD)
    assert found.mathml is None
    assert [c.code for c in found.failed] == ["unsupported_ce"]
    assert len(found.blocking) == 1 and "\\ce" in found.blocking[0]
    assert "unsupported_ce" not in found.blocking[0]  # words, not the code


# --------------------------------------------------------------------------- image pages


def test_an_inline_ce_formula_outside_the_subset_stays_in_the_text_and_asks_a_person():
    built = build(block("paragraph", runs=[text("Take "), math(BAD), text(" of it.")]))
    [paragraph] = by_kind(built, Kind.PARAGRAPH)
    assert by_kind(built, Kind.EQUATION) == []
    fix = built.fixes[paragraph.id]
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")
    assert "inline_math_invalid" in {c.code for c in fix.failed_checks}


def _block(**kw: Any) -> image_page.Block:
    return image_page.Block.model_validate(block("paragraph", **kw))


def test_a_lone_ce_formula_line_becomes_a_display_equation():
    lone = image_page.lone_formula(_block(runs=[math(r"\ce{CaCO3 -> CaO + CO2}")]))
    assert lone is not None and lone.kind == "equation" and r"\ce" not in lone.latex
    assert image_page.lone_formula(_block(runs=[math(BAD)])) is None


# ------------------------------------------------------------------------------ tables


# ------------------------------------------------------------------------------ page math
