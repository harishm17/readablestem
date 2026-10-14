"""Launch audit: hostile uploads.

Size and page limits hold before anything is parsed or any model is called; a PDF that
crashes, hangs or balloons the parser costs a child process and nothing else; file names
cannot carry markup, paths or header injection; and one person cannot fill the database
or the queue.
"""

from __future__ import annotations

import time
import zlib

import pytest
from sqlalchemy import func, select

from a11y_stem.web import uploads
from a11y_stem.web.db import Blob, Job, QuotaDay, session_scope
from a11y_stem.web.uploads import UploadRejected, inspect_pdf_isolated, safe_filename
from fixtures.web.support import (
    ALICE,
    WebEnv,
    csrf_from,
    job_id_from,
    login,
    make_pdf,
    token,
    upload,
)


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, max_upload_mb=1, max_pages_per_job=5)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def rows(env, model) -> int:
    with session_scope(env.ctx.factory) as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def pages_used(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return int(session.scalar(select(func.coalesce(func.sum(QuotaDay.pages), 0))) or 0)


# --------------------------------------------------------------------------- limits first


def test_an_oversized_body_is_refused_before_the_pdf_is_opened(web, monkeypatch):
    opened = []
    monkeypatch.setattr(uploads, "probe", lambda *a, **k: opened.append(1))
    monkeypatch.setattr(
        "a11y_stem.web.routes.jobs.inspect_pdf_isolated", lambda *a, **k: opened.append(1)
    )
    big = b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024)
    response = upload(web.browser, big)
    assert response.status_code == 413
    assert opened == [] and rows(web, Job) == 0 and pages_used(web) == 0


def test_a_streamed_body_with_no_length_is_cut_off_at_the_limit(tmp_path):
    """A real server, a chunked body with no Content-Length: the limit holds while the body
    streams in, so the server stops reading long before the end of a huge body."""
    import socket
    import threading

    import httpx
    import uvicorn

    env = WebEnv(tmp_path, max_upload_mb=1, max_pages_per_job=5)
    read = [0]

    async def counting_app(scope, receive, send):
        async def counted():
            message = await receive()
            read[0] += len(message.get("body", b""))
            return message

        await env.app(scope, counted, send)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(counting_app, host="127.0.0.1", port=port, log_config=None, access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        env.allow(ALICE)
        with httpx.Client(base_url=base, timeout=30) as client:
            client.post(
                "/dev-login",
                data={"csrf_token": csrf_from(client.get("/dev-login").text), "email": ALICE},
            )
            csrf = csrf_from(client.get("/app").text)
            sent = []

            def chunks():
                yield b'--b\r\nContent-Disposition: form-data; name="csrf_token"\r\n\r\n'
                yield csrf.encode() + b"\r\n"
                yield b'--b\r\nContent-Disposition: form-data; name="pdf"; filename="a.pdf"\r\n'
                yield b"Content-Type: application/pdf\r\n\r\n%PDF-1.4\n"
                for _ in range(256):  # 256 MiB offered, 1 MiB allowed
                    sent.append(1)
                    yield b"0" * (1024 * 1024)
                yield b"\r\n--b--\r\n"

            try:
                response = client.post(
                    "/jobs",
                    content=chunks(),
                    headers={"Content-Type": "multipart/form-data; boundary=b"},
                )
                assert response.status_code == 413
            except httpx.TransportError:
                pass  # the server closed the connection once it had its answer
        assert read[0] < 16 * 1024 * 1024, "the server read a body it was never going to accept"
        assert sent, "the body was offered"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    with session_scope(env.ctx.factory) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_too_many_parts_or_files_is_a_bad_form_not_a_crash(web):
    csrf = token(web.browser)
    files = [("pdf", (f"{n}.pdf", make_pdf(1), "application/pdf")) for n in range(3)]
    response = web.browser.post("/jobs", data={"csrf_token": csrf}, files=files)
    assert response.status_code in {400, 413}
    many = {f"f{n}": "x" for n in range(60)} | {"csrf_token": csrf}
    response = web.browser.post(
        "/jobs", data=many, files={"pdf": ("a.pdf", make_pdf(1), "application/pdf")}
    )
    assert response.status_code in {400, 413}
    assert rows(web, Job) == 0


def test_too_many_pages_is_refused_without_spending_the_daily_allowance(web):
    response = upload(web.browser, make_pdf(6))
    assert response.status_code == 422
    assert "limit is 5" in response.text
    assert rows(web, Job) == 0 and rows(web, Blob) == 0 and pages_used(web) == 0


def test_the_content_type_and_the_name_do_not_make_a_pdf(web):
    for name, body in [
        ("x.pdf", b"<html><script>alert(1)</script></html>"),
        ("x.pdf", b"PK\x03\x04" + b"0" * 100),  # a zip
        ("x.pdf", b"MZ" + b"0" * 100),
        ("x.pdf", b""),
    ]:
        response = upload(web.browser, body, filename=name)
        assert response.status_code in {400, 415}, body[:8]
    assert rows(web, Job) == 0


# --------------------------------------------------------------------------- the parser


def pdf_object_file(*objects: bytes, trailer_extra: bytes = b"") -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R %s>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        trailer_extra,
        xref,
    )
    return bytes(out)


