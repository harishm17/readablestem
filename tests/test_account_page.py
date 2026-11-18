"""The account page: what it counts, the name it keeps, and "Download everything"."""

from __future__ import annotations

import pytest

from fixtures.web.support import ALICE, BOB, WebEnv, csrf_from, job_id_from, login, make_pdf, upload
from readablestem.web.db import User, session_scope


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def _documents_kept(page: str) -> str:
    return page.split("Documents kept now", 1)[1].split("</dd>", 1)[0].rsplit(">", 1)[1]


def test_documents_kept_leaves_out_the_sample_and_what_is_past_its_time(web):
    sample = web.browser.post(
        "/sample", data={"csrf_token": csrf_from(web.browser.get("/app").text)}
    )
    assert sample.status_code == 303
    assert _documents_kept(web.browser.get("/account").text) == "0"  # the sample is ours
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    assert _documents_kept(web.browser.get("/account").text) == "1"
    from datetime import timedelta

    from readablestem.web.db import Job, utcnow

    with session_scope(web.ctx.factory) as session:
        session.get(Job, job_id).retain_until = utcnow() - timedelta(hours=1)
    assert _documents_kept(web.browser.get("/account").text) == "0"


def test_a_saved_name_is_shown_after_the_save_takes_focus_and_survives_sign_in(web):
    page = web.browser.get("/account").text
    saved = web.browser.post(
        "/account/name", data={"csrf_token": csrf_from(page), "name": " Dana  Lee\n"}
    )
    # No fragment: a browser skips autofocus when the address has one, so the notice would
    # not take focus.
    assert saved.status_code == 303 and saved.headers["location"] == "/account"
    after = web.browser.get("/account").text
    assert "Your name is saved." in after and 'value="Dana Lee"' in after
    login(web.browser, ALICE)  # the dev sign-in carries no name; a provider's must not win either
    with session_scope(web.ctx.factory) as session:
        assert session.query(User).filter_by(email=ALICE).one().name == "Dana Lee"
    assert web.browser.post("/account/name", data={"name": "x"}).status_code == 403
