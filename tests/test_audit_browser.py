"""Launch audit, in a real browser: the policy actually stops script.

Two layers are checked in Chromium. (1) The converted document, served from our origin, is
sandboxed: even a file that did contain a script, an inline handler and a form cannot run
anything or read the app's cookies. (2) The app's own pages refuse inline script: a script
injected into a page does not run under `script-src 'self'`.

Run with: uv run pytest -m browser tests/test_audit_browser.py
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from a11y_stem.web.db import Job, session_scope
from fixtures.web.support import ALICE, WebEnv, make_pdf

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

HOSTILE_DOCUMENT = """<!doctype html><html lang="en"><head><title>x</title>
<style>body{background:#fff}</style></head><body><main>
<h1 onclick="window.__pwned = 'handler'">Hostile</h1>
<script>window.__pwned = 'script'; try { window.__cookie = document.cookie } catch (e) {
  window.__cookie_error = e.name }</script>
<img src="x" onerror="window.__pwned = 'onerror'">
<form action="/logout" method="post" id="f"><button id="b">go</button></form>
<a id="a" href="javascript:window.__pwned='href'">link</a>
</main></body></html>"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    import uvicorn

    env = WebEnv(tmp_path_factory.mktemp("audit-browser"))
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(env.app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    env.base = f"http://127.0.0.1:{port}"
    env.allow(ALICE)
    yield env
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover - browsers not installed
            pytest.skip(f"Chromium unavailable ({type(exc).__name__}); run playwright install")
        yield chromium
        chromium.close()


def sign_in(page, base: str) -> None:
    page.goto(f"{base}/dev-login")
    page.get_by_label("Email address").fill(ALICE)
    page.get_by_role("button", name="Sign in").click()


def finished_job(site, page) -> str:
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": "notes.pdf", "mimeType": "application/pdf", "buffer": make_pdf(1)}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    assert site.run_worker() == job_id
    return job_id


def test_a_hostile_document_runs_nothing_and_sees_no_cookie(site, browser):
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base)
    job_id = finished_job(site, page)
    with session_scope(site.ctx.factory) as session:
        session.get(Job, job_id).html = HOSTILE_DOCUMENT
    page.goto(f"{site.base}/jobs/{job_id}/view")
    assert page.locator("h1").inner_text() == "Hostile"
    page.locator("h1").click()
    page.wait_for_timeout(300)
    assert page.evaluate("window.__pwned") is None, "script or a handler ran"
    assert page.evaluate("window.__cookie") is None
    # The sandbox gives the document an opaque origin: even reading the cookie is refused,
    # and the script that tried never ran at all.
    page.locator("#a").click()
    page.wait_for_timeout(200)
    assert page.evaluate("window.__pwned") is None
    page.locator("#b").click()  # a form in a sandbox without allow-forms does not submit
    page.wait_for_timeout(300)
    page.goto(f"{site.base}/app")
    assert "Upload" in page.content(), "the form must not have signed the user out"
    context.close()


def test_a_script_put_into_an_app_page_does_not_run(site, browser):
    """Even if markup were injected into one of our pages, `script-src 'self'` refuses an
    inline script, an inline handler and a javascript: link."""
    context = browser.new_context()
    page = context.new_page()
    violations: list[str] = []
    page.on("console", lambda message: violations.append(message.text))
    sign_in(page, site.base)
    page.goto(f"{site.base}/app")
    page.evaluate(
        """() => {
          const s = document.createElement('script');
          s.textContent = "window.__pwned = 'inline'";
          document.body.appendChild(s);
          const i = document.createElement('img');
          i.setAttribute('onerror', "window.__pwned = 'handler'");
          i.src = 'x';
          document.body.appendChild(i);
        }"""
    )
    page.wait_for_timeout(500)
    assert page.evaluate("window.__pwned") is None
    assert any("Content Security Policy" in text for text in violations), violations
    context.close()


def test_the_app_cannot_be_framed(site, browser):
    context = browser.new_context()
    page = context.new_page()
    framer = f"data:text/html,<iframe id=f src='{site.base}/privacy'></iframe>"
    page.goto(framer)
    page.wait_for_timeout(500)
    frame = [f for f in page.frames if f.url.startswith(site.base)]
    assert not frame or "privacy" not in (frame[0].content() if frame else ""), "the app was framed"
    context.close()


def test_a_hostile_file_name_is_text_in_the_page_not_markup(site, browser):
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base)
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[
            {
                "name": "<img src=x onerror=window.__pwned=1>.pdf",
                "mimeType": "application/pdf",
                "buffer": make_pdf(1),
            }
        ]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    page.goto(f"{site.base}/app")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__pwned") is None
    assert page.locator("img[onerror]").count() == 0
    context.close()
