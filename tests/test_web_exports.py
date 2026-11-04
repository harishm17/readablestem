"""W3: the Word and EPUB downloads of a finished job (`/jobs/{id}/download.docx` and
`.epub`, web/routes/exports.py), their buttons on the job page, and what they leave in the
usage events and the review record. The files themselves are tested in test_export_docx.py and
test_export_epub.py; here the routes follow the HTML download (tests/test_web_tier1.py,
test_web_events.py, test_web_canvas_download.py): who may get them, the clock, the events."""

from __future__ import annotations

import io
import re
import threading
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import docx
import pytest
from sqlalchemy import select

import readablestem
from fixtures.web.support import BOB, job_id_from, login, make_pdf, upload
from readablestem.pipeline import export_docx
from readablestem.web import events
from readablestem.web.database import as_utc
from readablestem.web.db import Event, Job, session_scope, utcnow
from readablestem.web.routes import exports
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


def stored_events(env, name: str) -> list[Event]:
    with session_scope(env.ctx.factory) as session:
        return list(session.scalars(select(Event).where(Event.name == name)))


def set_job(env, job_id: str, **fields) -> None:
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        for name, value in fields.items():
            setattr(job, name, value)


# --------------------------------------------------------------------------- the routes


@needs_pandoc
def test_a_finished_job_downloads_as_a_word_file(web):  # noqa: F811
    job_id = finished_job(web)
    response = web.browser.get(f"/jobs/{job_id}/download.docx")
    assert response.status_code == 200
    assert response.headers["content-type"] == DOCX
    assert response.headers["content-disposition"].startswith(
        f'attachment; filename="{folder_of(job_id)}.docx"'
    )
    assert response.headers["content-security-policy"] == VIEW_CSP
    document = docx.Document(io.BytesIO(response.content))
    assert "SECRET-DOC-TEXT" in " ".join(p.text for p in document.paragraphs)


def test_a_finished_job_downloads_as_an_epub(web):  # noqa: F811
    job_id = finished_job(web)
    response = web.browser.get(f"/jobs/{job_id}/download.epub")
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
def test_the_download_is_recorded_like_the_html_download(web, kind, media):  # noqa: F811
    job_id = finished_job(web)
    assert formats_of(web, job_id) == []
    assert web.browser.get(f"/jobs/{job_id}/download.{kind}").status_code == 200
    with session_scope(web.ctx.factory) as session:
        assert events.downloaded(session, web.job(job_id).user_id, job_id)
    assert formats_of(web, job_id) == [kind]


@pytest.mark.parametrize(("kind", "media"), PARAMS)
def test_a_download_with_decisions_open_is_recorded_as_open(web, kind, media):  # noqa: F811
    job_id = finished_job(web)  # the fake converter leaves decisions open
    assert web.browser.get(f"/jobs/{job_id}/download.{kind}").status_code == 200
    [event] = [e for e in stored_events(web, "downloaded") if e.job_ref == job_id]
    assert event.code == "open" and (event.value or 0) > 0


@pytest.mark.parametrize(("kind", "media"), PARAMS)
def test_the_download_restarts_the_deletion_clock(web, kind, media):  # noqa: F811
    job_id = finished_job(web)
    set_job(web, job_id, retain_until=utcnow() + timedelta(hours=2))
    assert web.browser.get(f"/jobs/{job_id}/download.{kind}").status_code == 200
    left = as_utc(web.job(job_id).retain_until) - utcnow()
    assert timedelta(hours=23, minutes=55) < left <= timedelta(hours=24)


@pytest.mark.parametrize(("kind", "media"), PARAMS)
def test_the_clock_of_a_dev_job_is_not_touched(web, kind, media):  # noqa: F811
    job_id = finished_job(web)
    soon = utcnow() + timedelta(hours=2)
    set_job(web, job_id, retention_mode="dev", retain_until=soon)
    assert web.browser.get(f"/jobs/{job_id}/download.{kind}").status_code == 200
    assert as_utc(web.job(job_id).retain_until) == as_utc(soon)


