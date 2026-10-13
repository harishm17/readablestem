"""The second automated check's note on the review screen and the job page (server-rendered).

The note ("a possible mismatch between the alt text and the image") is listed under
"Optional checks" by policy (ADR 0005), but it is not something small: a wrong alt text
changes what a screen reader user hears. So wherever the page describes the optional
checks, it says this one is worth a look instead of calling it small or "nothing to do".
The browser test (test_web_second_check_browser.py) covers axe and reflow.
"""

from __future__ import annotations

import re

import pytest

from a11y_stem.core import triage
from a11y_stem.core.types import (
    Check,
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    FigureFix,
    Fix,
    Kind,
    Status,
    Verification,
)
from a11y_stem.web.messages import CHECK_MESSAGES
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, upload

NOTE = CHECK_MESSAGES[("figure.second_check", "second_check_mismatch")]
SMALL_LEAD = (
    "These were accepted automatically: the checks found something small that does not "
    "change the meaning."
)
WORTH_A_LOOK = "Optional check (worth a look; it does not hold up the document):"
NOTHING_TO_DO = "Optional check (nothing to do unless you disagree):"


def flagged_figure(eid: str) -> Fix:
    return Fix(
        element_id=eid,
        kind=Kind.FIGURE,
        status=Status.ACCEPTED,
        confidence=0.98,
        figure=FigureFix(figure_type="chart", alt="Bar chart of votes: B has 9."),
        checks=[
            Check(
                name="figure.second_check",
                passed=False,
                severity="warning",
                code=triage.SECOND_CHECK_MISMATCH,
            )
        ],
        review="optional",
        verifications=[
            Verification(
                check="figure_alt",
                probabilities={"ok": 0.2, "factual_error": 0.9},
                flagged=True,
            )
        ],
    )


def long_alt_figure(eid: str) -> Fix:
    return Fix(
        element_id=eid,
        kind=Kind.FIGURE,
        status=Status.ACCEPTED,
        confidence=0.98,
        figure=FigureFix(figure_type="chart", alt="Bar chart of votes. " + "x" * 160),
        checks=[Check(name="alt.length", passed=False, severity="warning", code="too_long")],
        review="optional",
    )


def output(*, second: int, small: int) -> ConversionOutput:
    """One page: an equation that needs a decision, `second` figures with the second
    check's note and `small` figures with a long-alt note (all accepted, optional)."""
    fixes = [
        Fix(
            element_id="p1-e0",
            kind=Kind.EQUATION,
            status=Status.FAILED,
            reason="invalid_latex",
            review="required",
        ),
        *(flagged_figure(f"p1-e{1 + i}") for i in range(second)),
        *(long_alt_figure(f"p1-e{1 + second + i}") for i in range(small)),
    ]
    elements = [
        Element(
            id=f.element_id,
            kind=f.kind,
            page=0,
            bbox=(0, n, 1, n + 1),
            order=n,
            caption="Figure. Results of a class vote." if f.kind is Kind.FIGURE else None,
        )
        for n, f in enumerate(fixes)
    ]
    result = DocumentResult(
        pipeline_version="test",
        title="Second check",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(pages=1, cost_usd=0.01, latency_ms=5, model_calls=3),
        html="<!doctype html><html lang='en'><head><title>t</title></head><body></body></html>",
    )
    return ConversionOutput(result=result, crops={})


@pytest.fixture
def pages(tmp_path):
    """`pages(second=, small=)` -> (review page, job page), whitespace collapsed."""
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)

        def render(*, second: int, small: int) -> tuple[str, str]:
            env.converter.output = output(second=second, small=small)
            job_id = job_id_from(upload(client, make_pdf(1)))
            assert env.run_worker() == job_id
            review = client.get(f"/jobs/{job_id}/review/list")
            job = client.get(f"/jobs/{job_id}")
            assert review.status_code == job.status_code == 200
            return " ".join(review.text.split()), " ".join(job.text.split())

        yield render


def optional_section(page: str) -> str:
    start = page.index('<section id="optional-checks"')
    return page[start : page.index("</section>", start)]


def card(page: str, eid: str) -> str:
    start = page.index(f'<li id="item-{eid}"')
    return page[start : page.index("</li>", start)]


def progress(page: str) -> str:
    start = page.index('<div id="review-progress"')
    return page[start : page.index("</div>", start)]


