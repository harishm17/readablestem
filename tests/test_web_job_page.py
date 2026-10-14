"""The job page: what it says about incomplete and failed jobs, the results summary
(counts, cost, the checks of the HTML file) and the queue position.

Outcomes that need the real pipeline (a spend cap that runs out part way, scanned pages
in a real PDF) are in test_web_outcomes.py; here the fake converter returns results
shaped like them, so each page state can be checked on its own.
"""

from __future__ import annotations

import re
from datetime import timedelta
from html import unescape

import pytest

from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, upload
from readablestem.core.types import Check, Element, Fix, Kind, PageStats, Status
from readablestem.web.db import Job, User, session_scope, utcnow
from readablestem.web.outcome import FAILED_MESSAGES
from readablestem.web.views import job_error_message, queue_ahead


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def page_text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, flags=re.S)
    assert match
    return unescape(" ".join(match.group(1).split()))


def section(html: str, heading_id: str) -> str:
    start = html.index(f'id="{heading_id}"')
    return html[start : html.index("</section>", start)]


def convert(web, **update) -> str:
    """Upload a PDF and let the worker store the fake result, changed by `update`."""
    result = web.converter.output.result
    for name, value in update.items():
        setattr(result, name, value)
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    assert web.run_worker() == job_id
    return job_id


# --------------------------------------------------------------------------- outcomes


def stop_part_way(web) -> None:
    """The fake result as a daily-cap stop leaves it: the abstained equation was never
    converted because the budget ran out."""
    fix = web.converter.output.result.fix_for("p1-e5")
    assert fix is not None
    fix.status, fix.reason = Status.FAILED, "budget_daily"


def test_a_job_stopped_early_says_so_in_the_title_status_and_results(web):
    stop_part_way(web)
    job_id = convert(web, stopped_by="budget_daily")
    assert web.job(job_id).error_code == "budget_daily"
    html = web.browser.get(f"/jobs/{job_id}").text
    assert title(html) == "Stopped early: SECRET-HEADING Kinematics | a11y-stem"
    text = page_text(html)
    assert "1 of 4 equations and figures were not converted" in text
    assert "The accessible HTML is ready" not in text
    results = page_text(section(html, "results-heading"))
    assert "Stopped early: part of this document was not converted" in results
    assert 'id="partial-note"' in html


def test_a_job_with_scanned_pages_says_so_in_the_title_and_results(web):
    pages = [PageStats(page=p, elements=1, fixes=1, cost_usd=0, latency_ms=0) for p in (0, 1)]
    result = web.converter.output.result
    result.stats.per_page = pages
    job_id = convert(web, image_only_pages=[1])
    html = web.browser.get(f"/jobs/{job_id}").text
    assert (
        title(html) == "Finished, with pages not converted: SECRET-HEADING Kinematics | a11y-stem"
    )
    assert "Page 2 is a scanned image, so it was not converted." in page_text(html)
    assert 'id="partial-note"' in html


def test_a_complete_job_has_no_partial_note(web):
    job_id = convert(web)
    html = web.browser.get(f"/jobs/{job_id}").text
    assert title(html) == "Converted: SECRET-HEADING Kinematics | a11y-stem"  # 3 decisions are open
    assert 'id="partial-note"' not in html


def test_a_scanned_pdf_says_why_it_stopped_everywhere_on_the_page(web):
    """Before, the status box gave the OCR advice while the title said "Stopped" and the
    "Why the conversion stopped" section said "The conversion stopped unexpectedly"."""
    pages = [PageStats(page=0, elements=1, fixes=0, cost_usd=0, latency_ms=0)]
    web.converter.output.result.stats.per_page = pages
    job_id = convert(web, image_only_pages=[0])
    assert (web.job(job_id).status, web.job(job_id).error_code) == ("failed", "scanned")
    html = web.browser.get(f"/jobs/{job_id}").text
    assert title(html) == "Stopped: notes.pdf | a11y-stem"
    why = page_text(section(html, "failed-heading"))
    assert "Every page of this PDF is a scanned image, so this tool cannot convert it." in why
    assert "OCR" not in why  # the old advice made exactly these files
    assert "stopped unexpectedly" not in page_text(html)


def test_a_pdf_with_no_content_says_so_in_the_failed_section(web):
    job_id = convert(web, elements=[], fixes=[])
    assert web.job(job_id).error_code == "no_content"
    why = page_text(section(web.browser.get(f"/jobs/{job_id}").text, "failed-heading"))
    assert FAILED_MESSAGES["no_content"] in why


def test_every_failure_code_with_its_own_message_has_it_on_the_failed_section():
    for code, message in FAILED_MESSAGES.items():
        assert job_error_message(code) == message, code


# --------------------------------------------------------------------------- results


def test_the_job_page_shows_no_model_cost(web):
    """T1-11: what a conversion cost is for the admin page, not for an instructor."""
    job_id = convert(web)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.cost_usd = 0.5  # an earlier attempt was interrupted after spending
    html = web.browser.get(f"/jobs/{job_id}").text
    assert "Model cost" not in html and "$0.5" not in html and "$0.012" not in html


