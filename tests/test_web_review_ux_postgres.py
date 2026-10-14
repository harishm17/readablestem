"""The stale-tab guard on Postgres (UX review MF12, docs/web.md "Stale tabs").

Two tabs drawn at the same review version that decide at the same moment must not both
be applied: the job row is locked first (`owned_job(lock=True)`, SELECT ... FOR UPDATE),
so the version check and the count (`web.review.bump_result_version`) are one step. One
decision is applied, every other one is refused with a 409, and the version counts one
write.

Run with: uv run pytest -m postgres tests/test_web_review_ux_postgres.py
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import func, select

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload
from readablestem.web.db import ReviewEdit, session_scope
from test_web_postgres import pg_url as pg_url  # fixture, re-exported for pytest
from test_web_postgres import server_url as server_url  # fixture, re-exported for pytest

pytestmark = pytest.mark.postgres

THREADS = 8


def test_decisions_sent_at_once_from_one_page_are_applied_once(pg_url, tmp_path):
    env = WebEnv(tmp_path, database_url=pg_url)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        csrf = token(client, f"/jobs/{job_id}/review")
        cookies = dict(client.cookies)
        start = threading.Barrier(THREADS)
        codes: list[int] = []
        errors: list[BaseException] = []

        def decide(action: str) -> None:
            try:
                tab = env.client()
                tab.cookies.update(cookies)
                start.wait(timeout=30)
                response = tab.post(
                    f"/jobs/{job_id}/review/p1-e6",
                    data={"csrf_token": csrf, "action": action, "version": "0"},
                )
                codes.append(response.status_code)
            except BaseException as exc:  # reported below, with the thread's error
                errors.append(exc)

        threads = [
            threading.Thread(target=decide, args=("accept" if i % 2 else "reject",))
            for i in range(THREADS)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert not errors, errors
        assert sorted(codes) == [303] + [409] * (THREADS - 1), codes
        assert env.job(job_id).result_version == 1
        with session_scope(env.ctx.factory) as session:
            assert session.scalar(select(func.count()).select_from(ReviewEdit)) == 1
