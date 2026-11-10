"""A review click changes the stored result and only marks the HTML file out of date; the
file is made again when somebody asks for it (download, view, Canvas, the job page), once
for a run of clicks. The real pipeline and the offline client, as test_web_pipeline.py."""

from __future__ import annotations

import pytest

from readablestem.core.types import DocumentResult
from readablestem.pipeline.assemble import CHECKED
from readablestem.web.db import Job, session_scope
from test_web_pipeline import Site, csrf_from, paragraph, review, site  # noqa: F401


def accept(site: Site, element_id: str):
    return review(site, element_id, action="accept")


def decisions(site: Site) -> list[str]:
    result = DocumentResult.model_validate(site.job(site.job_id).result_json)
    return [f.element_id for f in result.fixes if f.pending]


@pytest.mark.parametrize("path", ["/view", "/download", "/download/canvas", ""])
def test_every_way_to_get_the_file_or_its_checks_refreshes_it(site, path):
    review(site, decisions(site)[0], action="reject")
    assert site.job(site.job_id).html_stale is True
    response = site.browser.get(f"/jobs/{site.job_id}{path}")
    assert response.status_code == 200, path
    assert site.job(site.job_id).html_stale is False, path


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


def test_the_student_note_is_dated_by_the_last_decision_not_by_the_render(site):
    for element_id in decisions(site):
        assert review(site, element_id, action="reject").status_code == 303
    with session_scope(site.ctx.factory) as session:
        from readablestem.web.db import ReviewEdit

        for edit in session.query(ReviewEdit).filter_by(job_id=site.job_id):
            edit.created_at = edit.created_at.replace(year=2026, month=1, day=2)
    served = site.browser.get(f"/jobs/{site.job_id}/download").text
    assert f"{CHECKED} on January 2, 2026." in served, served[-600:]


def test_the_file_made_again_keeps_the_mathml_setting_of_the_first_render(site):
    """`MATHML_ALTTEXT` is in the settings the app runs with: a re-render (a click, the
    contact line) writes the same <math> attributes as the conversion would, not the
    process-wide default."""
    from readablestem.core.types import EquationFix, Fix, Kind, Status

    mathml = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi><mo>=</mo><mn>2</mn></math>'
    )
    with session_scope(site.ctx.factory) as session:
        job = session.get(Job, site.job_id)
        assert job is not None
        result = DocumentResult.model_validate(job.result_json)
        target = next(e for e in result.elements if e.kind is Kind.EQUATION)
        equation = EquationFix(latex="x = 2", mathml=mathml, speech="x equals 2", display="block")
        result.fixes = [
            Fix(
                element_id=target.id,
                kind=Kind.EQUATION,
                status=Status.ACCEPTED,
                confidence=0.99,
                equation=equation,
            ),
            *[f for f in result.fixes if f.element_id != target.id],
        ]
        job.result_json = result.model_dump(mode="json", exclude={"html"})
        job.html_stale = True
    assert 'alttext="' not in site.browser.get(f"/jobs/{site.job_id}/download").text

    site.ctx.settings.mathml_alttext = True
    try:
        with session_scope(site.ctx.factory) as session:
            job = session.get(Job, site.job_id)
            assert job is not None
            job.html_stale = True
        served = site.browser.get(f"/jobs/{site.job_id}/download").text
        assert 'alttext="x equals 2"' in served
        token = csrf_from(site.browser.get(f"/jobs/{site.job_id}/review").text)
        contact = site.browser.post(
            f"/jobs/{site.job_id}/contact",
            data={"contact": "help@example.org", "csrf_token": token},
        )
        assert contact.status_code == 303
        assert 'alttext="x equals 2"' in (site.job(site.job_id).html or "")
    finally:
        site.ctx.settings.mathml_alttext = False
