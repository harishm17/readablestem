"""W3: the Word and EPUB downloads of a finished job (`/jobs/{id}/download.docx` and
`.epub`, web/routes/exports.py), their buttons on the job page, and what they leave in the
usage events and the review record. The files themselves are tested in test_export_docx.py and
test_export_epub.py; here the routes follow the HTML download (tests/test_web_tier1.py,
test_web_events.py, test_web_canvas_download.py): who may get them, the clock, the events."""

from __future__ import annotations

import contextlib
import io
import re
import threading
import zipfile
from datetime import UTC, datetime, timedelta
from html import unescape

import docx
import pytest
from sqlalchemy import func, select

from fixtures.web.support import (
    BOB,
    as_current,
    csrf_from,
    fetch,
    job_id_from,
    login,
    make_pdf,
    upload,
)
from readablestem.core.types import DocumentResult, Kind
from readablestem.ops import retention
from readablestem.pipeline import export_canvas, export_docx, export_epub
from readablestem.web import documents, events, export_store
from readablestem.web.database import as_utc
from readablestem.web.db import Blob, Job, session_scope, utcnow
from readablestem.web.routes import exports
from readablestem.web.routes.jobs import download_filename
from readablestem.web.security import VIEW_CSP
from test_web_canvas_download import (  # noqa: F401
    finished_job,
    folder_of,
    ready_job,
    ready_section,
    web,
)

needs_pandoc = pytest.mark.skipif(not export_docx.available(), reason="pandoc is not installed")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PARAMS = [
    pytest.param("docx", DOCX, marks=needs_pandoc),
    pytest.param("epub", "application/epub+zip"),
]


def formats_of(env, job_id: str) -> list[str]:
    with session_scope(env.ctx.factory) as session:
        return events.downloaded_formats(session, job_id)


def set_job(env, job_id: str, **fields) -> None:
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        for name, value in fields.items():
            setattr(job, name, value)


# --------------------------------------------------------------------------- the routes


@needs_pandoc
def test_a_finished_job_downloads_as_a_word_file(web):  # noqa: F811
    job_id = ready_job(web)
    response = fetch(web.browser, f"/jobs/{job_id}/download.docx")
    assert response.status_code == 200
    assert response.headers["content-type"] == DOCX
    assert response.headers["content-disposition"].startswith(
        f'attachment; filename="{folder_of(job_id)}.docx"'
    )
    assert response.headers["content-security-policy"] == VIEW_CSP
    document = docx.Document(io.BytesIO(response.content))
    assert "SECRET-DOC-TEXT" in " ".join(p.text for p in document.paragraphs)


def test_a_finished_job_downloads_as_an_epub(web):  # noqa: F811
    job_id = ready_job(web)
    response = fetch(web.browser, f"/jobs/{job_id}/download.epub")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/epub+zip"
    assert response.headers["content-disposition"].startswith(
        f'attachment; filename="{folder_of(job_id)}.epub"'
    )
    assert response.headers["content-security-policy"] == VIEW_CSP
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.namelist()[0] == "mimetype"
    assert b"SECRET-DOC-TEXT" in archive.read("EPUB/content.xhtml")


@pytest.mark.parametrize(("kind", "media"), PARAMS)
def test_the_download_restarts_the_deletion_clock(web, kind, media):  # noqa: F811
    job_id = finished_job(web)
    set_job(web, job_id, retain_until=utcnow() + timedelta(hours=2))
    assert fetch(web.browser, f"/jobs/{job_id}/download.{kind}").status_code == 200
    left = as_utc(web.job(job_id).retain_until) - utcnow()
    assert timedelta(hours=23, minutes=55) < left <= timedelta(hours=24)


