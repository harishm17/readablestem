"""The web app over the real pipeline: upload -> worker -> review -> aggregate -> delete.

Every other web test uses `FakeConverter`, whose stored HTML is a stub, and the review
tests monkeypatch the validators and the assembler. So nothing else checks the contracts
between what the worker stores and what review does with it: that a review re-render
reproduces the worker's HTML, that crop blobs are keyed by the pipeline's element ids,
and that an inline equation edited in review stays inside its paragraph. This runs
`pipeline.run.convert_pdf` and `assemble` for real on a fixture PDF, with the offline
model client (no network, no cost).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from a11y_stem.core.types import DocumentResult, Kind
from a11y_stem.models import make_vision_client
from a11y_stem.ops.aggregate import aggregate_finished
from a11y_stem.pipeline.html_checks import static_checks
from a11y_stem.web.app import create_app
from a11y_stem.web.db import Blob, ErrorAggregate, Job, ReviewEdit, session_scope
from a11y_stem.web.review import rerender_html
from fixtures.web.support import ALICE, csrf_from, job_id_from, login, make_settings, upload

PDF = Path(__file__).parent / "fixtures" / "layout" / "worked_example.pdf"


class Site:
    def __init__(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path, max_pages_per_job=30)
        self.app = create_app(
            settings, client_factory=lambda s, b: make_vision_client(s, b, fake=True)
        )
        self.ctx = self.app.state.ctx

    def job(self, job_id: str) -> Job:
        with session_scope(self.ctx.factory) as session:
            job = session.get(Job, job_id)
            assert job is not None
            return job

    def crops(self, job_id: str) -> dict[str, bytes]:
        with session_scope(self.ctx.factory) as session:
            rows = session.scalars(select(Blob).where(Blob.job_id == job_id, Blob.kind == "crop"))
            return {b.element_id: b.content for b in rows if b.element_id}


@pytest.fixture
def site(tmp_path):
    site = Site(tmp_path)
    with TestClient(site.app, follow_redirects=False) as browser:
        from a11y_stem.web.auth import set_allowed

        with session_scope(site.ctx.factory) as session:
            set_allowed(session, ALICE, allowed=True)
        login(browser, ALICE)
        site.browser = browser
        site.job_id = job_id_from(upload(browser, PDF.read_bytes(), filename="worked.pdf"))
        from a11y_stem.web.worker import Worker

        worker = Worker(
            site.ctx.settings,
            site.ctx.factory,
            converter=site.ctx.converter,
            client_factory=site.ctx.client_factory,
        )
        assert worker.run_once() == site.job_id
        yield site


def paragraph(html: str, element_id: str) -> str:
    match = re.search(rf'<p id="{element_id}">.*?</p>', html, flags=re.S)
    assert match, element_id
    return match.group(0)


def without(html: str, *parts: str) -> str:
    for part in parts:
        html = html.replace(part, "")
    return html


def about(html: str) -> str:
    match = re.search(r'<footer id="about-conversion".*?</footer>', html, flags=re.S)
    assert match
    return match.group(0)


def review(site: Site, element_id: str, **fields: str):
    token = csrf_from(site.browser.get(f"/jobs/{site.job_id}/review").text)
    return site.browser.post(
        f"/jobs/{site.job_id}/review/{element_id}", data={"csrf_token": token} | fields
    )


def test_the_worker_stores_what_review_would_render(site):
    job = site.job(site.job_id)
    assert job.status == "done" and job.error_code is None and job.html
    result = DocumentResult.model_validate(job.result_json)
    crops = site.crops(site.job_id)
    # Crop blobs are keyed by the pipeline's element ids: one per equation and figure.
    model_ids = {e.id for e in result.elements if e.kind in (Kind.EQUATION, Kind.FIGURE)}
    assert set(crops) == model_ids == {"p1-e5", "p1-e7", "p1-e9", "p1-e10"}
    # A review decision re-renders the whole document from the stored result; without
    # any decision, that must give back exactly what the worker stored.
    assert rerender_html(result, crops) == job.html


def test_an_inline_equation_edited_in_review_stays_in_its_paragraph(site):
    before = site.job(site.job_id).html or ""
    # LaTeX that differs from the PDF's own text (this test is about where the output
    # puts it), so it is saved only with "Use my edit anyway" (web.edit_check).
    assert review(site, "p1-e9", action="edit", latex="v = 2 t").status_code == 422
    response = review(site, "p1-e9", action="edit_anyway", latex="v = 2 t")
    assert response.status_code == 303, response.text[:300]
    after = site.job(site.job_id).html or ""
    edited = paragraph(after, "p1-e8")
    assert re.search(r'<math[^>]*\bdisplay="inline"[^>]*\bid="p1-e9"|<math[^>]*\bid="p1-e9"[^>]*'
                     r'\bdisplay="inline"', edited)  # fmt: skip
    assert 'id="p1-e10"' in edited  # its neighbour is still there, unconverted
    # Nothing else changed but that paragraph (and the student note, which may say
    # whether the instructor has checked the file).
    assert without(after, edited, about(after)) == without(
        before, paragraph(before, "p1-e8"), about(before)
    )
    with session_scope(site.ctx.factory) as session:
        edits = session.scalars(select(ReviewEdit).where(ReviewEdit.job_id == site.job_id)).all()
        assert [(e.element_id, e.action) for e in edits] == [("p1-e9", "edit")]


def test_review_then_aggregate_then_delete(site):
    assert review(site, "p1-e3", action="reject").status_code == 303  # a heading
    assert aggregate_finished(site.ctx.factory) == 1
    with session_scope(site.ctx.factory) as session:
        rows = {(r.step, r.category): r.count for r in session.scalars(select(ErrorAggregate))}
    # Counted as the pipeline left it (accepted with an optional note: a single heading
    # with weak evidence), not as the reviewer changed it.
    assert rows[("heading", "status:accepted")] == 4
    assert rows[("heading", "tier:optional")] == 2
    assert rows[("heading", "tier:optional:heading.weak_evidence")] == 1
    assert ("heading", "status:rejected") not in rows
    assert rows[("equation", "status:abstained")] == 3
    assert rows[("job", "pages")] == 1 and rows[("job", "status:done")] == 1
    token = csrf_from(site.browser.get(f"/jobs/{site.job_id}").text)
    deleted = site.browser.post(
        f"/jobs/{site.job_id}/delete", data={"csrf_token": token, "confirm": "yes"}
    )
    assert deleted.status_code == 303
    with session_scope(site.ctx.factory) as session:
        assert session.get(Job, site.job_id) is None
        assert session.scalar(select(Blob).where(Blob.job_id == site.job_id)) is None
        rows = {(r.step, r.category): r.count for r in session.scalars(select(ErrorAggregate))}
    assert rows[("heading", "review:reject")] == 1  # review actions are counted at deletion


def test_a_review_decision_reruns_the_checks_on_the_real_output(site):
    """The review screen's browser tests stub the assembler; here the re-rendered file is
    the real one, and the job page lists the checks of what the user downloads."""
    assert review(site, "p1-e3", action="accept").status_code == 303  # a heading
    job = site.job(site.job_id)
    result = DocumentResult.model_validate(job.result_json)
    assert result.html_checks == static_checks(job.html or "")
    assert result.html_checks and all(c.passed for c in result.html_checks)
    assert result.fix_for("p1-e3").reviewed  # type: ignore[union-attr]
    note = about(job.html or "")
    assert "accepted" not in note and "reviewer" not in note  # no counts for students
    page = site.browser.get(f"/jobs/{site.job_id}").text
    assert f"All {len(result.html_checks)} checks passed" in page


def test_the_student_note_gives_the_date_once_the_last_decision_is_made(site):
    """While a decision is open the file says some parts are not checked yet; after the
    last one, the re-rendered file says the instructor checked them, and on which day."""
    from a11y_stem.pipeline.assemble import CHECKED, NOT_CHECKED_YET, long_date
    from a11y_stem.web.db import utcnow

    assert NOT_CHECKED_YET in about(site.job(site.job_id).html or "")
    result = DocumentResult.model_validate(site.job(site.job_id).result_json)
    for fix in [f for f in result.fixes if f.pending]:
        if fix.group_key:
            response = review(site, fix.group_key, action="reject")
        else:
            response = review(site, fix.element_id, action="reject")
        assert response.status_code == 303, response.text[:300]
        result = DocumentResult.model_validate(site.job(site.job_id).result_json)
    assert not any(f.pending for f in result.fixes)
    note = about(site.job(site.job_id).html or "")
    assert f"{CHECKED} on {long_date(utcnow().date())}." in note
    assert NOT_CHECKED_YET not in note


@pytest.mark.browser
def test_the_output_rerendered_after_review_passes_axe(site):
    """The review browser tests stub the assembler, so they check the review page, not
    the file a review produces. Here the real file after an edit and a reject
    goes through axe-core."""
    from a11y_stem.pipeline.html_checks import axe_checks

    # Differs from the PDF's own text: used only with "Use my edit anyway" (web.edit_check).
    assert review(site, "p1-e9", action="edit_anyway", latex="v = 2 t").status_code == 303
    assert review(site, "p1-e3", action="reject").status_code == 303  # a heading
    html = site.job(site.job_id).html or ""
    checks = axe_checks(html)
    if len(checks) == 1 and checks[0].code == "skipped":
        pytest.skip("axe-core needs Playwright and its Chromium")
    assert [c.name for c in checks] == ["axe"] and checks[0].passed, checks
