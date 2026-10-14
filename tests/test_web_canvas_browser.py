"""The Canvas download on the job page, in a real browser (Playwright Chromium): reached and
used by keyboard, a field with a name, a number that is not digits stopped before it is
sent, the zip it downloads, and the same without JavaScript. (axe-core on this page, at
1280 and 320 CSS px, is run by test_web_ux_browser.py: `/jobs/{ready}` is one of its pages.)

Run with: uv run pytest -m browser tests/test_web_canvas_browser.py
"""

from __future__ import annotations

import io
import zipfile

import pytest

import test_web_browser as shared
from fixtures.web.support import ALICE, fake_output
from readablestem.core.types import Status
from test_web_browser import sign_in, upload_pdf

site = shared.site
browser = shared.browser

pytestmark = pytest.mark.browser

BUTTON = "For Canvas (zip with images)"


def ready_job(site, page) -> str:
    """A finished job with nothing left to decide, so the page shows the finish line."""
    output = fake_output()
    for fix in output.result.fixes:
        if fix.pending:
            fix.status, fix.reviewed = Status.REJECTED, True
    site.converter.output = output
    job_id = upload_pdf(page, site.base)
    assert site.run_worker() == job_id
    page.goto(f"{site.base}/jobs/{job_id}")
    assert page.locator("#ready-heading").is_visible()
    return job_id


def names_in(download) -> list[str]:
    with open(download.path(), "rb") as handle:
        return zipfile.ZipFile(io.BytesIO(handle.read())).namelist()


def test_the_form_is_named_and_works_from_the_keyboard(site, browser):
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    job_id = ready_job(site, page)

    field = page.get_by_label("Canvas course number (optional)")
    assert field.get_attribute("inputmode") == "numeric"
    button = page.get_by_role("button", name=BUTTON)
    assert button.get_attribute("aria-describedby") == "canvas-help"
    assert "keeps each picture as its own file" in page.locator("#canvas-help").inner_text()

    # Tab order: the form follows the two links of the download area, field before button.
    page.get_by_role("link", name="Preview the student version").focus()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "canvas-course"
    page.keyboard.type("48213")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.textContent.trim()") == BUTTON
    with page.expect_download() as download:
        page.keyboard.press("Enter")
    assert download.value.suggested_filename == f"canvas-secret-heading-kinematics-{job_id[:6]}.zip"
    assert names_in(download.value) == ["index.html", "canvas-page.html", "README.txt"]
    context.close()


def test_enter_in_the_field_sends_it_and_an_empty_field_still_downloads(site, browser):
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    ready_job(site, page)
    page.get_by_label("Canvas course number (optional)").fill("7")
    with page.expect_download() as download:
        page.keyboard.press("Enter")
    assert "canvas-page.html" in names_in(download.value)

    page.get_by_label("Canvas course number (optional)").fill("")
    with page.expect_download() as download:
        page.get_by_role("button", name=BUTTON).click()
    assert names_in(download.value) == ["index.html", "README.txt"]
    context.close()


def test_a_number_that_is_not_digits_is_stopped_before_it_is_sent(site, browser):
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    job_id = ready_job(site, page)
    page.get_by_label("Canvas course number (optional)").fill("12a")
    page.get_by_role("button", name=BUTTON).click()
    assert page.url == f"{site.base}/jobs/{job_id}"  # the browser did not send it
    assert page.evaluate("document.getElementById('canvas-course').validity.patternMismatch")
    context.close()


def test_the_questions_after_a_download_show_after_the_canvas_download_too(site, browser):
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    ready_job(site, page)
    questions = page.locator("#feedback")
    if questions.count() == 0:
        pytest.skip("the two questions are not offered for this job")
    assert questions.is_hidden()
    with page.expect_download():
        page.get_by_role("button", name=BUTTON).click()
    assert questions.is_visible()
    context.close()


def test_without_javascript_the_form_downloads_the_zip(site, browser):
    context = browser.new_context(java_script_enabled=False, accept_downloads=True)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    ready_job(site, page)
    page.get_by_label("Canvas course number (optional)").fill("48213")
    with page.expect_download() as download:
        page.get_by_role("button", name=BUTTON).click()
    assert names_in(download.value) == ["index.html", "canvas-page.html", "README.txt"]
    context.close()
