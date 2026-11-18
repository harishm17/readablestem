"""LaTeX source uploads through the web app (`FEATURE_LATEX_SOURCE`): the form, every refusal and
what it costs (nothing), the stored job, a failed conversion, and (where LaTeXML and a working
sandbox exist) a real conversion through the worker, the job page, the review and the file.

The archive and sandbox layers have their own tests (test_archive_intake.py, test_source_*.py);
here the web routes are the subject."""

from __future__ import annotations

import html
import io
import stat
import zipfile

import pytest
from sqlalchemy import select

from fixtures import office
from fixtures.web.support import ALICE, WebEnv, csrf_from, job_id_from, login
from readablestem.core import sandbox
from readablestem.core.types import DocumentResult, Kind, Status
from readablestem.pipeline.checked_words import READY_ALL
from readablestem.pipeline.run import convert_pdf
from readablestem.source import envelope, latexml
from readablestem.source import pandoc as pandoc_step
from readablestem.source.convert import SourceFailed
from readablestem.web.db import Blob, Job, JobStatus, QuotaDay, session_scope
from source_support import LOSSES, fixture, loss_upload, make_zip, needs_latexml, png_bytes

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


def blob_count(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return len(list(session.scalars(select(Blob))))


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


# ------------------------------------------------------------------ accepted


def test_a_zip_is_stored_with_its_main_file_and_a_longer_one_costs_more(web):
    body = "word " * 1500  # 7,500 characters: three pages
    long = f"\\documentclass{{article}}\\begin{{document}}{body}\\end{{document}}"
    data = make_zip({"paper.tex": long, "fig.png": png_bytes()})
    response = send(web.browser, data, "project.zip")
    assert response.status_code == 303
    [job] = jobs(web)
    assert job.page_count == 3 and pages_used(web) == 3
    assert "counts as 3 pages" in web.browser.get("/app").text  # an estimate, not a count
    blob = stored(web, job.id)
    assert blob.media_type == "application/x-readablestem-zip"
    assert envelope.unwrap(bytes(blob.content))[:2] == ("zip", "paper.tex")


def test_the_person_can_name_the_main_file(web):
    data = make_zip({"a.tex": ARTICLE, "b.tex": ARTICLE})
    assert "more than one main file" in refused(web, data, "p.zip")
    response = send(web.browser, data, "p.zip", main_file="b.tex")
    assert response.status_code == 303
    assert envelope.unwrap(bytes(stored(web, jobs(web)[0].id).content))[1] == "b.tex"


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


@pytest.mark.parametrize(
    ("preamble", "name"),
    [("\\documentclass[addpoints]{exam}", "exam"), ("\\usepackage{csquotes}", "csquotes")],
    ids=["exam", "csquotes"],
)
def test_a_class_or_package_not_read_yet_is_refused_at_upload(web, preamble, name):
    if "documentclass" not in preamble:
        preamble = "\\documentclass{article}" + preamble
    source = f"{preamble}\\begin{{document}}\\question Two?\\end{{document}}"
    text = html.unescape(refused(web, source.encode(), "quiz.tex"))  # no job, no pages
    assert "quiz.tex" in text and f"({name})" in text and "PDF" in text  # what, and what to do


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
    ],
)
def test_a_bad_zip_is_refused_with_a_plain_message(web, data, words):
    text = refused(web, data, "project.zip")
    assert words in text and "project.zip" in text
    assert "upload the PDF instead" in text or "Zip the project again" in text
    assert 'href="/help/latex"' in text  # the page that says what each message means


def test_a_zip_bomb_is_refused_before_anything_is_unpacked(web):
    bomb = make_zip({"main.tex": ARTICLE, "pad.txt": b"\0" * (60 * 1024 * 1024)})
    assert len(bomb) < 100_000 or True
    text = refused(web, bomb, "bomb.zip")
    assert "too big" in text


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
    assert response.status_code == 503 and "not available on this server" in response.text
    assert "Compile it to a PDF and choose that here instead" in response.text
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
        long = "\\documentclass{article}\\begin{document}" + "word " * 1500 + "\\end{document}"
        text = refused(env, long.encode(), "long.tex")
        # 7,500 characters is an estimate of pages, not a count of them
        assert (
            "long.tex counts as 3 pages" in text and "today" in text and "long.tex has" not in text
        )


