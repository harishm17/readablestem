"""The web app with scanned and handwritten pages read from an image of the page (ADR 0007):
the upload that used to be refused, the outcome of the job, and the review screen's decisions
for the text, equation and table of such a page.

The conversion is the real pipeline's with the offline model client (`convert_pdf` on a
generated scan); the app runs on its fake converter, which returns that result.
"""

from __future__ import annotations

from typing import Any

import pytest

from fixtures.web.support import (
    ALICE,
    WebEnv,
    job_id_from,
    login,
    token,
    upload,
)
from image_page_support import block, cell, math, scan_pdf, text
from readablestem.config import Settings
from readablestem.core.types import DocumentResult, Kind, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import run
from readablestem.web import uploads
from readablestem.web.pdf_probe import ProbeRejected, probe

PAGE = {
    "blocks": [
        block("heading", level=1, runs=[text("Motion")], y0=40, y1=80),
        block("paragraph", runs=[text("Speed is"), math("v"), text("over time.")], y0=100, y1=160),
        block("equation", latex="v = \\frac{d}{t}", y0=200, y1=260),
        block(
            "paragraph", confidence="low", runs=[text("Mass is [illegible] kg.")], y0=300, y1=340
        ),
        block(
            "table",
            rows=[
                {"cells": [cell("t", "col"), cell("d", "col")]},
                {"cells": [cell("1"), cell("5")]},
            ],
            y0=400,
            y1=500,
        ),
    ]
}


def converted(pdf: bytes, page: dict[str, Any] | None = PAGE):
    client = FakeVisionClient(
        responders={
            "image_page": lambda r: page,
            "equation": lambda r: {
                "is_equation": True,
                "latex": "v = \\frac{d}{t}",
                "confidence": 0.9,
            },
        }
    )
    settings = Settings(_env_file=None, feature_image_pages=True)  # type: ignore[call-arg]
    return run.convert_pdf(pdf, settings=settings, client=client)


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, feature_image_pages=True)
    pdf = scan_pdf()
    env.converter.output = converted(pdf)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.pdf = pdf
        env.job_id = job_id_from(upload(client, pdf, image_pages=True))
        env.run_worker()
        yield env


def result(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


def post(env, element_id: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"), "view": "list"} | fields
    return env.browser.post(f"/jobs/{env.job_id}/review/{element_id}", data=data)


def text_fix(res: DocumentResult):
    return next(
        f
        for f in res.fixes
        if f.kind is Kind.PARAGRAPH
        and f.review == "required"
        and any(c.code == "illegible" for c in f.failed_checks)
    )


# ------------------------------------------------------------------------- the upload


def test_a_scan_is_refused_at_upload_unless_the_flag_is_on():
    pdf = scan_pdf()
    with pytest.raises(ProbeRejected) as refused:
        probe(pdf, max_pages=10)
    assert refused.value.code == "scanned"
    assert probe(pdf, max_pages=10, allow_image_pages=True) == 1
    with pytest.raises(uploads.UploadRejected):
        uploads.inspect_pdf(pdf, max_pages=10)
    assert uploads.inspect_pdf(pdf, max_pages=10, allow_image_pages=True) == 1


def test_a_blank_page_is_still_refused_with_the_flag_on():
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    with pytest.raises(ProbeRejected) as refused:
        probe(doc.tobytes(), max_pages=10, allow_image_pages=True)
    assert refused.value.code == "no_content"


# ------------------------------------------------------------------------- the outcome


# ------------------------------------------------------------------------ the review


def test_the_split_review_shows_the_text_with_its_page_and_a_way_to_change_the_words(web):
    res = result(web)
    fix = text_fix(res)
    html = web.browser.get(f"/jobs/{web.job_id}/review?item={fix.element_id}").text
    assert "Text as read from the image" in html
    assert 'data-role="change-words"' in html
    assert "Keep as image" in html
    assert f"/regions/{fix.element_id}.png" in html


def test_saving_an_edit_to_text_is_refused_with_the_way_to_change_it(web):
    fix = text_fix(result(web))
    response = post(web, fix.element_id, action="edit")
    assert response.status_code == 422
    assert "Change the words" in response.text


def test_the_student_file_has_the_notice_and_the_original_and_it_still_downloads(web):
    html = web.browser.get(f"/jobs/{web.job_id}/view").text
    assert "Converted from an image of the page." in html
    assert "<summary>Show the original page 1</summary>" in html
    assert web.browser.get(f"/jobs/{web.job_id}/crops/p1-e0.png").status_code == 200


def test_every_decision_of_the_page_can_be_made_and_the_job_finishes(web):
    res = result(web)
    required = [f for f in res.fixes if f.review == "required" and f.pending]
    assert {f.kind for f in required} == {Kind.PARAGRAPH, Kind.EQUATION, Kind.TABLE}
    for fix in required:
        assert post(web, fix.element_id, action="accept").status_code in (200, 303), fix.kind
    after = result(web)
    assert not any(f.pending for f in after.fixes)


# ------------------------------------------------------- writing the reading left out


@pytest.fixture
def web_left_out(tmp_path):
    env = WebEnv(tmp_path, feature_image_pages=True)
    pdf = scan_pdf()
    env.converter.output = converted(pdf, {"blocks": [PAGE["blocks"][1]]})
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.pdf = pdf
        env.job_id = job_id_from(upload(client, pdf, image_pages=True))
        env.run_worker()
        yield env


def left_out_fix(res: DocumentResult):
    return next(f for f in res.fixes if any(c.code == "omitted_region" for c in f.failed_checks))


def test_keeping_the_left_out_region_as_an_image_settles_it_and_says_so_in_the_student_file(
    web_left_out,
):
    fix = left_out_fix(result(web_left_out))
    assert post(web_left_out, fix.element_id, action="reject", acknowledge="on").status_code in (
        200,
        303,
    )
    after = result(web_left_out).fix_for(fix.element_id)
    assert after is not None and after.status is Status.REJECTED and not after.pending
    html = web_left_out.browser.get(f"/jobs/{web_left_out.job_id}/view").text
    assert "Part of page 1, not converted." in html and "Not converted." in html
