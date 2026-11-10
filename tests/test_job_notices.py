"""pipeline.notices: the notices that follow from the fixes, merged with the others.

A notice never adds a decision, so these tests also check that the decision count of a
result is the same with or without its notices.
"""

from __future__ import annotations

from readablestem.core.types import (
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
from readablestem.pipeline import notices


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


def result(fixes: list[Fix], stored: list[Notice]) -> DocumentResult:
    return DocumentResult(
        pipeline_version="0.4.0",
        elements=ELEMENTS,
        fixes=fixes,
        stats=DocumentStats(pages=3, cost_usd=0, latency_ms=0, model_calls=0),
        notices=stored,
    )


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
    from readablestem.pipeline.layout import analyze

    layout = analyze(code_and_footnote_pdf())
    by_id = {e.id: e for e in layout.elements}
    found = [(n.code, n.review, (by_id[n.element_id].text or "")[:9]) for n in layout.notices]
    assert found == [("code_block", "none", "total = 0"), ("footnote", "none", "1 Python ")]
    # Plain text paragraphs, a footnote-sized line without a raised mark: nothing.
    plain = analyze(code_and_footnote_pdf(code_font="helv"))
    assert all(n.code != "code_block" for n in plain.notices)