@pytest.mark.parametrize(
    "path",
    [
        "download",
        "download/canvas",
        "download/cartridge",
        pytest.param("download.docx", marks=needs_pandoc),
        "download.epub",
    ],
)
def test_a_head_request_gets_the_headers_of_the_get_and_takes_nothing(web, path):  # noqa: F811
    """A link checker or a browser asking what a link is: the GET's status and content
    headers, no body, and no effect (no clock restart, no download recorded)."""
    job_id = ready_job(web)
    set_job(web, job_id, retain_until=utcnow() + timedelta(hours=2))
    kept_until = web.job(job_id).retain_until
    head = web.browser.head(f"/jobs/{job_id}/{path}")
    assert web.job(job_id).retain_until == kept_until
    assert formats_of(web, job_id) == []
    get = fetch(web.browser, f"/jobs/{job_id}/{path}")
    assert head.status_code == get.status_code == 200
    assert head.content == b""
    for name in ("content-type", "content-disposition", "content-security-policy"):
        assert head.headers[name] == get.headers[name], name


# --------------------------------------------------------------------------- built once

STORED = [
    pytest.param("download.docx", export_docx, "export_docx", marks=needs_pandoc),
    ("download.epub", export_epub, "export_epub"),
    ("download/canvas", export_canvas, "export_canvas"),
    ("download/cartridge", export_canvas, "export_canvas"),
]


@pytest.mark.parametrize(("path", "module", "builder"), STORED)
def test_a_file_is_built_once_per_version_of_the_document(
    web,  # noqa: F811
    monkeypatch,
    path,
    module,
    builder,
):
    job_id = ready_job(web)
    url = f"/jobs/{job_id}/{path}"
    built = []
    real = getattr(module, builder)
    monkeypatch.setattr(module, builder, lambda *a, **k: (built.append(1), real(*a, **k))[1])

    first = fetch(web.browser, url)
    again = fetch(web.browser, url)
    assert first.status_code == again.status_code == 200
    assert first.content == again.content and len(built) == 1
    assert formats_of(web, job_id) == [path.removeprefix("download").lstrip("./")]  # still counted

    set_job(web, job_id, result_version=7)  # a decision was made
    fetch(web.browser, url)
    fetch(web.browser, url)
    assert len(built) == 2

    monkeypatch.setattr(export_store, "RENDER_VERSION", export_store.RENDER_VERSION + 1)
    fetch(web.browser, url)
    fetch(web.browser, url)
    assert len(built) == 3

    bob = web.client()
    login(bob, BOB)
    refused = bob.get(url)
    assert refused.status_code == 404 and refused.content != first.content  # not the stored file
    with session_scope(web.ctx.factory) as session:
        stored = session.scalars(select(Blob.element_id).where(Blob.kind == export_store.KIND))
        assert len(stored.all()) == 1  # the older versions went when the newer one was kept
        retention.delete_jobs(session, [job_id])
    with session_scope(web.ctx.factory) as session:
        assert session.scalar(select(func.count()).select_from(Blob)) == 0


def test_the_canvas_course_number_is_part_of_what_is_kept(web, monkeypatch):  # noqa: F811
    job_id = ready_job(web)
    built = []
    real = export_canvas.export_canvas
    monkeypatch.setattr(
        export_canvas, "export_canvas", lambda *a, **k: (built.append(1), real(*a, **k))[1]
    )
    for course in ("", "", "123", "123", "456"):
        assert (
            fetch(web.browser, f"/jobs/{job_id}/download/canvas?course={course}").status_code == 200
        )
    assert len(built) == 3


def test_combined_files_do_not_push_the_files_of_the_first_part_out(web):  # noqa: F811
    """The first part of a long upload holds its own Word, EPUB, Canvas (one per course number)
    and Cartridge files and the three combined ones: the combined files have their own count."""
    job_id = ready_job(web)
    built: list[str] = []

    def keep(file_type: str, title: str = "t") -> None:
        def build() -> bytes:
            built.append(file_type)
            return b"x"

        with session_scope(web.ctx.factory) as session:
            export_store.get_or_build(
                lambda: contextlib.nullcontext(session), job_id, 1, file_type, "", title, build
            )

    own = ["docx", "epub", "cartridge", "canvas", "canvas-2", "canvas-3"]
    combined = [export_store.COMBINED + t for t in ("html", "docx", "epub")]
    for file_type in (*own, *combined):
        keep(file_type)
    built.clear()
    for file_type in (*own, *combined):
        keep(file_type)
    assert built == []  # all nine are still kept: none was built again


