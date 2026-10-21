"""The upload option "Convert scanned or handwritten pages" in a real browser (Playwright
Chromium; ADR 0007): the form with and without the offer, the refusal that points to it, the job
and the review screen of a converted scan, with axe-core (WCAG 2.2 AA and best practice, moderate
and above), reflow at 320 CSS px, and the keyboard.

Run with: uv run pytest -m browser tests/test_image_page_option_browser.py
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from fixtures.web.support import ALICE, WebEnv
from image_page_support import block, cell, math, scan_pdf, text
from readablestem.config import Settings
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline.run import convert_pdf
from test_web_browser import TEXT_SPACING, WCAG_TAGS, describe, free_port, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review_browser import sideways_overflow

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
NARROW = {"width": 320, "height": 640}
STRICT = {"moderate", "serious", "critical"}
OFFER = "Convert scanned or handwritten pages"
PAGE = {
    "blocks": [
        block("heading", level=1, runs=[text("Motion in one dimension")], y0=40, y1=80),
        block("paragraph", runs=[text("Speed is"), math("v"), text("over time.")], y0=100, y1=160),
        block("equation", latex="v = \\frac{d}{t}", label="(1)", y0=200, y1=260),
        block(
            "paragraph", confidence="low", runs=[text("Mass is [illegible] kg.")], y0=300, y1=340
        ),
        block(
            "table",
            rows=[
                {"cells": [cell("Time (s)", "col"), cell("Distance (m)", "col")]},
                {"cells": [cell("1"), cell("5")]},
            ],
            y0=400,
            y1=500,
        ),
    ]
}
SCAN = scan_pdf(2)


def converted():
    client = FakeVisionClient(
        responders={
            "image_page": lambda r: PAGE,
            "equation": lambda r: {
                "is_equation": True,
                "latex": "v = \\frac{d}{t}",
                "confidence": 0.9,
            },
        }
    )
    settings = Settings(_env_file=None, feature_image_pages=True)  # type: ignore[call-arg]
    return convert_pdf(SCAN, settings=settings, client=client)


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    import uvicorn

    env = WebEnv(
        tmp_path_factory.mktemp("image-option-browser"),
        feature_image_pages=True,
        per_user_daily_page_quota=1000,
    )
    env.converter.output = converted()
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


def open_page(browser, site, *, viewport=DESKTOP, javascript: bool = True):
    context = browser.new_context(
        bypass_csp=True, viewport=viewport, java_script_enabled=javascript
    )
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    return context, page


def strict_axe(page, axe_source: str) -> list[dict]:
    page.add_script_tag(content=axe_source)
    results = page.evaluate(
        "async (tags) => await axe.run(document, "
        "{runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']})",
        WCAG_TAGS,
    )
    return [v for v in results["violations"] if v["impact"] in STRICT]


def upload(page, site, *, ask: bool) -> None:
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF files").set_input_files(
        files=[{"name": "notes.pdf", "mimeType": "application/pdf", "buffer": SCAN}]
    )
    if ask:
        page.get_by_label(OFFER).check()
    page.get_by_role("button", name="Upload and convert").click()


@pytest.fixture(scope="module")
def job(site, browser) -> str:
    context, page = open_page(browser, site)
    upload(page, site, ask=True)
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    assert site.run_worker() == job_id
    return job_id


# ------------------------------------------------------------------------------- the form


def test_the_option_is_a_labelled_unchecked_checkbox_with_its_explanation(browser, site):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/app")
    box = page.get_by_label(OFFER)
    assert box.count() == 1 and not box.is_checked()
    label = page.locator("label[for=image_pages]").inner_text()
    assert "slower" in label and "every converted equation and table needs your check" in label
    assert "does nothing for a PDF that has no scanned or handwritten pages" in " ".join(
        page.locator("#image-pages-hint").inner_text().split()
    )
    described = box.get_attribute("aria-describedby")
    assert described == "image-pages-hint"
    context.close()


def test_the_option_can_be_ticked_with_the_keyboard(browser, site):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/app")
    page.get_by_label(OFFER).focus()
    page.keyboard.press("Space")
    assert page.get_by_label(OFFER).is_checked()
    page.keyboard.press("Space")
    assert not page.get_by_label(OFFER).is_checked()
    context.close()


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440px", "390px"])
def test_the_upload_form_with_the_option_passes_axe(browser, site, axe_source, viewport):
    context, page = open_page(browser, site, viewport=viewport)
    page.goto(f"{site.base}/app")
    found = strict_axe(page, axe_source)
    context.close()
    assert not found, describe(found)


@pytest.mark.parametrize("spacing", [False, True], ids=["plain", "text-spacing"])
def test_the_upload_form_does_not_scroll_sideways_at_320_px(browser, site, spacing):
    context, page = open_page(browser, site, viewport=NARROW)
    page.goto(f"{site.base}/app")
    if spacing:
        page.add_style_tag(content=TEXT_SPACING)
    assert not sideways_overflow(page)
    context.close()


# ------------------------------------------------------------------------------ the answers


def test_a_scan_without_the_option_is_refused_with_the_way_to_ask(browser, site, axe_source):
    context, page = open_page(browser, site)
    upload(page, site, ask=False)
    page.wait_for_load_state("networkidle")
    body = " ".join(page.locator("main").inner_text().split())
    assert "is scanned, so this tool cannot convert it" in body
    assert "Convert scanned or handwritten pages" in body
    assert page.get_by_label(OFFER).count() == 1  # the form is still there to tick it
    found = strict_axe(page, axe_source)
    context.close()
    assert not found, describe(found)


def test_a_scan_with_the_option_becomes_a_job_that_passes_axe(browser, site, axe_source, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}")
    found = strict_axe(page, axe_source)
    assert not found, describe(found)
    context.close()


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440px", "390px"])
def test_the_review_of_a_converted_scan_passes_axe(browser, site, axe_source, job, viewport):
    context, page = open_page(browser, site, viewport=viewport)
    review = f"{site.base}/jobs/{job}/review"
    page.goto(review)
    page.wait_for_load_state("networkidle")
    assert "Text as read from the image" in page.content() or "Equation" in page.content()
    found = strict_axe(page, axe_source)
    assert not found, describe(found)
    page.goto(f"{review}/list")
    page.wait_for_load_state("networkidle")
    assert "Text as read from the image" in page.content()
    found = strict_axe(page, axe_source)
    assert not found, describe(found)
    context.close()


def test_the_student_file_of_a_converted_scan_has_the_notice_and_the_original(browser, site, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}/view")
    assert page.get_by_text("Converted from an image of the page.").first.is_visible()
    page.get_by_text("Show the original page 1").first.click()
    assert page.locator(".image-page-note details[open] img").first.is_visible()
    context.close()
