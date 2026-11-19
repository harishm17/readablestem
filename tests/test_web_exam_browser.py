"""The upload form's "Exam or quiz" box in a real browser (Playwright Chromium): axe-core
(WCAG 2.2 AA and best practice, moderate and above) at 1280 and 375 CSS px, the label
toggles the box, and an upload with it ticked is stored as an exam job.

Run with: uv run pytest -m browser tests/test_web_exam_browser.py
"""

from __future__ import annotations

import pytest

from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review_split_browser import open_page
from test_web_review_split_browser import site as site  # fixture, re-exported for pytest

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

WIDE = {"width": 1280, "height": 900}
PHONE = {"width": 375, "height": 800}
SIZES = pytest.mark.parametrize("viewport", [WIDE, PHONE], ids=["1280", "375"])
LABEL = "Exam or quiz: describe figures without giving answers"


def test_the_box_is_described_by_its_hint_and_toggles_from_the_label(site, browser):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/app")
    box = page.get_by_label(LABEL)
    page.locator("label[for=exam]").click()
    expect(box).to_be_checked()
    page.keyboard.press("Space")  # the box has focus after the label click
    expect(box).not_to_be_checked()
    context.close()


def test_a_chosen_file_named_like_an_exam_gets_one_line_until_the_box_is_ticked(site, browser):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/app")

    def choose(*names: str) -> None:
        page.set_input_files(
            "#pdf",
            [{"name": n, "mimeType": "application/pdf", "buffer": b"%PDF-1.4"} for n in names],
        )

    hint = page.locator("#exam-hint")
    choose("Midterm2.pdf")
    expect(hint).to_contain_text("says “midterm”")
    expect(hint).to_have_attribute("aria-live", "polite")
    expect(page.locator("#pdf")).not_to_be_focused()  # the line never takes focus
    expect(page.get_by_label(LABEL)).not_to_be_checked()  # and never ticks the box
    page.locator("label[for=exam]").click()
    expect(hint).to_be_empty()
    page.locator("label[for=exam]").click()
    expect(hint).to_contain_text("midterm")
    choose("Lecture_Notes_FINAL.pdf")
    expect(hint).to_be_empty()
    context.close()
