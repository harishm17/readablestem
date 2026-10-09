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
)
from a11y_stem.web.mathml import sanitize_mathml

BOLD_D, BOLD_ZERO = "\U0001d41d", "\U0001d7ce"  # MATHEMATICAL BOLD SMALL D, DIGIT ZERO
ITALIC_V, ITALIC_D, ITALIC_T = "\U0001d463", "\U0001d451", "\U0001d461"  # math italic


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
