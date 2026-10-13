"""pipeline.notices: the notices that follow from the fixes, merged with the others.

A notice never adds a decision, so these tests also check that the decision count of a
result is the same with or without its notices.
"""

from __future__ import annotations

from a11y_stem.core import triage
from a11y_stem.core.types import (
    NOTICE_CODES,
    DocumentResult,
    DocumentStats,
    Element,
    FigureFix,
    Fix,
    Kind,
    ModelCall,
    Notice,
    Status,
)
from a11y_stem.pipeline import notices
from a11y_stem.web.messages import NOTICE_MESSAGES


def el(eid: str, kind: Kind, page: int, order: int) -> Element:
    return Element(id=eid, kind=kind, page=page, bbox=(0, 0, 1, 1), order=order)


# Described with long descriptions on: the figure step's long prompt made the proposal
# (`figure_step.long_description_missing` reads it from the calls).
LONG_CALL = ModelCall(step="figure", model="m", prompt_version="figure_long-0000")


def figure(eid: str, figure_type="chart", status=Status.ACCEPTED, long=True, **kw) -> Fix:
    return Fix(element_id=eid, kind=Kind.FIGURE, status=status, confidence=0.9,
               figure=FigureFix(figure_type=figure_type, alt="A rising line.", **kw),
               calls=[LONG_CALL] if long else [])  # fmt: skip


def failed(eid: str, kind: Kind, reason: str) -> Fix:
    return Fix(element_id=eid, kind=kind, status=Status.FAILED, reason=reason)


ELEMENTS = [
    el("p1-e0", Kind.TABLE, 0, 0),
    el("p1-e1", Kind.FIGURE, 0, 1),
    el("p2-e0", Kind.FIGURE, 1, 2),
    el("p3-e0", Kind.FIGURE, 2, 3),
    el("p3-e1", Kind.FIGURE, 2, 4),
]


def test_every_derived_code_is_known_and_has_a_sentence():
    assert notices.DERIVED_CODES <= NOTICE_CODES
    assert all(code in NOTICE_MESSAGES for code in notices.DERIVED_CODES)


def test_notices_follow_from_the_fixes():
    fixes = [
        failed("p1-e0", Kind.TABLE, "table_not_rebuilt"),
        figure("p1-e1", colour_only=True),
        failed("p2-e0", Kind.FIGURE, "scanned_page"),
        figure("p3-e0", figure_type="diagram"),
        figure("p3-e1", figure_type="photo"),
    ]
    found = notices.from_fixes(ELEMENTS, fixes, long_descriptions=True)
    assert [(n.code, n.page, n.element_id, n.review) for n in found] == [
        ("table_not_rebuilt", 0, "p1-e0", "required"),
        ("colour_only", 0, "p1-e1", "optional"),
        ("long_description_missing", 0, "p1-e1", "optional"),
        ("scanned_page", 1, "p2-e0", "required"),
        ("long_description_missing", 2, "p3-e0", "optional"),
    ]


def test_without_long_descriptions_a_chart_without_one_is_expected():
    found = notices.from_fixes(ELEMENTS, [figure("p1-e1")], long_descriptions=False)
    assert found == []


def test_a_chart_with_a_long_description_or_a_decorative_one_needs_nothing():
    fixes = [
        figure("p1-e1", long_description="The line rises from 0 to 10 m."),
        figure("p3-e0", figure_type="diagram", decorative=True),
    ]
    assert notices.from_fixes(ELEMENTS, fixes, long_descriptions=True) == []


def test_a_table_is_not_rebuilt_while_its_image_is_shown():
    rebuilt = Fix(element_id="p1-e0", kind=Kind.TABLE, status=Status.EDITED)
    kept = Fix(element_id="p1-e0", kind=Kind.TABLE, status=Status.REJECTED)
    stopped = failed("p1-e0", Kind.TABLE, "budget_daily")
    assert notices.from_fixes(ELEMENTS, [rebuilt], long_descriptions=False) == []
    for fix in (kept, stopped):
        found = notices.from_fixes(ELEMENTS, [fix], long_descriptions=False)
        assert [n.code for n in found] == ["table_not_rebuilt"]


def test_merge_keeps_one_notice_per_code_and_element_and_adds_page_counts():
    merged = notices.merge(
        [Notice(code="colour_only", page=0, element_id="p1-e1")],
        [
            Notice(code="colour_only", page=0, element_id="p1-e1", review="none"),
            Notice(code="colour_reference", page=2, count=2),
            Notice(code="colour_reference", page=2, count=3),
            Notice(code="colour_reference", page=1),
        ],
    )
    assert [(n.code, n.page, n.element_id, n.count, n.review) for n in merged] == [
        ("colour_only", 0, "p1-e1", 1, "optional"),
        ("colour_reference", 2, None, 5, "optional"),
        ("colour_reference", 1, None, 1, "optional"),
    ]


def test_merge_does_not_change_its_inputs():
    page_notice = Notice(code="footnote", page=0, count=2, review="none")
    notices.merge([page_notice], [Notice(code="footnote", page=0, review="none")])
    assert page_notice.count == 2