def test_the_lead_does_not_call_the_second_check_note_small(pages):
    review, _ = pages(second=1, small=2)
    lead = optional_section(review)
    assert SMALL_LEAD not in lead
    assert (
        "These were accepted automatically, and nothing here holds up the document, but you "
        "can still change any of them. For 1 figure, a second automated check found a "
        "possible mismatch between the alt text and the image: worth a look, because a "
        "wrong alt text changes what a screen reader user hears. For the other 2, the "
        "checks found something small that does not change the meaning."
    ) in lead
    assert NOTE in card(review, "p1-e1")
    assert WORTH_A_LOOK in card(review, "p1-e1")
    assert NOTHING_TO_DO in card(review, "p1-e2") and WORTH_A_LOOK not in card(review, "p1-e2")
    # The intro says the same before the reader gets to the section.
    assert (
        "Figures where a second automated check found a possible mismatch between the alt "
        "text and the image are among the optional checks. Those are worth a look"
    ) in review
    assert "Needs your decision (1)" in review  # still optional: never a decision
    # The line at the top, read first and announced after a decision, says so too.
    assert (
        "3 optional checks do not hold anything up, but 1 of them is worth a look."
    ) in progress(review)
    assert "can be looked at if you want" not in progress(review)


def test_only_second_check_notes(pages):
    review, _ = pages(second=2, small=0)
    lead = optional_section(review)
    assert "For 2 figures, a second automated check found a possible mismatch" in lead
    assert "something small" not in lead and "For the other" not in lead
    assert "2 optional checks do not hold anything up, but they are worth a look." in (
        progress(review)
    )


def test_one_other_small_note_reads_as_one(pages):
    review, _ = pages(second=1, small=1)
    assert "For the other one, the checks found something small" in optional_section(review)


def test_a_single_second_check_note_at_the_top(pages):
    review, _ = pages(second=1, small=0)
    assert "1 optional check does not hold anything up, but it is worth a look." in (
        progress(review)
    )


def test_without_a_second_check_note_the_wording_is_as_before(pages):
    review, job = pages(second=0, small=1)
    assert SMALL_LEAD in optional_section(review)
    assert "second automated check" not in review
    assert NOTHING_TO_DO in card(review, "p1-e1")
    assert "1 optional check can be looked at if you want; it does not hold anything up." in (
        progress(review)
    )
    assert re.search(r'<dt>Accepted for you</dt><dd>\d+ <span class="meta">\(1 with a note\)', job)


def test_the_job_page_does_not_say_nothing_to_do_for_a_second_check_note(pages):
    _, job = pages(second=1, small=2)
    assert '<span class="meta">(3 with a note, 1 worth a look)</span>' in job
    assert "nothing to do" not in job


def test_the_wording_rule(pages):
    review, job = pages(second=1, small=1)
    for page in (review, job):
        for word in ("verified", "checked for accuracy", "compl" + "iant"):
            assert word not in page.lower()


def test_second_check_note_is_only_an_unreviewed_optional_flag():
    flagged = flagged_figure("p1-e1")
    assert triage.second_check_note(flagged)
    assert not triage.second_check_note(long_alt_figure("p1-e2"))
    assert not triage.second_check_note(flagged.model_copy(update={"reviewed": True}))
    passed = flagged.model_copy(
        update={"checks": [Check(name="figure.second_check", passed=True, severity="warning")]}
    )
    assert not triage.second_check_note(passed)


def test_split_view_lists_the_second_check_note_as_worth_a_look(tmp_path):
    """The Split View's folded list of optional checks gives each one short line; the
    second check's comes first even when the figure has a small note too."""
    both = flagged_figure("p1-e1")
    both.checks.insert(
        0, Check(name="alt.length", passed=False, severity="warning", code="too_long")
    )
    out = output(second=1, small=1)
    out.result.fixes[1] = both
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.converter.output = out
        job_id = job_id_from(upload(client, make_pdf(1)))
        assert env.run_worker() == job_id
        page = client.get(f"/jobs/{job_id}/review")
        assert page.status_code == 200
        text = " ".join(page.text.split())
    worth = "Worth a look: a second automated check doubts the alt text."
    assert text.count(worth) == 1
    assert "The alt text is long." in text  # the other figure's note is unchanged
