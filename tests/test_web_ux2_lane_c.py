"""The second UX round, lane C: page picking (a parsed preview), sharing, account and deletion,
courses (documents per course, the starter set, adding documents already converted), the named
confirmations, the batch page's next step and elapsed time, and the error pages.

Browser behaviour (the listening player, the page fields, the keyboard walks) is in the
`*_browser.py` files next to these."""

from __future__ import annotations

import re
from html import unescape

import pytest
from sqlalchemy import select

from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, token
from readablestem.web import ranges
from readablestem.web.db import CourseNotation, JobStatus, session_scope
from readablestem.web.routes.parts import preview_summary


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


# --------------------------------------------------------------------------- the parts preview


def test_the_preview_agrees_with_the_parser_on_what_is_acceptable():
    for text in ("1-30\n31-59", "1-3, 7\n9", "5"):
        good = ranges.parse_parts(text, total=59, limit=30)
        shown = ranges.preview(text, total=59, limit=30)
        assert [p.label for p in good] == [r.label for r in shown]
        assert [len(p.pages) for p in good] == [r.count for r in shown]
    assert ranges.preview("", total=59, limit=30) == []
    assert ranges.preview("1-3", total=9, limit=30, taken={0})[0].error.endswith(
        "page 1 was already converted."
    )


def test_too_many_parts_are_one_more_row_not_a_crash():
    text = "\n".join(str(n) for n in range(1, ranges.MAX_PARTS + 4))
    rows = ranges.preview(text, total=99, limit=30)
    assert len(rows) == ranges.MAX_PARTS + 1 and rows[-1].error.startswith("That is ")


def test_the_summary_says_how_many_parts_and_pages_and_where_today_runs_out():
    rows = ranges.preview("1-30\n31-59", total=59, limit=30)
    assert preview_summary(rows, 100) == "2 parts, 59 pages."
    assert preview_summary(rows, 40) == (
        "2 parts, 59 pages. Only 40 pages are left today: "
        "the first 1 part starts, the rest wait here."
    )
    assert "none starts today" in preview_summary(rows, 10)
    bad = ranges.preview("1-30\nx", total=59, limit=30)
    assert preview_summary(bad, 100).endswith("Fix 1 before converting.")
    assert preview_summary([], 100) == "No parts yet."


# --------------------------------------------------------------------------- the chooser page


@pytest.fixture
def parts_web(tmp_path):
    env = WebEnv(
        tmp_path,
        max_large_upload_mb=5,
        max_source_pages=40,
        per_user_daily_page_quota=40,
    )
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.alice = client
        env.bob = env.client()
        login(env.bob, BOB)
        yield env


def keep_big(env, pages: int = 12) -> str:
    response = env.alice.post(
        "/jobs",
        data={"csrf_token": token(env.alice)},
        files={"pdf": ("chapter2.pdf", make_pdf(pages), "application/pdf")},
    )
    assert response.status_code == 303 and response.headers["location"].startswith("/uploads/")
    return response.headers["location"].rsplit("/", 1)[-1]


def test_the_preview_is_a_fragment_for_the_typed_text_and_only_the_owners(parts_web):
    upload_id = keep_big(parts_web)
    url = f"/uploads/{upload_id}/preview"
    hx = {"HX-Request": "true"}
    fragment = parts_web.alice.get(url, params={"parts": "1-2\n3-6"}, headers=hx)
    assert fragment.status_code == 200 and "<html" not in fragment.text
    assert 'id="parts-summary"' in fragment.text and 'hx-swap-oob="innerHTML"' in fragment.text
    assert "2 parts, 6 pages." in text_of(fragment.text)
    assert 'data-remove-part="2"' in fragment.text
    empty = parts_web.alice.get(url, params={"parts": ""}, headers=hx)
    assert "No parts yet." in text_of(empty.text)
    # Someone else's file answers as a missing one (htmx is told to reload).
    other = parts_web.bob.get(url, params={"parts": "1"}, headers=hx)
    assert other.status_code == 404


# --------------------------------------------------------------------------- named confirmations


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, feature_notation_memory=True, signup_mode="open")
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def finished(env, name: str = "notes.pdf", client=None) -> str:
    client = client or env.browser
    response = client.post(
        "/jobs",
        data={"csrf_token": token(client)},
        files={"pdf": (name, make_pdf(1), "application/pdf")},
    )
    job_id = job_id_from(response)
    assert env.run_worker() == job_id
    return job_id


# --------------------------------------------------------------------------- account


# --------------------------------------------------------------------------- sharing


# --------------------------------------------------------------------------- courses


def new_course(env, name: str = "Physics 1") -> str:
    response = env.browser.post(
        "/courses", data={"csrf_token": token(env.browser, "/courses"), "name": name}
    )
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def post(env, path: str, page: str, **fields):
    return env.browser.post(path, data={"csrf_token": token(env.browser, page)} | fields)


