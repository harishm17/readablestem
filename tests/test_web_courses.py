"""Courses and their notation in the web app (FEATURE_NOTATION_MEMORY): the pages, owner
only access, the upload form's course choice, the worker's options, the review screen's
course lines and "remember for this course", and erasure with the user."""

from __future__ import annotations

import logging
import re
from html import unescape

import pytest
from sqlalchemy import func, select

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, token
from readablestem.ops.retention import erase_user
from readablestem.web.db import Course, CourseNotation, Job, User, session_scope

BOLD_D = "\U0001d41d"
BOLD_OMEGA = "\U0001d6da"
REALS = chr(0x211D)  # DOUBLE-STRUCK CAPITAL R


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, feature_notation_memory=True)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


@pytest.fixture
def off(tmp_path):
    env = WebEnv(tmp_path)  # the flag's default: off
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def post(client, path: str, page: str | None = None, **fields):
    data = {"csrf_token": token(client, page or "/courses")} | fields
    return client.post(path, data=data)


def new_course(client, name: str = "Physics 1") -> str:
    response = post(client, "/courses", name=name)
    assert response.status_code == 303, response.text
    return response.headers["location"].rsplit("/", 1)[-1]


def add(client, course_id: str, symbol: str, meaning: str = "vector", note: str = ""):
    path = f"/courses/{course_id}"
    return post(client, f"{path}/entries", path, symbol=symbol, meaning=meaning, note=note)


def rows(env, model) -> list:
    with session_scope(env.ctx.factory) as session:
        return list(session.scalars(select(model)))


def upload_for(client, course_id: str | None, pdf: bytes | None = None):
    fields = {"csrf_token": token(client)}
    if course_id is not None:
        fields["course"] = course_id
    files = {"pdf": ("notes.pdf", pdf or make_pdf(1), "application/pdf")}
    return client.post("/jobs", data=fields, files=files)


# --------------------------------------------------------------------------- flag off


def test_with_the_flag_off_the_courses_pages_do_not_exist(off):
    assert off.browser.get("/courses").status_code == 404
    assert off.browser.get(f"/courses/{'a' * 32}").status_code == 404
    response = post(off.browser, "/courses", "/app", name="Physics")
    assert response.status_code == 404
    assert rows(off, Course) == []
    page = off.browser.get("/app").text
    assert 'name="course"' not in page and "/courses" not in page


# --------------------------------------------------------------------------- courses


def test_a_course_is_created_listed_renamed_and_deleted(web):
    page = web.browser.get("/courses").text
    assert "<h1>Your courses</h1>" in page and "You have no courses yet." in page
    course_id = new_course(web.browser, "Physics 1")
    (course,) = rows(web, Course)
    assert course.id == course_id and course.name == "Physics 1"
    listed = web.browser.get("/courses").text
    assert f'href="/courses/{course_id}">Physics 1</a>' in listed
    response = post(
        web.browser, f"/courses/{course_id}/rename", f"/courses/{course_id}", name="Mechanics"
    )
    assert response.status_code == 303
    assert "<h1>Mechanics</h1>" in web.browser.get(f"/courses/{course_id}").text
    # Delete asks first.
    asked = post(web.browser, f"/courses/{course_id}/delete", f"/courses/{course_id}")
    assert asked.status_code == 200 and "Delete Mechanics?" in asked.text
    assert len(rows(web, Course)) == 1
    done = post(web.browser, f"/courses/{course_id}/delete", f"/courses/{course_id}", confirm="yes")
    assert done.status_code == 303 and done.headers["location"] == "/courses"
    assert rows(web, Course) == []


def test_forms_need_the_csrf_token(web):
    response = web.browser.post("/courses", data={"name": "Physics", "csrf_token": "wrong"})
    assert response.status_code == 403
    assert rows(web, Course) == []


def test_names_and_symbols_are_escaped(web):
    course_id = new_course(web.browser, "<script>alert(1)</script>")
    add(web.browser, course_id, "bold d", note="<b>displacement</b>")
    page = web.browser.get(f"/courses/{course_id}").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page and "&lt;b&gt;displacement" in page


# --------------------------------------------------------------------------- owner only


def test_another_users_course_is_not_found(web):
    course_id = new_course(web.browser)
    add(web.browser, course_id, "bold d")
    (entry,) = rows(web, CourseNotation)
    bob = web.client()
    login(bob, BOB)
    base = f"/courses/{course_id}"
    for path in (base, f"{base}/delete", f"{base}/entries/{entry.id}"):
        assert bob.get(path).status_code == 404, path
    for path, fields in (
        (f"{base}/rename", {"name": "Mine"}),
        (f"{base}/delete", {"confirm": "yes"}),
        (f"{base}/entries", {"symbol": "bold v", "meaning": "vector"}),
        (f"{base}/entries/{entry.id}", {"symbol": "bold v", "meaning": "vector"}),
        (f"{base}/entries/{entry.id}/delete", {}),
    ):
        assert post(bob, path, **fields).status_code == 404, path
    assert [c.name for c in rows(web, Course)] == ["Physics 1"]
    assert [e.canonical for e in rows(web, CourseNotation)] == [BOLD_D]
    assert web.browser.get("/courses/not-an-id").status_code == 404


# --------------------------------------------------------------------------- entries


