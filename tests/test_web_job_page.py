"""The job page: what it says about incomplete and failed jobs, the results summary
(counts, cost, the checks of the HTML file) and the queue position.

Outcomes that need the real pipeline (a spend cap that runs out part way, scanned pages
in a real PDF) are in test_web_outcomes.py; here the fake converter returns results
shaped like them, so each page state can be checked on its own.
"""

from __future__ import annotations

import re
from html import unescape

import pytest

from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, upload
from readablestem.core.types import Check, PageStats, Status
from readablestem.web.db import Job, session_scope
from readablestem.web.outcome import FAILED_MESSAGES
from readablestem.web.views import job_error_message


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


def status_box(html: str) -> str:
    start = html.index('id="job-status"')
    return html[start : html.index("</div>", start)]


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
    assert title(html) == "Stopped early: SECRET-HEADING Kinematics | ReadableSTEM"
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
        title(html)
        == "Finished, with pages not converted: SECRET-HEADING Kinematics | ReadableSTEM"
    )
    assert "Page 2 is a scanned image, so it was not converted." in page_text(html)
    assert 'id="partial-note"' in html


def test_a_pdf_with_no_content_says_so_in_the_status_box(web):
    job_id = convert(web, elements=[], fixes=[])
    assert web.job(job_id).error_code == "no_content"
    why = page_text(status_box(web.browser.get(f"/jobs/{job_id}").text))
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


def test_a_reviewers_accept_counts_as_reviewed_not_as_automatic(web):
    job_id = convert(web)
    before = page_text(section(web.browser.get(f"/jobs/{job_id}").text, "results-heading"))
    assert "Decisions to make 3" in before and "Decisions made 0" in before
    page = web.browser.get(f"/jobs/{job_id}/review").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert token
    response = web.browser.post(
        f"/jobs/{job_id}/review/p1-e3", data={"csrf_token": token.group(1), "action": "accept"}
    )
    assert response.status_code == 303
    after = page_text(section(web.browser.get(f"/jobs/{job_id}").text, "results-heading"))
    assert "Decisions to make 2" in after and "Decisions made 1" in after
    review = page_text(web.browser.get(f"/jobs/{job_id}/review/list").text)
    assert "Accepted by you" in review and "Accepted automatically" in review


# --------------------------------------------------------------------------- queue
