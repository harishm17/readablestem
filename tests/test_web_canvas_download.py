"""The Canvas download of a finished job: `/jobs/{id}/download/canvas`, and its form on the
job page (web/routes/jobs.py, templates/_job_ready.html). The zip itself is tested in
test_export_canvas.py; here: who may get it, what the route does with the course number,
what the response says, and that the job page offers it without JavaScript."""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

import a11y_stem
import canvas_sanitizer as cs
from a11y_stem.core.types import Status
from a11y_stem.web import events
from a11y_stem.web.db import Job, session_scope
from a11y_stem.web.security import VIEW_CSP
from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, upload

DEMO = Path(a11y_stem.__file__).parent / "web" / "static" / "demo" / "index.html"


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def finished_job(env) -> str:
    job_id = job_id_from(upload(env.browser, make_pdf(2)))
    assert env.run_worker() == job_id
    return job_id


def ready_job(env) -> str:
    """A job with nothing left to decide, so its page is the finish line."""
    for fix in env.converter.output.result.fixes:
        if fix.pending:
            fix.status = Status.REJECTED if fix.status is Status.ABSTAINED else Status.ACCEPTED
            fix.reviewed = True
    return finished_job(env)


def zip_of(response) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(response.content))


def folder_of(job_id: str) -> str:
    return f"secret-heading-kinematics-{job_id[:6]}"


# --------------------------------------------------------------------------- the route


def test_a_finished_job_downloads_as_a_zip(web):
    job_id = finished_job(web)
    response = web.browser.get(f"/jobs/{job_id}/download/canvas")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith(
        f'attachment; filename="canvas-{folder_of(job_id)}.zip"'
    )
    assert response.headers["content-security-policy"] == VIEW_CSP
    archive = zip_of(response)
    assert archive.namelist() == ["index.html", "README.txt"]  # no course number, no page
    assert "SECRET-DOC-TEXT" in archive.read("index.html").decode()
    assert "<style" not in archive.read("index.html").decode()
    readme = archive.read("README.txt").decode()
    assert "course number" in readme and "have not been tried" in readme


def test_with_a_course_number_the_zip_also_holds_the_page_to_paste(web):
    job_id = finished_job(web)
    response = web.browser.get(f"/jobs/{job_id}/download/canvas?course=48213")
    assert response.status_code == 200
    archive = zip_of(response)
    assert archive.namelist() == ["index.html", "canvas-page.html", "README.txt"]
    assert "SECRET-DOC-TEXT" in archive.read("canvas-page.html").decode()
    readme = archive.read("README.txt").decode()
    assert folder_of(job_id) in readme and "48213" in readme


def test_the_pictures_of_a_real_document_are_files_in_the_zip(web):
    job_id = finished_job(web)
    source = DEMO.read_text(encoding="utf-8")
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.html = source
    response = web.browser.get(f"/jobs/{job_id}/download/canvas?course=48213")
    archive = zip_of(response)
    pictures = [n for n in archive.namelist() if n.startswith("images/")]
    assert len(pictures) == 7
    base = f"/courses/48213/file_contents/course%20files/{folder_of(job_id)}/"
    for name in ("index.html", "canvas-page.html"):
        text = archive.read(name).decode()
        assert "data:" not in text and "<style" not in text
        srcs = [i.attrs["src"] for i in cs.parse(text).find_all("img")]
        assert len(srcs) == 7
        expected = pictures if name == "index.html" else [base + p for p in pictures]
        assert sorted(srcs) == sorted(expected)
    # Canvas keeps all seven pictures and their alt text.
    page = cs.content(cs.sanitize(cs.parse(archive.read("canvas-page.html").decode())))
    assert [alt for _, alt in page.images] == [
        alt for _, alt in cs.content(cs.parse(source)).images
    ]
    assert all(src for src, _ in page.images)


@pytest.mark.parametrize("course", ["", "  ", "%20"])
def test_an_empty_course_number_is_the_same_as_none(web, course):
    job_id = finished_job(web)
    response = web.browser.get(f"/jobs/{job_id}/download/canvas?course={course}")
    assert response.status_code == 200
    assert zip_of(response).namelist() == ["index.html", "README.txt"]


@pytest.mark.parametrize("course", ["12a", "-1", "1/2", "٣", "9" * 21, "1%0A2"])
def test_a_course_number_that_is_not_digits_gets_a_plain_message(web, course):
    job_id = finished_job(web)
    response = web.browser.get(f"/jobs/{job_id}/download/canvas?course={course}")
    assert response.status_code == 400
    assert "Check the course number" in response.text
    assert "digits only" in response.text
    assert "SECRET" not in response.text


