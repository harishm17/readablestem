"""A review click changes the stored result and only marks the HTML file out of date; the
file is made again when somebody asks for it (download, view, Canvas, the job page), once
for a run of clicks. The real pipeline and the offline client, as test_web_pipeline.py."""

from __future__ import annotations

import pytest

from a11y_stem.core.types import DocumentResult
from a11y_stem.pipeline import assemble
from a11y_stem.pipeline.assemble import CHECKED
from a11y_stem.web.db import Job, session_scope
from a11y_stem.web.review import rerender_html
from a11y_stem.web.views import last_decision
from test_web_pipeline import Site, csrf_from, paragraph, review, site  # noqa: F401


def counting_renders(monkeypatch) -> list[int]:
    calls: list[int] = []
    original = assemble.render_html

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(assemble, "render_html", counted)
    return calls


def accept(site: Site, element_id: str):
    return review(site, element_id, action="accept")


def decisions(site: Site) -> list[str]:
    result = DocumentResult.model_validate(site.job(site.job_id).result_json)
    return [f.element_id for f in result.fixes if f.pending]


def test_a_click_marks_the_file_out_of_date_and_leaves_it_alone(site):
    before = site.job(site.job_id)
    assert before.html_stale is False and before.open_decisions == len(decisions(site))
    target = decisions(site)[0]
    assert review(site, target, action="reject").status_code == 303
    after = site.job(site.job_id)
    assert after.html_stale is True and after.html == before.html
    assert after.result_version == before.result_version + 1
    assert after.open_decisions == before.open_decisions - 1  # the list sees it at once


def test_a_run_of_clicks_costs_one_render_made_when_the_file_is_asked_for(site, monkeypatch):
    renders = counting_renders(monkeypatch)
    for element_id in decisions(site)[:3]:
        assert review(site, element_id, action="reject").status_code == 303
    assert renders == []  # no click rendered anything
    page = site.browser.get(f"/jobs/{site.job_id}/download")
    assert page.status_code == 200 and len(renders) == 1
    assert site.job(site.job_id).html_stale is False
    site.browser.get(f"/jobs/{site.job_id}/download")
    site.browser.get(f"/jobs/{site.job_id}/view")
    assert len(renders) == 1  # nothing changed since


def test_the_file_is_what_a_render_after_every_click_would_have_made(site):
    for element_id in decisions(site)[:2]:
        review(site, element_id, action="reject")
    served = site.browser.get(f"/jobs/{site.job_id}/download").text
    job = site.job(site.job_id)
    result = DocumentResult.model_validate(job.result_json)
    with session_scope(site.ctx.factory) as session:
        day = last_decision(session, session.get(Job, site.job_id))
    expected = rerender_html(result, site.crops(site.job_id), checked_on=day)
    assert served == expected == job.html
    # the checks stored with the result are those of this file
    assert [c.model_dump() for c in result.html_checks] == [
        c.model_dump() for c in DocumentResult.model_validate(job.result_json).html_checks
    ]


@pytest.mark.parametrize("path", ["/view", "/download", "/download/canvas", ""])
def test_every_way_to_get_the_file_or_its_checks_refreshes_it(site, path):
    review(site, decisions(site)[0], action="reject")
    assert site.job(site.job_id).html_stale is True
    response = site.browser.get(f"/jobs/{site.job_id}{path}")
    assert response.status_code == 200, path
    assert site.job(site.job_id).html_stale is False, path


def test_the_review_screens_do_not_make_the_file(site, monkeypatch):
    """Reading the review list or one item needs the result and the crops, not the file."""
    review(site, decisions(site)[0], action="reject")
    renders = counting_renders(monkeypatch)
    for path in ("/review", "/review?view=list", "/jobs/x"):
        site.browser.get(f"/jobs/{site.job_id}{path}")
    assert renders == [] and site.job(site.job_id).html_stale is True


def test_a_click_after_a_refresh_marks_it_again(site):
    ids = decisions(site)
    review(site, ids[0], action="reject")
    site.browser.get(f"/jobs/{site.job_id}/download")
    assert site.job(site.job_id).html_stale is False
    review(site, ids[1], action="reject")
    assert site.job(site.job_id).html_stale is True
    served = site.browser.get(f"/jobs/{site.job_id}/download").text
    assert served == site.job(site.job_id).html


def test_undo_marks_the_file_out_of_date_like_any_other_write(site):
    review(site, decisions(site)[0], action="reject")
    site.browser.get(f"/jobs/{site.job_id}/download")
    version = site.job(site.job_id).result_version
    page = site.browser.get(f"/jobs/{site.job_id}/review").text
    token = csrf_from(page)
    response = site.browser.post(
        f"/jobs/{site.job_id}/undo", data={"csrf_token": token, "version": str(version)}
    )
    assert response.status_code in (200, 303), response.status_code
    assert site.job(site.job_id).html_stale is True


def test_a_job_whose_file_is_missing_but_marked_is_made_when_asked(site):
    """What the stale-HTML migration leaves for a job downgraded and upgraded again."""
    with session_scope(site.ctx.factory) as session:
        job = session.get(Job, site.job_id)
        assert job is not None
        job.html, job.html_stale = None, True
    served = site.browser.get(f"/jobs/{site.job_id}/download")
    assert served.status_code == 200 and "<main" in served.text
    assert site.job(site.job_id).html == served.text


def test_the_student_note_is_dated_by_the_last_decision_not_by_the_render(site):
    for element_id in decisions(site):
        assert review(site, element_id, action="reject").status_code == 303
    with session_scope(site.ctx.factory) as session:
        from a11y_stem.web.db import ReviewEdit

        for edit in session.query(ReviewEdit).filter_by(job_id=site.job_id):
            edit.created_at = edit.created_at.replace(year=2026, month=1, day=2)
    served = site.browser.get(f"/jobs/{site.job_id}/download").text
    assert f"{CHECKED} on 2 January 2026." in served, served[-600:]
