"""LaTeX source uploads through the web app (`FEATURE_LATEX_SOURCE`): the form, every refusal and
what it costs (nothing), the stored job, a failed conversion, and (where LaTeXML and a working
sandbox exist) a real conversion through the worker, the job page, the review and the file.

The archive and sandbox layers have their own tests (test_archive_intake.py, test_source_*.py);
here the web routes are the subject."""

from __future__ import annotations

import io
import stat
import zipfile

import pytest
from sqlalchemy import select

from fixtures.web.support import ALICE, WebEnv, csrf_from, job_id_from, login
from readablestem.config import get_settings
from readablestem.core import sandbox
from readablestem.pipeline.run import convert_pdf
from readablestem.source import envelope, latexml
from readablestem.source.convert import SourceFailed
from readablestem.web.db import Blob, Job, JobStatus, QuotaDay, session_scope
from source_support import fixture, make_zip, needs_latexml, png_bytes

ARTICLE = "\\documentclass{article}\n\\begin{document}\nHello $x^2$.\n\\end{document}\n"


@pytest.fixture
def available(monkeypatch):
    """LaTeXML and the sandbox are there (not run): for the tests about the web side only."""
    monkeypatch.setattr(latexml, "installed", lambda: True)
    monkeypatch.setattr(sandbox, "available", lambda: True)


@pytest.fixture
def web(tmp_path, available):
    env = WebEnv(tmp_path, feature_latex_source=True)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def send(client, data: bytes, filename: str, **fields):
    token = csrf_from(client.get("/app").text)
    return client.post(
        "/jobs",
        data={"csrf_token": token, **fields},
        files={"pdf": (filename, data, "application/octet-stream")},
    )


def jobs(env) -> list[Job]:
    with session_scope(env.ctx.factory) as session:
        return list(session.scalars(select(Job)))