def test_an_empty_course_page_offers_an_editable_starter_set_and_adds_only_the_ticked(web):
    course_id = new_course(web)
    page = web.browser.get(f"/courses/{course_id}").text
    assert "Start from common symbols" in page and page.count('class="starter-row"') == 6
    assert 'value="bold v"' in page and 'value="bold A"' in page
    assert re.search(r'<option value="matrix" selected>A matrix</option>', page)
    path = f"/courses/{course_id}"
    # Tick the first row (changed to "bold w") and the fifth; nothing else is added.
    symbols = ["bold w", "bold a", "bold F", "bold r", "bold A", "bold B"]
    meanings = ["vector", "vector", "vector", "vector", "matrix", "matrix"]
    response = web.browser.post(
        f"{path}/starter",
        data={
            "csrf_token": token(web.browser, path),
            "symbol": symbols,
            "meaning": meanings,
            "pick": ["0", "4"],
        },
    )
    assert response.status_code == 303 and response.headers["location"] == path
    with session_scope(web.ctx.factory) as session:
        added = sorted(
            (e.symbol, e.meaning, e.source) for e in session.scalars(select(CourseNotation))
        )
    assert added == [("bold A", "matrix", "user"), ("bold w", "vector", "user")]
    # Once the course has symbols the starter set is not offered again.
    assert "Start from common symbols" not in web.browser.get(path).text


def test_nothing_is_added_from_the_starter_set_unasked_or_unreadable(web):
    course_id = new_course(web)
    path = f"/courses/{course_id}"
    none = web.browser.post(
        f"{path}/starter",
        data={"csrf_token": token(web.browser, path), "symbol": ["bold v"], "meaning": ["vector"]},
    )
    assert none.status_code == 303
    assert web.browser.get(path).text.count("Tick the symbols to add first") == 1
    with session_scope(web.ctx.factory) as session:
        assert session.scalar(select(CourseNotation)) is None
    odd = web.browser.post(
        f"{path}/starter",
        data={
            "csrf_token": token(web.browser, path),
            "symbol": ["???"],
            "meaning": ["vector"],
            "pick": ["0"],
        },
    )
    assert odd.status_code == 303
    assert "1 could not be added" in text_of(web.browser.get(path).text)


def test_documents_already_converted_can_be_added_to_a_course_and_the_page_says_what_that_does(web):
    course_id = new_course(web)
    kept = finished(web, "kept.pdf")
    other = new_course(web, "Other")
    inside = web.browser.post(
        "/jobs",
        data={"csrf_token": token(web.browser), "course": other},
        files={"pdf": ("inside.pdf", make_pdf(1), "application/pdf")},
    )
    assert web.run_worker() == job_id_from(inside)
    path = f"/courses/{course_id}"
    page = web.browser.get(path).text
    assert "Add converted documents (1)" in page
    assert (
        f'id="attach-{kept}"' in page
        and job_id_from(inside) not in page.split("attach-documents")[1]
    )
    text = text_of(page)
    assert "It is not converted again" in text and "upload it again for this course" in text

    response = web.browser.post(
        f"{path}/documents",
        data={
            "csrf_token": token(web.browser, path),
            "job": [kept, job_id_from(inside), "0" * 32],
        },
    )
    assert response.status_code == 303
    assert web.job(kept).course_id == course_id
    assert web.job(job_id_from(inside)).course_id == other  # already in a course: left alone
    assert "Added 1 document to Physics 1. They were not converted again." in text_of(
        web.browser.get(path).text
    )
    assert "Add converted documents" not in web.browser.get(path).text


def test_only_ones_own_finished_documents_can_be_added(web):
    course_id = new_course(web)
    bob = web.client()
    login(bob, BOB)
    theirs = finished(web, "bobs.pdf", client=bob)
    queued = web.browser.post(
        "/jobs",
        data={"csrf_token": token(web.browser)},
        files={"pdf": ("queued.pdf", make_pdf(1), "application/pdf")},
    )
    path = f"/courses/{course_id}"
    web.browser.post(
        f"{path}/documents",
        data={"csrf_token": token(web.browser, path), "job": [theirs, job_id_from(queued)]},
    )
    assert web.job(theirs).course_id is None
    assert web.job(job_id_from(queued)).course_id is None
    assert web.job(job_id_from(queued)).status == JobStatus.QUEUED
    # Someone else's course is a 404.
    assert bob.post(
        f"{path}/documents", data={"csrf_token": token(bob), "job": [theirs]}
    ).status_code in {403, 404}


# --------------------------------------------------------------------------- the batch page


# --------------------------------------------------------------------------- error pages


def test_a_404_for_someone_elses_job_matches_one_for_a_made_up_id(web):
    bob = web.client()
    login(bob, BOB)
    theirs = finished(web, "bobs.pdf", client=bob)
    strangers = web.browser.get(f"/jobs/{theirs}")
    invented = web.browser.get(f"/jobs/{'1' * 32}")
    assert strangers.status_code == invented.status_code == 404
    assert text_of(strangers.text) == text_of(invented.text)
