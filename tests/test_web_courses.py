"""Courses and their notation in the web app (FEATURE_NOTATION_MEMORY): the pages, owner
only access, the upload form's course choice, the worker's options, the review screen's
course lines and "remember for this course", and erasure with the user."""

from __future__ import annotations

import logging
import re
from html import unescape

import pytest
from sqlalchemy import func, select

from a11y_stem.core.types import ConversionOptions
from a11y_stem.ops.retention import erase_user
from a11y_stem.web.db import Course, CourseNotation, Job, User, session_scope
from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, token

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


def test_with_the_flag_off_an_upload_ignores_a_course(off):
    with session_scope(off.ctx.factory) as session:
        user = session.scalar(select(User).where(User.email == ALICE))
        course = Course(user_id=user.id, name="Physics")
        session.add(course)
        session.flush()
        course_id = course.id
    response = upload_for(off.browser, course_id)
    assert response.status_code == 303
    assert off.job(job_id_from(response)).course_id is None
    off.run_worker()
    assert off.converter.calls[-1]["options"] is None


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


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("   ", "Enter a name for the course."),
        ("x" * 201, "200 characters or fewer"),
    ],
)
def test_a_course_name_must_be_given_and_short(web, name, message):
    response = post(web.browser, "/courses", name=name)
    assert response.status_code == 422
    assert message in response.text
    assert 'href="#name"' in response.text and 'aria-invalid="true"' in response.text
    assert rows(web, Course) == []


def test_two_courses_cannot_share_a_name(web):
    new_course(web.browser, "Physics 1")
    response = post(web.browser, "/courses", name="physics 1")
    assert response.status_code == 422 and "already have a course with this name" in response.text


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


@pytest.mark.parametrize(
    ("symbol", "key"),
    [("bold d", BOLD_D), (r"\mathbf{d}", BOLD_D), (BOLD_D, BOLD_D), ("double-struck R", REALS)],
)
def test_a_symbol_is_added_in_any_accepted_spelling(web, symbol, key):
    course_id = new_course(web.browser)
    response = add(web.browser, course_id, symbol, "vector", "displacement")
    assert response.status_code == 303
    (entry,) = rows(web, CourseNotation)
    assert (entry.canonical, entry.symbol, entry.source) == (key, symbol, "user")
    assert entry.note == "displacement"
    page = text_of(web.browser.get(f"/courses/{course_id}").text)
    assert "Added:" in page and "is a vector" in page


def test_a_symbol_the_tool_cannot_read_gets_examples(web):
    course_id = new_course(web.browser)
    response = add(web.browser, course_id, "the velocity thing")
    assert response.status_code == 422
    page = text_of(response.text)
    assert "cannot tell which symbol that is" in page and "“bold d”" in page
    assert 'href="#symbol"' in response.text
    assert 'value="the velocity thing"' in response.text  # kept for correcting
    assert rows(web, CourseNotation) == []


def test_the_same_symbol_twice_is_refused(web):
    course_id = new_course(web.browser)
    add(web.browser, course_id, "bold d")
    response = add(web.browser, course_id, r"\mathbf{d}")
    assert response.status_code == 422 and "already has bold d" in response.text
    assert len(rows(web, CourseNotation)) == 1


def test_a_meaning_must_be_one_of_the_list(web):
    course_id = new_course(web.browser)
    response = add(web.browser, course_id, "bold d", meaning="velocity")
    assert response.status_code == 422 and "Choose what the symbol means." in response.text


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


def test_deleting_a_course_keeps_its_jobs(web):
    course_id = new_course(web.browser)
    add(web.browser, course_id, "bold d")
    job_id = job_id_from(upload_for(web.browser, course_id))
    post(web.browser, f"/courses/{course_id}/delete", f"/courses/{course_id}", confirm="yes")
    assert rows(web, CourseNotation) == [] and rows(web, Course) == []
    assert web.job(job_id).course_id is None


# --------------------------------------------------------------------------- upload


def test_the_upload_form_offers_the_users_courses(web):
    page = web.browser.get("/app").text
    assert 'name="course"' not in page and 'href="/courses">add a course</a>' in page
    course_id = new_course(web.browser, "Physics 1")
    page = web.browser.get("/app").text
    assert '<label for="course">Course (optional)</label>' in page
    assert '<option value="">No course</option>' in page
    assert f'<option value="{course_id}">Physics 1</option>' in page


