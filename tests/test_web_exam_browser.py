"""The upload form's "Exam or quiz" box in a real browser (Playwright Chromium): axe-core
(WCAG 2.2 AA and best practice, moderate and above) at 1280 and 375 CSS px, the label
toggles the box, and an upload with it ticked is stored as an exam job.

Run with: uv run pytest -m browser tests/test_web_exam_browser.py
"""

from __future__ import annotations

import pytest

from fixtures.web.review_doc import review_pdf
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_browser import describe
from test_web_review_browser import sideways_overflow
from test_web_review_split_browser import open_page, strict_axe
from test_web_review_split_browser import site as site  # fixture, re-exported for pytest

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

WIDE = {"width": 1280, "height": 900}
PHONE = {"width": 375, "height": 800}
SIZES = pytest.mark.parametrize("viewport", [WIDE, PHONE], ids=["1280", "375"])
LABEL = "Exam or quiz: describe figures without giving answers"


@SIZES
def test_the_upload_form_with_the_exam_box_passes_axe(site, browser, axe_source, viewport):
    context, page = open_page(browser, site, viewport=viewport)
    page.goto(f"{site.base}/app")
    box = page.get_by_label(LABEL)
    expect(box).not_to_be_checked()
    violations = strict_axe(page, axe_source)
    assert not violations, describe(violations)
    box.check()
    violations = strict_axe(page, axe_source)
    assert not violations, f"ticked: {describe(violations)}"
    assert not sideways_overflow(page)
    context.close()


def test_the_box_is_described_by_its_hint_and_toggles_from_the_label(site, browser):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/app")
    box = page.get_by_label(LABEL)
    assert box.get_attribute("aria-describedby") == "exam-hint"
    page.locator("label[for=exam]").click()
    expect(box).to_be_checked()
    page.keyboard.press("Space")  # the box has focus after the label click
    expect(box).not_to_be_checked()
    context.close()


def test_an_upload_with_the_box_ticked_is_an_exam_job(site, browser):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": "quiz.pdf", "mimeType": "application/pdf", "buffer": review_pdf()}]
    )
    page.get_by_label(LABEL).check()
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    assert site.job(job_id).exam_mode is True
    assert site.run_worker() == job_id
    assert site.converter.calls[-1]["options"].exam_mode is True
