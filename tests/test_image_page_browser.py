"""The output page of a document with pages converted from images, in a real browser
(Playwright Chromium; ADR 0007): the notice, the original page that opens and closes with
the keyboard and without a script, no sideways scrolling at 320 CSS px, and axe-core with the
WCAG 2.2 AA and best-practice rules (moderate and above), light and dark, with the original
closed and open.

Run with: uv run pytest -m browser tests/test_image_page_browser.py
"""

from __future__ import annotations

import pytest

from image_page_support import block, cell, math, scan_pdf, text
from readablestem.config import Settings
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import run
from test_web_browser import TEXT_SPACING, WCAG_TAGS, describe
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

STRICT = {"moderate", "serious", "critical"}
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
            caption="Table 1 Speeds",
            rows=[
                {"cells": [cell("Time (s)", "col"), cell("Distance (m)", "col")]},
                {"cells": [cell("1"), cell("5")]},
            ],
            y0=400,
            y1=500,
        ),
        block("figure", description="A cart on a ramp.", y0=550, y1=700),
    ]
}


@pytest.fixture(scope="module")
def html_file(tmp_path_factory) -> str:
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
    result = run.convert_pdf(scan_pdf(2), settings=settings, client=client).result
    path = tmp_path_factory.mktemp("image-page") / "out.html"
    path.write_text(result.html, encoding="utf-8")
    return path.as_uri()


def open_page(browser, url: str, *, viewport=None, scheme: str = "light", javascript: bool = True):
    context = browser.new_context(
        bypass_csp=True,
        viewport=viewport or {"width": 1280, "height": 900},
        color_scheme=scheme,
        java_script_enabled=javascript,
    )
    page = context.new_page()
    page.goto(url)
    return context, page


def strict_axe(page, axe_source: str) -> list[dict]:
    page.add_script_tag(content=axe_source)
    results = page.evaluate(
        "async (tags) => await axe.run(document, "
        "{runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']})",
        WCAG_TAGS,
    )
    return [v for v in results["violations"] if v["impact"] in STRICT]


def test_each_converted_page_starts_with_the_notice_and_a_closed_original(browser, html_file):
    context, page = open_page(browser, html_file)
    notes = page.locator(".image-page-note")
    assert notes.count() == 2
    first = notes.first
    assert first.get_by_text("Converted from an image of the page.").is_visible()
    assert first.get_by_text("Check it against the original.").is_visible()
    summary = first.locator("summary")
    assert summary.inner_text() == "Show the original page 1"
    assert first.locator("img").is_hidden()  # closed: the picture is not on screen
    assert page.locator(".image-page-note details[open]").count() == 0
    context.close()


def test_the_original_opens_and_closes_with_a_click_and_with_the_keyboard(browser, html_file):
    context, page = open_page(browser, html_file)
    details = page.locator(".image-page-note details").first
    summary = details.locator("summary")
    summary.click()
    assert details.get_attribute("open") is not None
    assert details.locator("img").is_visible()
    box = details.locator("img").bounding_box()
    assert box is not None and box["width"] > 100 and box["height"] > 100
    summary.click()
    assert details.get_attribute("open") is None
    summary.focus()
    page.keyboard.press("Enter")
    assert details.get_attribute("open") is not None
    page.keyboard.press("Space")
    assert details.get_attribute("open") is None
    context.close()


def test_the_toggle_needs_no_script(browser, html_file):
    context, page = open_page(browser, html_file, javascript=False)
    details = page.locator(".image-page-note details").first
    details.locator("summary").click()
    assert details.get_attribute("open") is not None
    assert details.locator("img").is_visible()
    context.close()


def test_the_original_has_a_text_alternative(browser, html_file):
    context, page = open_page(browser, html_file)
    alt = page.locator(".image-page-note img").first.get_attribute("alt")
    assert alt == "Original of page 1, shown for checking the converted text."
    context.close()


@pytest.mark.parametrize("spacing", [False, True], ids=["plain", "text-spacing"])
def test_nothing_scrolls_sideways_at_320_css_pixels(browser, html_file, spacing):
    context, page = open_page(browser, html_file, viewport={"width": 320, "height": 640})
    page.locator(".image-page-note summary").first.click()
    if spacing:
        page.add_style_tag(content=TEXT_SPACING)
    width = page.evaluate(
        "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    context.close()
    assert width[0] <= width[1], f"{width[0]} px of content in a {width[1]} px view"


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("opened", [False, True], ids=["closed", "open"])
@pytest.mark.parametrize("width", [1280, 390], ids=["1280px", "390px"])
def test_the_page_passes_wcag22_aa_and_best_practice(
    browser, html_file, axe_source, scheme, opened, width
):
    context, page = open_page(
        browser, html_file, viewport={"width": width, "height": 900}, scheme=scheme
    )
    if opened:
        for summary in page.locator(".image-page-note summary").all():
            summary.click()
    found = strict_axe(page, axe_source)
    context.close()
    assert not found, describe(found)