# --------------------------------------------------------------------------- drafts

BANNER = re.compile(r"^Draft: \d+ decisions? (is|are) still open\. Not for students\.")
DRAFT_PATHS = [
    "download",
    "download/canvas",
    "download/cartridge",
    pytest.param("download.docx", marks=needs_pandoc),
    "download.epub",
]


def words(html: str) -> str:
    body = re.split(r"<body\b[^>]*>", html, maxsplit=1)[-1]
    return " ".join(re.sub(r"<[^>]+>", " ", body).split())


def opening_words(path: str, data: bytes) -> str:
    """The first words a reader meets in the downloaded file."""
    if path == "download":
        return words(data.decode())
    if path == "download.docx":
        return " ".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs if p.text)
    archive = zipfile.ZipFile(io.BytesIO(data))
    name = {"download/canvas": "index.html", "download.epub": "EPUB/content.xhtml"}.get(path)
    if name is None:  # the cartridge: its one web page
        name = next(n for n in archive.namelist() if n.endswith(".html"))
    return words(archive.read(name).decode())


@pytest.mark.parametrize("path", DRAFT_PATHS)
def test_a_draft_leaves_only_through_its_page_and_says_so_inside(web, path):  # noqa: F811
    """F-126: a document with decisions open cannot be fetched by its address alone, and the
    file it gives after the draft page is named and marked as a draft."""
    job_id = finished_job(web)
    for query in ("", "?draft=", "?draft=99999999999.forged"):
        asked = web.browser.get(f"/jobs/{job_id}/{path}{query}")
        assert asked.status_code == 303, query
        assert asked.headers["location"].startswith(f"/jobs/{job_id}/draft"), query
    response = fetch(web.browser, f"/jobs/{job_id}/{path}")
    assert response.status_code == 200
    name = re.search(r'filename="([^"]+)"', response.headers["content-disposition"])
    assert name is not None and name.group(1).rpartition(".")[0].endswith("-draft")
    assert BANNER.match(opening_words(path, response.content))
    if path == "download":
        assert '<meta name="readablestem-state" content="draft">' in response.text


def test_a_confirm_ends_with_a_change_a_student_would_get_and_not_with_other_writes(web):  # noqa: F811
    """WS20.1: the draft confirm names the output version. A new title changes the file, so
    the confirm no longer lets it through; answering the cut-off question changes no element
    the student gets, so a confirm made before it still does."""
    job_id = finished_job(web)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.result_json = {  # a file that looks cut off, as `test_web_batch` makes one
            **(job.result_json or {}),
            "notices": [{"code": "file_cut_off", "page": 1, "count": 1, "review": "required"}],
        }
    page = f"/jobs/{job_id}"

    def confirm() -> str:
        return fetch(web.browser, f"{page}/download").request.url.params["draft"]

    def allowed(token: str) -> bool:
        return web.browser.get(f"{page}/download?draft={token}").status_code == 200

    token = confirm()
    assert allowed(token)
    answered = web.browser.post(
        f"{page}/cut-off", data={"csrf_token": csrf_from(web.browser.get(page).text)}
    )
    assert answered.status_code == 303
    assert allowed(token)  # a write that leaves the file alone
    titled = web.browser.post(
        f"{page}/title",
        data={"csrf_token": csrf_from(web.browser.get(page).text), "title": "Week 4"},
    )
    assert titled.status_code == 303
    again = web.browser.get(f"{page}/download?draft={token}")
    assert again.status_code == 303 and "/draft" in again.headers["location"]


# --------------------------------------------------------------------------- who may


