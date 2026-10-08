"""Uploads, quotas, the spend-cap page, job status, results and ownership."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from starlette.requests import Request

from a11y_stem.web.database import as_utc, utc_today
from a11y_stem.web.db import Blob, Job, QuotaDay, SpendDay, User, session_scope
from a11y_stem.web.routes.jobs import content_disposition, download_filename
from a11y_stem.web.security import VIEW_CSP
from a11y_stem.web.uploads import (
    UploadRejected,
    inspect_pdf,
    inspect_pdf_isolated,
    read_upload_form,
    safe_filename,
)
from fixtures.web.support import (
    ALICE,
    BOB,
    WebEnv,
    csrf_from,
    encrypted_pdf,
    job_id_from,
    login,
    make_pdf,
    token,
    upload,
)


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def count(env, model) -> int:
    with session_scope(env.ctx.factory) as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def finished_job(env) -> str:
    job_id = job_id_from(upload(env.browser, make_pdf(2)))
    assert env.run_worker() == job_id
    return job_id


# --------------------------------------------------------------------------- upload


def test_upload_queues_a_job_with_the_pdf_and_24_hour_retention(web):
    pdf = make_pdf(2)
    response = upload(web.browser, pdf, filename="Week 3 notes.pdf")
    assert response.status_code == 303
    job = web.job(job_id_from(response))
    assert job.status == "queued" and job.page_count == 2
    assert job.filename == "Week 3 notes.pdf"
    assert job.opt_in_retention is False
    keep = as_utc(job.retain_until) - as_utc(job.created_at)
    assert keep == timedelta(hours=24)
    with session_scope(web.ctx.factory) as session:
        blob = session.scalar(select(Blob).where(Blob.job_id == job.id))
        assert blob is not None and blob.kind == "upload" and blob.content == pdf


def test_opt_in_keeps_the_job_for_30_days(web):
    job = web.job(job_id_from(upload(web.browser, make_pdf(1), keep=True)))
    assert job.opt_in_retention is True
    assert as_utc(job.retain_until) - as_utc(job.created_at) == timedelta(days=30)


def test_opt_in_checkbox_is_off_by_default(web):
    page = web.browser.get("/app").text
    assert 'id="keep"' in page
    checkbox = page[page.index('id="keep"') - 60 : page.index('id="keep"') + 120]
    assert "checked" not in checkbox
    assert "Keep my document and results for 30 days to help improve the tool" in page


@pytest.mark.parametrize(
    ("data", "status", "message"),
    [
        (b"PK\x03\x04 a zip file pretending", 415, "not a PDF"),
        (b"", 400, "The file is empty"),
        (b"%PDF-1.7\nnot really a pdf at all", 422, "could not be opened"),
    ],
)
def test_upload_rejects_files_that_are_not_readable_pdfs(web, data, status, message):
    response = upload(web.browser, data, filename="notes.pdf")
    assert response.status_code == status
    assert message in response.text
    assert 'href="#pdf"' in response.text  # error summary linked to the file input
    assert 'aria-invalid="true"' in response.text
    assert count(web, Job) == 0


def test_upload_rejects_encrypted_pdfs(web):
    response = upload(web.browser, encrypted_pdf())
    assert response.status_code == 422
    assert "password-protected" in response.text
    assert count(web, Job) == 0


def test_upload_rejects_too_many_pages(web):
    response = upload(web.browser, make_pdf(6))  # max_pages_per_job=5 in tests
    assert response.status_code == 422
    assert "more than 5 pages" in response.text
    assert count(web, Job) == 0


def test_upload_rejects_files_over_the_size_limit(web):
    big = make_pdf(1) + b"%" + b"x" * (1024 * 1024)  # max_upload_mb=1 in tests
    response = upload(web.browser, big)
    assert response.status_code == 413
    assert "larger than 1 MB" in response.text
    assert count(web, Job) == 0


def test_upload_without_choosing_a_file(web):
    # What a browser sends when the file input is left empty: a part with no file name.
    response = web.browser.post(
        "/jobs",
        data={"csrf_token": token(web.browser)},
        files={"pdf": ("", b"", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "Choose a PDF file to upload." in response.text


def test_upload_that_is_not_multipart_is_refused(web):
    response = web.browser.post("/jobs", data={"csrf_token": token(web.browser)})
    assert response.status_code == 400
    assert count(web, Job) == 0


def test_upload_with_a_forged_csrf_token_is_refused(web):
    response = upload(web.browser, make_pdf(1), csrf="forged")
    assert response.status_code == 403
    assert count(web, Job) == 0


def test_size_limit_is_enforced_while_streaming():
    """A body that never ends is cut off just after the limit, not buffered."""
    received = 0
    head = (
        b'--b\r\nContent-Disposition: form-data; name="pdf"; filename="a.pdf"\r\n'
        b"Content-Type: application/pdf\r\n\r\n%PDF-1.7\n"
    )

    async def receive():
        nonlocal received
        received += 1
        body = head if received == 1 else b"x" * 65536
        return {"type": "http.request", "body": body, "more_body": True}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/jobs",
        "headers": [(b"content-type", b"multipart/form-data; boundary=b")],
    }

    async def run():
        with pytest.raises(UploadRejected) as exc:
            await read_upload_form(Request(scope, receive), max_bytes=100_000)
        return exc.value

    error = asyncio.run(run())
    assert error.code == "too_large"
    assert received <= 4  # 100 kB + 64 kB of form room = 3 chunks of 64 kB


def test_declared_content_length_over_the_limit_is_refused_before_reading():
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/jobs",
        "headers": [
            (b"content-type", b"multipart/form-data; boundary=b"),
            (b"content-length", str(50 * 1024 * 1024).encode()),
        ],
    }

    async def receive():  # pragma: no cover - must not be called
        raise AssertionError("body was read")

    async def run():
        with pytest.raises(UploadRejected):
            await read_upload_form(Request(scope, receive), max_bytes=1024)

    asyncio.run(run())


def test_isolated_and_in_process_inspection_agree():
    for data, expected in [(make_pdf(3), 3), (encrypted_pdf(), "encrypted"), (b"nope", "not_pdf")]:
        results = []
        for inspect in (inspect_pdf, inspect_pdf_isolated):
            try:
                results.append(inspect(data, max_pages=10))
            except UploadRejected as exc:
                results.append(exc.code)
        assert results == [expected, expected]


def test_isolated_inspection_treats_a_hang_as_unreadable(monkeypatch):
    import subprocess

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="probe", timeout=0.01)

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(UploadRejected) as exc:
        inspect_pdf_isolated(make_pdf(1), max_pages=5)
    assert exc.value.code == "unreadable"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("C:\\Users\\me\\notes.pdf", "notes.pdf"),
        ("../../etc/passwd", "passwd"),
        ("a\x00b\u202e.pdf", "ab.pdf"),
        ("", "document.pdf"),
        ("x" * 300 + ".pdf", "x" * 196 + ".pdf"),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


# --------------------------------------------------------------------------- limits


def test_uploads_in_progress_are_bounded_in_total_and_per_user(web):
    """Each upload costs about three times its size in memory and nothing limited how
    many ran at once: ten 19 MB uploads took the web process from 225 MB to 802 MB."""
    gate = web.ctx.uploads
    assert gate.limit == web.settings.max_concurrent_uploads
    with session_scope(web.ctx.factory) as session:
        alice_id = session.scalar(select(User.id).where(User.email == ALICE))
    assert alice_id is not None

    assert gate.enter(alice_id) is None  # an upload of Alice's is still in progress
    try:
        response = upload(web.browser, make_pdf(1))
        assert response.status_code == 429 and response.headers["retry-after"] == "10"
        assert "Your other upload is still in progress" in response.text
    finally:
        gate.leave(alice_id)

    others = [f"someone-{i}" for i in range(gate.limit)]
    for other in others:
        assert gate.enter(other) is None
    try:
        response = upload(web.browser, make_pdf(1))
        assert response.status_code == 503 and "Too many uploads right now" in response.text
    finally:
        for other in others:
            gate.leave(other)
    assert count(web, Job) == 0

    assert upload(web.browser, make_pdf(1)).status_code == 303
    assert gate.in_progress == 0  # released after a success ...
    assert upload(web.browser, b"not a pdf").status_code == 415
    assert gate.in_progress == 0  # ... and after a rejection


def test_daily_page_quota_returns_429_and_changes_nothing(web):
    for _ in range(4):  # quota 20 pages; 4 x 5 pages fits exactly
        assert upload(web.browser, make_pdf(5)).status_code == 303
    response = upload(web.browser, make_pdf(1))
    assert response.status_code == 429
    assert "Daily page limit reached" in response.text
    assert "you have 0 left today" in response.text
    assert response.headers.get("retry-after")
    assert count(web, Job) == 4
    with session_scope(web.ctx.factory) as session:
        assert session.scalar(select(QuotaDay.pages)) == 20


def test_quota_is_per_user(web):
    for _ in range(4):
        upload(web.browser, make_pdf(5))
    other = web.client()
    login(other, BOB)
    assert upload(other, make_pdf(5)).status_code == 303


def test_app_page_shows_pages_left_today(web):
    upload(web.browser, make_pdf(3))
    assert "You can convert 17 more pages today." in " ".join(web.browser.get("/app").text.split())


def test_when_the_daily_spend_cap_is_reached_uploads_get_a_friendly_503(web):
    with session_scope(web.ctx.factory) as session:
        session.add(SpendDay(day=utc_today(), spent_usd=5.0, held_usd=0.0))  # cap is $5
    page = web.browser.get("/app")
    assert "Conversions are paused for today" in page.text
    response = upload(web.browser, make_pdf(1))
    assert response.status_code == 503
    assert "Conversions are paused for today" in response.text
    assert "Nothing was uploaded" in response.text
    assert count(web, Job) == 0


# --------------------------------------------------------------------------- status


def test_job_page_polls_with_htmx_and_reloads_without_javascript_only_on_request(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    page = web.browser.get(f"/jobs/{job_id}").text
    assert f'hx-get="/jobs/{job_id}/status?v=queued-0-0"' in page
    # No reload the user did not ask for (WCAG 2.2.1, F41); a no-JS user can opt in.
    assert "http-equiv" not in page
    assert f'<a href="/jobs/{job_id}">refresh the status</a>' in page
    assert re.search(
        rf'<noscript>\s*<p><a href="/jobs/{job_id}\?auto=1">Reload this page every 15 seconds',
        page,
    )
    assert "next in line" in page

    auto = web.browser.get(f"/jobs/{job_id}?auto=1").text
    assert '<noscript><meta http-equiv="refresh" content="15"></noscript>' in auto
    assert f'<a href="/jobs/{job_id}">Stop reloading this page</a>' in auto
    assert f'<a href="/jobs/{job_id}?auto=1">refresh the status</a>' in auto
    assert "http-equiv" not in web.browser.get(f"/jobs/{job_id}?auto=yes").text

    web.run_worker()
    for path in (f"/jobs/{job_id}", f"/jobs/{job_id}?auto=1"):
        done = web.browser.get(path).text
        assert "http-equiv" not in done and "hx-get" not in done and "noscript" not in done
        assert "Results" in done and "Need your review" in done


def test_status_fragment_is_204_until_something_changes(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    assert web.browser.get(f"/jobs/{job_id}/status?v=queued-0-0").status_code == 204
    fragment = web.browser.get(f"/jobs/{job_id}/status?v=stale")
    assert fragment.status_code == 200 and 'id="job-status"' in fragment.text
    assert "<html" not in fragment.text  # a fragment, not a page

    web.run_worker()
    finished = web.browser.get(
        f"/jobs/{job_id}/status?v=queued-0-0", headers={"HX-Request": "true"}
    )
    assert finished.status_code == 200
    assert finished.headers["hx-refresh"] == "true"  # reload to show the results
    # htmx takes the document title from the fragment, so the tab title follows it.
    assert finished.text.startswith("<title>Finished: notes.pdf | a11y-stem</title>")
    assert "<title>" not in fragment.text  # not without htmx (it would be in <body>)


def test_queue_position_is_shown(web):
    first = job_id_from(upload(web.browser, make_pdf(1)))
    second = job_id_from(upload(web.browser, make_pdf(1)))
    assert "next in line" in web.browser.get(f"/jobs/{first}").text
    assert "1 document ahead of yours" in web.browser.get(f"/jobs/{second}").text
    # The polled fragment notices when the queue moves, not only when the status changes.
    assert web.browser.get(f"/jobs/{second}/status?v=queued-0-1").status_code == 204
    assert web.worker().claim() is not None  # the first job starts
    moved = web.browser.get(f"/jobs/{second}/status?v=queued-0-1")
    assert moved.status_code == 200 and "next in line" in moved.text


def test_status_poll_after_the_session_ends_sends_the_page_to_sign_in(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    web.browser.cookies.clear()
    response = web.browser.get(f"/jobs/{job_id}/status?v=x", headers={"HX-Request": "true"})
    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login"  # not a sign-in page swapped in


def test_status_poll_for_a_deleted_job_reloads_the_page(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    web.browser.post(
        f"/jobs/{job_id}/delete", data={"csrf_token": token(web.browser), "confirm": "yes"}
    )
    response = web.browser.get(f"/jobs/{job_id}/status?v=x", headers={"HX-Request": "true"})
    assert response.status_code == 404 and response.headers["hx-refresh"] == "true"


def test_failed_job_explains_why(web):
    from a11y_stem.core.budget import BudgetExceeded

    web.converter.raises = BudgetExceeded("daily", "cap")
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    web.run_worker()
    page = web.browser.get(f"/jobs/{job_id}").text
    assert "Stopped" in page and "budget ran out" in page
    assert "<code>budget_daily</code>" in page


# --------------------------------------------------------------------------- results


def test_download_view_and_crops_of_a_finished_job(web):
    job_id = finished_job(web)
    download = web.browser.get(f"/jobs/{job_id}/download")
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith(
        'attachment; filename="notes-accessible.html"'
    )
    assert download.headers["content-security-policy"] == VIEW_CSP
    assert "SECRET-DOC-TEXT" in download.text

    view = web.browser.get(f"/jobs/{job_id}/view")
    assert view.status_code == 200
    assert view.headers["content-type"].startswith("text/html")
    assert view.headers["content-security-policy"] == (
        "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    )
    assert "content-disposition" not in view.headers

    crop = web.browser.get(f"/jobs/{job_id}/crops/p1-e3.png")
    assert crop.status_code == 200 and crop.headers["content-type"] == "image/png"
    assert crop.content.startswith(b"\x89PNG")
    assert web.browser.get(f"/jobs/{job_id}/crops/p9-e9.png").status_code == 404
    assert web.browser.get(f"/jobs/{job_id}/crops/..%2Fx.png").status_code == 404


def test_results_are_not_available_before_the_job_finishes(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    for suffix in ("download", "view", "review"):
        response = web.browser.get(f"/jobs/{job_id}/{suffix}")
        assert response.status_code == 409, suffix
        assert "Not ready yet" in response.text


def test_download_filename_handles_any_name():
    assert download_filename("Lecture 1.pdf") == "Lecture 1-accessible.html"
    assert download_filename("noext") == "noext-accessible.html"
    header = content_disposition('Ünïcode "quotes"; x.html')
    assert header.startswith('attachment; filename="_n_code _quotes__ x.html"')
    assert "filename*=UTF-8''%C3%9Cn%C3%AFcode%20%22quotes%22%3B%20x.html" in header


# --------------------------------------------------------------------------- ownership


def test_other_users_get_404_on_every_job_route(web):
    job_id = finished_job(web)
    bob = web.client()
    login(bob, BOB)
    for path in (
        f"/jobs/{job_id}",
        f"/jobs/{job_id}/status",
        f"/jobs/{job_id}/download",
        f"/jobs/{job_id}/view",
        f"/jobs/{job_id}/crops/p1-e3.png",
        f"/jobs/{job_id}/review",
    ):
        response = bob.get(path)
        assert response.status_code == 404, path
        assert "SECRET" not in response.text
    bob_token = csrf_from(bob.get("/app").text)
    assert bob.post(f"/jobs/{job_id}/delete", data={"csrf_token": bob_token}).status_code == 404
    review = bob.post(
        f"/jobs/{job_id}/review/p1-e3", data={"csrf_token": bob_token, "action": "reject"}
    )
    assert review.status_code == 404
    assert web.job(job_id).status == "done"  # untouched
    assert "notes.pdf" not in bob.get("/app").text


def test_admins_cannot_see_other_peoples_documents_either(web):
    from fixtures.web.support import ADMIN

    job_id = finished_job(web)
    admin = web.client()
    login(admin, ADMIN)
    assert admin.get(f"/jobs/{job_id}").status_code == 404
    assert "notes.pdf" not in admin.get("/admin").text


@pytest.mark.parametrize("job_id", ["not-a-job", "0" * 31, "G" * 32])
def test_malformed_job_ids_are_404(web, job_id):
    assert web.browser.get(f"/jobs/{job_id}").status_code == 404


def test_delete_asks_first_and_one_post_without_confirmation_deletes_nothing(web):
    """WCAG 3.3.4: deleting stored user data must be confirmed, reversible or checked."""
    job_id = finished_job(web)
    blobs = count(web, Blob)
    page = web.browser.post(f"/jobs/{job_id}/delete", data={"csrf_token": token(web.browser)})
    assert page.status_code == 200
    assert "Delete notes.pdf?" in page.text and "cannot be undone" in page.text
    assert 'name="confirm" value="yes"' in page.text
    assert f'<a href="/jobs/{job_id}">No, keep it</a>' in page.text
    assert count(web, Job) == 1 and count(web, Blob) == blobs
    assert web.browser.get(f"/jobs/{job_id}/delete").status_code == 200  # the same page


def test_delete_now_removes_the_job_and_everything_derived(web):
    job_id = finished_job(web)
    assert count(web, Blob) > 1  # upload + crops
    response = web.browser.post(
        f"/jobs/{job_id}/delete", data={"csrf_token": token(web.browser), "confirm": "yes"}
    )
    assert response.status_code == 303 and response.headers["location"] == "/app"
    assert count(web, Job) == 0 and count(web, Blob) == 0
    page = web.browser.get("/app").text
    assert "have been deleted" in page
    assert '<div class="notice" role="status" tabindex="-1" autofocus>' in page
    assert web.browser.get(f"/jobs/{job_id}").status_code == 404


def test_app_lists_the_users_jobs_newest_first(web):
    first = job_id_from(upload(web.browser, make_pdf(1), filename="first.pdf"))
    second = job_id_from(upload(web.browser, make_pdf(1), filename="second.pdf"))
    page = web.browser.get("/app").text
    assert page.index(second) < page.index(first)
    assert 'Delete<span class="visually-hidden"> first.pdf</span>' in page


def test_retention_date_is_shown(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    expected = (datetime.now(UTC) + timedelta(hours=24)).strftime("%b %Y")
    assert expected in web.browser.get(f"/jobs/{job_id}").text


def test_a_job_past_its_retention_time_is_gone_before_the_purge_runs(web):
    """retain_until was only checked by the hourly purge: if the scheduler stopped, an
    expired document stayed viewable and downloadable."""
    job_id = finished_job(web)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.retain_until = datetime.now(UTC) - timedelta(minutes=1)
    for path in ("", "/status", "/view", "/download", "/review", "/crops/p1-e3.png"):
        assert web.browser.get(f"/jobs/{job_id}{path}").status_code == 404, path
    assert job_id not in web.browser.get("/app").text
    assert count(web, Job) == 1  # still in the database until the purge


def test_the_worker_purges_expired_jobs_if_the_scheduler_does_not(web, caplog, monkeypatch):
    import logging

    from a11y_stem.web import worker as worker_module

    caplog.set_level(logging.INFO)
    job_id = finished_job(web)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.retain_until = datetime.now(UTC) - timedelta(hours=2)
    monkeypatch.setattr(worker_module, "PURGE_FIRST_S", 0.0)
    worker = web.worker()
    assert worker.purge_backstop() == 1
    assert count(web, Job) == 0 and count(web, Blob) == 0
    assert "purge_backstop_deleted" in caplog.messages
    assert worker.purge_backstop() is None  # not due again for an hour