@pytest.mark.parametrize(("kind", "media"), PARAMS)
def test_a_stale_file_is_made_again_first(web, kind, media):  # noqa: F811
    job_id = finished_job(web)
    set_job(web, job_id, html_stale=True)
    assert web.browser.get(f"/jobs/{job_id}/download.{kind}").status_code == 200
    assert web.job(job_id).html_stale is False


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
    assert web.browser.get(f"/jobs/nonexistent/download.{kind}").status_code == 404
    assert formats_of(web, job_id) == []  # a refused request records nothing


@pytest.mark.parametrize(("kind", "media"), PARAMS)
def test_a_job_that_is_not_done_gives_no_file(web, kind, media):  # noqa: F811
    waiting = job_id_from(upload(web.browser, make_pdf(1)))
    response = web.browser.get(f"/jobs/{waiting}/download.{kind}")
    assert response.status_code == 409 and "Not ready yet" in response.text


@pytest.mark.parametrize(("kind", "media"), PARAMS)
def test_a_job_past_its_retention_time_gives_no_file(web, kind, media):  # noqa: F811
    job_id = finished_job(web)
    set_job(web, job_id, retain_until=datetime.now(UTC) - timedelta(minutes=1))
    assert web.browser.get(f"/jobs/{job_id}/download.{kind}").status_code == 404


@pytest.mark.parametrize(("kind", "media"), PARAMS)
def test_the_route_is_a_get_only(web, kind, media):  # noqa: F811
    job_id = finished_job(web)
    assert web.browser.post(f"/jobs/{job_id}/download.{kind}").status_code == 405


# --------------------------------------------------------------------------- when it cannot


def test_without_pandoc_there_is_no_word_button(web, monkeypatch):  # noqa: F811
    job_id = ready_job(web)
    monkeypatch.setattr(export_docx, "available", lambda *a, **k: False)
    response = web.browser.get(f"/jobs/{job_id}/download.docx")
    assert response.status_code == 503 and "not available here" in response.text
    assert formats_of(web, job_id) == []
    page = ready_section(web.browser.get(f"/jobs/{job_id}").text)
    assert "download.docx" not in page and f"/jobs/{job_id}/download.epub" in page
    assert web.browser.get(f"/jobs/{job_id}/download.epub").status_code == 200


def test_a_failed_word_export_is_a_plain_page_and_records_nothing(web, monkeypatch):  # noqa: F811
    job_id = finished_job(web)
    monkeypatch.setattr(export_docx, "available", lambda *a, **k: True)

    def refuse(*args, **kwargs):
        raise export_docx.DocxExportError("equations")

    monkeypatch.setattr(export_docx, "export_docx", refuse)
    response = web.browser.get(f"/jobs/{job_id}/download.docx")
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
    response = web.browser.get(f"/jobs/{job_id}/download.docx")
    assert response.status_code == 503 and response.headers["retry-after"] == "60"
    assert "Busy right now" in response.text


def test_a_failed_epub_export_is_a_plain_page(web):  # noqa: F811
    job_id = finished_job(web)
    set_job(
        web,
        job_id,
        html='<html lang="en"><body><img src="data:image/svg+xml;base64,PHN2Zz4="></body></html>',
    )
    response = web.browser.get(f"/jobs/{job_id}/download.epub")
    assert response.status_code == 409 and "cannot be made as an EPUB" in response.text
    assert formats_of(web, job_id) == []


# --------------------------------------------------------------------------- the job page