@pytest.mark.parametrize(("kind", "media"), PARAMS)
def test_other_users_and_signed_out_visitors_get_nothing(web, kind, media):  # noqa: F811
    job_id = finished_job(web)
    path = f"/jobs/{job_id}/download.{kind}"
    bob = web.client()
    login(bob, BOB)
    response = bob.get(path)
    assert response.status_code == 404 and "SECRET" not in response.text
    anonymous = web.client().get(path)
    assert anonymous.status_code in (303, 401) and anonymous.headers.get("content-type") != media
    assert fetch(web.browser, f"/jobs/nonexistent/download.{kind}").status_code == 404
    assert formats_of(web, job_id) == []  # a refused request records nothing


@pytest.mark.parametrize(("kind", "media"), PARAMS)
def test_a_job_that_is_not_done_gives_no_file(web, kind, media):  # noqa: F811
    waiting = job_id_from(upload(web.browser, make_pdf(1)))
    response = fetch(web.browser, f"/jobs/{waiting}/download.{kind}")
    assert response.status_code == 409 and "Not ready yet" in response.text


@pytest.mark.parametrize(("kind", "media"), PARAMS)
def test_a_job_past_its_retention_time_gives_no_file(web, kind, media):  # noqa: F811
    job_id = finished_job(web)
    set_job(web, job_id, retain_until=datetime.now(UTC) - timedelta(minutes=1))
    assert fetch(web.browser, f"/jobs/{job_id}/download.{kind}").status_code == 404


# --------------------------------------------------------------------------- when it cannot


def test_without_pandoc_there_is_no_word_button(web, monkeypatch):  # noqa: F811
    job_id = ready_job(web)
    monkeypatch.setattr(export_docx, "available", lambda *a, **k: False)
    response = fetch(web.browser, f"/jobs/{job_id}/download.docx")
    assert response.status_code == 503 and "not available here" in response.text
    assert formats_of(web, job_id) == []
    page = ready_section(web.browser.get(f"/jobs/{job_id}").text)
    assert "download.docx" not in page and f"/jobs/{job_id}/download.epub" in page
    assert fetch(web.browser, f"/jobs/{job_id}/download.epub").status_code == 200


def test_a_failed_word_export_is_a_plain_page_and_records_nothing(web, monkeypatch):  # noqa: F811
    job_id = finished_job(web)
    monkeypatch.setattr(export_docx, "available", lambda *a, **k: True)

    def refuse(*args, **kwargs):
        raise export_docx.DocxExportError("equations")

    monkeypatch.setattr(export_docx, "export_docx", refuse)
    response = fetch(web.browser, f"/jobs/{job_id}/download.docx")
    assert response.status_code == 409
    assert "cannot be made as a Word file" in response.text
    assert "SECRET" not in response.text
    assert formats_of(web, job_id) == []


def test_when_all_turns_are_taken_the_request_is_told_to_wait(web, monkeypatch):  # noqa: F811
    job_id = finished_job(web)
    monkeypatch.setattr(export_docx, "available", lambda *a, **k: True)
    monkeypatch.setattr(exports, "_turns", threading.BoundedSemaphore(1))
    monkeypatch.setattr(exports, "WAIT_S", 0.05)
    assert exports._turns.acquire(timeout=1)
    response = fetch(web.browser, f"/jobs/{job_id}/download.docx")
    assert response.status_code == 503 and response.headers["retry-after"] == "60"
    assert "Busy right now" in response.text


def test_a_failed_epub_export_is_a_plain_page(web):  # noqa: F811
    job_id = ready_job(web)  # a draft's page is made again from the result, not read from here
    set_job(
        web,
        job_id,
        html=as_current(
            '<html lang="en"><body><img src="data:image/svg+xml;base64,PHN2Zz4="></body></html>'
        ),
        html_stale=False,  # the page as stored, not made again from the result
    )
    response = fetch(web.browser, f"/jobs/{job_id}/download.epub")
    assert response.status_code == 409 and "cannot be made as an EPUB" in response.text
    assert formats_of(web, job_id) == []