def test_the_download_is_recorded_like_the_html_download(web):
    job_id = finished_job(web)
    with session_scope(web.ctx.factory) as session:
        user_id = web.job(job_id).user_id
        assert not events.downloaded(session, user_id, job_id)
    assert web.browser.get(f"/jobs/{job_id}/download/canvas").status_code == 200
    with session_scope(web.ctx.factory) as session:
        assert events.downloaded(session, user_id, job_id)


# --------------------------------------------------------------------------- who may


def test_other_users_and_signed_out_visitors_get_nothing(web):
    job_id = finished_job(web)
    bob = web.client()
    login(bob, BOB)
    for path in (f"/jobs/{job_id}/download/canvas", f"/jobs/{job_id}/download/canvas?course=1"):
        response = bob.get(path)
        assert response.status_code == 404, path
        assert "SECRET" not in response.text
    anonymous = web.client().get(f"/jobs/{job_id}/download/canvas")
    assert anonymous.status_code in (303, 401)
    assert (
        "SECRET" not in anonymous.text
        and anonymous.headers.get("content-type") != "application/zip"
    )
    assert web.browser.get("/jobs/nonexistent/download/canvas").status_code == 404


def test_a_job_that_is_not_done_gives_no_zip(web):
    waiting = job_id_from(upload(web.browser, make_pdf(1)))
    response = web.browser.get(f"/jobs/{waiting}/download/canvas")
    assert response.status_code == 409 and "Not ready yet" in response.text


def test_a_job_past_its_retention_time_gives_no_zip(web):
    job_id = finished_job(web)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.retain_until = datetime.now(UTC) - timedelta(minutes=1)
    assert web.browser.get(f"/jobs/{job_id}/download/canvas").status_code == 404


def test_the_route_is_a_get_only(web):
    job_id = finished_job(web)
    assert web.browser.post(f"/jobs/{job_id}/download/canvas").status_code == 405


# --------------------------------------------------------------------------- the job page


def ready_section(html: str) -> str:
    start = html.index('id="ready"')
    return html[start : html.index("</section>", start)]


def test_the_job_page_offers_the_zip_with_a_plain_form(web):
    job_id = ready_job(web)
    page = web.browser.get(f"/jobs/{job_id}").text
    assert 'id="ready-heading"' in page
    ready = ready_section(page)
    form = re.search(r'<form class="canvas-download".*?</form>', ready, re.S)
    assert form, "the Canvas download form is missing from the ready section"
    text = form.group(0)
    # works without JavaScript: a GET form to the route
    assert f'method="get" action="/jobs/{job_id}/download/canvas"' in re.sub(r"\s+", " ", text)
    assert "csrf" not in text.lower()  # a GET, like the HTML download
    # one button, named as asked, with its help attached
    assert ">For Canvas (zip with images)</button>" in text
    assert 'aria-describedby="canvas-help"' in text and 'id="canvas-help"' in text
    # the field has a visible label, is optional and digits only
    assert '<label for="canvas-course">Canvas course number (optional)</label>' in text
    assert 'id="canvas-course" name="course"' in text and "required" not in text
    assert 'inputmode="numeric"' in text and 'pattern="[0-9]{1,20}"' in text
    # the form is named by its heading; one sentence of help
    assert 'aria-labelledby="canvas-heading"' in text and 'id="canvas-heading"' in text
    help_text = re.search(r'<p id="canvas-help"[^>]*>(.*?)</p>', text, re.S)
    assert help_text and len(re.findall(r"[.!?](\s|$)", " ".join(help_text.group(1).split()))) == 1
    # the HTML download is still first and unchanged
    assert f'<a class="button" href="/jobs/{job_id}/download">Download the HTML file</a>' in ready
    assert ready.index("Download the HTML file") < ready.index("For Canvas (zip with images)")


def test_the_form_does_not_need_the_script(web):
    """The script only shows the two questions after a download; the form is plain HTML."""
    page = web.browser.get(f"/jobs/{ready_job(web)}").text
    form = re.search(r'<form class="canvas-download".*?</form>', page, re.S)
    assert form and "onsubmit" not in form.group(0) and "<script" not in form.group(0)
    app_js = (Path(a11y_stem.__file__).parent / "web" / "static" / "app.js").read_text()
    assert 'closest("form.canvas-download")' in app_js


def test_the_job_page_of_a_job_in_progress_has_no_canvas_form(web):
    waiting = job_id_from(upload(web.browser, make_pdf(1)))
    assert "canvas-download" not in web.browser.get(f"/jobs/{waiting}").text


def test_every_job_route_is_still_checked_for_the_owner(web):
    """The new route is in the list of routes another user must not reach."""
    job_id = finished_job(web)
    with session_scope(web.ctx.factory) as session:
        owner = session.scalar(select(Job.user_id).where(Job.id == job_id))
    assert owner is not None
    bob = web.client()
    login(bob, BOB)
    assert bob.get(f"/jobs/{job_id}/download/canvas").status_code == 404
