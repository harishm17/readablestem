"""T3-1, T3-2 with T3-5 and T3-16, the whole path: edits made by hand on the review screen
(words, a heading, a picture added, an image described, a page left out) come back on a
revised upload and on a document tried again, only onto what is unchanged.

The converter is the offline fake; the PDFs are real (`fixtures.web.review_doc`), so the
page and region comparisons of the revised file are real renders.
"""

from __future__ import annotations

import re
from html import unescape

import pymupdf
import pytest
from sqlalchemy import select

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload
from readablestem.core.types import DocumentResult, Kind, Status
from readablestem.web import carry
from readablestem.web.db import Job, JobStatus, ReviewEdit, session_scope

PLAIN = "p1-e4"  # "Discuss vectors and scalars with the class."
LATER = "p2-e1"  # "Average speed is distance over time." (page 2)
EQUATION = "p2-e2"
REGION = {"left": "10", "top": "75", "right": "60", "bottom": "90"}  # empty on page 1
ALT = "A sketch of a cart on a ramp."


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.first = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def version(env, job_id: str) -> str:
    return str(env.job(job_id).result_version or 0)


def stored(env, job_id: str) -> DocumentResult:
    return DocumentResult.model_validate(env.job(job_id).result_json)


def edit(env, job_id: str, element_id: str, **fields):
    path = f"/jobs/{job_id}/review"
    data = {"csrf_token": token(env.browser, path), "version": version(env, job_id)} | fields
    response = env.browser.post(f"/jobs/{job_id}/edit/{element_id}", data=data)
    assert response.status_code == 303, response.text[:300]


def decide(env, job_id: str, element_id: str, **fields):
    path = f"/jobs/{job_id}/review"
    data = {"csrf_token": token(env.browser, path), "view": "split"} | fields
    response = env.browser.post(f"{path}/{element_id}", data=data)
    assert response.status_code == 303, response.text[:300]


def page_post(env, job_id: str, number: int, what: str, **fields):
    path = f"/jobs/{job_id}/review"
    data = {"csrf_token": token(env.browser, path), "version": version(env, job_id)} | fields
    response = env.browser.post(f"/jobs/{job_id}/pages/{number}/{what}", data=data)
    assert response.status_code == 303, response.text[:300]


def make_edits(env, job_id: str) -> None:
    """One of each edit that no fix covers, except a page left out."""
    edit(env, job_id, PLAIN, action="text", text="Discuss vectors and scalars with your class.")
    edit(env, job_id, LATER, action="heading", level="2")
    page_post(env, job_id, 1, "add", kind="picture", alt=ALT, **REGION)
    decide(env, job_id, EQUATION, action="reject")
    decide(env, job_id, EQUATION, action="describe", description="v equals d over t")


def pdf_with(change) -> bytes:
    """The review document with something done to it: bytes differ, the page may or not."""
    doc = pymupdf.open(stream=review_pdf(), filetype="pdf")
    change(doc)
    doc.set_metadata({"title": "Second version"})
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data


def revise(env, first: str, data: bytes) -> str:
    response = env.browser.post(
        "/jobs",
        data={"csrf_token": token(env.browser), "revises": first},
        files={"pdf": ("notes-v2.pdf", data, "application/pdf")},
    )
    second = job_id_from(response)
    env.converter.output = review_output()
    env.run_worker()
    return second


def element(result: DocumentResult, element_id: str):
    return next((e for e in result.elements if e.id == element_id), None)


def carried_rows(env, job_id: str) -> dict[str, dict]:
    with session_scope(env.ctx.factory) as session:
        rows = session.scalars(
            select(ReviewEdit).where(
                ReviewEdit.job_id == job_id, ReviewEdit.action == carry.CARRIED_ACTION
            )
        )
        return {r.element_id: dict(r.after or {}) for r in rows}


# --------------------------------------------------------------------------- a revised PDF


def test_edits_by_hand_come_back_on_a_revised_pdf_where_nothing_changed(web):
    make_edits(web, web.first)
    second = revise(web, web.first, pdf_with(lambda doc: None))  # other bytes, same pages
    result = stored(web, second)
    assert element(result, PLAIN).text == "Discuss vectors and scalars with your class."
    heading = element(result, LATER)
    fix = result.fix_for(LATER)
    assert heading and heading.kind is Kind.HEADING
    assert fix and fix.heading and fix.heading.level == 2
    added = element(result, "p1-x1")
    figure = result.fix_for("p1-x1")
    assert added and added.kind is Kind.FIGURE and figure and figure.figure
    assert figure.figure.alt == ALT
    described = result.fix_for(EQUATION)
    assert described and described.described == "v equals d over t"
    assert described.status is Status.REJECTED

    report = carry.CarryReport.from_json(env_job(web, second).carried_json)
    assert report.edits == [PLAIN, LATER, "p1-x1"] and report.edits_dropped == 0
    rows = carried_rows(web, second)
    assert rows[PLAIN] == {"carried_edit": "text"}
    assert rows[LATER] == {"carried_edit": "heading"}
    assert rows["p1-x1"] == {"carried_edit": "picture"}
    html = env_job(web, second).html or ""
    assert "with your class." in html and f'alt="{ALT}"' in html

    page = text_of(web.browser.get(f"/jobs/{second}").text)
    assert "3 changes you made by hand were carried over" in page
    assert "not carried over" not in page


def env_job(env, job_id: str) -> Job:
    return env.job(job_id)


# --------------------------------------------------------------------------- tried again


def stop(env, job_id: str) -> None:
    """The conversion stopped part way (the day's budget ran out): "Convert the missing parts"."""
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.error_code = "budget_daily"
        job.status = JobStatus.DONE


def try_again(env, job_id: str, output=None) -> None:
    response = env.browser.post(
        f"/jobs/{job_id}/retry", data={"csrf_token": token(env.browser, f"/jobs/{job_id}")}
    )
    assert response.status_code == 303, response.text[:300]
    env.converter.output = output or review_output()
    env.run_worker()


def test_edits_by_hand_survive_trying_a_document_again(web):
    make_edits(web, web.first)
    page_post(web, web.first, 2, "omit")
    stop(web, web.first)
    try_again(web, web.first)
    result = stored(web, web.first)
    assert element(result, PLAIN).text == "Discuss vectors and scalars with your class."
    assert element(result, LATER) is None  # on the page left out: held back with it
    assert result.omitted_pages == [1]
    held = next(o for o in result.omitted if o.page == 1)
    kept = next(e for e in held.elements if e.id == LATER)
    assert kept.kind is Kind.HEADING  # made a heading before the page was left out
    figure = result.fix_for("p1-x1")
    assert figure and figure.figure and figure.figure.alt == ALT
    described = result.fix_for(EQUATION) or next(f for f in held.fixes if f.element_id == EQUATION)
    assert described.described == "v equals d over t"
    # A try-again keeps its own review edits and adds none (carried rows are for revisions).
    assert carried_rows(web, web.first) == {}
