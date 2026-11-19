"""Courses and their notation in the web app (FEATURE_NOTATION_MEMORY): the pages, owner
only access, the upload form's course choice, the worker's options, the review screen's
course lines and "remember for this course", and erasure with the user."""

from __future__ import annotations

import logging
import re
import threading
import time
from html import unescape

import pytest
from sqlalchemy import func, select

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, token
from readablestem.ops.retention import erase_user
from readablestem.web import shares
from readablestem.web.db import Course, CourseNotation, Job, User, session_scope
from readablestem.web.recipients import model_vendor

BOLD_D = "\U0001d41d"
BOLD_OMEGA = "\U0001d6da"
REALS = chr(0x211D)  # DOUBLE-STRUCK CAPITAL R


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, feature_notation_memory=True, feature_course_grants=True)
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


def request_token(html: str) -> str:
    """The request token of the page's first form that has one (SW43, `web.forms`)."""
    found = re.search(r'name="request_token" value="([^"]+)"', html)
    assert found, "no request token in the page"
    return found.group(1)


def test_one_click_makes_one_course_however_often_it_is_sent(web, monkeypatch):
    """The one submit rule (SW43): a form sent again with its request token gets the first
    answer and runs nothing, also when the two arrive together; another person's token
    replays nothing; a repeat with a new token finds the course; signing out forgets."""
    from readablestem.web import notation
    from readablestem.web.db import RequestToken

    sent = request_token(web.browser.get("/courses").text)
    first = post(web.browser, "/courses", name="Physics 1", request_token=sent)
    again = post(web.browser, "/courses", name="Physics 1", request_token=sent)
    assert first.status_code == again.status_code == 303
    assert again.headers["location"] == first.headers["location"]
    assert len(rows(web, Course)) == 1
    # A second form drawn before the first click saved (a new token): the course is there.
    fresh = post(web.browser, "/courses", name="Physics 1")
    assert fresh.headers["location"] == first.headers["location"]
    assert len(rows(web, Course)) == 1

    # Two requests with one token at the same moment: one course, the same two answers.
    slow = notation.create_course

    def create_slowly(*args, **kwargs):
        time.sleep(0.4)
        return slow(*args, **kwargs)

    monkeypatch.setattr(notation, "create_course", create_slowly)
    sent = request_token(web.browser.get("/courses").text)
    csrf = token(web.browser, "/courses")
    tabs = [web.client() for _ in range(2)]
    for tab in tabs:
        tab.cookies.update(dict(web.browser.cookies))
    answers: list = [None, None]

    def send(n: int) -> None:
        data = {"csrf_token": csrf, "name": "Physics 2", "request_token": sent}
        answers[n] = tabs[n].post("/courses", data=data)

    threads = [threading.Thread(target=send, args=(n,)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert [a.status_code for a in answers] == [303, 303]
    assert answers[0].headers["location"] == answers[1].headers["location"]
    assert sorted(c.name for c in rows(web, Course)) == ["Physics 1", "Physics 2"]

    # Bob sends Alice's token: his own course is made, nothing of hers comes back.
    bob = web.client()
    login(bob, BOB)
    theirs = post(bob, "/courses", name="Chemistry", request_token=sent)
    assert theirs.status_code == 303
    assert theirs.headers["location"] not in {a.headers["location"] for a in answers}
    assert sorted(c.name for c in rows(web, Course)) == ["Chemistry", "Physics 1", "Physics 2"]

    # Signing out ends the session's tokens.
    alice = next(u.id for u in rows(web, User) if u.email == ALICE)
    assert any(r.user_id == alice for r in rows(web, RequestToken))
    post(web.browser, "/logout", "/courses")
    assert all(r.user_id != alice for r in rows(web, RequestToken))


@pytest.mark.parametrize("kind", ["delete", "save", "question"])
def test_the_course_forms_answer_a_second_submit_as_the_first(web, monkeypatch, kind):
    """The one submit rule (SW43, stage 2) on forms of other kinds: a delete (a second one
    would be 'Page not found'), a save that redirects (a second one would run the update
    again), and the question a button asks before deleting (it writes nothing, so no
    token is kept and the page is the answer each time)."""
    from readablestem.web import notation
    from readablestem.web.db import RequestToken

    course_id = new_course(web.browser, "Physics 1")
    base = f"/courses/{course_id}"
    if kind == "delete":
        sent = request_token(web.browser.get(f"{base}/delete").text)
        sends = [
            post(web.browser, f"{base}/delete", base, confirm="yes", request_token=sent)
            for _ in range(2)
        ]
        assert [r.status_code for r in sends] == [303, 303]
        assert sends[0].headers["location"] == sends[1].headers["location"] == "/courses"
        assert rows(web, Course) == []
    elif kind == "save":
        add(web.browser, course_id, "bold d")
        (entry,) = rows(web, CourseNotation)
        saved = []
        update = notation.update_entry
        monkeypatch.setattr(
            notation, "update_entry", lambda *a, **k: saved.append(1) or update(*a, **k)
        )
        path = f"{base}/entries/{entry.id}"
        page = web.browser.get(path).text
        symbol = re.search(r'id="symbol"[^>]*value="([^"]*)"', page)
        assert symbol, "the entry page shows the symbol"
        fields = {"symbol": unescape(symbol.group(1)), "meaning": "matrix"}
        fields["request_token"] = request_token(page)
        sends = [post(web.browser, path, base, **fields) for _ in range(2)]
        assert [r.status_code for r in sends] == [303, 303]
        assert sends[0].headers["location"] == sends[1].headers["location"]
        assert saved == [1]
    else:
        sent = request_token(web.browser.get(base).text)
        sends = [post(web.browser, f"{base}/delete", base, request_token=sent) for _ in range(2)]
        assert [r.status_code for r in sends] == [200, 200]
        assert all("Delete Physics 1?" in r.text for r in sends)
        assert len(rows(web, Course)) == 1 and rows(web, RequestToken) == []


# --------------------------------------------------------------------------- owner only


def test_another_users_course_is_not_found(web):
    course_id = new_course(web.browser)
    add(web.browser, course_id, "bold d")
    (entry,) = rows(web, CourseNotation)
    bob = web.client()
    login(bob, BOB)
    base = f"/courses/{course_id}"
    for path in (
        base,
        f"{base}/delete",
        f"{base}/entries/{entry.id}",
        f"{base}/entries/{entry.id}/delete",
    ):
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
    missing = web.browser.get("/courses/not-an-id")
    assert missing.status_code == 404
    assert "There is no course at this address" in missing.text  # not "no document"
    assert "no document" not in bob.get(base).text


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
    # "Remove" only opens the question; the symbol stays until it is answered.
    assert f'href="{path}/delete"' in web.browser.get(path).text
    question = web.browser.get(f"{path}/delete")
    assert question.status_code == 200 and "Remove bold d from" in question.text
    assert f'action="{path}/delete"' in question.text and f'href="{path}"' in question.text
    assert len(rows(web, CourseNotation)) == 2
    removed = post(web.browser, f"{path}/delete", f"/courses/{course_id}")
    assert removed.status_code == 303
    assert post(web.browser, f"{path}/delete", f"/courses/{course_id}").status_code in (303, 404)
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


# --------------------------------------------------------------------------- what was applied

CAROL = "carol@example.org"


def read_for_course(web) -> str:
    """A finished document converted in a course that has bold v and the reals."""
    course_id = new_course(web.browser, "Physics 201")
    add(web.browser, course_id, "bold v")
    add(web.browser, course_id, "double-struck R", meaning="set")
    web.course_id = course_id
    web.job_id = job_id_from(upload_for(web.browser, course_id))
    assert web.run_worker() == web.job_id
    return web.job_id


def job_page_for(web, who: str) -> str:
    client = web.client()
    login(client, who)
    return text_of(client.get(f"/jobs/{web.job_id}").text)


def test_a_job_lists_the_symbols_it_was_read_with_for_its_owner_and_a_reviewer_not_a_reader(web):
    web.allow(CAROL)
    job_id = read_for_course(web)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        shares.share(session, job, ALICE, BOB, "review")
        shares.share(session, job, ALICE, CAROL, "view")
    add(web.browser, web.course_id, "bold F")  # added after the conversion
    for who in (ALICE, BOB):
        page = job_page_for(web, who)
        assert "bold v is a vector" in page and "double-struck R is a set" in page, who
        assert "bold F" not in page, who  # the record, not the course as it is now
    assert "Physics 201" in job_page_for(web, BOB)
    reader = job_page_for(web, CAROL)
    assert "bold v" not in reader and "Physics 201" not in reader


def test_a_course_reviewer_gets_what_a_reviewer_of_the_document_gets_and_no_more(web):
    """A person given the course to review reads the course's symbols on its documents as a
    document's reviewer does; the course's own pages, and keeping a letter for the course, stay
    the owner's."""
    job_id = read_for_course(web)
    ta, reader = "ta@example.org", "reader@example.org"
    for email in (ta, reader):
        web.allow(email)
    with session_scope(web.ctx.factory) as session:
        course = session.get(Course, web.course_id)
        shares.grant(session, course, ALICE, ta, "review")
        shares.grant(session, course, ALICE, reader, "view")
    page = job_page_for(web, ta)
    assert "bold v is a vector" in page and "Physics 201" in page
    assert "bold v" not in job_page_for(web, reader)
    client = web.client()
    login(client, ta)
    assert client.get(f"/jobs/{job_id}/review").status_code == 200
    assert client.get(f"/courses/{web.course_id}").status_code == 404
    fields = {"job": job_id, "item": "p1-e2", "symbol": BOLD_OMEGA, "meaning": "vector"}
    remember = post(client, f"/courses/{web.course_id}/remember", "/app", **fields)
    assert remember.status_code == 403  # the document is theirs to review, not the course's
    assert not any(e.canonical == BOLD_OMEGA for e in rows(web, CourseNotation))


def test_a_job_with_no_record_says_the_course_notation_was_applied_and_lists_nothing(web):
    job_id = read_for_course(web)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        stored = dict(job.result_json)
        assert {s["meaning"] for s in stored.pop("notation_applied")} == {"vector", "set"}
        job.result_json = stored
    page = web.browser.get(f"/jobs/{job_id}").text
    assert 'id="job-course"' in page and "Physics 201" in page
    assert "bold v" not in page and "double-struck" not in page


def test_the_privacy_sentence_names_the_provider_the_recipients_source_names(tmp_path):
    for provider in ("anthropic", "openai"):
        env = WebEnv(tmp_path / provider, feature_notation_memory=True, model_provider=provider)
        with env.running() as client:
            env.allow(ALICE)
            login(client, ALICE)
            env.browser = client
            course_id = new_course(client)
            add(client, course_id, "bold v")
            vendor = model_vendor(env.settings)
            pages = [
                client.get(path).text for path in ("/courses", f"/courses/{course_id}", "/app")
            ]
        for page in pages:
            text = text_of(page)
            assert f"sent to {vendor}" in text and "each equation" in text, provider


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
    assert "Nothing was saved:" in bad.text and "standard form" in bad.text  # says which check
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
