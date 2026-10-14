"""The upload form with Word and PowerPoint files, in a real browser (Playwright Chromium):
the check app.js makes before sending, an upload of a Word file, and axe-core at 1280 and
375 CSS px.

Run with: uv run pytest -m browser tests/test_web_office_browser.py
"""

from __future__ import annotations

import threading
import time

import pytest

from fixtures import office
from fixtures.web.support import ALICE, WebEnv
from test_office_convert import stand_in  # noqa: F401  (the stand-in helper)
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_browser import describe, free_port, sign_in
from test_web_office import fake_soffice
from test_web_ux_browser import strict_violations

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    import uvicorn

    tmp = tmp_path_factory.mktemp("office-browser")
    env = WebEnv(tmp, office_input_enabled=True, soffice_path=fake_soffice(tmp))
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


def choose(page, name: str, content: bytes, mime: str) -> None:
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": name, "mimeType": mime, "buffer": content}]
    )


def test_a_word_file_is_accepted_by_the_form_and_uploaded(site, browser):
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    choose(page, "notes.docx", office.docx(), "application/octet-stream")
    assert page.locator("#pdf-check").inner_text() == ""
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    context.close()


def test_other_files_are_turned_away_in_the_browser_in_plain_words(site, browser):
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    choose(page, "data.xlsx", b"x", "application/octet-stream")
    message = page.locator("#pdf-check").inner_text()
    assert "data.xlsx is not a PDF, Word (.docx) or PowerPoint (.pptx) file" in message
    assert "Choose a PDF, .docx or .pptx file." in message
    context.close()


@pytest.mark.parametrize("width", [1280, 375])
def test_the_upload_form_passes_axe_with_the_word_hint(site, browser, axe_source, width):
    context = browser.new_context(viewport={"width": width, "height": 800}, bypass_csp=True)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    assert "Word (.docx) and PowerPoint (.pptx)" in page.locator("#pdf-hint").inner_text()
    violations = strict_violations(page, axe_source)
    scroll = page.evaluate(
        "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    context.close()
    assert not violations, describe(violations)
    assert scroll[0] <= scroll[1]