def page_tree_cycle() -> bytes:
    return pdf_object_file(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [2 0 R 3 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    )


def lying_page_count() -> bytes:
    return pdf_object_file(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1000000000 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    )


def huge_page() -> bytes:
    return pdf_object_file(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1000000 1000000] >>",
    )


def endless_stream_length() -> bytes:
    return pdf_object_file(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length 999999999999 >>\nstream\nBT ET\nendstream",
    )


def truncated(fraction: float) -> bytes:
    data = make_pdf(3)
    return data[: int(len(data) * fraction)]


HOSTILE = {
    "page tree that points at itself": page_tree_cycle(),
    "page count that lies": lying_page_count(),
    "a page a million points wide": huge_page(),
    "a stream length past the end of the file": endless_stream_length(),
    "garbage after the header": b"%PDF-1.7\n" + bytes(range(256)) * 400,
    "header only": b"%PDF-1.7\n",
    "truncated at 10 percent": truncated(0.1),
    "truncated at 50 percent": truncated(0.5),
    "truncated at 90 percent": truncated(0.9),
    "text file with a PDF header buried in it": b"hello\n" * 50 + b"%PDF-1.4\n" + b"x" * 500,
}
OUTCOMES = {
    "empty",
    "not_pdf",
    "encrypted",
    "unreadable",
    "damaged",
    "no_pages",
    "too_many_pages",
    "page_too_large",
    "scanned",
    "scanned_ocr",
    "handwritten",
    "handwritten_no_text",
    "no_content",
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_a_hostile_pdf_gets_a_category_and_never_an_exception(name):
    started = time.monotonic()
    try:
        pages = inspect_pdf_isolated(HOSTILE[name], max_pages=5, timeout_s=20)
    except UploadRejected as rejected:
        assert rejected.code in OUTCOMES, name
    else:
        assert 1 <= pages <= 5, name  # accepted only if it really is a small document
    assert time.monotonic() - started < 20, name


def test_a_hostile_pdf_through_the_form_is_a_page_not_a_500(web):
    for name, body in HOSTILE.items():
        response = upload(web.browser, body)
        assert response.status_code in {303, 400, 415, 422}, (name, response.status_code)
    assert rows(web, Job) == sum(
        1
        for name, body in HOSTILE.items()
        if _accepted(body)  # whatever the probe accepts is a small, ordinary job
    )


def _accepted(body: bytes) -> bool:
    try:
        inspect_pdf_isolated(body, max_pages=5, timeout_s=20)
    except UploadRejected:
        return False
    return True


def flate_zeros(size: int) -> bytes:
    compressor = zlib.compressobj(9)
    chunk = b"\0" * (1 << 24)
    out = bytearray()
    for _ in range(size >> 24):
        out += compressor.compress(chunk)
    return bytes(out + compressor.flush())


def test_a_decompression_bomb_costs_a_child_process_for_a_bounded_time():
    """About 1 MB of PDF that inflates to 1 GiB of page content. The probe is killed at its
    timeout and the answer is a category; the web process never holds the inflated bytes."""
    stream = flate_zeros(1 << 30)
    assert len(stream) < 2 * 1024 * 1024
    bomb = pdf_object_file(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream",
    )
    started = time.monotonic()
    with pytest.raises(UploadRejected) as rejected:
        inspect_pdf_isolated(bomb, max_pages=5, timeout_s=1.5)
    assert rejected.value.code in {"unreadable", "no_content"}
    assert time.monotonic() - started < 15


def test_the_probe_child_gets_no_secrets_from_the_environment(monkeypatch):
    """The child parses bytes from the internet in C. It is started with a minimal
    environment, so a bug in the parser cannot read the keys of the server's own."""
    monkeypatch.setenv("SESSION_SECRET", "s3cret-session-value")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host/db")
    seen = {}
    real_run = uploads.subprocess.run

    def spy(*args, **kwargs):
        seen.update(kwargs.get("env") or {"INHERITED": "all"})
        return real_run(*args, **kwargs)

    monkeypatch.setattr(uploads.subprocess, "run", spy)
    assert inspect_pdf_isolated(make_pdf(1), max_pages=5) == 1
    assert "INHERITED" not in seen, "the child inherited the whole environment"
    assert not any(
        word in key for key in seen for word in ("SECRET", "KEY", "TOKEN", "DATABASE", "PASSWORD")
    )


# --------------------------------------------------------------------------- names


HOSTILE_NAMES = [
    "../../etc/passwd.pdf",
    "..\\..\\windows\\system32.pdf",
    "a\x00b.pdf",
    "‮gpj.pdf",  # right-to-left override: "fdp.jpg" to the eye
    "x" * 5000 + ".pdf",
    "bad\r\nSet-Cookie: pwned=1.pdf",
    '"><img src=x onerror=alert(1)>.pdf',
    "<script>alert(1)</script>.pdf",
    "CON.pdf",
    ".pdf",
    "",
]


@pytest.mark.parametrize("raw", HOSTILE_NAMES)
def test_safe_filename_is_a_short_plain_base_name(raw):
    name = safe_filename(raw)
    assert 0 < len(name) <= 200
    assert "/" not in name and "\\" not in name and ".." not in name.split(".")[0:1]
    assert all(ord(ch) >= 32 and ch not in "\x7f‮‭⁦⁧" for ch in name)


def test_hostile_names_are_shown_escaped_and_never_reach_headers(web):
    name = '"><img src=x onerror=alert(1)>\r\nX-Evil: 1.pdf'
    job_id = job_id_from(upload(web.browser, make_pdf(1), filename=name))
    assert web.run_worker() == job_id
    for path in ("/app", f"/jobs/{job_id}", f"/jobs/{job_id}/review"):
        body = web.browser.get(path).text
        assert "<img src=x" not in body, path
        assert "onerror=alert" not in body.replace("&", "") or "&lt;img" in body, path
    download = web.browser.get(f"/jobs/{job_id}/download")
    assert "x-evil" not in {key.lower() for key in download.headers}
    assert "\r" not in download.headers["content-disposition"]
    assert "\n" not in download.headers["content-disposition"]


# --------------------------------------------------------------------------- quotas


def test_one_person_cannot_queue_without_limit(web):
    """At most a few conversions wait or run at once for one person: a long queue of
    their own files must not push everyone else back."""
    from a11y_stem.web.routes.jobs import MAX_ACTIVE_JOBS_PER_USER

    queued = 0
    for _ in range(MAX_ACTIVE_JOBS_PER_USER + 3):
        response = upload(web.browser, make_pdf(1))
        if response.status_code == 303:
            queued += 1
        else:
            assert response.status_code == 429
            assert "still being converted" in response.text
    assert queued == MAX_ACTIVE_JOBS_PER_USER
    assert pages_used(web) == MAX_ACTIVE_JOBS_PER_USER  # a refusal costs no pages
    web.run_worker()  # one finishes: room for one more
    assert upload(web.browser, make_pdf(1)).status_code == 303


def test_one_person_cannot_store_without_limit(tmp_path):
    from a11y_stem.web.routes.jobs import MAX_LIVE_JOBS_PER_USER

    env = WebEnv(tmp_path, per_user_daily_page_quota=1000)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.ctx.limiter.rules["upload_user"] = env.ctx.limiter.rules["upload"] = type(
            env.ctx.limiter.rules["upload"]
        )(10_000, 60)
        stored = 0
        for _ in range(MAX_LIVE_JOBS_PER_USER + 3):
            response = upload(client, make_pdf(1), keep=True)
            if response.status_code == 303:
                stored += 1
                while env.run_worker():
                    pass
            else:
                assert response.status_code == 429
                assert "delete" in response.text.lower()
        assert stored == MAX_LIVE_JOBS_PER_USER
        assert csrf_from(client.get("/app").text)


def test_child_env_keeps_only_what_an_interpreter_needs(monkeypatch):
    from a11y_stem.core.childenv import child_env

    for name in ("SESSION_SECRET", "CRON_TOKEN", "GOOGLE_CLIENT_SECRET", "MS_CLIENT_SECRET"):
        monkeypatch.setenv(name, "secret-value")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = child_env()
    assert env.get("PATH") == "/usr/bin"
    assert "secret-value" not in env.values()