# --------------------------------------------------------------------------- the job page


def test_a_document_with_decisions_open_offers_neither(web):  # noqa: F811
    job_id = finished_job(web)
    page = web.browser.get(f"/jobs/{job_id}").text
    assert "download.docx" not in page and "download.epub" not in page
    assert f"/jobs/{job_id}/draft" in page


# --------------------------------------------------------------------------- siblings


def test_download_all_without_pandoc_has_no_word_zip(web, monkeypatch):  # noqa: F811
    ready_job(web)
    assert "/app/download" not in web.browser.get("/app").text  # one document is not "all"
    ready_job(web)
    monkeypatch.setattr(export_docx, "available", lambda *a, **k: False)
    response = web.browser.get("/app/download?format=docx")
    assert response.status_code == 503 and "not available here" in response.text
    page = web.browser.get("/app").text
    assert "format=docx" not in page and "/app/download?format=epub" in page
    assert web.browser.get("/app/download?format=epub").status_code == 200


def test_a_document_that_cannot_be_made_is_left_out_and_said(web, monkeypatch):  # noqa: F811
    job_id = ready_job(web)
    set_job(
        web,
        job_id,
        html=as_current(
            '<html lang="en"><body><img src="data:image/svg+xml;base64,PHN2Zz4="></body></html>'
        ),
        html_stale=False,  # the page as stored, not made again from the result
    )
    response = web.browser.get("/app/download?format=epub")
    assert response.status_code == 409 and "cannot be made in that file type" in response.text
    assert formats_of(web, job_id) == []
    assert web.browser.get("/app/download").status_code == 200  # the HTML zip is unaffected


def test_when_all_turns_are_taken_a_word_zip_is_told_to_wait(web, monkeypatch):  # noqa: F811
    ready_job(web)
    monkeypatch.setattr(export_docx, "available", lambda *a, **k: True)
    monkeypatch.setattr(exports, "_turns", threading.BoundedSemaphore(1))
    monkeypatch.setattr(exports, "WAIT_S", 0.05)
    assert exports._turns.acquire(timeout=1)
    response = web.browser.get("/app/download?format=docx")
    assert response.status_code == 503 and response.headers["retry-after"] == "60"


# --------------------------------------------------------------------------- the record


def record_words(env, job_id: str) -> str:
    page = env.browser.get(f"/jobs/{job_id}/record").text
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))


def test_the_record_says_nothing_about_downloads_when_events_are_off(web):  # noqa: F811
    job_id = finished_job(web)
    fetch(web.browser, f"/jobs/{job_id}/download.epub")
    object.__setattr__(web.ctx.settings, "events_enabled", False)
    try:
        assert "File types downloaded" not in record_words(web, job_id)
    finally:
        object.__setattr__(web.ctx.settings, "events_enabled", True)


def test_the_record_does_not_vouch_for_the_other_files(web):  # noqa: F811
    words = record_words(web, finished_job(web))
    assert "Nobody has checked how they open in Word or a reading app" in words


@needs_pandoc
def test_download_everything_holds_each_file_type_the_records_and_the_settings(web):  # noqa: F811
    import json

    ready_job(web)
    response = web.browser.get("/account/export")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    for folder, ending in (("html", ".html"), ("word", ".docx"), ("epub", ".epub")):
        assert any(n.startswith(f"documents/{folder}/") and n.endswith(ending) for n in names)
    assert any(n.startswith("records/") and n.endswith(".csv") for n in names)
    assert json.loads(archive.read("account.json"))["email"]
    # Someone else's account holds none of it.
    bob = web.client()
    login(bob, BOB)
    other = zipfile.ZipFile(io.BytesIO(bob.get("/account/export").content)).namelist()
    assert not any(n.startswith(("documents/", "records/")) for n in other)
    bob.close()


