"""Word and PowerPoint files (T3-30) together with several files in one upload (T3-4) and
with large files kept for choosing their pages (T3-3).

A .docx among PDFs is made into a PDF like a single upload; one that cannot be used does not
stop the others; a Word file whose PDF is over the page limit is kept for choosing its pages
(as the PDF, whose parts are PDFs); the time several slow conversions may take is bounded.
"""

from __future__ import annotations

import re
import stat
import sys
from html import unescape
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select

from fixtures import office
from fixtures.web.support import ALICE, WebEnv, login, make_pdf, token
from readablestem.web.db import Blob, Job, PendingUpload, session_scope
from readablestem.web.routes import jobs as jobs_routes

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def fake_soffice(tmp_path: Path, pages: int) -> str:
    """A stand-in `soffice` that writes a PDF of `pages` pages whose first page says so."""
    pdf = tmp_path / "made.pdf"
    doc = pymupdf.open()
    for n in range(pages):
        doc.new_page().insert_text((72, 72), f"From Word page {n + 1}")
    pdf.write_bytes(doc.tobytes())
    script = tmp_path / "soffice"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nargs = sys.argv[1:]\n"
        "out = args[args.index('--outdir') + 1]\n"
        f"open(out + '/input.pdf', 'wb').write(open({str(pdf)!r}, 'rb').read())\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def environment(tmp_path: Path, pages: int) -> WebEnv:
    # A conversion takes 5 pages and 1 MB; a file up to 40 pages and 5 MB is kept.
    return WebEnv(
        tmp_path,
        office_input_enabled=True,
        soffice_path=fake_soffice(tmp_path, pages),
        max_large_upload_mb=5,
        max_source_pages=40,
        per_user_daily_page_quota=40,
    )


@pytest.fixture
def web(tmp_path):
    env = environment(tmp_path, pages=2)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.alice = client
        yield env


def send(client, files: list[tuple[str, bytes]], **fields):
    return client.post(
        "/jobs",
        data={"csrf_token": token(client)} | fields,
        files=[("pdf", (name, data, DOCX)) for name, data in files],
    )


def results(client, response) -> str:
    assert response.status_code == 303 and response.headers["location"].startswith("/app/upload/")
    return text_of(client.get(response.headers["location"]).text)


def jobs_of(env) -> list[Job]:
    with session_scope(env.ctx.factory) as session:
        rows = list(session.scalars(select(Job).order_by(Job.created_at, Job.id)))
        session.expunge_all()
        return rows


def stored(env, job_id: str) -> bytes:
    with session_scope(env.ctx.factory) as session:
        found = session.scalar(
            select(Blob.content).where(Blob.job_id == job_id, Blob.kind == "upload")
        )
        return bytes(found or b"")


def test_a_word_file_among_pdfs_is_made_into_a_pdf_and_queued_with_them(web):
    response = send(
        web.alice,
        [("week1.pdf", make_pdf(1)), ("notes.docx", office.docx()), ("week3.pdf", make_pdf(3))],
    )
    page = results(web.alice, response)
    assert "3 of 3 files uploaded" in page
    jobs = jobs_of(web)
    assert [j.filename for j in jobs] == ["week1.pdf", "notes.docx", "week3.pdf"]
    assert [j.page_count for j in jobs] == [1, 2, 3]  # the Word file's count is its PDF's
    assert stored(web, jobs[1].id).startswith(b"%PDF-")  # the PDF is stored, not the .docx
    assert office.docx() not in stored(web, jobs[1].id)


def test_a_word_file_that_cannot_be_used_does_not_stop_the_others(web):
    macro = office.rebuild(office.docx_parts(), add={"word/vbaProject.bin": b"\x00"})
    response = send(web.alice, [("good.pdf", make_pdf(1)), ("macro.docx", macro)])
    page = results(web.alice, response)
    assert [j.filename for j in jobs_of(web)] == ["good.pdf"]
    assert "1 of 2 files uploaded" in page and "Not uploaded" in page
    assert "macro.docx has something this tool will not open" in page


def test_a_word_file_is_refused_in_a_multiple_upload_when_office_input_is_off(tmp_path):
    env = WebEnv(tmp_path, office_input_enabled=False, max_large_upload_mb=5, max_source_pages=40)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        page = results(client, send(client, [("a.pdf", make_pdf(1)), ("n.docx", office.docx())]))
        assert [j.filename for j in jobs_of(env)] == ["a.pdf"]
        assert "n.docx is not a PDF" in page


