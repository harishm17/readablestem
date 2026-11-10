"""The account's "Keep new documents for 7 days by default" and the job page's retention line.

The setting is remembered on the account (`users.keep_week_default`) and only sets the starting
state of the upload form's 7-day box: each upload still decides, and the job page says, near the
top, when this document will be deleted, with the one-click way to keep it a week.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from fixtures.web.support import ALICE, WebEnv, csrf_from, job_id_from, login, make_pdf, upload
from readablestem.web.database import as_utc
from readablestem.web.db import User, session_scope
from readablestem.web.routes.account import KEEP_OFF, KEEP_ON


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def keep_box(page: str) -> str:
    match = re.search(r'<input type="checkbox" id="keep"[^>]*>', page)
    assert match, "no 7-day box"
    return match.group(0)


def stored(env) -> bool:
    with session_scope(env.ctx.factory) as session:
        user = session.query(User).filter_by(email=ALICE).one()
        return user.keep_week_default


def save(env, on: bool):
    page = env.browser.get("/account").text
    data = {"csrf_token": csrf_from(page)}
    if on:
        data["keep_default"] = "on"
    return env.browser.post("/account/keep-default", data=data, follow_redirects=False)


def test_the_setting_is_off_by_default_and_the_box_starts_unticked(web):
    assert stored(web) is False
    account = web.browser.get("/account").text
    assert "Keep new documents for 7 days by default" in account
    assert "checked" not in re.search(r'<input type="checkbox" id="keep-default"[^>]*>', account)[0]
    assert "checked" not in keep_box(web.browser.get("/app").text)


def test_turning_it_on_ticks_the_box_and_is_remembered(web):
    response = save(web, on=True)
    assert response.status_code == 303 and response.headers["location"] == "/account#account-keep"
    assert stored(web) is True
    assert KEEP_ON in web.browser.get("/account").text  # the notice, once
    app = web.browser.get("/app").text
    assert "checked" in keep_box(app)
    assert "Checked by your" in app and 'href="/account#account-keep"' in app


def test_the_person_can_still_untick_it_for_one_upload(web):
    save(web, on=True)
    job = web.job(job_id_from(upload(web.browser, make_pdf(1), keep=False)))
    assert job.retention_mode == "day"
    kept = web.job(job_id_from(upload(web.browser, make_pdf(1), keep=True)))
    assert kept.retention_mode == "week"
    assert as_utc(kept.retain_until) - as_utc(kept.created_at) == timedelta(days=7)


def test_turning_it_off_unticks_the_box(web):
    save(web, on=True)
    save(web, on=False)
    assert stored(web) is False
    assert KEEP_OFF in web.browser.get("/account").text
    assert "checked" not in keep_box(web.browser.get("/app").text)


def test_the_setting_needs_the_form_token(web):
    response = web.browser.post("/account/keep-default", data={"keep_default": "on"})
    assert response.status_code == 403 and stored(web) is False


def test_the_job_page_says_when_it_is_deleted_and_offers_a_week(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    web.run_worker()
    page = web.browser.get(f"/jobs/{job_id}").text
    line = page.split('id="retention-line"', 1)[1].split("</div>", 1)[0]
    flat = " ".join(re.sub(r"<[^>]+>", " ", line).split())
    assert "Deleted automatically on" in flat and "24 hours after you last work on it" in flat
    assert f'action="/jobs/{job_id}/keep"' in line and "Keep for 7 days" in flat
    # Keeping it: the line then says 7 days and the button goes (it was just extended).
    web.browser.post(f"/jobs/{job_id}/keep", data={"csrf_token": csrf_from(page)})
    page = web.browser.get(f"/jobs/{job_id}").text
    line = page.split('id="retention-line"', 1)[1].split("</div>", 1)[0]
    assert "7 days after you last work on it" in line and "/keep" not in line