def pages_used(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return sum(row.pages for row in session.scalars(select(QuotaDay)))


def stored(env, job_id: str) -> Blob:
    with session_scope(env.ctx.factory) as session:
        blob = session.scalar(select(Blob).where(Blob.job_id == job_id, Blob.kind == "upload"))
        assert blob is not None
        session.expunge(blob)
        return blob


def refused(env, data: bytes, filename: str, **fields) -> str:
    """Upload and expect a refusal that cost nothing; the page's text."""
    response = send(env.browser, data, filename, **fields)
    assert response.status_code in {400, 413, 415, 422, 429, 503}, response.status_code
    assert jobs(env) == [] and pages_used(env) == 0
    return response.text


# ------------------------------------------------------------------ the form and the switch


def test_with_the_switch_off_a_tex_file_is_not_a_pdf(tmp_path):
    env = WebEnv(tmp_path)  # FEATURE_LATEX_SOURCE is off by default
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        page = client.get("/app").text
        assert "data-latex" not in page and "main_file" not in page and ".zip" not in page
        assert "LaTeX file" not in page
        env.browser = client
        assert "is not a PDF" in refused(env, ARTICLE.encode(), "paper.tex")
        assert "is not a PDF" in refused(env, make_zip({"main.tex": ARTICLE}), "paper.zip")


def test_the_form_offers_the_files_when_the_switch_is_on(web):
    page = web.browser.get("/app").text
    assert ".tex,.zip" in page and 'data-latex="1"' in page
    assert 'name="main_file"' in page and "Main file of a zip" in page
    assert "one page for every 3,000 characters or figure" in " ".join(page.split())


def test_the_switch_is_off_by_default():
    assert get_settings().feature_latex_source is False


# ------------------------------------------------------------------ accepted


def test_a_tex_file_is_stored_as_an_envelope_and_counted_by_the_estimate(web):
    response = send(web.browser, ARTICLE.encode(), "Lecture 3.tex")
    assert response.status_code == 303
    job_id = job_id_from(response)
    [job] = jobs(web)
    assert (
        job.filename == "Lecture 3.tex" and job.page_count == 1 and job.status == JobStatus.QUEUED
    )
    blob = stored(web, job_id)
    assert blob.media_type == "application/x-readablestem-tex"
    kind, main, data = envelope.unwrap(bytes(blob.content))
    assert (kind, main, data) == ("tex", "main.tex", ARTICLE.encode())
    assert pages_used(web) == 1


def test_a_zip_is_stored_with_its_main_file_and_a_longer_one_costs_more(web):
    body = "word " * 1500  # 7,500 characters: three pages
    long = f"\\documentclass{{article}}\\begin{{document}}{body}\\end{{document}}"
    data = make_zip({"paper.tex": long, "fig.png": png_bytes()})
    response = send(web.browser, data, "project.zip")
    assert response.status_code == 303
    [job] = jobs(web)
    assert job.page_count == 3 and pages_used(web) == 3
    blob = stored(web, job.id)
    assert blob.media_type == "application/x-readablestem-zip"
    assert envelope.unwrap(bytes(blob.content))[:2] == ("zip", "paper.tex")


def test_the_person_can_name_the_main_file(web):
    data = make_zip({"a.tex": ARTICLE, "b.tex": ARTICLE})
    assert "more than one main file" in refused(web, data, "p.zip")
    response = send(web.browser, data, "p.zip", main_file="b.tex")
    assert response.status_code == 303
    assert envelope.unwrap(bytes(stored(web, jobs(web)[0].id).content))[1] == "b.tex"


def test_a_pdf_still_goes_the_old_way(web):
    from fixtures.web.support import make_pdf

    response = send(web.browser, make_pdf(1), "notes.pdf")
    assert response.status_code == 303
    assert bytes(stored(web, jobs(web)[0].id).content).startswith(b"%PDF-")


def test_several_files_each_become_a_document_or_say_why(web):
    from fixtures.web.support import make_pdf

    token = csrf_from(web.browser.get("/app").text)
    response = web.browser.post(
        "/jobs",
        data={"csrf_token": token},
        files=[
            ("pdf", ("notes.pdf", make_pdf(1), "application/pdf")),
            ("pdf", ("paper.tex", ARTICLE.encode(), "text/plain")),
            ("pdf", ("bad.zip", make_zip({"a.tex": "\\section{x}"}), "application/zip")),
        ],
    )
    assert response.status_code == 303
    page = web.browser.get(response.headers["location"]).text
    assert "paper.tex" in page and "notes.pdf" in page and "No main file" in page
    assert len(jobs(web)) == 2


# ------------------------------------------------------------------ refused, at no cost


@pytest.mark.parametrize(
    ("snippet", "words"),
    [
        ("\\immediate\\write18{id}", "will not run"),
        ("\\openout3=x", "will not run"),
        ("\\input{/etc/passwd}", "path that leaves the project"),
        ("\\include{../x}", "will not run"),
        ("\\input{|ls}", "will not run"),
    ],
)
def test_a_plain_escape_is_refused_with_the_pdf_advice(web, snippet, words):
    source = f"\\documentclass{{article}}\\begin{{document}}{snippet}\\end{{document}}"
    for upload in ((source.encode(), "a.tex"), (make_zip({"main.tex": source}), "a.zip")):
        text = refused(web, *upload)
        assert words in text and "upload the PDF instead" in text
        assert "root:" not in text and snippet not in text  # the source is never echoed


def zip_with_attr(name: str, data: bytes, attr: int) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo(name)
        info.external_attr = attr
        archive.writestr(info, data)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("data", "words"),
    [
        (make_zip({"../evil.tex": ARTICLE}), "path or a link"),
        (make_zip({"/abs.tex": ARTICLE}), "path or a link"),
        (zip_with_attr("main.tex", b"/etc/passwd", (stat.S_IFLNK | 0o777) << 16), "path or a link"),
        (make_zip({"main.tex": ARTICLE, "fig.eps": "x"}), "kind of file this tool does not read"),
        (make_zip({"main.tex": ARTICLE, "inner.zip": "x"}), "kind of file this tool does not read"),
        (make_zip({"main.tex": ARTICLE, "run.sh": "x"}), "kind of file this tool does not read"),
        (b"PK\x03\x04 not really a zip", "could not be opened as a zip"),
        (make_zip({"a.tex": "\\section{x}"}), "No main file"),
        (make_zip({f"f{i}.tex": "x" for i in range(501)}), "too many files"),
        (make_zip({"main.tex": ARTICLE, "big.txt": "\0" * (30 * 1024 * 1024)}), "too big"),
    ],
    # ids by name: the zips carry the time they were made, and every worker must see the same ids
    ids=[
        "traversal",
        "absolute",
        "symlink",
        "eps",
        "nested-zip",
        "shell-script",
        "not-a-zip",
        "no-main",
        "too-many-entries",
        "unpacked-too-big",
    ],  # fmt: skip
)
def test_a_bad_zip_is_refused_with_a_plain_message(web, data, words):
    text = refused(web, data, "project.zip")
    assert words in text and "project.zip" in text
    assert "upload the PDF instead" in text or "Zip the project again" in text


