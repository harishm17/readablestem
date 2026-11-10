"""UX round 2, lane D, in a real browser (Playwright Chromium).

The offices page's tables at 320px (axe `scrollable-region-focusable`) and by keyboard, the
one-page summary printed on Letter and A4, the 44px footer targets, and axe on the pages this
lane rewrote, in light and dark.

Run with: uv run pytest -m browser tests/test_web_ux2_lane_d_browser.py
"""

from __future__ import annotations

import pytest

from fixtures.web.support import ALICE, WebEnv
from readablestem.web import offices
from test_web_browser import WCAG_TAGS, describe, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_browser import finished_job as finished_job  # fixture, re-exported for pytest
from test_web_browser import site as site  # fixture, re-exported for pytest

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

BLOCKING = {"serious", "critical", "moderate"}
PAGES = [
    "/for-accessibility-offices",
    "/privacy",
    "/terms",
    "/limits",
    "/login",
    "/status",
    "/style",
    "/guides",
    "/guides/readablestem-mathpix-latexml",
    "/guides/ada-title-ii-stem-course-materials",
    "/help/posting",
    "/",
]


def axe(page, source: str) -> list[dict]:
    page.add_script_tag(content=source)
    result = page.evaluate(
        "async (tags) => await axe.run(document, "
        "{runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']})",
        WCAG_TAGS,
    )
    return [v for v in result["violations"] if v["impact"] in BLOCKING]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [1280, 320])
@pytest.mark.parametrize("path", PAGES)
def test_the_pages_pass_axe_at_1280_and_320_in_light_and_dark(
    site, browser, axe_source, path, width, scheme
):
    context = browser.new_context(
        viewport={"width": width, "height": 800}, color_scheme=scheme, bypass_csp=True
    )
    page = context.new_page()
    assert page.goto(site.base + path).status == 200
    violations = axe(page, axe_source)
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    context.close()
    assert not violations, f"{path} {width} {scheme}: {describe(violations)}"
    assert overflow <= 0, f"{path} is {overflow}px wider than {width}"


def test_the_footer_targets_are_44px_on_public_and_signed_in_pages(site, browser):
    for width in (1280, 390):
        for path, signed in (("/privacy", False), ("/login", False), ("/app", True)):
            context = browser.new_context(viewport={"width": width, "height": 800})
            page = context.new_page()
            if signed:
                sign_in(page, site.base, ALICE)
            page.goto(site.base + path)
            small = page.evaluate(
                """() => [...document.querySelectorAll('footer li > a')]
                    .map(a => [a.textContent.trim(), Math.round(a.getBoundingClientRect().height)])
                    .filter(([, h]) => h < 44)"""
            )
            context.close()
            assert not small, f"{path} at {width}: {small}"


# ------------------------------------------------------------------------ print


def test_the_one_page_summary_prints_on_one_letter_and_one_a4_page(tmp_path, browser):
    import pymupdf

    env = WebEnv(
        tmp_path,
        model_provider="anthropic",
        feature_tables=True,
        feature_image_pages=True,
        feature_notation_memory=True,
        analytics_enabled=True,
        contact_email="help@readablestem.com",
    )
    with env.running() as client:
        html = client.get(offices.SUMMARY_PATH).text
    page = browser.new_page()
    page.set_content(html)
    for paper in ("Letter", "A4"):
        pdf = page.pdf(format=paper, print_background=True)
        assert pymupdf.open(stream=pdf, filetype="pdf").page_count == 1, paper
    # The headings step down: title, section, text (computed in print).
    page.emulate_media(media="print")
    sizes = page.evaluate(
        """() => ['h1', 'h2', 'p'].map(
            t => parseFloat(getComputedStyle(document.querySelector('main ' + t)).fontSize))"""
    )
    page.close()
    assert sizes[0] >= 1.4 * sizes[1] and sizes[1] >= 1.35 * sizes[2], sizes