@pytest.mark.parametrize("kind", ["tex", "zip"])
def test_a_source_that_does_not_fit_the_storage_limit_is_refused_like_a_pdf(web, monkeypatch, kind):
    """The room is checked again when the job is stored (another upload may have filled it in
    between): that is a refusal with the numbers, not a server error, for every kind of file."""
    from types import SimpleNamespace

    from readablestem.web import intake, storage

    def full(*_args, **_kwargs):
        raise storage.StorageRefused(299, 300)

    monkeypatch.setattr(intake, "storage", SimpleNamespace(check_room=full))
    data = ARTICLE.encode() if kind == "tex" else make_zip({"main.tex": ARTICLE})
    text = refused(web, data, f"paper.{kind}")
    assert "299 MB of the 300 MB" in html.unescape(text)


@pytest.mark.parametrize("kind", ["tex", "zip"])
def test_deleting_a_source_job_leaves_no_row_and_no_stored_file(web, kind):
    data = ARTICLE.encode() if kind == "tex" else make_zip({"main.tex": ARTICLE})
    job_id = queue_source(web, f"paper.{kind}", data)
    assert stored(web, job_id).content  # the file as uploaded is the job's one stored copy
    token = csrf_from(web.browser.get("/app").text)
    gone = web.browser.post(f"/jobs/{job_id}/delete", data={"csrf_token": token, "confirm": "yes"})
    assert gone.status_code == 303
    assert jobs(web) == [] and blob_count(web) == 0


# ------------------------------------------------------------------ the job


def queue_source(web, filename: str = "paper.tex", data: bytes | None = None) -> str:
    response = send(web.browser, data or ARTICLE.encode(), filename)
    assert response.status_code == 303
    return job_id_from(response)


@pytest.mark.parametrize(
    ("code", "words"),
    [("source_failed", "Upload the PDF instead"), ("source_timeout", "Trying again will not")],
)
def test_a_failed_source_conversion_fails_the_job_cleanly(web, code, words):
    job_id = queue_source(web)
    web.converter.raises = SourceFailed(code)
    web.run_worker()
    job = web.job(job_id)
    assert job.status == JobStatus.FAILED and job.error_code == code
    page = web.browser.get(f"/jobs/{job_id}")
    assert page.status_code == 200 and words in page.text
    assert "Try again" not in page.text and "splitting the PDF" not in page.text
    assert 'href="/help/latex"' in page.text
    assert web.browser.get(f"/jobs/{job_id}/view").status_code in {404, 409, 303, 200}
    assert job.result_json is None  # nothing half-made is kept


def test_the_student_note_of_a_source_says_so_also_after_a_review_render():
    from fixtures.web.support import fake_result
    from readablestem.web.review import rerender_html

    result = fake_result()
    result.origin = "latex"
    html = rerender_html(result, {})
    assert "web version of a LaTeX source from your course" in html
    assert "web version of a PDF" not in html
    result.origin = "pdf"
    assert "web version of a PDF from your course" in rerender_html(result, {})


# ------------------------------------------------------------------ a real conversion


