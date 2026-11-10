"""The second automated check's note on the review screen and the job page (server-rendered).

The note ("a possible mismatch between the alt text and the image") is listed under
"Optional checks" by policy (ADR 0005), but it is not something small: a wrong alt text
changes what a screen reader user hears. So wherever the page describes the optional
checks, it says this one is worth a look instead of calling it small or "nothing to do".
The browser test (test_web_second_check_browser.py) covers axe and reflow.
"""

from __future__ import annotations

import pytest

from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, upload
from readablestem.core import triage
from readablestem.core.types import (
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
from readablestem.web.messages import CHECK_MESSAGES

NOTE = CHECK_MESSAGES[("figure.second_check", "second_check_mismatch")]


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


def card(page: str, eid: str) -> str:
    start = page.index(f'<li id="item-{eid}"')
    return page[start : page.index("</li>", start)]


def progress(page: str) -> str:
    start = page.index('<div id="review-progress"')
    return page[start : page.index("</div>", start)]


def test_second_check_note_is_only_an_unreviewed_optional_flag():
    flagged = flagged_figure("p1-e1")
    assert triage.second_check_note(flagged)
    assert not triage.second_check_note(long_alt_figure("p1-e2"))
    assert not triage.second_check_note(flagged.model_copy(update={"reviewed": True}))
    passed = flagged.model_copy(
        update={"checks": [Check(name="figure.second_check", passed=True, severity="warning")]}
    )
    assert not triage.second_check_note(passed)
