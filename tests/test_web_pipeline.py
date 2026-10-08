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
    match = re.search(r'<section id="about-conversion".*?</section>', html, flags=re.S)
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
    response = review(site, "p1-e9", action="edit", latex="v = 2 t")
    assert response.status_code == 303, response.text[:300]
    after = site.job(site.job_id).html or ""
    edited = paragraph(after, "p1-e8")
    assert re.search(r'<math[^>]*\bdisplay="inline"[^>]*\bid="p1-e9"|<math[^>]*\bid="p1-e9"[^>]*'
                     r'\bdisplay="inline"', edited)  # fmt: skip
    assert 'id="p1-e10"' in edited  # its neighbour is still there, unconverted
    # Nothing else changed but that paragraph and the About section's counts.
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
    # Counted as the pipeline left it (needs_review), not as the reviewer changed it.
    assert rows[("heading", "status:needs_review")] == 1
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