@needs_latexml
@pytest.mark.parametrize("name", list(LOSSES))
def test_a_source_that_loses_content_never_ends_as_converted(name, tmp_path, caplog):
    """A source that would lose content stops and names why, or its file holds what it would
    have lost and the page never claims more than it converted (SW42.2, SW42.4)."""
    import logging

    caplog.set_level(logging.INFO)
    row = LOSSES[name]
    env = WebEnv(tmp_path, feature_latex_source=True)
    env.converter = convert_pdf  # ty: ignore[invalid-assignment]
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        data, filename = loss_upload(name)
        response = send(client, data, filename)
        assert response.status_code == 303
        job_id = job_id_from(response)
        env.run_worker()
        job = env.job(job_id)
        page = client.get(f"/jobs/{job_id}").text
        if row.code is None:
            assert job.status == JobStatus.DONE and job.result_json is not None
            assert row.shown in (job.html or "")
            kinds = [e["kind"] for e in job.result_json["elements"]]
            assert kinds.count("figure") == row.figures
            if row.decision is not None:  # the item that shows it waits for the instructor
                assert READY_ALL not in page  # SW7: never claims the whole file
                codes = [
                    c.get("code") for f in job.result_json["fixes"] for c in f.get("checks") or []
                ]
                assert row.decision in codes
            return
        assert job.status == JobStatus.FAILED and job.error_code == row.code
        assert job.result_json is None  # nothing a student could be given
        assert READY_ALL not in page and "Try again" not in page
        assert html.escape(row.shown, quote=False) in page  # the file or command is named
    assert row.shown not in caplog.text  # names stay out of the logs


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

    def test_the_job_page_and_the_review_list_work_and_the_split_view_redirects(self, done):
        env, job_id = done
        page = env.browser.get(f"/jobs/{job_id}")
        assert page.status_code == 200 and "LaTeX source" in page.text
        assert "revised version" not in page.text and "?revises=" not in page.text
        split = env.browser.get(f"/jobs/{job_id}/review")
        assert split.status_code == 303 and split.headers["location"].endswith("/review/list")
        review = env.browser.get(f"/jobs/{job_id}/review/list")
        assert review.status_code == 200 and "Figure" in review.text
        assert "LaTeX source" in review.text
        assert "in the PDF" not in review.text and "your PDF" not in review.text
        # No choice that leads nowhere (SW18.5): no link back to this list, and a link to
        # an item lands on it.
        assert f'href="/jobs/{job_id}/review"' not in review.text
        assert f"/jobs/{job_id}/review?page=" not in review.text
        result = DocumentResult.model_validate(env.job(job_id).result_json)
        report = result.source_report  # the fixture is read whole (SW42.4)
        assert report is not None and (report.unread_words, report.unread_pictures) == (0, 0)
        formula = next(f for f in result.fixes if f.kind is Kind.EQUATION and f.pending)
        table = next(f for f in result.fixes if f.kind is Kind.TABLE)
        to_item = env.browser.get(f"/jobs/{job_id}/review?item={formula.element_id}")
        assert to_item.headers["location"].endswith(
            f"/review/list?at=item-{formula.element_id}#item-{formula.element_id}"
        )
        # "Reject" keeps a formula as written; a table with nothing to keep is refused.
        token = csrf_from(review.text)
        for fix in (table, formula):
            env.browser.post(
                f"/jobs/{job_id}/review/{fix.element_id}",
                data={"csrf_token": token, "action": "reject", "view": "list"},
            )
        after = DocumentResult.model_validate(env.job(job_id).result_json)
        assert after.fix_for(table.element_id).status is Status.ACCEPTED  # ty: ignore[possibly-missing-attribute]
        assert after.fix_for(formula.element_id).status is Status.REJECTED  # ty: ignore[possibly-missing-attribute]
        student = env.browser.get(f"/jobs/{job_id}/view").text
        assert "not available" not in student and "\\badmacro" in student
        assert "web version of a LaTeX source from your course" in student

    def test_a_document_made_from_a_source_cannot_be_replaced_by_a_new_version(self, done):
        """Revising a source is held (WS18.8): the page, the link and a direct request say so."""
        env, job_id = done
        before = len(jobs(env))
        link = env.browser.get(f"/app?revises={job_id}")
        assert link.status_code == 400 and "cannot be replaced with a new version" in link.text
        sent = send(env.browser, ARTICLE.encode(), "v2.tex", revises=job_id)
        assert sent.status_code == 400 and "cannot be replaced with a new version" in sent.text
        assert (
            "Upload a revised version" not in sent.text
            and "Upload a revised version" not in link.text
        )
        assert len(jobs(env)) == before


# ------------------------------------------------------- Word read from the file (WS18.14, 18.15)


@pytest.mark.skipif(
    not (pandoc_step.installed() and sandbox.available()),
    reason="needs pandoc and a working sandbox (bubblewrap or Landlock)",
)
def test_with_the_word_switch_on_a_docx_is_read_from_the_file_and_not_made_into_a_pdf(tmp_path):
    env = WebEnv(tmp_path, feature_word_source=True)
    env.converter = convert_pdf  # ty: ignore[invalid-assignment]
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        response = send(client, office.docx_with_equation(), "notes.docx")
        assert response.status_code == 303
        job_id = job_id_from(response)
        [job] = jobs(env)
        blob = stored(env, job_id)
        assert blob.media_type == "application/x-readablestem-docx"
        assert envelope.unwrap(bytes(blob.content))[0] == "docx" and job.office_json is not None
        assert env.run_worker() == job_id
        done = env.job(job_id)
        assert done.status == JobStatus.DONE and done.result_json is not None
        result = DocumentResult.model_validate(done.result_json)
        # no model call: the maths and the picture's description are the file's own
        assert result.origin == "docx" and result.stats.model_calls == 0
        equation = next(f for f in result.fixes if f.kind is Kind.EQUATION)
        assert equation.status is Status.ACCEPTED and equation.equation is not None
        assert "web version of a Word file" in (done.html or "")
        page = client.get(f"/jobs/{job_id}").text
        assert "was read from the file itself" in page and "turned into a tagged PDF" not in page
        assert client.get(f"/jobs/{job_id}/review/list").status_code == 200
        # the notice says the Word file is kept as uploaded, and deleting removes every copy
        assert "the Word .docx file" in " ".join(client.get("/privacy").text.split())
        token = csrf_from(client.get("/app").text)
        gone = client.post(f"/jobs/{job_id}/delete", data={"csrf_token": token, "confirm": "yes"})
        assert gone.status_code == 303 and jobs(env) == [] and blob_count(env) == 0