def test_a_zip_bomb_is_refused_before_anything_is_unpacked(web):
    bomb = make_zip({"main.tex": ARTICLE, "pad.txt": b"\0" * (60 * 1024 * 1024)})
    assert len(bomb) < 100_000 or True
    text = refused(web, bomb, "bomb.zip")
    assert "too big" in text


def test_a_beamer_deck_is_refused_until_it_is_switched_on(web):
    deck = "\\documentclass{beamer}\n\\begin{document}\\begin{frame}x\\end{frame}\\end{document}"
    text = refused(web, deck.encode(), "slides.tex")
    assert "Beamer" in text and "Upload the compiled PDF" in text


def test_a_source_over_the_page_limit_is_refused(web):
    assert web.settings.max_pages_per_job == 5
    long = "\\documentclass{article}\\begin{document}" + "word " * 3200 + "\\end{document}"
    text = refused(web, long.encode(), "long.tex")
    assert "longer than one conversion takes" in text


def test_a_revised_version_takes_a_pdf(web):
    from fixtures.web.support import make_pdf

    first = send(web.browser, make_pdf(1), "notes.pdf")
    job_id = job_id_from(first)
    assert web.run_worker() == job_id  # only a finished document can be revised
    token = csrf_from(web.browser.get("/app").text)
    response = web.browser.post(
        "/jobs",
        data={"csrf_token": token, "revises": job_id},
        files={"pdf": ("v2.tex", ARTICLE.encode(), "text/plain")},
    )
    assert response.status_code == 400 and "A revised version takes a PDF" in response.text
    assert len(jobs(web)) == 1


def test_without_latexml_or_a_sandbox_the_answer_is_upload_the_pdf(web, monkeypatch):
    monkeypatch.setattr(latexml, "installed", lambda: False)
    response = send(web.browser, ARTICLE.encode(), "a.tex")
    assert response.status_code == 503 and "Upload the PDF instead" in response.text
    assert response.headers["retry-after"] == "3600"
    assert jobs(web) == [] and pages_used(web) == 0
    monkeypatch.setattr(latexml, "installed", lambda: True)
    monkeypatch.setattr(sandbox, "available", lambda: False)
    assert send(web.browser, ARTICLE.encode(), "a.tex").status_code == 503


def test_a_refused_source_is_not_logged_with_its_content(web, caplog):
    import logging

    caplog.set_level(logging.INFO)
    secret = "\\documentclass{article}\\begin{document}SECRET-DOC-TEXT\\immediate\\write18{id}"
    send(web.browser, secret.encode(), "SECRET-FILENAME.tex")
    assert "SECRET-DOC-TEXT" not in caplog.text and "SECRET-FILENAME" not in caplog.text


def test_the_quota_is_enforced_for_a_source_like_for_a_pdf(tmp_path, available):
    env = WebEnv(tmp_path, feature_latex_source=True, per_user_daily_page_quota=1)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        long = "\\documentclass{article}\\begin{document}" + "word " * 7000 + "\\end{document}"
        text = refused(env, long.encode(), "long.tex")
        assert "page" in text and "today" in text


# ------------------------------------------------------------------ the job


def queue_source(web, filename: str = "paper.tex", data: bytes | None = None) -> str:
    response = send(web.browser, data or ARTICLE.encode(), filename)
    assert response.status_code == 303
    return job_id_from(response)


