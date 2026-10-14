"""What the review screen says differs in an equation (web.compare), where it crops the
page, and the queue of decisions it walks through (web.queue)."""

from __future__ import annotations

import pytest

from a11y_stem.core.types import (
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
)
from a11y_stem.pipeline.validate import safe_latex_to_mathml
from a11y_stem.web import queue
from a11y_stem.web.compare import (
    COMPARE_PX_PER_PT,
    band_clip,
    compare_equation,
    compare_geometry,
    context_clip,
    suggest_fix,
)
from a11y_stem.web.mathml import sanitize_mathml

BOLD_D, BOLD_ZERO = "\U0001d41d", "\U0001d7ce"  # MATHEMATICAL BOLD SMALL D, DIGIT ZERO
ITALIC_V, ITALIC_D, ITALIC_T = "\U0001d463", "\U0001d451", "\U0001d461"  # math italic
BOLD_V, BOLD_X, BOLD_Y = "\U0001d42f", "\U0001d431", "\U0001d432"  # MATHEMATICAL BOLD SMALL
DOUBLE_R = "\u211d"  # DOUBLE-STRUCK CAPITAL R, the reals


def equation(text: str | None, latex: str, *, regular: str | None = None, display="inline"):
    element = Element(
        id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(100, 100, 130, 112), order=1,
        text=text, font_size=10, display=display, regular_letters=regular,
    )  # fmt: skip
    fix = Fix(
        element_id="p1-e1",
        kind=Kind.EQUATION,
        status=Status.NEEDS_REVIEW,
        equation=EquationFix(latex=latex, mathml=safe_latex_to_mathml(latex, display), speech=""),
    )
    return element, fix


# --------------------------------------------------------------------------- compare


def test_a_vector_read_as_a_scalar_is_a_meaning_level_style_row():
    c = compare_equation(*equation("𝛚", r"\omega"))
    assert c.summary == "The ω is bold in the PDF (a vector) but plain in the proposal."
    assert [(r.what, r.symbol, r.pdf, r.proposal, r.meaning) for r in c.rows] == [
        ("Style", "𝛚", "bold", "plain", True)
    ]
    assert c.pdf_marks == c.proposal_marks == {"ω"}


def test_a_bold_subscript_is_a_cosmetic_row_and_same_letters_are_listed():
    c = compare_equation(*equation(BOLD_D + BOLD_ZERO, r"\mathbf{d}_0"))
    assert [(r.symbol, r.meaning) for r in c.rows] == [(BOLD_ZERO, False)]
    assert c.summary == (
        "The 0 is bold in the PDF but plain in the proposal, which does not change the meaning."
    )
    assert c.same == ["d (bold)"]


def test_a_scalar_known_to_be_plain_read_as_a_vector():
    c = compare_equation(*equation(f"{ITALIC_V} = {ITALIC_D}/{ITALIC_T}", r"\mathbf{v} = d / t"))
    assert c.summary == "The v is plain in the PDF but bold (a vector) in the proposal."


def test_a_wrong_digit_is_a_symbol_row_with_one_sentence():
    c = compare_equation(*equation("g = 9.80", r"g = 9.30"))
    assert c.summary == "The PDF has 8 where the proposal has 3."
    assert {(r.symbol, r.pdf, r.proposal) for r in c.rows} == {
        ("8", "once", "not there"),
        ("3", "not there", "once"),
    }
    assert c.pdf_marks == {"8"} and c.proposal_marks == {"3"}


def test_a_dropped_variable_and_a_dropped_sign():
    c = compare_equation(*equation("e−λt", r"e^{\lambda}"))
    assert {(r.what, r.symbol) for r in c.rows} == {("Symbol", "t"), ("Sign", "−")}
    assert c.summary == "The PDF has t, which the proposal does not."
    c = compare_equation(*equation("x = −b", r"x = b"))
    assert c.summary == "The PDF has 1 minus sign; the proposal has none."


