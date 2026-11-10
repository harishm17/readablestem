"""web.job_page: the wording and counting rules of the job page, without the app.

The page tests (test_web_job_page*.py) check what a user sees; these check the rules one
by one: the steps of live progress, the headline of every state, one breakdown whose
parts add up, the before/after card (specific, honest, no score), the reading of a text
layer, the notices lists and the language.
"""

from __future__ import annotations

import re

import pytest

from readablestem.core.types import (
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Notice,
    SourceFacts,
    Status,
)
from readablestem.web import job_page
from readablestem.web.outcome import job_outcome

MATH = "http://www.w3.org/1998/Math/MathML"


def math(body: str) -> str:
    return f'<math xmlns="{MATH}" display="block">{body}</math>'


# --------------------------------------------------------------------------- progress


def progress(**stages: tuple[int, int] | tuple[int, int, dict]) -> dict:
    out = {}
    for stage, value in stages.items():
        done, total, *rest = value
        out[stage] = {
            "stage": stage,
            "done": done,
            "total": total,
            "counts": rest[0] if rest else {},
        }
    return {"stages": out}


# --------------------------------------------------------------------------- counts


def fix(eid: str, kind=Kind.EQUATION, status=Status.ACCEPTED, **kw) -> Fix:
    return Fix(element_id=eid, kind=kind, status=status, **kw)


# --------------------------------------------------------------------------- headline


def outage_result(failed: int, total: int) -> dict:
    fixes = [
        {"element_id": f"e{i}", "kind": "equation", "status": "failed", "reason": "model_api_error"}
        for i in range(failed)
    ] + [
        {"element_id": f"k{i}", "kind": "figure", "status": "accepted"}
        for i in range(total - failed)
    ]
    return {"fixes": fixes}


def test_an_ai_outage_is_not_finished():
    outcome = job_outcome(status="done", error_code=None, result_json=outage_result(3, 4))
    assert outcome is not None and outcome.code == "model_outage" and outcome.partial
    assert outcome.label == "Stopped: the AI service did not answer"
    assert "3 of 4 equations and figures" in outcome.message
    assert "Nothing is wrong with your PDF" in outcome.message
    assert job_outcome(status="done", error_code=None, result_json=outage_result(2, 4)) is None
    assert job_outcome(status="done", error_code=None, result_json=outage_result(0, 4)) is None


# --------------------------------------------------------------------------- reading


@pytest.mark.parametrize(
    ("text", "reading"),
    [
        ("λ2 − 5λ − 2 = 0", "lambda 2 minus 5 lambda minus 2 equals 0"),
        ("\U0001d41d= \U0001d42f\U0001d461+ \U0001d41d0", "d equals vt plus d 0"),
        ("v = 1.0 m/s", "v equals 1.0 m slash s"),
        ("x² + y₁", "x superscript two plus y subscript one"),
        ("Ax = b.", "Ax equals b period"),
    ],
)
def test_a_text_layer_is_read_as_one_flat_line(text, reading):
    assert job_page.pdf_reading(text) == reading


# --------------------------------------------------------------------------- the card


def el(eid: str, kind: Kind, order: int, **kw) -> Element:
    return Element(id=eid, kind=kind, page=0, bbox=(0, 0, 1, 1), order=order, **kw)


def eq_fix(eid: str, mathml: str, speech: str, status=Status.ACCEPTED) -> Fix:
    return Fix(element_id=eid, kind=Kind.EQUATION, status=status, confidence=0.9,
               equation=EquationFix(latex="x", mathml=mathml, speech=speech))  # fmt: skip


def card_result(facts: SourceFacts | None, **kw) -> DocumentResult:
    elements = [
        el("e1", Kind.EQUATION, 1, text="λ2 − 5λ = 0", display="block", equation_label="(3.1)"),
        el("e2", Kind.EQUATION, 2, text="v0", display="inline"),
        el("e3", Kind.EQUATION, 3, text="x = 5", display="block"),
        el("e4", Kind.EQUATION, 4, text="a/b"),
        el("f1", Kind.FIGURE, 5),
        el("h1", Kind.HEADING, 0, text="Vectors"),
        el("l1", Kind.LIST_ITEM, 6, text="one", list_marker="•", list_ordered=False, list_level=1),
        el("l2", Kind.LIST_ITEM, 7, text="two", list_marker="•", list_ordered=False, list_level=1),
    ]
    fixes = [
        eq_fix("e1", math("<msup><mi>λ</mi><mn>2</mn></msup><mo>−</mo><mn>5</mn><mi>λ</mi>"),
               "lambda squared minus 5 lambda equals 0"),
        eq_fix("e2", math("<msub><mi>v</mi><mn>0</mn></msub>"), "v sub 0"),
        eq_fix("e3", math("<mi>x</mi><mo>=</mo><mn>5</mn>"), "x equals 5"),
        Fix(element_id="e4", kind=Kind.EQUATION, status=Status.REJECTED, reviewed=True),
        Fix(element_id="f1", kind=Kind.FIGURE, status=Status.ACCEPTED,
            figure=FigureFix(figure_type="chart", alt="A rising line.")),
        Fix(element_id="h1", kind=Kind.HEADING, status=Status.ACCEPTED,
            heading=HeadingFix(level=1, text="Vectors")),
    ]  # fmt: skip
    return DocumentResult(
        pipeline_version="0.4.0",
        title="Vectors",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0, model_calls=0),
        source_facts=facts,
        **kw,
    )


