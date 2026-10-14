"""The review edits on Postgres (T3-5, T3-7, T3-11, T3-16).

The stored result, the structural snapshots in `review_edits` (JSON), the crops of added
elements, the job row's spend counters and the stale-tab guard behave as on SQLite. The edits
take the job row lock first (`owned_job(lock=True)`), so two forms drawn at the same version that
arrive together are applied once; the spend of a model read is held and settled on the row.

Run with: uv run pytest -m postgres tests/test_web_edit_postgres.py
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import func, select

from a11y_stem.core.types import DocumentResult, Status
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.web.db import Blob, ReviewEdit, session_scope
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload, with_file
from test_web_edit_page import edit_output
from test_web_edit_tables import clean_output
from test_web_postgres import pg_url as pg_url  # fixture, re-exported for pytest
from test_web_postgres import server_url as server_url  # fixture, re-exported for pytest

pytestmark = pytest.mark.postgres

BOX = {"left": "10", "top": "55", "right": "50", "bottom": "70"}


def stored(env, job_id) -> DocumentResult:
    return DocumentResult.model_validate(env.job(job_id).result_json)


def test_the_structural_edits_are_stored_applied_and_undone(pg_url, tmp_path):
    from fixtures.web.review_doc import review_pdf

    env = WebEnv(tmp_path, database_url=pg_url)
    env.converter.output = edit_output()
    env.ctx.client_factory = lambda settings, budget: FakeVisionClient(
        budget=budget,
        defaults={"equation": {"is_equation": True, "latex": "x^2 + 1", "confidence": 0.99}},
    )
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        env.browser = client

        def post(path: str, **fields):
            data = {
                "csrf_token": token(client, f"/jobs/{job_id}/review"),
                "version": str(env.job(job_id).result_version or 0),
            }
            return client.post(f"/jobs/{job_id}{path}", data=data | fields)

        # The words, with a NUL and an invisible character in them (Postgres refuses NUL).
        assert post("/edit/p1-e4", action="text", text="New\x00 words​ here.").status_code == 303
        assert next(e for e in stored(env, job_id).elements if e.id == "p1-e4").text == (
            "New words here."
        )
        assert post("/edit/p1-e4", action="heading", level="2").status_code == 303
        assert (
            post("/pages/1/add", kind="picture", alt="A cart on a ramp.", **BOX).status_code == 303
        )
        before = env.job(job_id)
        assert post("/pages/1/add", kind="equation", **BOX).status_code == 303
        after = env.job(job_id)
        assert after.model_calls > before.model_calls and after.cost_usd > before.cost_usd
        assert post("/pages/2/omit").status_code == 303
        assert stored(env, job_id).omitted_pages == [1]
        result = stored(env, job_id)
        assert {"p1-x1", "p1-x2"} <= {e.id for e in result.elements}
        fix = result.fix_for("p1-x2")
        assert fix is not None and fix.status is Status.NEEDS_REVIEW and fix.review == "required"
        with session_scope(env.ctx.factory) as session:
            crops = set(session.scalars(select(Blob.element_id).where(Blob.kind == "crop")).all())
            assert {"p1-x1", "p1-x2"} <= crops
            actions = session.scalars(select(ReviewEdit.action).order_by(ReviewEdit.created_at))
            assert list(actions) == [
                "edit_text",
                "make_heading",
                "add_figure",
                "add_equation",
                "omit_page",
            ]
        html = with_file(env, job_id).html or ""
        assert "A cart on a ramp." in html and "Average speed" not in html
        # Undo takes back the last write only (the page left out); an undo is never undone.
        assert post("/undo").status_code == 303
        assert stored(env, job_id).omitted == []
        # An added picture goes with its crop: add one more, undo it.
        assert (
            post("/pages/1/add", kind="picture", alt="Another picture here.", **BOX).status_code
            == 303
        )
        assert any(e.id == "p1-x3" for e in stored(env, job_id).elements)
        assert post("/undo").status_code == 303
        result = stored(env, job_id)
        assert not any(e.id == "p1-x3" for e in result.elements)
        assert {"p1-x1", "p1-x2"} <= {e.id for e in result.elements}
        with session_scope(env.ctx.factory) as session:
            crops = set(session.scalars(select(Blob.element_id).where(Blob.kind == "crop")).all())
            assert "p1-x3" not in crops and {"p1-x1", "p1-x2"} <= crops


def test_the_table_and_description_edits_on_postgres(pg_url, tmp_path):
    from fixtures.web.table_doc import table_pdf

    env = WebEnv(tmp_path, database_url=pg_url)
    env.converter.output = clean_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, table_pdf()))
        env.run_worker()
        env.browser = client

        def post(path: str, **fields):
            data = {
                "csrf_token": token(client, f"/jobs/{job_id}/review"),
                "version": str(env.job(job_id).result_version or 0),
                "view": "split",
            }
            return client.post(f"/jobs/{job_id}{path}", data=data | fields)

        assert post("/tables/accept-clean").status_code == 303
        result = stored(env, job_id)
        assert all(result.fix_for(e).status is Status.ACCEPTED for e in ("p2-e1", "p2-e2"))  # type: ignore[union-attr]
        assert post("/review/p1-e3", action="edit", change="not_a_table").status_code == 303
        assert (
            post("/review/p2-e0", action="describe", description="Three trials.").status_code == 303
        )
        result = stored(env, job_id)
        described = result.fix_for("p2-e0")
        assert described is not None and described.described == "Three trials."
        assert env.job(job_id).html_stale is True
        assert "Three trials." in (with_file(env, job_id).html or "")


def test_forms_drawn_at_the_same_version_are_applied_once(pg_url, tmp_path):
    from fixtures.web.review_doc import review_pdf

    env = WebEnv(tmp_path, database_url=pg_url)
    env.converter.output = edit_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        csrf = token(client, f"/jobs/{job_id}/review")
        cookies = dict(client.cookies)
        threads_n = 6
        barrier = threading.Barrier(threads_n)
        codes: list[int] = []
        errors: list[BaseException] = []

        def edit(n: int) -> None:
            try:
                tab = env.client()
                tab.cookies.update(cookies)
                barrier.wait(timeout=30)
                response = tab.post(
                    f"/jobs/{job_id}/edit/p1-e4",
                    data={
                        "csrf_token": csrf,
                        "action": "text",
                        "text": f"Edit number {n}.",
                        "version": "0",
                    },
                )
                codes.append(response.status_code)
            except BaseException as exc:  # reported below
                errors.append(exc)

        threads = [threading.Thread(target=edit, args=(i,)) for i in range(threads_n)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert not errors, errors
        # One is applied (303); the others are refused as stale (a redirect with a notice).
        assert env.job(job_id).result_version == 1
        with session_scope(env.ctx.factory) as session:
            assert session.scalar(select(func.count()).select_from(ReviewEdit)) == 1
        assert codes.count(303) == threads_n  # applied or refused, always a redirect
