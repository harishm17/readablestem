"""T3-16: "Describe it in words" on an item kept as an image, and pages left out.

The description becomes the alt text of the image after the same checks as a figure's alt
text (cleaned, checked, escaped when written). A page left out of review and of the student
file moves into `result.omitted`; nothing is deleted, and Undo or "Put it back" returns it.
Uses fixtures.web.review_doc (equations, a figure, a heading group over two pages) and
fixtures.web.table_doc (tables kept as images).
"""

from __future__ import annotations

import re
from html import unescape

import pytest

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload, with_file
from fixtures.web.table_doc import table_output, table_pdf
from readablestem.core.types import (
    ConversionOutput,
    DocumentResult,
    Element,
    Fix,
    Kind,
    Status,
)
from readablestem.web.review import (
    DESCRIBE_EMPTY,
)

# --------------------------------------------------------------------------- fixtures


def kept_page_output() -> ConversionOutput:
    """The review document plus a handwritten page 2 kept as an image (one decision)."""
    output = review_output()
    result = output.result
    result.elements.append(
        Element(id="p2-e9", kind=Kind.FIGURE, page=1, bbox=(0.0, 0.0, 612.0, 792.0), order=99)
    )
    result.fixes.append(
        Fix(
            element_id="p2-e9",
            kind=Kind.FIGURE,
            status=Status.FAILED,
            reason="handwritten_page",
            review="required",
            group_key="pg-p2-e9",
            group_size=1,
        )
    )
    output.crops["p2-e9"] = output.crops["p1-e6"]
    return output


@pytest.fixture
def review(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


@pytest.fixture
def tables(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = table_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, table_pdf()))
        env.run_worker()
        yield env


