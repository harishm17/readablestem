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


def test_a_picture_added_by_hand_is_not_put_on_a_page_that_now_looks_different(web):
    make_edits(web, web.first)

    def draw_over_the_region(doc):
        page = doc[0]
        page.draw_rect(pymupdf.Rect(70, 600, 330, 700), fill=(0, 0, 0))

    second = revise(web, web.first, pdf_with(draw_over_the_region))
    result = stored(web, second)
    assert element(result, "p1-x1") is None
    assert element(result, PLAIN).text == "Discuss vectors and scalars with your class."
    report = carry.CarryReport.from_json(env_job(web, second).carried_json)
    assert report.edits == [PLAIN, LATER] and report.edits_dropped == 1
    page = text_of(web.browser.get(f"/jobs/{second}").text)
    assert "1 change you made by hand was not carried over" in page


def test_words_are_not_put_on_a_paragraph_whose_words_changed(web):
    make_edits(web, web.first)
    changed = review_output()
    changed.result.elements = [
        e.model_copy(update={"text": "Discuss vectors, scalars and tensors."})
        if e.id == PLAIN
        else e
        for e in changed.result.elements
    ]
    response = web.browser.post(
        "/jobs",
        data={"csrf_token": token(web.browser), "revises": web.first},
        files={"pdf": ("v2.pdf", pdf_with(lambda doc: None), "application/pdf")},
    )
    second = job_id_from(response)
    web.converter.output = changed
    web.run_worker()
    result = stored(web, second)
    assert element(result, PLAIN).text == "Discuss vectors, scalars and tensors."
    report = carry.CarryReport.from_json(env_job(web, second).carried_json)
    assert PLAIN not in report.edits and report.edits_dropped == 1


def test_a_page_left_out_is_left_out_again_only_while_it_looks_the_same(web):
    page_post(web, web.first, 2, "omit")
    assert stored(web, web.first).omitted_pages == [1]
    same = revise(web, web.first, pdf_with(lambda doc: None))
    assert stored(web, same).omitted_pages == [1]
    assert not [e for e in stored(web, same).elements if e.page == 1]
    assert carried_rows(web, same)["page-2"] == {"carried_edit": "page_left_out"}

    def write_on_page_two(doc):
        doc[1].insert_text((72, 300), "A new paragraph on this page.", fontsize=11)

    different = revise(web, web.first, pdf_with(write_on_page_two))
    assert stored(web, different).omitted_pages == []
    assert [e for e in stored(web, different).elements if e.page == 1]
    report = carry.CarryReport.from_json(env_job(web, different).carried_json)
    assert report.edits == [] and report.edits_dropped == 1


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


def test_a_try_again_does_not_add_a_picture_the_tool_found_this_time(web):
    page_post(web, web.first, 1, "add", kind="picture", alt=ALT, **REGION)
    stop(web, web.first)
    output = review_output()
    x0, y0, x1, y1 = 100.0, 620.0, 300.0, 700.0  # inside the region drawn by hand
    output.result.elements.append(
        output.result.elements[0].model_copy(
            update={
                "id": "p1-e20",
                "kind": Kind.FIGURE,
                "bbox": (x0, y0, x1, y1),
                "order": 40,
                "text": None,
                "caption": None,
            }
        )
    )
    fix = next(f for f in output.result.fixes if f.element_id == "p1-e6")
    output.result.fixes.append(fix.model_copy(update={"element_id": "p1-e20"}))
    output.crops["p1-e20"] = output.crops["p1-e6"]
    try_again(web, web.first, output)
    result = stored(web, web.first)
    assert [e.id for e in result.elements if e.kind is Kind.FIGURE and e.bbox[1] > 600] == [
        "p1-e20"
    ]
    assert element(result, "p1-x1") is None