@needs_pandoc
def test_the_job_page_offers_both_files_as_plain_links(web):  # noqa: F811
    job_id = ready_job(web)
    ready = ready_section(web.browser.get(f"/jobs/{job_id}").text)
    # "Where will you post it?": one list of routes, the web page first and recommended
    assert 'id="post-heading"' in ready and 'aria-labelledby="post-heading"' in ready
    assert ready.index('id="route-web"') < ready.index('id="route-word"')
    assert (
        f'<a class="button-secondary" href="/jobs/{job_id}/download.docx">'
        "Download as Word (.docx)</a>" in ready
    )
    assert (
        f'<a class="button-secondary" href="/jobs/{job_id}/download.epub">'
        "Download as EPUB</a>" in ready
    )
    # after the HTML download; Canvas, then Word, then EPUB, each with its own link
    assert ready.index("Download the HTML file") < ready.index("Download as Word")
    assert ready.index("For Canvas") < ready.index("Download as Word")
    assert ready.index("Download as Word") < ready.index("Download as EPUB")


def test_a_document_with_decisions_open_offers_neither(web):  # noqa: F811
    job_id = finished_job(web)
    page = web.browser.get(f"/jobs/{job_id}").text
    assert "download.docx" not in page and "download.epub" not in page
    assert f"/jobs/{job_id}/draft" in page


def test_the_questions_open_when_a_new_file_type_is_used():
    """app.js shows the two optional questions when a download link is used."""
    js = (Path(readablestem.__file__).parent / "web" / "static" / "app.js").read_text()
    for ending in ("/download", "/download/cartridge", "/download.docx", "/download.epub"):
        assert f'a[href$="{ending}"]' in js


# --------------------------------------------------------------------------- siblings


def test_the_canvas_and_cartridge_downloads_restart_the_clock_too(web):  # noqa: F811
    for path in ("download/canvas", "download/cartridge"):
        job_id = finished_job(web)
        set_job(web, job_id, retain_until=utcnow() + timedelta(hours=2))
        assert web.browser.get(f"/jobs/{job_id}/{path}").status_code == 200
        left = as_utc(web.job(job_id).retain_until) - utcnow()
        assert timedelta(hours=23, minutes=55) < left <= timedelta(hours=24), path


def test_every_download_names_its_file_type(web):  # noqa: F811
    job_id = finished_job(web)
    for path in ("download", "download/canvas", "download/cartridge", "download.epub"):
        assert web.browser.get(f"/jobs/{job_id}/{path}").status_code == 200
    assert formats_of(web, job_id) == ["html", "canvas", "cartridge", "epub"]


def test_download_all_counts_the_html_file_type(web):  # noqa: F811
    job_id = ready_job(web)
    response = web.browser.get("/app/download")
    assert response.status_code == 200
    assert formats_of(web, job_id) == ["html"]
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert [n for n in names if n.endswith((".docx", ".epub"))] == []  # the default is HTML


@pytest.mark.parametrize(("kind", "media"), PARAMS)
def test_download_all_can_make_the_zip_in_this_file_type(web, kind, media):  # noqa: F811
    job_id = ready_job(web)
    set_job(web, job_id, retain_until=utcnow() + timedelta(hours=2))
    response = web.browser.get(f"/app/download?format={kind}")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    word = "word" if kind == "docx" else kind
    assert response.headers["content-disposition"].startswith(
        f'attachment; filename="my-{word}-documents.zip"'
    )
    assert response.headers["content-security-policy"] == VIEW_CSP
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.namelist() == [f"{folder_of(job_id)}.{kind}", "contents.txt"]
    inner = archive.read(f"{folder_of(job_id)}.{kind}")
    if kind == "docx":
        words = " ".join(p.text for p in docx.Document(io.BytesIO(inner)).paragraphs)
        assert "SECRET-DOC-TEXT" in words
        assert "Word files (.docx)" in archive.read("contents.txt").decode()
    else:
        assert b"SECRET-DOC-TEXT" in zipfile.ZipFile(io.BytesIO(inner)).read("EPUB/content.xhtml")
        assert "EPUB files" in archive.read("contents.txt").decode()
    # recorded and the clock restarted, as for the single download
    assert formats_of(web, job_id) == [kind]
    left = as_utc(web.job(job_id).retain_until) - utcnow()
    assert timedelta(hours=23, minutes=55) < left <= timedelta(hours=24)