# ------------------------------------------------------------------------------ one title


def _title_in(markup: str, tag: str) -> str:
    found = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", markup, re.S)
    assert found, tag
    return unescape(found.group(1)).strip()


def _titles_of(env, job_id: str) -> dict[str, str]:
    """What each place calls the document: the student page, the Word and EPUB files, the
    Canvas page, the Cartridge manifest and the feedback page."""
    found = {"html": _title_in(fetch(env.browser, f"/jobs/{job_id}/download").text, "title")}
    epub = zipfile.ZipFile(io.BytesIO(fetch(env.browser, f"/jobs/{job_id}/download.epub").content))
    found["epub"] = _title_in(epub.read("EPUB/package.opf").decode(), "dc:title")
    if export_docx.available():
        word = fetch(env.browser, f"/jobs/{job_id}/download.docx").content
        found["word"] = docx.Document(io.BytesIO(word)).core_properties.title
    canvas = fetch(env.browser, f"/jobs/{job_id}/download/canvas").content
    found["canvas"] = _title_in(
        zipfile.ZipFile(io.BytesIO(canvas)).read("index.html").decode(), "title"
    )
    cartridge = fetch(env.browser, f"/jobs/{job_id}/download/cartridge").content
    manifest = zipfile.ZipFile(io.BytesIO(cartridge)).read("imsmanifest.xml").decode()
    found["cartridge"] = _title_in(manifest, "title")
    feedback = _title_in(fetch(env.browser, f"/jobs/{job_id}/feedback").text, "h1")
    found["feedback"] = feedback.removeprefix("Two questions about ")
    return found


def test_a_document_has_one_title_in_every_place_and_file(web):  # noqa: F811
    typed = ready_job(web)
    set_job(web, typed, title_override="Unit 4 handout", html_stale=True)
    own = ready_job(web)  # the document's own title and heading, as its page writes them
    set_job(web, own, html_stale=True)  # the fake converter's page is not made by the renderer

    nameless = web.converter.output.result  # no title and no headings: the file name is it
    nameless.title = None
    nameless.elements = [e for e in nameless.elements if e.kind is not Kind.HEADING]
    kept = {e.id for e in nameless.elements}
    nameless.fixes = [f for f in nameless.fixes if f.element_id in kept]
    named = job_id_from(upload(web.browser, make_pdf(2), filename="week_3-notes.pdf"))
    assert web.run_worker() == named

    zipped = zipfile.ZipFile(io.BytesIO(fetch(web.browser, "/app/download").content)).namelist()
    for job_id, expected in ((typed, "Unit 4 handout"), (own, None), (named, "week 3 notes")):
        with session_scope(web.ctx.factory) as session:
            job = session.get(Job, job_id)
            wanted = documents.document_title(job).text
        assert expected is None or wanted == expected
        found = _titles_of(web, job_id)
        assert set(found.values()) == {wanted}, found
        assert download_filename(wanted, job_id) in zipped


def _version_of(env, job_id: str) -> dict[str, str]:
    """The version id and date as the job page, the student file's About block and its head say
    them (WS20.1), each asserted to say the same."""
    page = fetch(env.browser, f"/jobs/{job_id}").text
    on_page = re.search(r'id="job-version">Version (\w{6}-\d+), ([A-Z]\w+ \d+, \d{4})<', page)
    html = fetch(env.browser, f"/jobs/{job_id}/download").text
    in_about = re.search(r'class="about-version">Version (\w{6}-\d+), ([A-Z]\w+ \d+, \d{4})<', html)
    in_head = re.search(r'<meta name="readablestem-version" content="(\w{6}-\d+)">', html)
    assert on_page and in_about and in_head
    assert on_page.groups() == in_about.groups() and in_head.group(1) == on_page.group(1)
    assert on_page.group(1).startswith(job_id[:6])
    return {"id": on_page.group(1), "date": on_page.group(2)}