def test_matching_equations_and_missing_text_layers_have_no_rows():
    assert compare_equation(*equation("v = d/t", r"v = d / t")).rows == []
    assert not compare_equation(*equation(None, r"x")).has_text_layer
    assert not compare_equation(*equation("=", r"x")).has_text_layer
    element, fix = equation("x", "x")
    fix.equation = EquationFix(latex="x", mathml="<math><mi>x</mi>", speech="")  # broken
    assert compare_equation(element, fix).rows == []
    fix.equation = None
    assert compare_equation(element, fix).rows == []


def test_a_style_the_text_layer_cannot_show_is_not_a_difference():
    """A double-struck R from an MSBM font is a plain "R" in the PDF's text layer, so a
    proposal with \\mathbb{R} differs from the text layer only in a style the text layer
    cannot carry: the screen must not say the PDF has a plain R (its image shows it
    double-struck)."""
    for text, latex in (
        ("Rn", r"\mathbb{R}^{n}"),
        (f"{BOLD_V} ∈Rn", r"\mathbf{v} \in \mathbb{R}^{n}"),
    ):
        c = compare_equation(*equation(text, latex, regular="n"))
        assert not [r for r in c.rows if r.what == "Style"], text
        assert c.unseen == [f"double-struck {DOUBLE_R}"]
        assert c.summary == (
            f"The proposal has a double-struck {DOUBLE_R}. The PDF's text cannot show that "
            "style, so check it in the image."
        )
        assert "R" not in c.pdf_marks and "R" not in c.proposal_marks  # nothing outlined
    # A letter known to come from a regular text font really is plain there: a row.
    c = compare_equation(*equation("Rn", r"\mathbb{R}^{n}", regular="Rn"))
    assert [(r.symbol, r.pdf, r.proposal) for r in c.rows] == [("R", "plain", "double-struck")]
    assert c.unseen == []


@pytest.mark.parametrize(
    ("text", "latex", "regular", "label", "fixed"),
    [
        ("𝛚", r"\omega", None, "Use bold ω", r"\boldsymbol{\omega}"),
        (f"{BOLD_V} = 3", r"v = 3", None, "Use bold v", r"\mathbf{v} = 3"),
        (f"{BOLD_V} = {BOLD_V}0", r"v = v_0", None, "Use bold v", r"\mathbf{v} = \mathbf{v}_0"),
        (f"{ITALIC_V} = 3", r"\mathbf{v} = 3", None, "Use plain v", "v = 3"),
        (DOUBLE_R, "R", None, "Use double-struck R", r"\mathbb{R}"),
    ],
)
def test_a_meaning_level_style_difference_gets_a_one_click_fix(text, latex, regular, label, fixed):
    element, fix = equation(text, latex, regular=regular)
    c = compare_equation(element, fix)
    assert c.meaning
    suggestion = suggest_fix(element, fix, c)
    assert suggestion is not None
    assert (suggestion.label, suggestion.latex) == (label, fixed)
    # What it saves compares clean: no meaning-level difference left.
    after = Fix(
        element_id=fix.element_id,
        kind=Kind.EQUATION,
        status=Status.NEEDS_REVIEW,
        equation=EquationFix(latex=fixed, mathml=safe_latex_to_mathml(fixed, "inline"), speech=""),
    )
    assert not compare_equation(element, after).meaning


@pytest.mark.parametrize(
    ("text", "latex"),
    [
        ("g = 9.80", r"g = 9.30"),  # a symbol, not a style: no rewrite can be sure
        (f"{BOLD_V} + v", r"v + v"),  # bold in one place, plain in another: which one?
        (f"{BOLD_D}0", r"\mathbf{d}_0"),  # cosmetic only
        ("𝛚", r"\text{ω}"),  # the letter is only inside words: nothing to wrap
        (f"{BOLD_X} = {BOLD_Y} \u2212 z", r"x = y + z"),  # more than one difference that matters
    ],
)
def test_no_one_click_fix_unless_it_is_one_style_and_it_checks_out(text, latex):
    element, fix = equation(text, latex)
    assert suggest_fix(element, fix, compare_equation(element, fix)) is None


