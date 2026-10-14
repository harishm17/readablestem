"""T3-4: several files in one upload (one job each), the course page listing its documents,
and "download all" (a zip of what is ready).

The launch audit's controls still hold per file: the page quota, the cap on documents waiting
and kept, the rate limits (each file after the first counts as an upload), the owner check,
the token on every POST.
"""

from __future__ import annotations

import io
import re
import zipfile
from html import unescape

import pytest
from sqlalchemy import func, select

from fixtures.web.support import (
    ALICE,
    BOB,
    WebEnv,
    csrf_from,
    fake_output,
    login,
    make_pdf,
    token,
)
from readablestem.web.db import Event, Job, QuotaDay, session_scope
from readablestem.web.intake import MAX_ACTIVE_JOBS_PER_USER
from readablestem.web.uploads import MAX_FILES_PER_UPLOAD


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


@pytest.fixture
def web(tmp_path):
    env = WebEnv(
        tmp_path,
        feature_notation_memory=True,
        max_upload_mb=1,
        max_large_upload_mb=4,
        max_pages_per_job=30,
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
        env.bob.close()


def send(client, files: list[tuple[str, bytes]], **fields):
    return client.post(
        "/jobs",
        data={"csrf_token": token(client)} | fields,
        files=[("pdf", (name, data, "application/pdf")) for name, data in files],
    )


def answer(client, response):
    """The page the redirect after an upload of several files leads to."""
    assert response.status_code == 303, response.status_code
    assert response.headers["location"].startswith("/app/upload/")
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    return page


def jobs_of(env) -> list[Job]:
    with session_scope(env.ctx.factory) as session:
        rows = list(session.scalars(select(Job).order_by(Job.created_at, Job.id)))
        session.expunge_all()
        return rows


def pages_used(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return int(session.scalar(select(func.coalesce(func.sum(QuotaDay.pages), 0))) or 0)


# --------------------------------------------------------------------------- several files


def test_several_files_make_one_job_each(web):
    response = send(
        web.alice,
        [("week1.pdf", make_pdf(1)), ("week2.pdf", make_pdf(2)), ("week3.pdf", make_pdf(3))],
    )
    results = answer(web.alice, response)  # a redirect to the results page
    assert [j.filename for j in jobs_of(web)] == ["week1.pdf", "week2.pdf", "week3.pdf"]
    assert pages_used(web) == 6
    page = text_of(results.text)
    assert "3 of 3 files uploaded" in page
    for job in jobs_of(web):
        assert f'href="/jobs/{job.id}"' in results.text
    assert "Not uploaded" not in page


def test_one_file_among_several_behaves_as_before(web):
    response = send(web.alice, [("only.pdf", make_pdf(1))])
    assert response.status_code == 303 and response.headers["location"].startswith("/jobs/")


def test_a_file_that_cannot_be_used_does_not_stop_the_others(web):
    response = send(
        web.alice,
        [("good1.pdf", make_pdf(1)), ("notes.docx", b"PK not a pdf"), ("good2.pdf", make_pdf(1))],
    )
    assert [j.filename for j in jobs_of(web)] == ["good1.pdf", "good2.pdf"]
    page = text_of(answer(web.alice, response).text)
    assert "2 of 3 files uploaded" in page
    assert "notes.docx is not a PDF" in page and "Not uploaded" in page
    assert pages_used(web) == 2  # the refused file used none


def test_when_every_file_is_refused_nothing_is_queued_and_the_page_says_why(web):
    response = send(web.alice, [("a.docx", b"junk"), ("b.docx", b"more junk")])
    results = answer(web.alice, response)
    assert jobs_of(web) == [] and pages_used(web) == 0
    page = text_of(results.text)
    assert "0 of 2 files uploaded" in page and "a.docx is not a PDF" in page


def test_more_files_than_allowed_is_refused_whole(web):
    files = [(f"f{n}.pdf", make_pdf(1)) for n in range(MAX_FILES_PER_UPLOAD + 1)]
    response = send(web.alice, files)
    assert response.status_code == 400
    assert f"more than {MAX_FILES_PER_UPLOAD} files" in text_of(response.text)
    assert jobs_of(web) == []


def test_the_body_of_all_the_files_together_is_limited(web):
    big = make_pdf(1) + b"\0" * (2_500_000)
    response = send(web.alice, [("a.pdf", big), ("b.pdf", big)])  # 5 MB over a 4 MB request
    assert response.status_code == 413
    assert jobs_of(web) == []


def test_the_chosen_course_and_keeping_apply_to_every_file(web):
    made = web.alice.post(
        "/courses", data={"csrf_token": csrf_from(web.alice.get("/courses").text), "name": "P1"}
    )
    course_id = made.headers["location"].rsplit("/", 1)[-1]
    send(web.alice, [("a.pdf", make_pdf(1)), ("b.pdf", make_pdf(1))], course=course_id, keep="on")
    jobs = jobs_of(web)
    assert {j.course_id for j in jobs} == {course_id}
    assert {j.retention_mode for j in jobs} == {"week"}


def test_a_revised_version_replaces_one_document_not_several(web):
    web.converter.output = fake_output()
    first = send(web.alice, [("a.pdf", make_pdf(1))]).headers["location"].rsplit("/", 1)[-1]
    web.run_worker()
    response = send(web.alice, [("v2.pdf", make_pdf(1)), ("v3.pdf", make_pdf(1))], revises=first)
    assert response.status_code == 400 and "replaces one document" in text_of(response.text)
    assert len(jobs_of(web)) == 1


def test_each_file_is_held_to_the_daily_page_quota(web):
    # 40 pages a day: the second file would be the 31st page onward.
    response = send(web.alice, [("big.pdf", make_pdf(30)), ("more.pdf", make_pdf(20))])
    assert [j.filename for j in jobs_of(web)] == ["big.pdf"]
    page = text_of(answer(web.alice, response).text)
    assert "1 of 2 files uploaded" in page
    assert "more.pdf has 20 pages, but you have 10 of today's 40 pages left" in page


def test_the_cap_on_documents_waiting_holds_for_each_file(web):
    send(web.alice, [(f"w{n}.pdf", make_pdf(1)) for n in range(MAX_FILES_PER_UPLOAD)])
    assert len(jobs_of(web)) == MAX_ACTIVE_JOBS_PER_USER == MAX_FILES_PER_UPLOAD
    response = send(web.alice, [("late1.pdf", make_pdf(1)), ("late2.pdf", make_pdf(1))])
    assert len(jobs_of(web)) == MAX_ACTIVE_JOBS_PER_USER
    assert "You already have 10 documents waiting" in text_of(answer(web.alice, response).text)


def test_every_file_after_the_first_counts_for_the_rate_limit(tmp_path):
    env = WebEnv(tmp_path, rate_limits_enabled=True, per_user_daily_page_quota=1000)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        limit = env.ctx.limiter.rules["upload_user"].limit
        files = [(f"f{n}.pdf", make_pdf(1)) for n in range(MAX_FILES_PER_UPLOAD)]
        statuses = []
        for _ in range(3):
            statuses.append(send(client, files).status_code)
        # 10 files cost 10 of the 15 allowed uploads: the second request would need 10 more.
        assert limit == 15
        assert statuses[0] == 303 and statuses[1] == 429
        assert len(jobs_of(env)) == MAX_FILES_PER_UPLOAD


def test_refreshing_the_results_page_does_not_upload_again(web):
    """The answer is a GET page: opening it again (a refresh, the back button, a bookmark)
    changes nothing. Before, it was the POST's own response and a refresh sent every file
    again."""
    response = send(web.alice, [("a.pdf", make_pdf(1)), ("b.pdf", make_pdf(2))])
    assert response.status_code == 303
    url = response.headers["location"]
    first = web.alice.get(url)
    jobs, used = [j.id for j in jobs_of(web)], pages_used(web)
    for _ in range(3):
        again = web.alice.get(url)
        assert again.status_code == 200 and text_of(again.text) == text_of(first.text)
    assert [j.id for j in jobs_of(web)] == jobs and pages_used(web) == used == 3
    # Following the redirect as a browser does leaves the address bar on the GET page.
    followed = web.alice.post(
        "/jobs",
        data={"csrf_token": token(web.alice)},
        files=[
            ("pdf", ("c.pdf", make_pdf(1), "application/pdf")),
            ("pdf", ("d.pdf", make_pdf(1), "application/pdf")),
        ],
        follow_redirects=True,
    )
    assert str(followed.url).split("/")[-2:-1] == ["upload"] and followed.request.method == "GET"


def test_the_results_page_is_for_its_owner_and_for_an_hour(web):
    response = send(web.alice, [("secret-name.pdf", make_pdf(1)), ("b.docx", b"junk")])
    url = response.headers["location"]
    assert "secret-name.pdf" in web.alice.get(url).text
    stranger = web.bob.get(url)
    missing = web.bob.get("/app/upload/" + "0" * 32)
    assert stranger.status_code == missing.status_code == 404
    assert "secret-name.pdf" not in stranger.text
    assert web.client().get(url).status_code in {303, 401, 403}  # signed out
    # Past its hour the page is gone (404) and the purge deletes the row, names and all.
    from datetime import timedelta

    from readablestem.ops.retention import purge_upload_results
    from readablestem.web.db import UploadResult, utcnow

    later = utcnow() + timedelta(hours=2)
    with session_scope(web.ctx.factory) as session:
        row = session.scalar(select(UploadResult))
        assert row is not None and row.user_id == jobs_of(web)[0].user_id
        row.retain_until = utcnow() - timedelta(minutes=1)
    assert web.alice.get(url).status_code == 404
    with session_scope(web.ctx.factory) as session:
        assert purge_upload_results(session, later) == 1
        assert session.scalar(select(func.count()).select_from(UploadResult)) == 0


def test_the_upload_form_takes_several_files_and_says_so(web):
    page = web.alice.get("/app").text
    assert re.search(r'<input[^>]*type="file"[^>]*multiple', page)
    assert re.search(r'<input[^>]*name="pdf"', page)
    assert "up to 10 files" in text_of(page).lower() or "10 files" in text_of(page)


# --------------------------------------------------------------------------- the course page


def make_course(env, name="Physics 1") -> str:
    made = env.alice.post(
        "/courses", data={"csrf_token": csrf_from(env.alice.get("/courses").text), "name": name}
    )
    return made.headers["location"].rsplit("/", 1)[-1]


def finish_all(env) -> None:
    while env.run_worker():
        pass


def test_the_course_page_lists_its_documents_and_only_those(web):
    course = make_course(web)
    send(web.alice, [("in-a.pdf", make_pdf(1)), ("in-b.pdf", make_pdf(1))], course=course)
    send(web.alice, [("outside.pdf", make_pdf(1))])
    page = web.alice.get(f"/courses/{course}")
    assert page.status_code == 200
    text = text_of(page.text)
    assert "Documents in this course" in text
    assert "in-a.pdf" in text and "in-b.pdf" in text and "outside.pdf" not in text
    # The polled list stays inside the course.
    assert f"/app/documents?course={course}" in page.text
    polled = web.alice.get(f"/app/documents?course={course}")
    assert "outside.pdf" not in polled.text and "in-a.pdf" in polled.text
    assert "outside.pdf" in web.alice.get("/app/documents").text


def test_another_persons_course_documents_are_not_listed_to_you(web):
    course = make_course(web)
    send(web.alice, [("secret.pdf", make_pdf(1))], course=course)
    assert web.bob.get(f"/courses/{course}").status_code == 404
    polled = web.bob.get(f"/app/documents?course={course}")  # someone else's id: an empty list
    assert "secret.pdf" not in polled.text
    assert web.bob.get(f"/courses/{course}/download").status_code == 404


# --------------------------------------------------------------------------- download all


def open_zip(response) -> zipfile.ZipFile:
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(response.content))


def test_download_all_zips_what_is_ready_and_says_what_was_left_out(web):
    send(web.alice, [("ready1.pdf", make_pdf(1)), ("ready2.pdf", make_pdf(1))])
    web.converter.output = fake_output()
    finish_all(web)
    # Both have decisions open (the fake document needs three): decide every one of the first.
    jobs = jobs_of(web)
    first = jobs[0]
    assert "Nothing to download yet" in web.alice.get("/app/download").text
    for element_id, action in (("p1-e3", "accept"), ("p1-e4", "accept"), ("p1-e5", "reject")):
        page = f"/jobs/{first.id}/review"
        web.alice.post(
            f"{page}/{element_id}",
            data={"csrf_token": token(web.alice, page), "view": "list", "action": action},
        )
    archive = open_zip(web.alice.get("/app/download"))
    names = archive.namelist()
    assert len(names) == 2 and names[-1] == "contents.txt"
    html_name = next(n for n in names if n.endswith(".html"))
    assert html_name.endswith(f"-{first.id[:6]}.html")
    assert b"<html" in archive.read(html_name)
    contents = archive.read("contents.txt").decode()
    assert html_name in contents
    assert "Not included: 1 document with decisions still to make" in contents
    # Downloaded is recorded for the one document in the zip, and the clock restarted.
    with session_scope(web.ctx.factory) as session:
        downloads = session.scalar(
            select(func.count()).select_from(Event).where(Event.name == "downloaded")
        )
    assert downloads == 1


def test_the_zip_is_the_same_bytes_each_time(web):
    send(web.alice, [("a.pdf", make_pdf(1))])
    web.converter.output = fake_output()
    finish_all(web)
    job = jobs_of(web)[0]
    for element_id, action in (("p1-e3", "accept"), ("p1-e4", "accept"), ("p1-e5", "reject")):
        page = f"/jobs/{job.id}/review"
        web.alice.post(
            f"{page}/{element_id}",
            data={"csrf_token": token(web.alice, page), "view": "list", "action": action},
        )
    assert web.alice.get("/app/download").content == web.alice.get("/app/download").content


def test_a_course_zip_holds_only_that_courses_documents(web):
    course = make_course(web)
    send(web.alice, [("in.pdf", make_pdf(1))], course=course)
    send(web.alice, [("out.pdf", make_pdf(1))])
    web.converter.output = fake_output()
    finish_all(web)
    for job in jobs_of(web):
        for element_id, action in (("p1-e3", "accept"), ("p1-e4", "accept"), ("p1-e5", "reject")):
            page = f"/jobs/{job.id}/review"
            web.alice.post(
                f"{page}/{element_id}",
                data={"csrf_token": token(web.alice, page), "view": "list", "action": action},
            )
    mine = {j.filename: j.id for j in jobs_of(web)}
    everything = open_zip(web.alice.get("/app/download")).namelist()
    assert len([n for n in everything if n.endswith(".html")]) == 2
    only = open_zip(web.alice.get(f"/courses/{course}/download"))
    names = [n for n in only.namelist() if n.endswith(".html")]
    assert len(names) == 1 and names[0].endswith(f"-{mine['in.pdf'][:6]}.html")
    disposition = web.alice.get(f"/courses/{course}/download").headers["content-disposition"]
    assert "physics-1-documents.zip" in disposition


def test_the_zip_is_bounded(monkeypatch):
    from readablestem.web import bundle

    monkeypatch.setattr(bundle, "MAX_DOCUMENTS", 2)
    made = bundle.build([(f"T{n}", f"t{n}.html", "<html></html>") for n in range(5)])
    assert len(made.included) == 2 and made.left_out_size == 3
    archive = zipfile.ZipFile(io.BytesIO(made.data))
    assert archive.namelist() == ["t0.html", "t1.html", "contents.txt"]
    assert "3 more documents" in archive.read("contents.txt").decode()
    monkeypatch.setattr(bundle, "MAX_DOCUMENTS", 25)
    monkeypatch.setattr(bundle, "MAX_BYTES", 20)
    made = bundle.build([("A", "a.html", "x" * 15), ("B", "b.html", "y" * 15)])
    assert [n for _, n in made.included] == ["a.html"] and made.left_out_size == 1


def test_with_courses_off_the_course_zip_is_a_404(tmp_path):
    env = WebEnv(tmp_path)  # FEATURE_NOTATION_MEMORY off
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        assert client.get(f"/courses/{'0' * 32}/download").status_code == 404
