r"""`\ce{...}` (mhchem) on every LaTeX path (`equation_step.read_latex`): the review screen's
edit box (equation and table cell), the image-page reader and its second reading, the table
cells' HTML, and the page-math spans. A `\ce` inside the subset is written out as plain LaTeX
and read like any other; one outside it is refused with the same category on every path
(`unsupported_ce`), never guessed and never converted to something else."""

from __future__ import annotations

from typing import Any

from latex2mathml.converter import convert

from image_page_support import block, cell, math, text
from readablestem.core.types import (
    Element,
    EquationFix,
    Fix,
    Kind,
    Status,
    TableCell,
    TableGrid,
)
from readablestem.pipeline import equation_step, image_page, page_math, validate
from readablestem.pipeline.page_math import PageMathOutput
from readablestem.pipeline.table_html import render_table
from readablestem.web import edit_check
from readablestem.web.messages import explain_check
from test_image_page import build, by_kind
from test_image_page_check import run_second, second_client
from test_page_math import expression, row

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


def test_read_latex_writes_ce_out_and_cleans():
    assert equation_step.read_latex(r"$\ce{H2O}$") == WATER
    assert equation_step.read_latex(r"x = \ce{Zn^2+(aq, 0.10 M)}") == (
        r"x = \text{Zn}^{2+}\text{(aq, 0.10 M)}"
    )
    assert equation_step.read_latex("x^2") == "x^2"
    assert equation_step.read_latex(BAD) == BAD  # left alone for the check to refuse


def test_a_refused_ce_has_its_own_category_and_a_message():
    checks = validate.check_latex(BAD)
    failed = [c for c in checks if not c.passed]
    assert [(c.name, c.code) for c in failed] == [("latex.allowed_commands", "unsupported_ce")]
    assert explain_check(failed[0]).known
    # another unknown command stays an unknown command
    other = [c for c in validate.check_latex(r"\ce{H2O} \foo") if not c.passed]
    assert [c.code for c in other] == ["unknown_command"]
    # the allowlist is unchanged: nothing else is let through by the new category
    assert validate.has_errors(checks)


# ----------------------------------------------------------------------- the review edit


def test_an_edit_in_ce_is_accepted_and_stored_as_plain_latex():
    element, fix = equation_case()
    found = edit_check.check_equation_edit(element, fix, r"\ce{H2O}")
    assert found.latex == WATER and found.mathml and not found.blocking
    assert not [c for c in found.failed if c.severity == "error"]


def test_an_edit_with_a_condition_a_star_and_a_mark_is_accepted():
    element, fix = equation_case("")
    for typed in (r"\ce{Zn^2+(aq, 0.10 M)}", r"\ce{^{60}_{28}Ni^*}", r"\ce{Ag+ + Cl- -> AgCl v}"):
        found = edit_check.check_equation_edit(element, fix, typed)
        assert found.mathml and not found.blocking, typed


def test_a_ce_edit_outside_the_subset_is_refused_in_words():
    element, fix = equation_case()
    found = edit_check.check_equation_edit(element, fix, BAD)
    assert found.mathml is None
    assert [c.code for c in found.failed] == ["unsupported_ce"]
    assert len(found.blocking) == 1 and "\\ce" in found.blocking[0]
    assert "unsupported_ce" not in found.blocking[0]  # words, not the code


def test_a_table_cell_edit_in_ce_is_accepted_and_a_bad_one_refused():
    found = edit_check.check_cell_edit("H2O", r"\ce{H2O}")
    assert found.latex == WATER and found.mathml and not found.blocking
    refused = edit_check.check_cell_edit("NO", BAD)
    assert refused.mathml is None and [c.code for c in refused.failed] == ["unsupported_ce"]


# --------------------------------------------------------------------------- image pages


def test_an_inline_ce_formula_on_a_scanned_page_is_an_inline_equation():
    built = build(block("paragraph", runs=[text("Water is "), math(r"\ce{H2O}"), text(" here.")]))
    [equation] = by_kind(built, Kind.EQUATION)
    fix = built.fixes[equation.id]
    assert fix.equation is not None and fix.equation.latex == WATER
    assert fix.status is Status.ACCEPTED


def test_an_inline_ce_formula_outside_the_subset_stays_in_the_text_and_asks_a_person():
    built = build(block("paragraph", runs=[text("Take "), math(BAD), text(" of it.")]))
    [paragraph] = by_kind(built, Kind.PARAGRAPH)
    assert by_kind(built, Kind.EQUATION) == []
    fix = built.fixes[paragraph.id]
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")
    assert "inline_math_invalid" in {c.code for c in fix.failed_checks}


def test_a_display_ce_equation_goes_to_the_equation_step_as_plain_latex():
    built = build(block("equation", latex=r"\ce{2 H2 + O2 -> 2 H2O}"))
    [equation] = by_kind(built, Kind.EQUATION)
    assert equation.page_status == "span"
    assert equation.page_latex == r"2 \text{H}_2 + \text{O}_2 \longrightarrow 2 \text{H}_2\text{O}"