def test_flagged_tokens_are_marked_in_the_proposal_only_by_our_own_class():
    mathml = safe_latex_to_mathml(r"\mathbf{d} = \omega t", "inline")
    marked = str(sanitize_mathml(mathml, flag={"𝛚"}))
    assert '<mi class="flagged">ω</mi>' in marked
    assert marked.count('class="flagged"') == 1
    assert "class=" not in str(sanitize_mathml(mathml))
    # A class in the input is never passed through.
    assert "evil" not in str(sanitize_mathml('<math><mi class="evil">x</mi></math>', flag={"x"}))


def test_the_compare_crop_shows_words_on_each_side_and_stays_on_the_page():
    element, _ = equation("x", "x")
    x0, y0, x1, y1 = context_clip(element, (612, 792))
    assert x0 == 60 and x1 == 170  # 4 font sizes either side of an inline equation
    assert 97 < y0 < 100 and 112 < y1 < 115
    element.bbox = (5, 5, 20, 15)
    assert context_clip(element, (612, 792))[:2] == (0.0, 3.0)
    element.bbox = (600, 780, 611, 791)
    assert context_clip(element, (612, 792))[2:] == (612, 792)
    element.display = "block"
    element.bbox = (100, 100, 300, 120)
    assert context_clip(element, None)[0] == 85  # 1.5 font sizes for a displayed one


def test_the_proposal_is_drawn_at_the_crops_scale():
    element, _ = equation("x", "x")
    clip = (60.0, 98.0, 170.0, 114.0)
    geometry = compare_geometry(element, clip, max_side_px=1600)
    assert geometry.max_width_px == pytest.approx(110 * COMPARE_PX_PER_PT)
    assert geometry.font_cqw == pytest.approx(100 * 10 / 110, abs=0.001)  # 10 pt of 110 pt
    assert geometry.left_cqw == pytest.approx(100 * 40 / 110, abs=0.001)
    assert geometry.zoom == 2 * COMPARE_PX_PER_PT
    wide = compare_geometry(element, (0.0, 0.0, 600.0, 20.0), max_side_px=1600)
    assert wide.zoom == pytest.approx(1599 / 600)


def test_a_headings_band_starts_left_of_it_and_takes_lines_around_it():
    heading = Element(id="p1-e3", kind=Kind.HEADING, page=0, bbox=(72, 170, 155, 183),
                      order=3, font_size=10)  # fmt: skip
    x0, y0, x1, y1 = band_clip(heading, (612, 792))
    assert x0 == 57 and y0 == 154 and y1 == 213
    assert x1 == pytest.approx(57 + 0.55 * 612)


# --------------------------------------------------------------------------- queue


def _result(fixes: list[Fix], pages: dict[str, int]) -> DocumentResult:
    elements = [
        Element(id=f.element_id, kind=f.kind, page=pages[f.element_id], bbox=(0, 0, 1, 1), order=i)
        for i, f in enumerate(fixes)
    ]
    stats = DocumentStats(pages=3, cost_usd=0, latency_ms=0, model_calls=0)
    return DocumentResult(pipeline_version="t", elements=elements, fixes=fixes, stats=stats)


def _heading(eid: str, status=Status.NEEDS_REVIEW, group: str | None = None, **kw) -> Fix:
    heading = HeadingFix(level=2, text="Teacher Support")
    return Fix(
        element_id=eid, kind=Kind.HEADING, status=status, confidence=0.6, heading=heading,
        group_key=group, **kw,
    )  # fmt: skip


def _equation(eid: str, status=Status.NEEDS_REVIEW, **kw) -> Fix:
    return Fix(element_id=eid, kind=Kind.EQUATION, status=status, **kw)


