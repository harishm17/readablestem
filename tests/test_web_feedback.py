"""The two optional questions after a download (web/feedback.py) and the footer's
feedback link: when they show, what is stored, validation, once per document, off."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from fixtures.web.support import (
    ALICE,
    BOB,
    WebEnv,
    csrf_from,
    job_id_from,
    login,
    make_pdf,
    make_settings,
    token,
    upload,
)
from readablestem.web.db import Event, session_scope


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def finished(env) -> str:
    job_id = job_id_from(upload(env.browser, make_pdf(1)))
    assert env.run_worker() == job_id
    return job_id


def answers(env) -> list[tuple[str, str | None, float | None]]:
    with session_scope(env.ctx.factory) as session:
        rows = session.scalars(
            select(Event).where(Event.name.in_(["will_post", "minutes_saved"])).order_by(Event.id)
        )
        return [(e.name, e.code, e.value) for e in rows]


def send(env, job_id: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{job_id}/feedback")} | fields
    return env.browser.post(f"/jobs/{job_id}/feedback", data=data)


QUESTIONS = 'id="feedback-heading">Two quick questions (optional)</h3>'


def test_answers_are_stored_as_a_code_and_a_number_once_per_document(web):
    job_id = finished(web)
    response = send(web, job_id, will_post="not_sure", minutes=" 45 min ")
    assert response.status_code == 303 and response.headers["location"] == f"/jobs/{job_id}"
    assert answers(web) == [("will_post", "not_sure", None), ("minutes_saved", None, 45.0)]
    page = web.browser.get(f"/jobs/{job_id}").text
    assert "Thank you. Your answers were saved." in page
    assert QUESTIONS not in page  # asked once per document
    again = send(web, job_id, will_post="yes", minutes="5")
    assert again.status_code == 303
    assert len(answers(web)) == 2  # the first answers are kept
    assert "your first answers are kept" in web.browser.get(f"/jobs/{job_id}").text
    assert "You have answered these questions" in web.browser.get(f"/jobs/{job_id}/feedback").text


def test_other_people_cannot_see_or_answer(web):
    job_id = finished(web)
    bob = web.client()
    login(bob, BOB)
    assert bob.get(f"/jobs/{job_id}/feedback").status_code == 404
    bob_token = csrf_from(bob.get("/app").text)
    response = bob.post(
        f"/jobs/{job_id}/feedback", data={"csrf_token": bob_token, "will_post": "yes"}
    )
    assert response.status_code == 404
    assert answers(web) == []


@pytest.mark.parametrize("off", ["feedback_questions_enabled", "events_enabled"])
def test_no_questions_when_they_or_the_events_are_off(tmp_path, off):
    env = WebEnv(tmp_path, **{off: False})
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        job_id = finished(env)
        client.get(f"/jobs/{job_id}/download")
        page = client.get(f"/jobs/{job_id}").text
        assert QUESTIONS not in page
        assert "How to post it in your course" in page  # the posting link stays
        assert client.get(f"/jobs/{job_id}/feedback").status_code == 404
        response = client.post(
            f"/jobs/{job_id}/feedback",
            data={"csrf_token": token(client), "will_post": "yes"},
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------- footer link


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,x", "forms.example.edu"])
def test_the_feedback_address_must_be_a_web_address(tmp_path, bad):
    with pytest.raises(ValidationError, match="FEEDBACK_URL"):
        make_settings(tmp_path, feedback_url=bad)
    assert make_settings(tmp_path, feedback_url="  ").feedback_url is None