def test_a_large_word_file_is_kept_for_choosing_its_pages_as_a_pdf(tmp_path):
    env = environment(tmp_path, pages=12)  # 12 pages once made into a PDF, over the 5 a go
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        response = send(client, [("Long notes.docx", office.docx())])
        assert response.status_code == 303 and response.headers["location"].startswith("/uploads/")
        upload_id = response.headers["location"].rsplit("/", 1)[-1]
        with session_scope(env.ctx.factory) as session:
            row = session.get(PendingUpload, upload_id)
            assert row is not None and (row.pages, row.filename) == (12, "Long notes.docx")
            assert bytes(row.content).startswith(b"%PDF-")  # the PDF, never the .docx
        assert jobs_of(env) == []
        page = text_of(client.get(response.headers["location"]).text)
        assert "Long notes.docx has 12 pages" in page
        # Two parts: each is a PDF of its pages, named as a PDF.
        url = f"/uploads/{upload_id}"
        chosen = client.post(url, data={"csrf_token": token(client, url), "parts": "1-5\n6-10"})
        assert chosen.status_code == 303
        jobs = jobs_of(env)
        assert [j.filename for j in jobs] == [
            "Long notes (pages 1-5).pdf",
            "Long notes (pages 6-10).pdf",
        ]
        first = pymupdf.open(stream=stored(env, jobs[0].id), filetype="pdf")
        second = pymupdf.open(stream=stored(env, jobs[1].id), filetype="pdf")
        assert first.page_count == second.page_count == 5
        assert first[0].get_text().strip() == "From Word page 1"
        assert second[0].get_text().strip() == "From Word page 6"


def test_a_large_word_file_among_pdfs_waits_for_its_pages_and_the_rest_queue(tmp_path):
    env = environment(tmp_path, pages=12)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        response = send(client, [("small.pdf", make_pdf(2)), ("Long notes.docx", office.docx())])
        page = results(client, response)
        assert "2 of 2 files uploaded" in page and "Choose which pages to convert" in page
        assert "Choose pages of Long notes.docx" in page
        assert [j.filename for j in jobs_of(env)] == ["small.pdf"]
        with session_scope(env.ctx.factory) as session:
            [row] = list(session.scalars(select(PendingUpload)))
            assert row.filename == "Long notes.docx" and bytes(row.content).startswith(b"%PDF-")


def test_a_revised_version_can_be_a_word_file_that_fits_one_conversion(web):
    first = send(web.alice, [("v1.pdf", make_pdf(1))]).headers["location"].rsplit("/", 1)[-1]
    web.run_worker()
    response = web.alice.post(
        "/jobs",
        data={"csrf_token": token(web.alice), "revises": first},
        files={"pdf": ("v2.docx", office.docx(), DOCX)},
    )
    assert response.status_code == 303 and response.headers["location"].startswith("/jobs/")
    assert [j.revises_job_id for j in jobs_of(web)] == [None, first]


def test_a_large_word_file_cannot_be_a_revised_version(tmp_path):
    env = environment(tmp_path, pages=12)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        first = send(client, [("v1.pdf", make_pdf(1))]).headers["location"].rsplit("/", 1)[-1]
        # (the stand-in makes 12 pages for every Word file; v1 is a PDF)
        env.run_worker()
        response = client.post(
            "/jobs",
            data={"csrf_token": token(client), "revises": first},
            files={"pdf": ("v2.docx", office.docx(), DOCX)},
        )
        assert response.status_code == 422
        assert "cannot be a revised version" in text_of(response.text)


def test_the_conversions_of_one_upload_share_a_time_budget(web, monkeypatch):
    """Several slow Word files must not hold one request for minutes: once the time for them
    is used the rest are refused, naming the reason, and nothing about them is queued."""
    monkeypatch.setattr(jobs_routes, "OFFICE_BUDGET_S", 4.0)  # under the 5 s a conversion needs
    response = send(
        web.alice,
        [("a.docx", office.docx()), ("b.pdf", make_pdf(1)), ("c.docx", office.docx())],
    )
    page = results(web.alice, response)
    assert [j.filename for j in jobs_of(web)] == ["b.pdf"]
    assert page.count("was not converted: Word and PowerPoint files take a while") == 2