def test_a_display_ce_equation_outside_the_subset_has_no_page_reading():
    built = build(block("equation", latex=BAD))
    [equation] = by_kind(built, Kind.EQUATION)
    assert equation.page_status is None and equation.page_latex is None


def _block(**kw: Any) -> image_page.Block:
    return image_page.Block.model_validate(block("paragraph", **kw))


def test_a_lone_ce_formula_line_becomes_a_display_equation():
    lone = image_page.lone_formula(_block(runs=[math(r"\ce{CaCO3 -> CaO + CO2}")]))
    assert lone is not None and lone.kind == "equation" and r"\ce" not in lone.latex
    assert image_page.lone_formula(_block(runs=[math(BAD)])) is None


def test_a_ce_cell_of_a_scanned_table_is_written_out_and_a_bad_one_is_not_valid():
    good = build(block("table", rows=[{"cells": [cell("h", "col")]}, {"cells": [
        cell("H2O", latex=r"\ce{H2O}")]}]))  # fmt: skip
    [table] = by_kind(good, Kind.TABLE)
    grid = good.fixes[table.id].table.grid  # type: ignore[union-attr]
    assert [c.latex for c in grid.cells if c.latex] == [WATER]
    bad = build(block("table", rows=[{"cells": [cell("h", "col")]}, {"cells": [
        cell("NO", latex=BAD)]}]))  # fmt: skip
    [table] = by_kind(bad, Kind.TABLE)
    assert "invalid_latex" in {c.code for c in bad.fixes[table.id].failed_checks}


def test_the_second_reading_of_a_scan_agrees_across_ce_and_plain_latex():
    second = second_client(
        {
            "index": 0,
            "confidence": "high",
            "runs": [
                {"kind": "text", "value": "Since "},
                {"kind": "math", "value": r"\ce{H2O}"},
                {"kind": "text", "value": " we obtain it."},
            ],
        }
    )
    plain = block(
        "paragraph",
        y0=140,
        confidence="medium",
        runs=[text("Since "), math(WATER), text(" we obtain it.")],
    )
    result = run_second(second, plain)
    assert result.reads[0].verdicts[0].agree


def test_a_second_reading_with_a_bad_ce_is_no_agreement():
    second = second_client(
        {
            "index": 0,
            "confidence": "high",
            "runs": [
                {"kind": "text", "value": "Since "},
                {"kind": "math", "value": BAD},
                {"kind": "text", "value": " we obtain it."},
            ],
        }
    )
    plain = block(
        "paragraph", confidence="medium", runs=[text("Since "), math("NO"), text(" we obtain it.")]
    )
    assert not run_second(second, plain).reads[0].verdicts[0].agree


# ------------------------------------------------------------------------------ tables


def grid_with(latex: str, mathml: str | None) -> TableGrid:
    return TableGrid(
        rows=2,
        cols=1,
        cells=[
            TableCell(row=0, col=0, text="Species", header="col"),
            TableCell(row=1, col=0, text="the ion Zn", latex=latex, mathml=mathml),
        ],
        header_rows=1,
        header_cols=0,
    )


def test_a_cell_of_words_and_ce_formulas_is_words_and_maths_in_the_html():
    latex = r"\text{the ion } \ce{Zn^2+(aq, 0.10 M)}"
    html = render_table(grid_with(latex, convert(r"x")), id_prefix="p1-e3")
    assert "<math" in html and "the ion" in html
    assert "(aq,\xa00.10\xa0M)" in html and r"\ce" not in html


def test_a_cell_with_a_bad_ce_is_written_whole_and_never_as_a_converted_guess():
    latex = r"\text{the ion } " + BAD
    html = render_table(grid_with(latex, convert(r"x")), id_prefix="p1-e3")
    # the cell fell back to its whole MathML, not to words and a half-read formula
    assert html.count("<math") == 1 and "the ion" not in html


# ------------------------------------------------------------------------------ page math


def test_a_page_math_ce_expression_is_written_out_and_a_bad_one_is_left_to_fail():
    rows = [row("L0", "the water H2O here"), row("L1", "and NO here")]
    out = PageMathOutput(
        expressions=[
            expression([("L0", 10, "H2O")], latex=r"\ce{H2O}"),
            expression([("L1", 4, "NO")], latex=BAD),
        ]
    )
    spans, _ = page_math.verify(out, rows)
    assert [s.latex for s in spans] == [WATER, BAD]
    assert page_math.latex_stream(spans[0].latex) is not None
    other: Any = expression([("L0", 10, "H2O")], role="exercise_or_item_number", latex=r"\ce{H2O}")
    spans, _ = page_math.verify(PageMathOutput(expressions=[other]), rows)
    assert [s.latex for s in spans] == [r"\ce{H2O}"]  # a text role is not maths
