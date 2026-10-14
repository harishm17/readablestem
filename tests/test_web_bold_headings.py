"""T1-3: "Make bold text" on a group of repeated headings is a decision, not an image.

A heading made bold text is rejected as a heading (`Status.REJECTED`), and `REJECTED` is the
status of an equation or figure kept as the original image. The job page used to count the
three bold headings as "shown as an image", so a job whose decisions were all made never
said "Ready to share", and the counts disagreed between the job page, the review screen and
the documents list. One decision unit everywhere: a group of headings is one decision, and
a heading is never an image.

Uses the real-shaped document of fixtures.web.review_doc: decision 1 the bold omega (p1-e2),
2 the figure (p1-e6), 3 the "Teacher Support" group of three headings.
"""

from __future__ import annotations

import re
from html import unescape

import pytest

from a11y_stem.core.types import DocumentResult
from a11y_stem.web import job_page
from fixtures.web.review_doc import GROUP, review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload


@pytest.fixture
def ux(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def decide(env, target: str, **fields) -> None:
    csrf = token(env.browser, f"/jobs/{env.job_id}/review")
    version = env.job(env.job_id).result_version
    data = {"csrf_token": csrf, "version": str(version)} | fields
    response = env.browser.post(f"/jobs/{env.job_id}/review/{target}", data=data)
    assert response.status_code == 303, response.text


def stored(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


def test_bold_headings_are_not_counted_as_images():
    result = review_output().result
    for fix in result.fixes:
        if fix.group_key == GROUP:
            fix.status = fix.status.REJECTED
            fix.reviewed = True
    counts = job_page.breakdown(result.fixes)
    assert counts.as_image == 0
    assert counts.as_image_sentence == ""
    # The three headings are one decision made by the instructor.
    assert counts.decided == 3
    assert counts.decided_units == 1


def test_a_heading_group_made_bold_leaves_the_job_ready_when_the_rest_is_decided(ux):
    decide(ux, "p1-e2", action="accept")
    decide(ux, "p1-e6", action="accept")
    page = ux.browser.get(f"/jobs/{ux.job_id}").text
    assert "1 decision needs you" in text_of(page) or "Review 1 decision" in text_of(page)
    decide(ux, GROUP, action="reject")
    assert {f.status.value for f in stored(ux).fixes if f.group_key == GROUP} == {"rejected"}

    page = ux.browser.get(f"/jobs/{ux.job_id}").text
    text = text_of(page)
    assert "<title>Ready to share: notes.pdf" in page
    assert 'id="ready"' in page
    assert "shown as an image" not in text
    assert "shown as images" not in text
    assert "3 headings" not in text.split("Ready to share", 1)[1].split("Results", 1)[0]


def test_the_three_screens_count_the_same_decisions(ux):
    from a11y_stem.web import documents

    decide(ux, GROUP, action="reject")
    job_text = text_of(ux.browser.get(f"/jobs/{ux.job_id}").text)
    assert "2 decisions need you" in job_text or "Review 2 decisions" in job_text
    listing = ux.browser.get("/app").text
    assert "2 decisions to make" in text_of(listing)
    assert documents.open_decisions(ux.job(ux.job_id).result_json["fixes"]) == 2
    review = text_of(ux.browser.get(f"/jobs/{ux.job_id}/review").text)
    assert "1 of 3 decided" in review  # the group of three headings is one decision