@pytest.fixture
def kept(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = kept_page_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def post(env, target: str, view: str = "split", **fields):
    path = f"/jobs/{env.job_id}/review"
    data = {"csrf_token": token(env.browser, path), "view": view} | fields
    return env.browser.post(f"{path}/{target}", data=data)


def stored(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


def page_of(env, query: str = "") -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review{query}")
    assert response.status_code == 200
    return response.text


def alts(html: str) -> list[str]:
    return [unescape(a) for a in re.findall(r'<img [^>]*alt="([^"]*)"', html)]


# --------------------------------------------------------------------------- describing


def test_an_equation_kept_as_an_image_can_be_described(review):
    assert post(review, "p1-e2", action="reject").status_code == 303
    page = page_of(review, "?item=p1-e2")
    assert "Describe it in words" in page and 'name="description"' in page
    response = post(review, "p1-e2", action="describe", description="omega, the angular velocity")
    assert response.status_code == 303
    fix = stored(review).fix_for("p1-e2")
    assert fix is not None
    assert fix.described == "omega, the angular velocity"
    assert fix.status is Status.REJECTED and fix.reviewed
    html = with_file(review).html or ""
    assert "omega, the angular velocity" in alts(html)
    # The fixed words and the PDF text are replaced, not added to.
    assert not any("not checked" in a for a in alts(html) if "omega" in a)


@pytest.mark.parametrize(
    ("typed", "problem"),
    [
        ("   ", DESCRIBE_EMPTY),
        ("<script>alert(1)</script> omega", "was not saved"),
        ("see https://example.org for omega", "was not saved"),
        ("image of omega", None),  # only a warning: saved, as for any alt text
        ("x" * 1001, "under 1000 characters"),
    ],
)
def test_a_description_goes_through_the_alt_text_checks(review, typed, problem):
    post(review, "p1-e2", action="reject")
    response = post(review, "p1-e2", action="describe", description=typed)
    fix = stored(review).fix_for("p1-e2")
    assert fix is not None
    if problem is None:
        assert response.status_code == 303 and fix.described == typed
        return
    assert response.status_code == 422
    assert problem in unescape(response.text)
    assert fix.described is None  # nothing saved
    if "script" in typed:
        assert "<script>alert" not in response.text  # shown escaped, never as markup


def test_a_description_is_cleaned_like_alt_text_and_escaped_when_written(review):
    post(review, "p1-e2", action="reject")
    typed = 'omega​  the "angular" &   velocity\n'
    assert post(review, "p1-e2", action="describe", description=typed).status_code == 303
    fix = stored(review).fix_for("p1-e2")
    assert fix is not None and fix.described == 'omega the "angular" & velocity'
    html = with_file(review).html or ""
    assert 'alt="omega the &quot;angular&quot; &amp; velocity"' in html
    assert "​" not in html


def test_an_empty_box_takes_the_description_away(review):
    post(review, "p1-e2", action="reject")
    post(review, "p1-e2", action="describe", description="omega")
    assert post(review, "p1-e2", action="describe", description="").status_code == 303
    fix = stored(review).fix_for("p1-e2")
    assert fix is not None and fix.described is None and fix.status is Status.REJECTED
    html = with_file(review).html or ""
    assert "omega" not in " ".join(a for a in alts(html) if "not checked" not in a)


# --------------------------------------------------------------------------- tables, pages


# --------------------------------------------------------------------------- pages left out


def omitted(env) -> list[int]:
    return stored(env).omitted_pages


def pages_post(env, number: int, action: str, **fields):
    path = f"/jobs/{env.job_id}/review"
    data = {"csrf_token": token(env.browser, path)} | fields
    return env.browser.post(f"/jobs/{env.job_id}/pages/{number}/{action}", data=data)


def current_version(env) -> str:
    return str(env.job(env.job_id).result_version or 0)


def test_a_page_is_left_out_of_review_and_output(review):
    before = stored(review)
    decisions_before = [f for f in before.fixes if f.pending]
    assert any(e.page == 1 for e in before.elements)
    response = pages_post(review, 2, "omit", version=current_version(review))
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/jobs/{review.job_id}/review?")
    after = stored(review)
    assert after.omitted_pages == [1]
    assert not any(e.page == 1 for e in after.elements)
    assert not any(f.element_id.startswith("p2") for f in after.fixes)
    # Nothing was deleted: it is all held in the result.
    held = after.omitted[0]
    assert {e.id for e in held.elements} == {e.id for e in before.elements if e.page == 1}
    assert {f.element_id for f in held.fixes} == {
        f.element_id for f in before.fixes if f.element_id.startswith("p2")
    }
    assert len([f for f in after.fixes if f.pending]) < len(decisions_before)
    notice = [n for n in after.notices if n.code == "page_omitted"]
    assert [n.page for n in notice] == [1]
    html = with_file(review).html or ""
    assert "Average speed is distance over time" not in html
    assert "Page 2 of the original PDF was left out of this file." in html


def test_the_last_page_cannot_be_left_out(review):
    pages_post(review, 2, "omit", version=current_version(review))
    response = pages_post(review, 1, "omit", version=current_version(review))
    assert response.status_code == 303
    assert omitted(review) == [1]  # unchanged
    page = page_of(review, "?page=1")
    assert "At least one page has to stay in the file." in unescape(page)


def test_leaving_out_the_only_kept_page_ends_its_outcome_and_putting_it_back_restores_it(kept):
    """The job was "Finished, with pages not converted" because of page 2; without it the
    file has nothing of the kind, and the job page does not say so any more."""
    job = kept.job(kept.job_id)
    assert job.error_code == "pages_not_converted"
    page = kept.browser.get(f"/jobs/{kept.job_id}").text
    assert "Finished, with pages not converted" in page
    pages_post(kept, 2, "omit", version=current_version(kept))
    assert "Finished, with pages not converted" not in kept.browser.get(f"/jobs/{kept.job_id}").text
    pages_post(kept, 2, "restore", version=current_version(kept))
    assert "Finished, with pages not converted" in kept.browser.get(f"/jobs/{kept.job_id}").text
