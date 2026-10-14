"""The two optional questions after a download (web/feedback.py) and the footer's
feedback link: when they show, what is stored, validation, once per document, off."""

from __future__ import annotations

import re

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
from readablestem.web import feedback
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


def test_the_questions_wait_hidden_until_a_download(web):
    job_id = finished(web)
    page = web.browser.get(f"/jobs/{job_id}").text
    assert QUESTIONS in page
    assert '<div id="feedback" class="feedback" hidden data-after-download>' in page
    assert 'href="/help/posting">How to post it in your course</a>' in page
    web.browser.get(f"/jobs/{job_id}/download")
    page = web.browser.get(f"/jobs/{job_id}").text
    assert '<div id="feedback" class="feedback">' in page  # shown: a download was recorded
    assert "Will you post this for students?" in page
    assert "About how many minutes did this save you?" in page


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


def test_both_questions_are_optional(web):
    job_id = finished(web)
    assert send(web, job_id).status_code == 303
    assert answers(web) == []
    assert "Nothing was sent" in web.browser.get(f"/jobs/{job_id}").text
    assert send(web, job_id, minutes="0").status_code == 303
    assert answers(web) == [("minutes_saved", None, 0.0)]


@pytest.mark.parametrize(
    ("fields", "field_id", "message"),
    [
        ({"minutes": "about an hour"}, "minutes-saved", "whole number from 0 to 2000"),
        ({"minutes": "-5"}, "minutes-saved", "whole number"),
        ({"minutes": "100000"}, "minutes-saved", "whole number"),
        # Unicode digits that int() refuses (a superscript two) are an error, not a 500.
        ({"minutes": "\u00b2"}, "minutes-saved", "whole number"),
        ({"minutes": "\u2075"}, "minutes-saved", "whole number"),
        ({"will_post": "maybe"}, "will-post-yes", "Choose yes, no or not sure."),
    ],
)
def test_a_wrong_answer_is_explained_next_to_its_question(web, fields, field_id, message):
    job_id = finished(web)
    response = send(web, job_id, **fields)
    assert response.status_code == 422
    page = response.text
    assert f'href="#{field_id}"' in page  # error summary linked to the field
    assert message in page
    assert page.count('aria-invalid="true"') <= 1
    assert re.search(r"<title>Error: Two questions about [^<]+</title>", page)
    assert answers(web) == []
    if "minutes" in fields:  # what was typed is kept, so it can be corrected
        assert f'value="{fields["minutes"]}"' in page


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


def test_parse_reads_both_fields():
    assert feedback.parse("yes", "30") == (feedback.Answers("yes", 30), [])
    assert feedback.parse("", "") == (feedback.Answers(), [])
    assert feedback.parse("no", "12 minutes")[0].minutes == 12
    answers_, errors = feedback.parse("x", "1.5")
    assert answers_.empty and {e.field_id for e in errors} == {"will-post-yes", "minutes-saved"}


# --------------------------------------------------------------------------- footer link


def test_the_footer_links_to_feedback_by_email_or_a_configured_address(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        page = client.get("/").text
    assert (
        '<a href="mailto:help@example.org?subject=ReadableSTEM%20feedback">'
        "Send feedback by email</a>" in page
    )
    other = WebEnv(tmp_path / "url", feedback_url="https://forms.example.edu/a11y")
    with other.running() as client:
        page = client.get("/privacy").text
    assert '<a href="https://forms.example.edu/a11y">Send feedback</a>' in page


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,x", "forms.example.edu"])
def test_the_feedback_address_must_be_a_web_address(tmp_path, bad):
    with pytest.raises(ValidationError, match="FEEDBACK_URL"):
        make_settings(tmp_path, feedback_url=bad)
    assert make_settings(tmp_path, feedback_url="  ").feedback_url is None