def test_order_is_whole_document_first_then_page_and_reading_order():
    found = notices.ordered(
        [
            Notice(code="colour_only", page=2, element_id="p3-e1"),
            Notice(code="colour_reference", page=2),
            Notice(code="lang_detected"),
            Notice(code="colour_only", page=2, element_id="p3-e0"),
            Notice(code="table_not_rebuilt", page=0, element_id="p1-e0", review="required"),
        ],
        ELEMENTS,
    )
    assert [(n.code, n.element_id) for n in found] == [
        ("lang_detected", None),
        ("table_not_rebuilt", "p1-e0"),
        ("colour_reference", None),
        ("colour_only", "p3-e0"),
        ("colour_only", "p3-e1"),
    ]


def result(fixes: list[Fix], stored: list[Notice]) -> DocumentResult:
    return DocumentResult(
        pipeline_version="0.4.0",
        elements=ELEMENTS,
        fixes=fixes,
        stats=DocumentStats(pages=3, cost_usd=0, latency_ms=0, model_calls=0),
        notices=stored,
    )


def test_current_notices_follow_the_fixes_as_they_are_now():
    """A notice stored at conversion time for a table the reviewer later rebuilt is gone;
    the text pass's notices stay as they were."""
    stored = [
        Notice(code="table_not_rebuilt", page=0, element_id="p1-e0", review="required"),
        Notice(code="lang_detected"),
        Notice(code="long_description_missing", page=2, element_id="p3-e0"),
    ]
    rebuilt = Fix(element_id="p1-e0", kind=Kind.TABLE, status=Status.EDITED, reviewed=True)
    described = figure("p3-e0", figure_type="diagram", long_description="Two carts.")
    still_missing = figure("p3-e1", figure_type="chart")
    now = notices.current(result([rebuilt, described, still_missing], stored))
    assert [(n.code, n.element_id) for n in now] == [
        ("lang_detected", None),
        ("long_description_missing", "p3-e1"),
    ]


def test_current_notices_of_a_result_without_any_are_empty():
    """A chart described with long descriptions off has nothing to report."""
    assert notices.current(result([figure("p1-e1", long=False)], [])) == []


def test_notices_never_change_the_decision_count():
    fixes = [failed("p1-e0", Kind.TABLE, "table_not_rebuilt"), figure("p1-e1", colour_only=True)]
    with_notices = result(fixes, notices.from_fixes(ELEMENTS, fixes, long_descriptions=True))
    assert triage.open_decisions(with_notices.fixes) == 1
    assert triage.required_decisions(with_notices.fixes) == triage.required_decisions(fixes)
    assert notices.section(with_notices.notices[0]) == "not_handled"
    assert notices.section(with_notices.notices[1]) == "worth_checking"


# --------------------------------------------------------------------------- layout facts


def code_and_footnote_pdf(code_font: str = "cour") -> bytes:
    """A handout with a Python loop set in Courier and a footnote whose mark is raised in
    the text above it."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Loops in Python", fontsize=18, fontname="helv")
    lines = [
        "The loop below adds the numbers from one to ten and prints the total once the loop",
        "has finished. Indentation decides which lines belong to the body of the loop.",
    ]
    y = 102
    for line in lines:
        page.insert_text((72, y), line, fontsize=11, fontname="helv")
        y += 15
    width = pymupdf.get_text_length(lines[-1], fontname="helv", fontsize=11)
    page.insert_text((72 + width + 0.5, y - 19), "1", fontsize=7, fontname="helv")
    y += 10
    for line in ["total = 0", "for i in range(1, 11):", "    total += i", "print(total)"]:
        page.insert_text((90, y), line, fontsize=10, fontname=code_font)
        y += 13
    page.insert_text((72, y + 10), "Run it and check that it prints 55.", fontsize=11)
    page.draw_line((72, 720), (200, 720))
    page.insert_text((72, 735), "1 Python counts from zero unless told otherwise.", fontsize=8)
    data = doc.tobytes()
    doc.close()
    return data


def test_layout_lists_a_code_block_and_a_footnote_as_notices_with_no_decision():
    from a11y_stem.pipeline.layout import analyze

    layout = analyze(code_and_footnote_pdf())
    by_id = {e.id: e for e in layout.elements}
    found = [(n.code, n.review, (by_id[n.element_id].text or "")[:9]) for n in layout.notices]
    assert found == [("code_block", "none", "total = 0"), ("footnote", "none", "1 Python ")]
    # Plain text paragraphs, a footnote-sized line without a raised mark: nothing.
    plain = analyze(code_and_footnote_pdf(code_font="helv"))
    assert all(n.code != "code_block" for n in plain.notices)


def test_a_conversion_carries_the_layout_notices_to_the_job_page():
    from a11y_stem.config import Settings
    from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
    from a11y_stem.models.fake import FakeVisionClient
    from a11y_stem.pipeline.run import convert_pdf

    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=100)
    output = convert_pdf(
        code_and_footnote_pdf(),
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        client=FakeVisionClient(budget=budget),
    )
    result = output.result
    codes = [n.code for n in notices.current(result)]
    assert "code_block" in codes and "footnote" in codes
    assert triage.required_decisions(result.fixes) == 0