def test_download_all_in_another_file_type_leaves_a_draft_out(web):  # noqa: F811
    finished_job(web)  # decisions open: a draft
    response = web.browser.get("/app/download?format=epub")
    assert response.status_code == 409 and "Nothing to download yet" in response.text


def test_download_all_knows_only_its_file_types(web):  # noqa: F811
    ready_job(web)
    for bad in ("pdf", "", "HTML ", "docx.epub"):
        response = web.browser.get("/app/download", params={"format": bad})
        assert response.status_code == 400 and "Unknown file type" in response.text


def test_download_all_without_pandoc_has_no_word_zip(web, monkeypatch):  # noqa: F811
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
        html='<html lang="en"><body><img src="data:image/svg+xml;base64,PHN2Zz4="></body></html>',
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


def test_a_word_zip_holds_fewer_documents():
    from readablestem.web import bundle

    assert bundle.limit("docx") == 10 and bundle.limit("epub") == bundle.limit("html") == 25
    made = bundle.build(
        [(f"T{n}", f"t{n}.docx", b"x") for n in range(12)], left_out=2, file_type="docx"
    )
    assert len(made.included) == 10 and made.left_out_size == 4
    contents = zipfile.ZipFile(io.BytesIO(made.data)).read("contents.txt").decode()
    assert "4 more documents" in contents and "at most 10 documents" in contents


def test_the_documents_page_offers_download_all_in_each_file_type(web):  # noqa: F811
    ready_job(web)
    page = web.browser.get("/app").text
    assert 'id="download-all" href="/app/download"' in page  # the HTML zip stays first
    assert (
        '<a class="button-secondary" href="/app/download?format=epub">Download all as EPUB</a>'
        in page
    )
    if export_docx.available():
        assert "/app/download?format=docx" in page and "Download all as Word (.docx)" in page


def test_the_one_zip_page_has_no_format_links_without_ready_documents(web):  # noqa: F811
    finished_job(web)
    assert "Download all as" not in web.browser.get("/app").text


def test_an_unknown_file_type_is_a_bug(web):  # noqa: F811
    with pytest.raises(ValueError, match="unknown file type"):
        events.record_download(
            web.ctx.factory,
            web.ctx.settings,
            user_id="u",
            job_id="a" * 32,
            open_decisions=0,
            file_type="pdf",
        )


# --------------------------------------------------------------------------- the record


def record_words(env, job_id: str) -> str:
    page = env.browser.get(f"/jobs/{job_id}/record").text
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))


def test_the_record_says_which_file_types_were_downloaded(web):  # noqa: F811
    job_id = finished_job(web)
    assert "File types downloaded None yet" in record_words(web, job_id)
    web.browser.get(f"/jobs/{job_id}/download.epub")
    web.browser.get(f"/jobs/{job_id}/download")
    assert "File types downloaded HTML file, EPUB" in record_words(web, job_id)
    csv_text = web.browser.get(f"/jobs/{job_id}/record.csv").text
    assert "File types downloaded" in csv_text and "HTML file, EPUB" in csv_text


def test_the_record_says_nothing_about_downloads_when_events_are_off(web):  # noqa: F811
    job_id = finished_job(web)
    web.browser.get(f"/jobs/{job_id}/download.epub")
    object.__setattr__(web.ctx.settings, "events_enabled", False)
    try:
        assert "File types downloaded" not in record_words(web, job_id)
    finally:
        object.__setattr__(web.ctx.settings, "events_enabled", True)


def test_the_record_does_not_vouch_for_the_other_files(web):  # noqa: F811
    words = record_words(web, finished_job(web))
    assert "Nobody has checked how they open in Word or a reading app" in words