UNTAGGED = SourceFacts(
    tagged=False, images=1, equations=4, equations_flattened=2, bold_maths_letters=3, links=2
)


def test_the_card_rows_say_what_is_lost_specifically_and_never_score():
    card = job_page.before_after(card_result(UNTAGGED))
    assert card is not None
    rows = {row.question: row for row in card.rows}
    assert len(card.rows) <= job_page.MAX_ROWS
    maths = rows["hear equations with their structure"]
    assert (
        maths.pdf
        == "The symbols are there, on one line. In 2 of 4, the powers and subscripts are gone."
    )
    assert maths.html == "3 of 4 are MathML, read with their structure; 1 is shown as an image."
    assert rows["tell a bold vector from a plain letter"].pdf.startswith("No: 3 bold letters")
    assert rows["hear what a figure shows"].html == "1 of 1 figure has a short description."
    assert rows["jump from section to section"].html == "Yes: 1 heading in one outline."
    assert rows["hear lists as lists"].html == "Yes: 1 list with 2 items."
    # T1-15: the count says how many were kept, not "Yes" or "Not yet".
    assert rows["follow the links"].html == "0 of 2 links kept."
    for row in card.rows:
        for text in (row.pdf, row.html):
            assert "%" not in text and "score" not in text.lower()
            assert not re.search(r"\b0 (?:readable|of \d+ readable)", text)
            assert "unreadable" not in text.lower()


def test_tables_still_waiting_for_a_decision_are_said_so():
    # tables.pdf: five rebuilt tables waiting for review were "5 of 5 tables rebuilt".
    from readablestem.core.types import TableCell, TableFix, TableGrid

    result = card_result(UNTAGGED.model_copy(update={"tables": 2}))
    grid = TableGrid(rows=1, cols=1, cells=[TableCell(row=0, col=0, text="x")])
    result.elements += [el("t1", Kind.TABLE, 8), el("t2", Kind.TABLE, 9)]
    result.fixes += [
        Fix(element_id="t1", kind=Kind.TABLE, status=Status.NEEDS_REVIEW,
            table=TableFix(grid=grid)),
        Fix(element_id="t2", kind=Kind.TABLE, status=Status.FAILED, reason="table_not_rebuilt"),
    ]  # fmt: skip
    card = job_page.before_after(result)
    assert card is not None
    rows = {row.question: row for row in card.rows}
    assert rows["read a table cell by cell"].html == (
        "1 of 2 tables rebuilt as data tables; 1 is shown as an image; 2 wait for your decision."
    )


def test_a_tagged_pdf_is_not_called_unstructured():
    facts = UNTAGGED.model_copy(update={"tagged": True, "images_with_alt": 1})
    card = job_page.before_after(card_result(facts))
    assert card is not None
    rows = {row.question: row for row in card.rows}
    assert rows["hear what a figure shows"].pdf == "1 of 1 picture has alt text."
    assert rows["jump from section to section"].pdf.startswith("Perhaps: your PDF is tagged")
    assert "we did not check" in rows["hear equations with their structure"].pdf


# --------------------------------------------------------------------------- notices


# --------------------------------------------------------------------------- language


def test_setting_the_language_answers_its_notices_and_unmarks_passages():
    """The job page's control uses the text work's rule (`set_document_language`)."""
    from readablestem.pipeline.text_pass import set_document_language

    result = card_result(UNTAGGED, notices=[Notice(code="lang_assumed"), Notice(code="footnote")])
    result.elements[5].lang = "fr"
    changed = set_document_language(result, "fr")
    assert (changed.lang, changed.lang_source) == ("fr", "user")
    assert [n.code for n in changed.notices] == ["footnote"]
    assert changed.elements[5].lang is None
    # The stored result is replaced by the caller, not changed in place.
    assert (result.lang, result.elements[5].lang, len(result.notices)) == ("en", "fr", 2)


def test_language_choices_keep_a_tag_the_list_does_not_have():
    choices = job_page.language_choices("gd")
    assert choices[0] == ("gd", "gd")
    assert ("en", "English") in choices
    names = [name for _, name in job_page.language_choices("en")]
    assert names == sorted(names)


# --------------------------------------------------------------------------- finish line
