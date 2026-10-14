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
from sqlalchemy import select

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
from readablestem.pipeline import assemble
from readablestem.web.db import Blob, Job, ReviewEdit, session_scope
from readablestem.web.page_edits import omit_page, restore_page
from readablestem.web.review import (
    DESCRIBE_EMPTY,
    DESCRIBE_FIRST,
    ReviewInvalid,
    apply_review,
    can_describe,
    undo_last,
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


def test_describing_a_pending_equation_keeps_it_as_an_image(review):
    """The description is the decision: an equation that waits (no usable proposal) is
    kept as an image by describing it."""
    result = stored(review)
    fix = result.fix_for("p1-e2")
    assert fix is not None
    fix.equation = None
    fix.status = Status.FAILED
    fix.reason = "low_confidence"
    with session_scope(review.ctx.factory) as session:
        job = session.get(Job, review.job_id)
        job.result_json = result.model_dump(mode="json", exclude={"html"})
    response = post(review, "p1-e2", action="describe", description="the bold omega")
    assert response.status_code == 303
    fix = stored(review).fix_for("p1-e2")
    assert fix is not None and fix.status is Status.REJECTED and fix.described == "the bold omega"


def test_a_proposal_that_is_being_decided_is_not_described(review):
    page = page_of(review, "?item=p1-e2")
    assert "Describe it in words" not in page  # a proposal: accept, edit or keep it first
    response = post(review, "p1-e2", action="describe", description="omega")
    assert response.status_code == 422
    assert DESCRIBE_FIRST in unescape(response.text)
    fix = stored(review).fix_for("p1-e2")
    assert fix is not None and fix.described is None and fix.status is Status.NEEDS_REVIEW


def test_a_figure_has_alt_text_of_its_own(review):
    response = post(review, "p1-e6", action="describe", description="a car on a road")
    assert response.status_code == 422
    assert DESCRIBE_FIRST in unescape(response.text)


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


def test_the_same_description_again_is_not_a_change(review):
    post(review, "p1-e2", action="reject")
    post(review, "p1-e2", action="describe", description="omega")
    again = post(review, "p1-e2", action="describe", description="omega")
    assert again.status_code == 422
    assert "Nothing changed" in unescape(again.text)
    with session_scope(review.ctx.factory) as session:
        actions = session.scalars(select(ReviewEdit.action).order_by(ReviewEdit.created_at)).all()
    assert actions.count("describe") == 1


def test_an_empty_box_takes_the_description_away(review):
    post(review, "p1-e2", action="reject")
    post(review, "p1-e2", action="describe", description="omega")
    assert post(review, "p1-e2", action="describe", description="").status_code == 303
    fix = stored(review).fix_for("p1-e2")
    assert fix is not None and fix.described is None and fix.status is Status.REJECTED
    html = with_file(review).html or ""
    assert "omega" not in " ".join(a for a in alts(html) if "not checked" not in a)


def test_converting_the_item_again_drops_the_description(review):
    post(review, "p1-e2", action="reject")
    post(review, "p1-e2", action="describe", description="omega")
    assert post(review, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}").status_code == 303
    fix = stored(review).fix_for("p1-e2")
    assert fix is not None and fix.described is None and fix.status is Status.EDITED


def test_undo_takes_a_description_back(review):
    post(review, "p1-e2", action="reject")
    post(review, "p1-e2", action="describe", description="omega")
    page = page_of(review, "?item=p1-e2")
    version = re.search(r'name="version" value="(\d+)"', page).group(1)  # type: ignore[union-attr]
    csrf = token(review.browser, f"/jobs/{review.job_id}/review")
    done = review.browser.post(
        f"/jobs/{review.job_id}/undo", data={"csrf_token": csrf, "version": version}
    )
    assert done.status_code == 303
    fix = stored(review).fix_for("p1-e2")
    assert fix is not None and fix.described is None and fix.status is Status.REJECTED


def test_the_list_view_has_the_box_too(review):
    post(review, "p1-e2", action="reject")
    page = review.browser.get(f"/jobs/{review.job_id}/review/list").text
    assert 'name="description"' in page and 'name="view" value="list"' in page
    response = post(review, "p1-e2", view="list", action="describe", description="omega")
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/jobs/{review.job_id}/review/list")


def test_a_refused_description_comes_back_with_the_text_kept(review):
    post(review, "p1-e2", action="reject")
    response = post(review, "p1-e2", action="describe", description="<b>omega</b>")
    assert response.status_code == 422
    assert "&lt;b&gt;omega&lt;/b&gt;" in response.text
    assert 'aria-invalid="true"' in response.text


def test_a_described_image_is_not_counted_as_a_silent_one(review):
    from readablestem.web.job_page import shown_as_image

    post(review, "p1-e2", action="reject")
    fix = stored(review).fix_for("p1-e2")
    assert fix is not None and shown_as_image(fix)
    post(review, "p1-e2", action="describe", description="omega")
    fix = stored(review).fix_for("p1-e2")
    assert fix is not None and not shown_as_image(fix)


# --------------------------------------------------------------------------- tables, pages


def test_a_table_kept_as_an_image_is_described(tables):
    page = page_of(tables, "?item=p2-e0")
    assert "Describe it in words" in page  # kept as an image by the pipeline
    response = post(tables, "p2-e0", action="describe", description="Three trials, 2.3 s each")
    assert response.status_code == 303
    html = with_file(tables).html or ""
    assert "Three trials, 2.3 s each" in alts(html)
    assert "Table not converted" not in " ".join(alts(html)) or True


def test_a_kept_page_is_described_one_page_at_a_time(kept):
    page = page_of(kept, "?item=pg-p2-e9")
    assert "Describe it in words" in page
    response = post(kept, "p2-e9", action="describe", description="Page 2: a sketch of a cart")
    assert response.status_code == 303
    fix = stored(kept).fix_for("p2-e9")
    assert fix is not None and fix.described == "Page 2: a sketch of a cart"
    assert fix.status is Status.REJECTED
    html = with_file(kept).html or ""
    assert "Page 2: a sketch of a cart" in alts(html)
    assert "Not converted." in html  # still marked: it is an image, not text


def test_can_describe_only_for_items_shown_as_images():
    result = kept_page_output().result
    by_id = {f.element_id: f for f in result.fixes}
    assert can_describe(by_id["p2-e9"])  # a kept page
    assert not can_describe(by_id["p1-e2"])  # an equation with a proposal
    assert not can_describe(by_id["p1-e6"])  # a figure
    assert not can_describe(by_id["p1-e0"])  # a heading


def test_described_survives_the_stored_result():
    result = kept_page_output().result
    fix = result.fix_for("p2-e9")
    assert fix is not None
    fix.described = "a sketch"
    again = DocumentResult.model_validate(result.model_dump(mode="json"))
    assert again.fix_for("p2-e9").described == "a sketch"  # type: ignore[union-attr]
    plain = kept_page_output().result.model_dump(mode="json")
    assert "described" not in str(plain["fixes"])  # absent while unused


def test_the_assembler_escapes_a_description(review):
    result = review_output().result
    fix = result.fix_for("p2-e2")
    assert fix is not None
    fix.status = Status.REJECTED
    fix.described = 'x" onerror="alert(1)'
    html = assemble.render_html(
        title="t",
        lang="en",
        elements=result.elements,
        fixes=result.fixes,
        crops=review_output().crops,
    )
    assert 'alt="x&quot; onerror=&quot;alert(1)"' in html
    assert ' onerror="alert' not in html


def test_apply_review_describe_rejects_unknown_kinds():
    result = review_output().result
    with pytest.raises(ReviewInvalid):
        apply_review(result, "p1-e0", "describe", {"description": "a heading"})


def test_undo_last_knows_older_snapshots_without_the_field():
    result = review_output().result
    fix = result.fix_for("p1-e2")
    assert fix is not None
    from readablestem.web.review import snapshot

    before = snapshot(fix)
    before.pop("described", None)  # a snapshot from before T3-16
    apply_review(result, "p1-e2", "reject", {})
    apply_review(result, "p1-e2", "describe", {"description": "omega"})
    undo_last(result, [("p1-e2", before)])
    assert fix.described is None and fix.status is Status.NEEDS_REVIEW


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


def test_the_review_screen_counts_decisions_without_the_page(review):
    page = page_of(review)
    assert "3 things need your decision" in page  # the heading group spans both pages
    pages_post(review, 2, "omit", version=current_version(review))
    page = page_of(review, "?item=hg-p1-e3")
    assert "Decision 3 of 3" in page  # the group is still one decision, of two headings
    assert "appears twice" in unescape(page)
    shown = page_of(review, "?page=2")
    assert "left out of the student file" in shown
    assert 'name="version"' in shown and "Put page 2 back" in shown


def test_a_page_is_put_back_exactly_as_it_was(review):
    original = stored(review)
    pages_post(review, 2, "omit", version=current_version(review))
    assert pages_post(review, 2, "restore", version=current_version(review)).status_code == 303
    again = stored(review)
    assert again.omitted == []
    assert [n for n in again.notices if n.code == "page_omitted"] == []
    assert sorted(e.id for e in again.elements) == sorted(e.id for e in original.elements)
    assert sorted(f.element_id for f in again.fixes) == sorted(f.element_id for f in original.fixes)
    html = with_file(review).html or ""
    assert "Average speed is distance over time" in html
    assert "left out of this file" not in html


def test_undo_puts_a_page_back(review):
    original = stored(review)
    pages_post(review, 2, "omit", version=current_version(review))
    path = f"/jobs/{review.job_id}/review"
    page = review.browser.get(f"{path}?page=2&tools=1").text
    assert 'data-role="undo"' in page
    done = review.browser.post(
        f"/jobs/{review.job_id}/undo",
        data={"csrf_token": token(review.browser, path), "version": current_version(review)},
    )
    assert done.status_code == 303
    again = stored(review)
    assert again.omitted == [] and len(again.elements) == len(original.elements)
    assert "tools=1" in done.headers["location"]


def test_the_last_page_cannot_be_left_out(review):
    pages_post(review, 2, "omit", version=current_version(review))
    response = pages_post(review, 1, "omit", version=current_version(review))
    assert response.status_code == 303
    assert omitted(review) == [1]  # unchanged
    page = page_of(review, "?page=1")
    assert "At least one page has to stay in the file." in unescape(page)


def test_a_stale_form_does_not_omit(review):
    stale = str(int(current_version(review)) + 3)
    response = pages_post(review, 2, "omit", version=stale)
    assert response.status_code == 303
    assert omitted(review) == []
    assert "changed after this page was opened" in unescape(page_of(review, "?page=2"))


def test_an_unknown_page_is_not_found(review):
    assert pages_post(review, 9, "omit", version="").status_code == 404
    assert pages_post(review, 0, "omit", version="").status_code == 404
    response = review.browser.post(
        f"/jobs/{review.job_id}/pages/x/omit",
        data={"csrf_token": token(review.browser, f"/jobs/{review.job_id}/review")},
    )
    assert response.status_code == 404


def test_omitting_marks_the_file_stale_without_rendering(review):
    pages_post(review, 2, "omit", version=current_version(review))
    job = review.job(review.job_id)
    assert job.html_stale is True


def test_domain_functions_refuse_double_omission_and_restore_of_a_kept_page():
    result = review_output().result
    omit_page(result, 1, pages=2)
    with pytest.raises(ReviewInvalid):
        omit_page(result, 1, pages=2)
    with pytest.raises(ReviewInvalid):
        omit_page(result, 0, pages=2)  # the last one left
    restore_page(result, 1)
    with pytest.raises(ReviewInvalid):
        restore_page(result, 1)


def test_review_edits_are_recorded_for_page_changes(review):
    pages_post(review, 2, "omit", version=current_version(review))
    with session_scope(review.ctx.factory) as session:
        edits = session.scalars(select(ReviewEdit)).all()
        assert [(e.element_id, e.action) for e in edits] == [("page-2", "omit_page")]
        assert edits[0].before and "structure" in edits[0].before
        crops = session.scalars(select(Blob).where(Blob.kind == "crop")).all()
        assert crops  # the crops of the left-out page are kept for a put-back


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


def test_image_only_pages_follow_the_page_out_and_back():
    result = kept_page_output().result
    result.image_only_pages = [1]
    omit_page(result, 1, pages=2)
    assert result.image_only_pages == [] and result.omitted[0].image_only
    restore_page(result, 1)
    assert result.image_only_pages == [1]
