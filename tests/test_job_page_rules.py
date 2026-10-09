"""web.job_page: the wording and counting rules of the job page, without the app.

The page tests (test_web_job_page*.py) check what a user sees; these check the rules one
by one: the steps of live progress, the headline of every state, one breakdown whose
parts add up, the before/after card (specific, honest, no score), the reading of a text
layer, the notices lists and the language.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from a11y_stem.core.types import (
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Link,
    Notice,
    SourceFacts,
    Status,
)
from a11y_stem.web import job_page
from a11y_stem.web.outcome import JobOutcome, job_outcome

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


def test_no_progress_yet_is_the_start():
    assert job_page.stage_lines(None) == []
    assert job_page.stage_sentence(None) == "Starting the conversion."
    assert job_page.current_stage({"stages": "broken"}) is None


def test_the_steps_say_what_was_found_and_how_far_each_stage_is():
    counts = {"equation": 21, "figure": 7, "heading": 52, "paragraph": 80}
    state = progress(layout=(16, 16, counts), equations=(14, 21), figures=(7, 7))
    lines = [(line.text, line.state) for line in job_page.stage_lines(state)]
    assert lines == [
        ("Reading the layout: 16 pages", "done"),
        ("Found 21 equations, 7 figures and 52 headings", "done"),
        ("Equations: 14 of 21 read and checked against your PDF's text", "now"),
        ("Figures: 7 of 7 described", "done"),
    ]
    assert job_page.current_stage(state) == "equations"
    assert job_page.stage_sentence(state) == (
        "Reading the equations and checking each against your PDF's text."
    )


def test_the_status_sentence_changes_per_step_not_per_element():
    """The status box is a live region: it must not change while the counts move."""
    first = progress(layout=(1, 1), equations=(1, 30), figures=(0, 4))
    later = progress(layout=(1, 1), equations=(17, 30), figures=(3, 4))
    assert job_page.stage_sentence(first) == job_page.stage_sentence(later)
    assert job_page.stage_lines(first) != job_page.stage_lines(later)


def test_the_last_steps_follow_the_model_work():
    state = progress(layout=(1, 1), equations=(3, 3), assemble=(0, 0), checks=(0, 0))
    assert job_page.current_stage(state) == "checks"
    texts = [line.text for line in job_page.stage_lines(state)]
    assert texts[-2:] == ["Building the HTML file", "Running the structure checks"]
    assert [line.state for line in job_page.stage_lines(state)][-2:] == ["done", "now"]


@pytest.mark.parametrize(
    ("ahead", "yours", "seconds", "sentence"),
    [
        (0, 0, 20, "Your document is next in line, less than a minute to go."),
        (1, 0, 150, "1 document ahead of yours, about 2 minutes to go."),
        (4, 2, 600, "4 documents ahead of yours (2 are yours), about 10 minutes to go."),
        (2, 1, None, "2 documents ahead of yours (1 is yours)."),
    ],
)
def test_the_wait_says_where_and_how_long_and_that_leaving_is_fine(ahead, yours, seconds,
                                                                   sentence):  # fmt: skip
    text = job_page.queue_sentence(ahead, yours, seconds)
    assert text == sentence.removesuffix(".") + ". You can leave this page; it keeps its place."


# --------------------------------------------------------------------------- counts


def fix(eid: str, kind=Kind.EQUATION, status=Status.ACCEPTED, **kw) -> Fix:
    return Fix(element_id=eid, kind=kind, status=status, **kw)


def test_the_breakdown_adds_up_and_counts_decisions_like_the_review_screen():
    fixes = [
        fix("a"),
        fix("b", review="optional"),  # a note
        fix("c", status=Status.NEEDS_REVIEW, review="required"),
        fix("h1", Kind.HEADING, Status.NEEDS_REVIEW, review="required", group_key="hg-h1"),
        fix("h2", Kind.HEADING, Status.NEEDS_REVIEW, review="required", group_key="hg-h1"),
        fix("d", status=Status.REJECTED, reviewed=True, review="required"),
        fix("e", status=Status.ACCEPTED, reviewed=True, review="required"),
        fix("f", status=Status.FAILED, reason="model_timeout", review="required"),
    ]
    counts = job_page.breakdown(fixes)
    assert counts.waiting + counts.accepted + counts.decided == counts.items == 8
    assert (counts.waiting, counts.decisions) == (4, 3)  # the heading group is one
    assert (counts.accepted, counts.notes, counts.decided, counts.decided_units) == (2, 1, 2, 2)
    assert counts.as_image == 2 and counts.as_image_kinds == {"equation": 2}


# --------------------------------------------------------------------------- headline


def test_every_state_has_its_own_headline():
    nothing = job_page.Breakdown()
    waiting = job_page.Breakdown(items=3, waiting=2, decisions=2)
    images = job_page.Breakdown(items=3, decided=1, as_image=1, as_image_kinds={"equation": 1})
    stop = JobOutcome("budget_daily", "Stopped early", "Today's budget ran out.", partial=True)
    cases = [
        (("queued", "Next in line.", None, None), ("Waiting", "queued")),
        (("running", "Describing the figures.", None, None), ("Converting", "running")),
        (("failed", "It stopped.", None, None), ("Stopped", "failed")),
        (("done", "", stop, waiting), ("Stopped early", "stopped")),
        (("done", "", None, waiting), ("Converted", "decisions")),
        (("done", "", None, images), ("Ready", "images")),
        (("done", "", None, nothing), ("Ready to share", "ready")),
    ]
    for args, (label, state) in cases:
        headline = job_page.headline(*args)
        assert (headline.label, headline.state) == (label, state), args
    assert job_page.headline("done", "", None, waiting).message == (
        "2 decisions need you before you share it."
    )
    assert job_page.headline("done", "", None, images).message == (
        "1 equation is shown as an image that screen readers cannot read."
    )


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


def test_a_long_reading_is_cut():
    reading = job_page.pdf_reading("x + " * 200)
    assert len(reading) <= job_page.MAX_READING_CHARS + 4 and reading.endswith(" ...")


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


def test_results_measured_before_source_facts_have_no_card():
    assert job_page.before_after(card_result(None)) is None


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
    assert rows["follow the links"].html == "Not yet: links are plain text in the HTML."
    for row in card.rows:
        for text in (row.pdf, row.html):
            assert "%" not in text and "score" not in text.lower()
            assert not re.search(r"\b0 (?:readable|of \d+ readable)", text)
            assert "unreadable" not in text.lower()


def test_tables_still_waiting_for_a_decision_are_said_so():
    # tables.pdf: five rebuilt tables waiting for review were "5 of 5 tables rebuilt".
    from a11y_stem.core.types import TableCell, TableFix, TableGrid

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


def test_a_language_to_check_is_a_row_and_is_not_called_right():
    result = card_result(UNTAGGED)  # assumed: the PDF says none and none was detected
    card = job_page.before_after(result)
    assert card is not None and len(card.rows) == job_page.MAX_ROWS
    rows = {row.question: row for row in card.rows}
    assert "know which document is open" not in rows  # the title row gives way
    language = rows["hear the right language"]
    assert language.pdf == "No: the PDF does not say, so the screen reader guesses."
    assert language.html == (
        "English (assumed; your PDF did not say and none was detected): check it under "
        "Language below."
    )
    result.source_facts = UNTAGGED.model_copy(update={"has_lang": True})
    card = job_page.before_after(result)
    assert card is not None
    assert "hear the right language" not in {row.question for row in card.rows}


def test_kept_links_are_counted():
    result = card_result(UNTAGGED)
    result.elements[5].links = [Link(start=0, end=3, kind="uri", href="https://openstax.org")]
    card = job_page.before_after(result)
    assert card is not None
    assert {r.question: r for r in card.rows}["follow the links"].html == "Yes: 1 link."


def test_examples_are_picked_by_the_structure_their_text_layer_loses():
    card = job_page.before_after(card_result(UNTAGGED))
    assert card is not None
    assert [e.element_id for e in card.examples] == ["e1", "e2"]  # x = 5 loses nothing
    power = card.examples[0]
    assert power.title == "Equation (3.1), page 1"
    assert power.pdf_reading == "lambda 2 minus 5 lambda equals 0"
    assert power.html_reading == "lambda squared minus 5 lambda equals 0"
    assert power.lost == "The power is gone."
    assert card.examples[1].title == "Equation on page 1"
    assert card.examples[1].lost == "The subscript is gone."


def test_bold_lost_in_the_reading_is_named():
    result = card_result(UNTAGGED)
    result.elements[1].text = "\U0001d42f0"
    fixes = result.fixes
    fixes[1] = eq_fix("e2", math('<msub><mi mathvariant="bold">v</mi><mn>0</mn></msub>'),
                      "bold v sub 0")  # fmt: skip
    card = job_page.before_after(result)
    assert card is not None
    example = next(e for e in card.examples if e.element_id == "e2")
    assert example.lost == "The subscript and the bold of the vectors are gone."
    assert example.pdf_reading == "v 0"


def test_at_most_four_examples():
    result = card_result(UNTAGGED)
    for i in range(6):
        result.elements.append(el(f"x{i}", Kind.EQUATION, 10 + i, text=f"a{i}/b"))
        result.fixes.append(
            eq_fix(f"x{i}", math("<mfrac><mi>a</mi><mi>b</mi></mfrac>"), "a over b")
        )
    card = job_page.before_after(result)
    assert card is not None and len(card.examples) == job_page.MAX_EXAMPLES


def test_each_kind_of_loss_is_shown_before_any_twice():
    result = card_result(UNTAGGED)
    for i in range(3):  # three fractions, each earlier in the document than the others
        result.elements.append(el(f"x{i}", Kind.EQUATION, -10 + i, text=f"a{i}/b"))
        result.fixes.append(
            eq_fix(f"x{i}", math("<mfrac><mi>a</mi><mi>b</mi></mfrac>"), "a over b")
        )
    card = job_page.before_after(result)
    assert card is not None
    # Ranked, the three fractions come before the subscript (they are earlier); shown,
    # the subscript comes before a second fraction.
    assert [example.lost for example in card.examples] == [
        "The power is gone.",
        "The fraction bar is gone.",
        "The subscript is gone.",
        "The fraction bar is gone.",
    ]


# --------------------------------------------------------------------------- notices


def test_notices_are_listed_where_they_belong_with_their_decision():
    result = card_result(UNTAGGED)
    result.elements.append(el("t1", Kind.TABLE, 9))
    result.fixes.append(Fix(element_id="t1", kind=Kind.TABLE, status=Status.FAILED,
                            reason="table_not_rebuilt", review="required"))  # fmt: skip
    result.notices = [Notice(code="lang_detected"), Notice(code="footnote", page=0, count=2,
                                                            review="none")]  # fmt: skip
    lists = job_page.notice_lists(result)
    assert [(n.where, n.code, n.decision) for n in lists.not_handled] == [
        ("Page 1", "footnote", None),
        ("Page 1", "table_not_rebuilt", "open"),
    ]
    assert [(n.where, n.code) for n in lists.worth_checking] == [
        ("The whole document", "lang_detected")
    ]
    assert lists.not_handled[0].count == 2
    assert lists.not_handled[1].text.startswith("A table is kept as an image")
    assert lists.total == 3


# --------------------------------------------------------------------------- language


def test_the_language_line_says_where_the_language_came_from():
    result = card_result(UNTAGGED)
    assert job_page.language_line(result) == (
        "English (assumed; your PDF did not say and none was detected)"
    )
    assert job_page.language_needs_check(result)
    result.source_facts = UNTAGGED.model_copy(update={"has_lang": True})
    assert job_page.language_line(result) == "English (from your PDF)"  # before detection
    result.lang_source = "detected"
    assert job_page.language_line(result) == "English (detected; your PDF did not say)"
    result.lang, result.lang_source = "pt-BR", "user"
    assert job_page.language_line(result) == "Portuguese (pt-BR) (you chose it)"
    assert not job_page.language_needs_check(result)
    result.source_facts = None
    result.lang, result.lang_source = "en", "default"
    assert job_page.language_line(result) == "English"  # an older result: not known


def test_setting_the_language_answers_its_notices_and_unmarks_passages():
    """The job page's control uses the text work's rule (`set_document_language`)."""
    from a11y_stem.pipeline.text_pass import set_document_language

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


def test_the_finish_sentence_says_who_decided_and_when():
    made = job_page.Finish(decided_units=3, last_decision=date(2026, 10, 9), notes=0,
                           not_handled=0)  # fmt: skip
    assert made.sentence == "You made 3 decisions on 9 October."
    none = job_page.Finish(decided_units=0, last_decision=None, notes=0, not_handled=0)
    assert none.sentence == "Nothing in this file needed your decision."


def test_the_draft_sentence_says_what_students_get():
    fixes = [
        fix("a", status=Status.NEEDS_REVIEW, review="required"),
        fix("b", Kind.FIGURE, Status.NEEDS_REVIEW, review="required"),
        fix("c", status=Status.ABSTAINED, review="required"),
    ]
    assert job_page.draft_sentence(fixes) == (
        "For 1 equation and 1 figure, students will get the tool's suggestion as it is. "
        "1 equation is an image marked “Not converted”."
    )


def test_plural_losses_take_are():
    assert job_page._lost_sentence(["rows"]) == "The rows and columns are gone."
    assert job_page._lost_sentence(["limits"]) == "The limits above and below are gone."
    assert job_page._lost_sentence(["power"]) == "The power is gone."