def test_list_items_are_counted(web):
    items = [
        Element(id=f"p1-e{9 + i}", kind=Kind.LIST_ITEM, page=0, bbox=(0, 5, 1, 6), order=9 + i,
                text=f"item {i}", list_marker="•", list_ordered=False, list_level=1)
        for i in range(3)
    ]  # fmt: skip
    job_id = convert(web, elements=[*web.converter.output.result.elements, *items])
    results = page_text(section(web.browser.get(f"/jobs/{job_id}").text, "results-heading"))
    assert "List items 3" in results


def test_failed_checks_of_the_html_file_are_listed(web):
    checks = [
        Check(name="html.lang", passed=True),
        Check(name="html.heading_order", passed=False, code="heading_skip"),
        Check(name="html.math_display", passed=False, severity="warning",
              code="math_missing_display"),
    ]  # fmt: skip
    job_id = convert(web, html_checks=checks)
    results = section(web.browser.get(f"/jobs/{job_id}").text, "results-heading")
    text = page_text(results)
    assert "2 of 3 checks did not pass" in text
    assert "Must be fixed: The headings skip a level" in text
    assert "heading_skip" not in text and "html.heading_order" not in text  # no codes (T1-11)
    assert "Worth checking: An equation does not say" in text


def test_passing_checks_are_summarised_and_old_results_show_none(web):
    passed = convert(web, html_checks=[Check(name="html.lang", passed=True)])
    text = page_text(section(web.browser.get(f"/jobs/{passed}").text, "results-heading"))
    assert "All 1 checks passed" in text
    old = convert(web, html_checks=[])  # stored before results carried their checks
    assert 'id="checks-heading"' not in web.browser.get(f"/jobs/{old}").text


def test_a_reviewers_accept_counts_as_reviewed_not_as_automatic(web):
    job_id = convert(web)
    before = page_text(section(web.browser.get(f"/jobs/{job_id}").text, "results-heading"))
    assert "Decisions to make 3" in before and "Decisions you made 0" in before
    page = web.browser.get(f"/jobs/{job_id}/review").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert token
    response = web.browser.post(
        f"/jobs/{job_id}/review/p1-e3", data={"csrf_token": token.group(1), "action": "accept"}
    )
    assert response.status_code == 303
    after = page_text(section(web.browser.get(f"/jobs/{job_id}").text, "results-heading"))
    assert "Decisions to make 2" in after and "Decisions you made 1" in after
    review = page_text(web.browser.get(f"/jobs/{job_id}/review/list").text)
    assert "Accepted by you" in review and "Accepted automatically" in review


def test_results_stored_before_the_reviewed_flag_still_load_and_count(web):
    job_id = convert(web)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None and job.result_json is not None
        stored = dict(job.result_json)
        stored["fixes"] = [
            {k: v for k, v in f.items() if k != "reviewed"} | {"status": s}
            for f, s in zip(
                stored["fixes"],
                ["accepted", "edited", "needs_review", "rejected", "abstained"],
                strict=True,
            )
        ]
        job.result_json = stored
    text = page_text(section(web.browser.get(f"/jobs/{job_id}").text, "results-heading"))
    assert "Decisions to make 2" in text and "Decisions you made 2" in text


def test_fix_reviewed_defaults_to_false_for_stored_json():
    fix = Fix.model_validate({"element_id": "p1-e0", "kind": "heading", "status": "accepted"})
    assert fix.reviewed is False and not fix.accepted_by_reviewer
    assert Fix.model_validate(fix.model_dump() | {"reviewed": True}).accepted_by_reviewer


# --------------------------------------------------------------------------- queue


def test_queue_position_follows_the_workers_claim_order(web):
    """The worker takes the fewest attempts first, then the oldest: a job requeued after
    a lost lease waits behind fresh ones, so it is not "next in line" although it is the
    oldest. Jobs of people no longer allowed are never claimed and do not count."""
    retried = job_id_from(upload(web.browser, make_pdf(1)))
    fresh = job_id_from(upload(web.browser, make_pdf(1)))
    bob = web.client()
    login(bob, BOB)
    removed = job_id_from(upload(bob, make_pdf(1)))
    with session_scope(web.ctx.factory) as session:
        start = utcnow() - timedelta(minutes=5)
        for minutes, job_id in enumerate((removed, retried, fresh)):
            job = session.get(Job, job_id)
            assert job is not None
            job.created_at = start + timedelta(minutes=minutes)
        job = session.get(Job, retried)
        assert job is not None
        job.attempts = 1  # its first attempt lost its lease and was requeued
        user = session.get(User, session.get(Job, removed).user_id)  # type: ignore[union-attr]
        assert user is not None
        user.is_allowed = False
    with session_scope(web.ctx.factory) as session:
        ahead = {j: queue_ahead(session, session.get(Job, j)) for j in (fresh, retried)}  # type: ignore[arg-type]
    assert ahead == {fresh: 0, retried: 1}
    assert "next in line" in web.browser.get(f"/jobs/{fresh}").text
    assert "1 document ahead of yours" in web.browser.get(f"/jobs/{retried}").text
    claim = web.worker().claim()
    assert claim is not None and claim.job_id == fresh  # the order the page promised
    assert "next in line" in web.browser.get(f"/jobs/{retried}").text
    bob.close()
