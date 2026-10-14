"""Tier 1 web items in a real browser (Playwright Chromium): the job page with its new
language line, checks disclosure, title and contact fields and keeping controls; the
repeated-heading card with its copies; the table card's headers button on a phone; and the
admin tables at 375 CSS px (axe scrollable-region-focusable).

axe-core (WCAG 2.2 AA and best practice, moderate and above) runs at 1280 and 375 CSS px
in each state. Set A11Y_SHOTS to a directory to also write a screenshot of each state.

Run with: uv run pytest -m browser tests/test_web_tier1_browser.py
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

import pytest

from a11y_stem.core.types import Check
from a11y_stem.web.db import ErrorAggregate, session_scope
from fixtures.web.review_doc import GROUP
from fixtures.web.support import ADMIN
from fixtures.web.table_doc import table_output, table_pdf
from test_web_browser import TEXT_SPACING, describe, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review_browser import sideways_overflow
from test_web_review_split_browser import job as job  # fixture, re-exported for pytest
from test_web_review_split_browser import open_page, strict_axe, wait_images
from test_web_review_split_browser import site as site  # fixture, re-exported for pytest

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

WIDE = {"width": 1280, "height": 900}
PHONE = {"width": 375, "height": 800}
SIZES = pytest.mark.parametrize("viewport", [WIDE, PHONE], ids=["1280", "375"])


def shoot(page, name: str, viewport: dict) -> None:
    folder = os.environ.get("A11Y_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}-{viewport['width']}.png"), full_page=True)


def clean(page, axe_source: str, what: str) -> None:
    violations = strict_axe(page, axe_source)
    assert not violations, f"{what}: {describe(violations)}"


# --------------------------------------------------------------------------- the job page


@SIZES
def test_the_job_page_passes_axe_also_with_the_language_control_open(
    site, browser, job, axe_source, viewport
):
    context, page = open_page(browser, site, viewport=viewport)
    page.goto(f"{site.base}/jobs/{job}")
    clean(page, axe_source, "job page")
    shoot(page, "job-page", viewport)
    page.locator("details.inline-details > summary").click()
    expect(page.get_by_label("Language of this document")).to_be_visible()
    clean(page, axe_source, "job page, language open")
    page.locator("#checks-ran summary").click()
    expect(page.get_by_text("Did not run on this file:")).to_be_visible()
    clean(page, axe_source, "job page, checks disclosure open")
    shoot(page, "job-page-open", viewport)
    assert not sideways_overflow(page)
    context.close()


def test_the_language_change_control_opens_from_the_keyboard(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}")
    summary = page.locator("details.inline-details > summary")
    summary.focus()
    page.keyboard.press("Enter")
    expect(page.get_by_label("Language of this document")).to_be_visible()
    context.close()


def test_the_job_page_reflows_at_320_css_px_with_text_spacing(site, browser, job):
    context, page = open_page(browser, site, viewport={"width": 320, "height": 640})
    page.goto(f"{site.base}/jobs/{job}")
    page.add_style_tag(content=TEXT_SPACING)
    assert not sideways_overflow(page)
    context.close()


def test_the_title_and_contact_line_are_saved_from_the_page(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}")
    page.get_by_label("Title students hear").fill("Vectors, week 3")
    page.get_by_role("button", name="Save the title").click()
    page.wait_for_url(f"{site.base}/jobs/{job}")
    expect(page.get_by_role("heading", level=1)).to_have_text("Vectors, week 3")
    assert page.title().startswith("Converted: Vectors, week 3")
    page.get_by_label("Contact line for students (optional)").fill("prof.lee@example.edu")
    page.get_by_role("button", name="Save the contact line").click()
    page.wait_for_url(f"{site.base}/jobs/{job}")
    preview = context.new_page()
    preview.goto(f"{site.base}/jobs/{job}/view")
    expect(preview.locator("footer a[href='mailto:prof.lee@example.edu']")).to_be_visible()
    assert preview.title() == "Vectors, week 3"
    context.close()


@SIZES
def test_a_refused_contact_line_shows_the_error_with_what_was_typed(
    site, browser, job, axe_source, viewport
):
    context, page = open_page(browser, site, viewport=viewport)
    page.goto(f"{site.base}/jobs/{job}")
    page.get_by_label("Contact line for students (optional)").fill("not an address")
    page.get_by_role("button", name="Save the contact line").click()
    expect(page.locator(".error-summary")).to_be_visible()
    expect(page.get_by_label("Contact line for students (optional)")).to_have_value(
        "not an address"
    )
    clean(page, axe_source, "refused contact line")
    shoot(page, "job-page-contact-error", viewport)
    context.close()


def test_keep_7_more_days_works_without_javascript(site, browser, job):
    context, page = open_page(browser, site, javascript=False)
    page.goto(f"{site.base}/jobs/{job}")
    page.get_by_role("button", name="Keep 7 more days").click()
    expect(page.locator("[role=status], .notice").first).to_contain_text("7 more days")
    expect(page.get_by_text("kept for 7 days, and only you can see it")).to_be_visible()
    context.close()


# --------------------------------------------------------------------------- the group card


@SIZES
def test_the_group_card_shows_its_copies_and_passes_axe(site, browser, job, axe_source, viewport):
    context, page = open_page(browser, site, viewport=viewport)
    page.goto(f"{site.base}/jobs/{job}/review?item={GROUP}")
    wait_images(page)
    images = page.locator(".group-crops img")
    assert images.count() == 3
    for i in range(3):
        assert images.nth(i).evaluate("img => img.complete && img.naturalWidth > 0")
    expect(page.locator(".group-fact")).to_contain_text("Suggested: keep them as headings.")
    clean(page, axe_source, "group card")
    shoot(page, "group-card", viewport)
    assert not sideways_overflow(page)
    context.close()


def test_the_list_view_group_card_has_no_codes_or_confidence(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}/review/list")
    text = page.locator("main").inner_text()
    assert "Confidence" not in text and "Code:" not in text
    assert not re.search(r"\(\d+%\)", text)
    context.close()


# --------------------------------------------------------------------------- the table card


@SIZES
def test_the_table_card_offers_the_first_row_as_headers(site, browser, axe_source, viewport):
    output = table_output()
    fix = output.result.fix_for("p1-e1")
    assert fix is not None
    fix.checks = [Check(name="table.headers", passed=False, severity="warning", code="no_headers")]
    original = site.converter.output
    site.converter.output = output
    try:
        context, page = open_page(browser, site, viewport=viewport)
        page.goto(f"{site.base}/app")
        page.get_by_label("PDF file").set_input_files(
            files=[{"name": "tables.pdf", "mimeType": "application/pdf", "buffer": table_pdf()}]
        )
        page.get_by_role("button", name="Upload and convert").click()
        page.wait_for_url(f"{site.base}/jobs/*")
        job_id = page.url.rsplit("/", 1)[-1]
        assert site.run_worker() == job_id
    finally:
        site.converter.output = original
    page.goto(f"{site.base}/jobs/{job_id}/review?item=p1-e1")
    wait_images(page)
    main = page.get_by_role("button", name="Use the first row as headers")
    expect(main).to_be_visible()
    assert main.evaluate("b => b.classList.contains('button')")
    crop = page.locator(".table-compare .table-crop")
    box = crop.bounding_box()
    assert box
    room = page.locator(".table-compare").evaluate(
        "el => { const s = getComputedStyle(el); "
        "return el.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight); }"
    )
    if viewport["width"] <= 640:
        assert box["width"] >= room - 2, (box["width"], room)  # the whole width
    clean(page, axe_source, "table card")
    shoot(page, "table-card", viewport)
    assert not sideways_overflow(page)
    main.click()
    page.wait_for_load_state("networkidle")
    assert "Use the first row as headers" not in page.content()
    context.close()


# --------------------------------------------------------------------------- admin tables


@SIZES
def test_the_admin_tables_scroll_in_a_region_a_keyboard_can_reach(
    site, browser, axe_source, viewport
):
    site.allow(ADMIN, admin=True)
    category = "review:accept_with_an_unbreakable_category_code_that_cannot_wrap"
    with session_scope(site.ctx.factory) as session:
        found = session.query(ErrorAggregate).filter_by(category=category).first()
        if found is None:
            session.add(
                ErrorAggregate(day=date.today(), step="equation", category=category, count=3)
            )
    context = browser.new_context(bypass_csp=True, viewport=viewport)
    page = context.new_page()
    sign_in(page, site.base, ADMIN)
    page.goto(f"{site.base}/admin")
    boxes = page.locator(".table-scroll")
    assert boxes.count() >= 2
    scrolling = page.evaluate(
        "[...document.querySelectorAll('.table-scroll')]"
        ".filter(e => e.scrollWidth > e.clientWidth + 1).length"
    )
    if viewport["width"] <= 400:
        assert scrolling >= 1  # the test would pass for the wrong reason otherwise
    for i in range(boxes.count()):
        box = boxes.nth(i)
        assert box.get_attribute("tabindex") == "0" and box.get_attribute("role") == "region"
        labelled = box.get_attribute("aria-labelledby")
        assert labelled and page.locator(f"#{labelled}").count() == 1
    clean(page, axe_source, "admin")
    results = page.evaluate(
        "async () => await axe.run(document, {runOnly: ['scrollable-region-focusable'], "
        "resultTypes: ['violations']})"
    )
    assert not results["violations"], describe(results["violations"])
    shoot(page, "admin", viewport)
    context.close()