@pytest.fixture
def doc() -> DocumentResult:
    fixes = [
        _heading("p1-e0", group="hg-p1-e0", group_size=3),
        _equation("p2-e1"),
        _heading("p2-e2", group="hg-p1-e0", group_size=3),
        _equation("p1-e5", Status.ACCEPTED, review="optional"),
        _heading("p3-e0", group="hg-p1-e0", group_size=3),
        _equation("p3-e1", Status.FAILED, reason="model_timeout"),
        _equation("p3-e2", Status.ACCEPTED),
    ]
    pages = {"p1-e0": 0, "p2-e1": 1, "p2-e2": 1, "p1-e5": 0, "p3-e0": 2, "p3-e1": 2, "p3-e2": 2}
    return _result(fixes, pages)


def test_decisions_are_numbered_equations_first_and_a_group_is_one(doc):
    decisions = queue.decisions(doc)
    assert [(d.number, d.key, len(d.fixes)) for d in decisions] == [
        (1, "p2-e1", 1),
        (2, "p3-e1", 1),
        (3, "hg-p1-e0", 3),
    ]
    assert all(d.open for d in decisions)
    assert queue.find(decisions, "p3-e0") is decisions[2]  # any member names the group
    assert queue.find(decisions, "p1-e5") is None  # an optional note is not a decision


def test_numbers_stay_while_decisions_are_made_and_next_wraps(doc):
    doc.fixes[1].status = Status.ACCEPTED  # decision 1, p2-e1
    doc.fixes[1].reviewed = True
    decisions = queue.decisions(doc)
    assert [d.number for d in decisions] == [1, 2, 3]  # still asked for, now decided
    assert not decisions[0].open
    assert queue.first_open(decisions) is decisions[1]
    assert queue.next_open(decisions, decisions[2]) is decisions[1]  # wraps round
    assert queue.next_open(decisions, decisions[1]) is decisions[2]
    for fix in doc.fixes:
        if fix.pending:
            fix.status = Status.REJECTED
    assert queue.next_open(queue.decisions(doc), None) is None


def test_a_group_with_one_member_decided_is_still_open_and_led_by_the_next(doc):
    doc.fixes[0].status = Status.REJECTED
    group = queue.decisions(doc)[2]
    assert group.open and group.lead.element_id == "p2-e2"


def test_pages_count_each_open_decision_once(doc):
    decisions = queue.decisions(doc)
    elements = {e.id: e for e in doc.elements}
    # The group's first heading is on page 1; the equations on pages 2 and 3.
    assert queue.open_by_page(decisions, elements) == {0: 1, 1: 1, 2: 1}
    assert sum(queue.open_by_page(decisions, elements).values()) == len(decisions)


def test_results_stored_before_tiers_count_what_is_waiting():
    old = _result(
        [Fix.model_validate({"element_id": "p1-e0", "kind": "equation", "status": "abstained"})],
        {"p1-e0": 0},
    )
    assert [d.key for d in queue.decisions(old)] == ["p1-e0"]


def test_a_swapped_number_is_a_number_row_with_one_sentence():
    c = compare_equation(*equation("v = 36 m/s", r"v = 63\ \text{m/s}"))
    assert c.summary == "The PDF has the number 36 where the proposal has 63."
    assert [(r.what, r.symbol, r.pdf, r.proposal, r.meaning) for r in c.rows] == [
        ("Number", "36", "there", "not there", True),
        ("Number", "63", "not there", "there", True),
    ]
    assert c.said["number"] == [c.summary]
    assert c.pdf_marks == {"3", "6"} and c.proposal_marks == {"6", "3"}


def test_a_changed_case_is_a_case_row_with_one_sentence():
    c = compare_equation(*equation("\U0001d464/\U0001d454", r"W/g"))
    assert c.summary == "The PDF has a small w; the proposal has a capital W."
    assert [(r.what, r.symbol, r.pdf, r.proposal) for r in c.rows] == [
        ("Case", "w", "small once", "capital once")
    ]


def test_numbers_and_case_are_not_compared_when_the_symbols_already_differ():
    c = compare_equation(*equation("g = 9.80", r"g = 9.30"))
    assert {r.what for r in c.rows} == {"Symbol"}