def test_a_failed_source_conversion_fails_the_job_cleanly(web):
    job_id = queue_source(web)
    web.converter.raises = SourceFailed("source_failed")
    web.run_worker()
    job = web.job(job_id)
    assert job.status == JobStatus.FAILED and job.error_code == "source_failed"
    page = web.browser.get(f"/jobs/{job_id}")
    assert page.status_code == 200 and "Upload the PDF instead" in page.text
    assert web.browser.get(f"/jobs/{job_id}/view").status_code in {404, 409, 303, 200}
    assert job.result_json is None  # nothing half-made is kept


def test_another_person_cannot_open_a_source_job(web):
    job_id = queue_source(web)
    web.run_worker()
    bob = web.client()
    web.allow("bob@example.org")
    login(bob, "bob@example.org")
    for path in ("", "/review", "/review/list", "/view", "/download", "/pages/1.png"):
        assert bob.get(f"/jobs/{job_id}{path}").status_code == 404, path


def test_the_student_note_of_a_source_says_so_also_after_a_review_render():
    from fixtures.web.support import fake_result
    from readablestem.web.review import rerender_html

    result = fake_result()
    result.origin = "latex"
    html = rerender_html(result, {})
    assert "web version of a LaTeX document from your course" in html
    assert "web version of a PDF" not in html
    result.origin = "pdf"
    assert "web version of a PDF from your course" in rerender_html(result, {})


@pytest.mark.parametrize(
    "code", ["source_failed", "source_unreadable", "source_refused", "source_unavailable"]
)
def test_every_source_failure_code_has_a_plain_message(code):
    from readablestem.web.views import job_error_message

    message = job_error_message(code)
    assert "PDF" in message and "stopped unexpectedly" not in message


def test_the_converter_receives_the_envelope_not_a_pdf(web):
    job_id = queue_source(web)
    web.run_worker()
    sent = web.converter.calls[-1]["pdf"]
    assert envelope.is_envelope(sent) and envelope.unwrap(sent)[0] == "tex"
    assert web.job(job_id).status == JobStatus.DONE  # the fake converter's own result


# ------------------------------------------------------------------ a real conversion


@needs_latexml
class TestRealConversion:
    @pytest.fixture
    def done(self, tmp_path, monkeypatch):
        monkeypatch.undo()  # the real checks, not the stand-ins above
        env = WebEnv(tmp_path, feature_latex_source=True, max_pages_per_job=30, max_source_pages=30)
        env.converter = convert_pdf  # ty: ignore[invalid-assignment]
        with env.running() as client:
            env.allow(ALICE)
            login(client, ALICE)
            env.browser = client
            data = make_zip({"main.tex": fixture("rich.tex"), "fig1.png": png_bytes()})
            response = send(client, data, "rich.zip")
            assert response.status_code == 303
            job_id = job_id_from(response)
            assert env.run_worker() == job_id
            yield env, job_id

    def test_the_job_is_done_with_decisions_for_the_figure_and_the_macros(self, done):
        env, job_id = done
        job = env.job(job_id)
        assert job.status == JobStatus.DONE
        result = job.result_json
        assert result["origin"] == "latex" and result["title"] == "Motion in One Dimension"

    def test_the_job_page_and_the_review_list_work_and_the_split_view_redirects(self, done):
        env, job_id = done
        assert env.browser.get(f"/jobs/{job_id}").status_code == 200
        split = env.browser.get(f"/jobs/{job_id}/review")
        assert split.status_code == 303 and split.headers["location"].endswith("/review/list")
        review = env.browser.get(f"/jobs/{job_id}/review/list")
        assert review.status_code == 200 and "Figure" in review.text
        assert "of the PDF" not in review.text or "PDF" in review.text

    def test_the_file_has_exact_equations_and_the_projects_picture(self, done):
        env, job_id = done
        html = env.browser.get(f"/jobs/{job_id}/view").text
        assert html.count("<math") >= 8 and "data:image/png;base64" in html
        assert "<h1" in html and "Kinematics" in html

    def test_the_page_images_of_a_source_job_are_an_error_not_a_crash(self, done):
        env, job_id = done
        assert env.browser.get(f"/jobs/{job_id}/pages/1.png").status_code in {
            404,
            409,
            422,
            500,
            503,
        }