def test_an_entry_is_edited_and_removed(web):
    course_id = new_course(web.browser)
    add(web.browser, course_id, "bold d")
    add(web.browser, course_id, "bold v")
    entry = next(e for e in rows(web, CourseNotation) if e.canonical == BOLD_D)
    path = f"/courses/{course_id}/entries/{entry.id}"
    form = web.browser.get(path).text
    assert "<h1>Edit bold d</h1>" in form
    saved = post(web.browser, path, path, symbol="bold d", meaning="other", note="distance")
    assert saved.status_code == 303
    changed = next(e for e in rows(web, CourseNotation) if e.id == entry.id)
    assert (changed.meaning, changed.note) == ("other", "distance")
    clash = post(web.browser, path, path, symbol="bold v", meaning="vector")
    assert clash.status_code == 422 and "already has bold v" in clash.text
    removed = post(web.browser, f"{path}/delete", f"/courses/{course_id}")
    assert removed.status_code == 303
    assert [e.canonical for e in rows(web, CourseNotation)] == ["\U0001d42f"]


# --------------------------------------------------------------------------- upload


def test_an_upload_for_someone_elses_course_is_refused(web):
    bob = web.client()
    login(bob, BOB)
    bobs = new_course(bob, "Bob's course")
    response = upload_for(web.browser, bobs)
    assert response.status_code == 400
    assert "That course was not found" in response.text and 'href="#course"' in response.text
    assert rows(web, Job) == []
    assert upload_for(web.browser, "not-a-course-id").status_code == 400


# --------------------------------------------------------------------------- worker


# --------------------------------------------------------------------------- review


@pytest.fixture
def reviewing(web):
    """The review document (a bold omega read as plain: decision 1) for a course."""
    web.converter.output = review_output()
    web.course_id = new_course(web.browser, "Physics 1")
    add(web.browser, web.course_id, "bold v")
    web.job_id = job_id_from(upload_for(web.browser, web.course_id, review_pdf()))
    web.run_worker()
    return web


def decide(env, target: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review")} | fields
    response = env.browser.post(f"/jobs/{env.job_id}/review/{target}", data=data)
    assert response.status_code == 303, response.text
    return env.browser.get(response.headers["location"]).text


def test_a_letter_the_course_has_is_not_offered_again(reviewing):
    add(reviewing.browser, reviewing.course_id, r"\boldsymbol{\omega}")
    page = decide(reviewing, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}", view="split")
    assert "Remember for" not in page


def test_remember_checks_the_job_and_the_course(reviewing):
    bob = reviewing.client()
    login(bob, BOB)
    path = f"/courses/{reviewing.course_id}/remember"
    fields = {"job": reviewing.job_id, "item": "p1-e2", "symbol": BOLD_OMEGA, "meaning": "vector"}
    assert post(bob, path, "/courses", **fields).status_code == 404
    other = new_course(reviewing.browser, "Other")
    wrong = post(reviewing.browser, f"/courses/{other}/remember", **fields)
    assert wrong.status_code == 404
    bad = post(reviewing.browser, path, **(fields | {"symbol": "bold omega"}))
    assert bad.status_code == 400
    assert not any(e.canonical == BOLD_OMEGA for e in rows(reviewing, CourseNotation))


def test_remember_never_copies_an_item_the_stored_result_does_not_have(reviewing):
    """The address is built from the owned job and from an element the stored result has:
    a crafted `item` is not reflected."""
    path = f"/courses/{reviewing.course_id}/remember"
    fields = {"job": reviewing.job_id, "symbol": BOLD_OMEGA, "meaning": "vector"}
    response = post(reviewing.browser, path, **(fields | {"item": "p9-e9999"}))
    assert response.status_code == 303
    assert response.headers["location"] == f"/jobs/{reviewing.job_id}/review#decision-title"
    response = post(reviewing.browser, path, **(fields | {"item": "//evil.example/"}))
    assert response.headers["location"] == f"/jobs/{reviewing.job_id}/review#decision-title"


# --------------------------------------------------------------------------- privacy


def test_course_names_and_symbols_never_reach_the_logs(web, caplog):
    caplog.set_level(logging.INFO)
    course_id = new_course(web.browser, "SECRET-COURSE")
    add(web.browser, course_id, "bold d", note="SECRET-NOTE")
    post(web.browser, f"/courses/{course_id}/rename", f"/courses/{course_id}", name="SECRET-2")
    logged = "\n".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "course_created" in logged
    for secret in ("SECRET-COURSE", "SECRET-NOTE", "SECRET-2", BOLD_D, "bold d"):
        assert secret not in logged


def test_courses_and_notation_go_with_the_user(web):
    course_id = new_course(web.browser)
    add(web.browser, course_id, "bold d")
    upload_for(web.browser, course_id)
    bob = web.client()
    login(bob, BOB)
    bobs = new_course(bob, "Bob's course")
    add(bob, bobs, "bold v")
    with session_scope(web.ctx.factory) as session:
        alice = session.scalar(select(User.id).where(User.email == ALICE))
        erase_user(session, alice)
    with session_scope(web.ctx.factory) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert [c.id for c in session.scalars(select(Course))] == [bobs]
        assert [e.course_id for e in session.scalars(select(CourseNotation))] == [bobs]
