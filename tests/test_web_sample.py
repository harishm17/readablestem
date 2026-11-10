"""T3-9: the sample document, a recorded conversion to practise on before risking a real file.

It is made from the recorded result and the source PDF under static/demo/, with no model call
for anyone, no pages, no spend, no place taken from the caps, and nothing in the usage
numbers; everything else about it is an ordinary finished document of its owner.
"""

from __future__ import annotations

import re
from datetime import timedelta
from html import unescape

import pytest
from sqlalchemy import func, select

from fixtures.web.support import ALICE, BOB, WebEnv, csrf_from, login, make_pdf, token, upload
from readablestem.core.types import DocumentResult
from readablestem.ops.aggregate import aggregate_finished
from readablestem.ops.retention import purge_expired
from readablestem.web import sample
from readablestem.web.db import (
    Blob,
    ErrorAggregate,
    Event,
    Job,
    QuotaDay,
    ReviewEdit,
    SpendDay,
    session_scope,
    utcnow,
)


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, per_user_daily_page_quota=20, max_pages_per_job=30, max_upload_mb=5)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.alice = client
        env.bob = env.client()
        login(env.bob, BOB)
        yield env
        env.bob.close()


def open_sample(client) -> str:
    response = client.post("/sample", data={"csrf_token": token(client)})
    assert response.status_code == 303, response.text[:300]
    return response.headers["location"].rsplit("/", 1)[-1]


def table_count(env, model) -> int:
    with session_scope(env.ctx.factory) as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def get_job(env, job_id: str) -> Job:
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        session.expunge(job)
        return job


def test_opening_it_twice_gives_the_same_document_and_each_person_has_their_own(web):
    first = open_sample(web.alice)
    assert open_sample(web.alice) == first
    other = open_sample(web.bob)
    assert other != first
    assert table_count(web, Job) == 2
    # Nobody else can open yours.
    assert web.bob.get(f"/jobs/{first}").status_code == 404
    assert web.bob.get(f"/jobs/{first}/review").status_code == 404


def test_it_uses_no_pages_no_spend_and_takes_no_place_from_the_caps(web):
    from readablestem.web.intake import (
        MAX_ACTIVE_JOBS_PER_USER,
        MAX_LIVE_JOBS_PER_USER,
        live_job_count,
    )

    # The day's pages are used up, and the day's spend too: the sample still opens.
    web.settings.per_user_daily_page_quota = 1
    upload(web.alice, make_pdf(1))
    assert table_count(web, QuotaDay) == 1
    pages_before = table_count(web, QuotaDay)
    with session_scope(web.ctx.factory) as session:
        session.merge(
            SpendDay(day=utcnow().date(), spent_usd=web.settings.daily_spend_cap_usd, held_usd=0.0)
        )
    job_id = open_sample(web.alice)
    assert get_job(web, job_id).is_sample
    with session_scope(web.ctx.factory) as session:
        used = session.scalar(select(func.sum(QuotaDay.pages)))
        spent = session.scalar(select(func.sum(SpendDay.spent_usd)))
    assert used == 1 and spent == web.settings.daily_spend_cap_usd
    assert table_count(web, QuotaDay) == pages_before
    # It is not one of the documents waiting or kept.
    with session_scope(web.ctx.factory) as session:
        user_id = session.scalar(select(Job.user_id).where(Job.id == job_id))
        assert live_job_count(session, user_id, utcnow()) == 1  # only the uploaded file
    assert MAX_ACTIVE_JOBS_PER_USER == 10 and MAX_LIVE_JOBS_PER_USER == 40


def test_it_is_not_counted_in_the_usage_numbers(web):
    job_id = open_sample(web.alice)
    page = f"/jobs/{job_id}/review"
    # A decision, a view, a download: none writes a usage event.
    result = DocumentResult.model_validate(get_job(web, job_id).result_json)
    pending_fix = next(f for f in result.fixes if f.pending)
    decided = web.alice.post(
        f"{page}/{pending_fix.element_id}",
        data={"csrf_token": token(web.alice, page), "view": "list", "action": "accept"},
    )
    assert decided.status_code in {200, 303}
    assert web.alice.get(f"/jobs/{job_id}/view").status_code == 200
    assert web.alice.get(f"/jobs/{job_id}/download").status_code == 200
    assert table_count(web, ReviewEdit) >= 1
    with session_scope(web.ctx.factory) as session:
        mine = session.scalar(
            select(func.count()).select_from(Event).where(Event.job_ref == job_id)
        )
    assert mine == 0
    # No questions after the download of a sample, and a posted answer writes nothing.
    assert "will-post" not in web.alice.get(f"/jobs/{job_id}").text
    web.alice.post(
        f"/jobs/{job_id}/feedback",
        data={"csrf_token": token(web.alice, f"/jobs/{job_id}"), "will_post": "yes"},
    )
    assert table_count(web, Event) == table_count_of_signins(web)
    # The hourly counts leave it out, and so does its deletion.
    assert aggregate_finished(web.ctx.factory, utcnow()) == 0
    web.alice.post(
        f"/jobs/{job_id}/delete",
        data={"csrf_token": token(web.alice, "/app"), "confirm": "yes"},
    )
    with session_scope(web.ctx.factory) as session:
        assert session.scalar(select(func.count()).select_from(ErrorAggregate)) == 0


def table_count_of_signins(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return int(
            session.scalar(select(func.count()).select_from(Event).where(Event.name == "signed_in"))
            or 0
        )


def test_it_is_deleted_by_the_same_clock_as_every_document(web):
    job_id = open_sample(web.alice)
    with session_scope(web.ctx.factory) as session:
        session.get(Job, job_id).retain_until = utcnow() - timedelta(minutes=1)
    assert web.alice.get(f"/jobs/{job_id}").status_code == 404
    purge_expired(web.ctx.factory, utcnow())
    assert table_count(web, Job) == 0 and table_count(web, Blob) == 0


def test_a_sample_is_not_a_document_to_revise(web):
    job_id = open_sample(web.alice)
    assert 'name="revises"' not in web.alice.get(f"/app?revises={job_id}").text
    response = web.alice.post(
        "/jobs",
        data={"csrf_token": token(web.alice), "revises": job_id},
        files={"pdf": ("v2.pdf", make_pdf(1), "application/pdf")},
    )
    assert response.status_code == 400 and "was not found" in text_of(response.text)


def test_opening_it_needs_the_token_and_a_signed_in_person(web):
    assert web.alice.post("/sample", data={"csrf_token": "forged"}).status_code == 403
    assert web.alice.post("/sample").status_code == 403
    anonymous = web.client()
    try:
        assert anonymous.post("/sample", data={"csrf_token": "x"}).status_code in {303, 401, 403}
    finally:
        anonymous.close()
    assert table_count(web, Job) == 0


def test_without_its_files_the_section_is_not_offered_and_the_route_says_so(web, monkeypatch):
    monkeypatch.setattr(sample, "available", lambda: False)
    assert "sample-heading" not in web.alice.get("/app").text
    response = web.alice.post("/sample", data={"csrf_token": token(web.alice)})
    assert response.status_code == 404 and table_count(web, Job) == 0


def test_opening_it_is_rate_limited(tmp_path):
    env = WebEnv(tmp_path, rate_limits_enabled=True)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        rule = env.ctx.limiter.rules["sample"]
        statuses = [
            client.post(
                "/sample", data={"csrf_token": csrf_from(client.get("/app").text)}
            ).status_code
            for _ in range(rule.limit + 1)
        ]
        assert statuses[-1] == 429 and 429 not in statuses[: rule.limit]
        assert table_count(env, Job) == 1