def test_each_change_to_a_document_moves_its_version_id_and_other_writes_do_not(web):  # noqa: F811
    """WS20.1: a saved decision and a new title each make the next version; the first render of
    a page that was only waiting to be made, and adding a share, do not."""
    job_id = finished_job(web)
    now = utcnow()
    today = f"{now:%B} {now.day}, {now.year}"
    first = _version_of(web, job_id)  # the first view makes the stored page: it counts nothing
    assert first == {"id": f"{job_id[:6]}-1", "date": today}
    assert not web.job(job_id).html_stale

    token = csrf_from(web.browser.get(f"/jobs/{job_id}/share").text)
    shared = web.browser.post(
        f"/jobs/{job_id}/share",
        data={"csrf_token": token, "email": "erin@example.org", "role": "view"},
    )
    assert shared.status_code == 303
    assert _version_of(web, job_id)["id"] == first["id"]

    stored = DocumentResult.model_validate(web.job(job_id).result_json)
    pending = next(f.element_id for f in stored.fixes if f.pending)
    decided = web.browser.post(
        f"/jobs/{job_id}/review/{pending}",
        data={
            "csrf_token": csrf_from(web.browser.get(f"/jobs/{job_id}/review").text),
            "view": "list",
            "action": "accept",
        },
    )
    assert decided.status_code == 303
    assert _version_of(web, job_id)["id"] == f"{job_id[:6]}-2"

    titled = web.browser.post(
        f"/jobs/{job_id}/title",
        data={"csrf_token": csrf_from(web.browser.get(f"/jobs/{job_id}").text), "title": "Week 4"},
    )
    assert titled.status_code == 303
    assert _version_of(web, job_id)["id"] == f"{job_id[:6]}-3"


# --------------------------------------------------------------------------- download all


def _statements(env, url: str) -> tuple[int, list[str]]:
    from sqlalchemy import event

    engine = env.ctx.factory.kw["bind"]
    seen: list[str] = []

    def spy(conn, cursor, statement, *rest):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", spy)
    try:
        status = env.browser.get(url).status_code
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    return status, seen


def test_download_all_reads_what_is_ready_from_one_query_however_many_documents(
    web,  # noqa: F811
    monkeypatch,
):
    """F-283: which documents are ready, held back or over the zip's cap is read from the jobs'
    summary columns in one query, so the statements do not grow with the documents, and a 409
    (nothing ready) loads no document."""
    from sqlalchemy import delete, select

    from readablestem.web import bundle

    first = ready_job(web)
    fetch(web.browser, f"/jobs/{first}")  # the first view makes the page the copies are of
    monkeypatch.setattr(bundle, "MAX_DOCUMENTS", 2)

    def copies(count: int, open_decisions: int) -> None:
        with session_scope(web.ctx.factory) as session:
            source = session.get(Job, first)
            assert source is not None
            for i in range(count):
                copy = Job(
                    user_id=source.user_id,
                    status=source.status,
                    filename=f"copy{i}.pdf",
                    page_count=source.page_count,
                    retain_until=source.retain_until,
                )
                copy.result_json = source.result_json
                copy.html = source.html
                session.add(copy)
        with session_scope(web.ctx.factory) as session:
            for job in session.scalars(select(Job)):
                job.open_decisions = open_decisions

    def drop_copies() -> None:
        with session_scope(web.ctx.factory) as session:
            session.execute(delete(Job).where(Job.id != first))

    for open_decisions, expected in ((3, 409), (0, 200)):  # all held back, then all ready
        copies(1, open_decisions)  # 2 documents
        status, few = _statements(web, "/app/download")
        copies(48, open_decisions)  # 50 documents
        status_many, many = _statements(web, "/app/download")
        assert (status, status_many) == (expected, expected)
        assert len(many) == len(few)  # ready: the two the cap takes, the rest cost nothing
        if open_decisions:
            assert not any("WHERE jobs.id = ?" in sql for sql in many)  # no document loaded
        drop_copies()