def test_an_upload_for_a_course_records_it_and_the_job_page_says_so(web):
    course_id = new_course(web.browser, "Physics 1")
    response = upload_for(web.browser, course_id)
    assert response.status_code == 303
    job_id = job_id_from(response)
    assert web.job(job_id).course_id == course_id
    page = web.browser.get(f"/jobs/{job_id}").text
    assert f'Course: <a href="/courses/{course_id}">Physics 1</a>' in page
    plain = job_id_from(upload_for(web.browser, None))
    assert web.job(plain).course_id is None
    assert 'id="job-course"' not in web.browser.get(f"/jobs/{plain}").text


def test_an_upload_error_keeps_the_course_chosen(web):
    # A file that is not a PDF: the form comes back with the course still chosen, so the
    # file chosen again is read with the course's notation.
    course_id = new_course(web.browser, "Physics 1, fall term")
    response = upload_for(web.browser, course_id, pdf=b"not a pdf at all")
    assert response.status_code == 415 and "is not a PDF" in response.text
    assert f'<option value="{course_id}" selected>Physics 1, fall term</option>' in response.text
    # Without a course chosen, none is.
    response = upload_for(web.browser, None, pdf=b"not a pdf at all")
    assert " selected>" not in response.text.split('id="course"', 1)[1].split("</select>")[0]


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


def test_the_worker_passes_the_courses_notation(web):
    course_id = new_course(web.browser)
    add(web.browser, course_id, "bold d")
    add(web.browser, course_id, "double-struck R", "set")
    upload_for(web.browser, course_id)
    web.run_worker()
    options = web.converter.calls[-1]["options"]
    assert isinstance(options, ConversionOptions) and options.notation_memory
    assert sorted((e.canonical, e.meaning) for e in options.notation) == [
        (REALS, "set"),
        (BOLD_D, "vector"),
    ]


def test_without_a_course_or_entries_the_worker_passes_no_options(web):
    upload_for(web.browser, None)
    web.run_worker()
    assert web.converter.calls[-1]["options"] is None
    empty = new_course(web.browser, "Empty")
    upload_for(web.browser, empty)
    web.run_worker()
    assert web.converter.calls[-1]["options"] is None


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


def test_using_bold_omega_offers_to_remember_it(reviewing):
    page = decide(reviewing, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}", view="split")
    assert "Remember for Physics 1: bold ω is a vector" in text_of(page)
    form = re.search(r'<form method="post" action="/courses/[^"]+/remember".*?</form>', page, re.S)
    assert form is not None
    fields = dict(re.findall(r'name="(\w+)" value="([^"]*)"', form.group(0)))
    assert fields["symbol"] == BOLD_OMEGA and fields["meaning"] == "vector"
    assert fields["job"] == reviewing.job_id and fields["item"] == "p1-e6"
    response = reviewing.browser.post(form.group(0).split('action="')[1].split('"')[0], data=fields)
    assert response.status_code == 303
    assert (
        response.headers["location"] == f"/jobs/{reviewing.job_id}/review?item=p1-e6#decision-title"
    )
    remembered = next(e for e in rows(reviewing, CourseNotation) if e.canonical == BOLD_OMEGA)
    assert (remembered.source, remembered.meaning, remembered.symbol) == (
        "review",
        "vector",
        "bold ω",
    )
    after = reviewing.browser.get(response.headers["location"]).text
    assert "Remembered for Physics 1: bold ω is a vector." in text_of(after)
    assert "Remember for Physics 1" not in text_of(after)  # offered once, after the decision


def test_a_decision_that_settles_no_style_offers_nothing(reviewing):
    page = decide(reviewing, "p1-e2", action="reject", view="split")
    assert "Remember for" not in page
    page = decide(reviewing, "p1-e6", action="accept", view="split")
    assert "Remember for" not in page


def test_a_letter_the_course_has_is_not_offered_again(reviewing):
    add(reviewing.browser, reviewing.course_id, r"\boldsymbol{\omega}")
    page = decide(reviewing, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}", view="split")
    assert "Remember for" not in page


def test_the_card_says_what_the_course_notation_says(reviewing):
    add(reviewing.browser, reviewing.course_id, "bold omega")
    page = reviewing.browser.get(f"/jobs/{reviewing.job_id}/review").text
    assert "Your course notation: bold ω is a vector." in text_of(page)
    assert "Your course notation: bold v" not in text_of(page)  # no v in this equation


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


def test_the_review_screen_without_a_course_has_no_notation(web):
    web.converter.output = review_output()
    web.job_id = job_id_from(upload_for(web.browser, None, review_pdf()))
    web.run_worker()
    page = decide(web, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}", view="split")
    assert "Remember for" not in page and "Your course notation" not in page


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
