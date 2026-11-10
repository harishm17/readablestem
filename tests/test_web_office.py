"""T3-30: Word and PowerPoint uploads through the web app.

The conversion itself is tested in test_office_convert.py. Here: the form, the limits, the
messages, the stored PDF, and that a refused file costs nothing. Most tests use a stand-in
for `soffice`; the ones that run the real program are skipped where it is not installed.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select

from fixtures import office
from fixtures.web.support import ALICE, WebEnv, csrf_from, job_id_from, login, make_pdf
from readablestem.web import office_convert, uploads
from readablestem.web.db import Blob, Job, QuotaDay, session_scope

REAL = office_convert.soffice_command()
HAS_WRITER = "docx" in office_convert.available_kinds(REAL)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def fake_soffice(tmp_path: Path, pages: int = 1) -> str:
    """A stand-in `soffice` that writes a PDF of `pages` pages."""
    pdf = tmp_path / "made.pdf"
    pdf.write_bytes(make_pdf(pages, "From Word"))
    script = tmp_path / "soffice"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nargs = sys.argv[1:]\n"
        "out = args[args.index('--outdir') + 1]\n"
        f"open(out + '/input.pdf', 'wb').write(open({str(pdf)!r}, 'rb').read())\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, office_input_enabled=True, soffice_path=fake_soffice(tmp_path))
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def send(client, data: bytes, filename: str, media_type: str = DOCX):
    token = csrf_from(client.get("/app").text)
    return client.post(
        "/jobs", data={"csrf_token": token}, files={"pdf": (filename, data, media_type)}
    )


def jobs(env) -> list[Job]:
    with session_scope(env.ctx.factory) as session:
        return list(session.scalars(select(Job)))


def pages_used(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return sum(row.pages for row in session.scalars(select(QuotaDay)))


def stored_upload(env, job_id: str) -> bytes:
    with session_scope(env.ctx.factory) as session:
        blob = session.scalar(select(Blob).where(Blob.job_id == job_id, Blob.kind == "upload"))
        assert blob is not None
        return bytes(blob.content)


# --------------------------------------------------------------------------- accepted


def test_a_word_file_is_converted_probed_and_stored_as_a_pdf(web):
    response = send(web.browser, office.docx(), "Lecture 2 notes.docx")
    assert response.status_code == 303
    job_id = job_id_from(response)
    [job] = jobs(web)
    assert job.filename == "Lecture 2 notes.docx" and job.page_count == 1
    stored = stored_upload(web, job_id)
    assert stored.startswith(b"%PDF-")  # the Word file itself is not kept
    assert office.docx() not in stored
    assert pages_used(web) == 1
    assert web.run_worker() == job_id
    assert web.converter.calls[-1]["pdf"] == stored


def test_a_presentation_is_converted_the_same_way(web):
    assert send(web.browser, office.pptx(), "Slides.pptx", PPTX).status_code == 303


def test_the_name_and_media_type_decide_nothing(web):
    assert send(web.browser, office.docx(), "notes.pdf", "application/pdf").status_code == 303
    plain = make_pdf(1)
    assert send(web.browser, plain, "notes.docx", DOCX).status_code == 303  # a PDF stays one
    [first, second] = sorted(jobs(web), key=lambda j: j.created_at)
    assert stored_upload(web, first.id).startswith(b"%PDF-")
    assert stored_upload(web, second.id) == plain


# --------------------------------------------------------------------------- refused


def test_a_presentation_when_impress_is_missing_says_this_server_cannot(tmp_path):
    root = tmp_path / "lo"
    (root / "share" / "registry").mkdir(parents=True)
    (root / "program").mkdir()
    for name in ("main.xcd", "writer.xcd"):
        (root / "share" / "registry" / name).write_text("")
    soffice = root / "program" / "soffice"
    soffice.write_text("#!/bin/sh\nexit 1\n")
    soffice.chmod(0o755)
    env = WebEnv(tmp_path, office_input_enabled=True, soffice_path=str(soffice))
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        page = client.get("/app").text
        assert "Word (.docx)" in page and "PowerPoint (.pptx)" not in page
        response = send(client, office.pptx(), "Slides.pptx", PPTX)
        assert response.status_code == 422 and "is not a PDF or Word (.docx) file" in response.text


def test_a_conversion_that_fails_is_a_message_not_an_error_page(tmp_path):
    broken = tmp_path / "soffice"
    broken.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(4)\n")
    broken.chmod(0o755)
    env = WebEnv(tmp_path, office_input_enabled=True, soffice_path=str(broken))
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        response = send(client, office.docx(), "notes.docx")
        assert response.status_code == 422 and "could not be turned into a PDF" in response.text
        assert jobs(env) == [] and pages_used(env) == 0


def test_a_second_conversion_waits_briefly_then_is_told_to_try_again(web, monkeypatch):
    monkeypatch.setattr(uploads, "OFFICE_WAIT_S", 0.2)
    assert uploads._OFFICE_SLOT.acquire(timeout=1)
    try:
        response = send(web.browser, office.docx(), "notes.docx")
    finally:
        uploads._OFFICE_SLOT.release()
    assert response.status_code == 503 and response.headers["retry-after"] == "10"
    assert "Another Word or PowerPoint file is being converted" in response.text
    assert jobs(web) == []


def test_the_log_carries_a_category_and_never_the_file_name(web, caplog):
    caplog.set_level("DEBUG")
    send(web.browser, office.with_external_picture(), "SECRET-FILENAME-linked.docx")
    dumped = " ".join(str(record.__dict__) for record in caplog.records)
    assert "upload_rejected" in dumped and "office_unsafe" in dumped
    assert "SECRET-FILENAME" not in dumped and "SECRET-FILENAME" not in caplog.text


def test_an_office_upload_is_counted_as_one_upload_by_the_limits(web):
    """The per-person and per-address upload limits sit in front of the conversion."""
    from readablestem.web.abuse import RateLimiter, Rule

    web.ctx.limiter = RateLimiter(rules={**web.ctx.limiter.rules, "upload_user": Rule(2, 600)})
    web.settings.__dict__["rate_limits_enabled"] = True
    statuses = [send(web.browser, office.docx(), f"n{n}.docx").status_code for n in range(4)]
    assert statuses[:2] == [303, 303] and set(statuses[2:]) == {429}


# --------------------------------------------------------------------------- the real program


@pytest.mark.skipif(not HAS_WRITER, reason="LibreOffice Writer is not installed")
def test_a_real_word_file_goes_all_the_way_to_a_job_with_its_tags(tmp_path):
    env = WebEnv(tmp_path, office_input_enabled=True)
    assert env.settings.soffice_path is None  # the real program, found on PATH
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        response = send(client, office.docx(), "Kinematics.docx")
        assert response.status_code == 303, response.text
        job_id = job_id_from(response)
        stored = stored_upload(env, job_id)
        document = pymupdf.open(stream=stored)
        assert document.page_count == 1 and "Kinematics in one dimension" in document[0].get_text()
        from readablestem.pipeline import tagged

        found = tagged.read(stored, [0])
        assert [h.level for h in found.headings] == [1, 2]
        assert [f.alt for f in found.figures] == [office.ALT]


def test_the_child_that_reads_the_file_gets_no_secrets(web, monkeypatch):
    import subprocess

    for name in ("SESSION_SECRET", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL"):
        monkeypatch.setenv(name, "secret-value")
    seen = []
    real = subprocess.Popen

    def watching(*args, **kwargs):
        seen.append(kwargs.get("env"))
        return real(*args, **kwargs)

    monkeypatch.setattr(uploads.subprocess, "Popen", watching)
    assert send(web.browser, office.docx(), "notes.docx").status_code == 303
    assert len(seen) >= 2  # the converter's child, then the probe's
    for environment in seen:
        assert environment is not None and "secret-value" not in " ".join(environment.values())


def test_nothing_of_the_file_is_left_in_the_temporary_folder(web, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))
    assert send(web.browser, office.docx(), "notes.docx").status_code == 303
    assert send(web.browser, office.with_external_picture(), "linked.docx").status_code == 422
    assert list(scratch.iterdir()) == []


def test_the_privacy_page_says_how_these_files_are_handled_only_when_they_are_taken(web, tmp_path):
    page = web.browser.get("/privacy").text
    assert "Word or PowerPoint file is checked in a separate process" in page
    assert "the Word or PowerPoint file itself is not kept" in page
    env = WebEnv(tmp_path / "off", office_input_enabled=False)
    with env.running() as client:
        assert "Word or PowerPoint file is checked" not in client.get("/privacy").text
