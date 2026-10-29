"""The pages outside the review screen and the job page, in a real browser (Playwright
Chromium): axe-core at 1280 and 320 CSS px, reflow with text spacing, the pages as a
browser without JavaScript sees them, and what app.js adds (local times, the upload
check, the questions after a download, the documents list refreshing in place).

Run with: uv run pytest -m browser tests/test_web_ux_browser.py
The app, the browser and axe-core come from test_web_browser.py's fixtures.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

import test_web_browser as shared
from fixtures.web.support import ADMIN, ALICE, BOB, fake_output, make_pdf
from readablestem.core.types import Status
from readablestem.web.database import utc_today
from readablestem.web.db import Job, QuotaDay, User, session_scope
from test_web_browser import (
    STRICT_IMPACTS,
    TEXT_SPACING,
    WCAG_TAGS,
    axe_violations,
    describe,
    no_js_violations,
    sign_in,
    upload_pdf,
)

# The app on a free port, Chromium and axe-core: test_web_browser.py's module fixtures
# (an app of this module's own).
site = shared.site
browser = shared.browser
axe_source = shared.axe_source

pytestmark = pytest.mark.browser

LONG_TITLE = "Physics_Chapter_02_Kinematics_and_Dynamics_Lecture_Notes_Final_Version_3_Revised"


def user_id(site, email: str) -> str:
    with session_scope(site.ctx.factory) as session:
        found = session.query(User.id).filter(User.email == email).scalar()
        assert found is not None
        return found


@pytest.fixture(scope="module")
def documents(site, browser) -> dict[str, str]:
    """Alice's documents in every group of the list: one needing decisions (with a long
    title), one ready, one converting (a row the worker leaves alone: its lease runs for
    a day), one deleted after its 24 hours."""
    site.allow(BOB)
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    out: dict[str, str] = {}

    output = fake_output()
    output.result.title = LONG_TITLE
    site.converter.output = output
    out["decide"] = upload_pdf(page, site.base)
    assert site.run_worker() == out["decide"]

    output = fake_output()
    output.result.title = "Problem Set 2: Kinematics"
    for fix in output.result.fixes:
        if fix.pending:
            fix.status, fix.reviewed = Status.REJECTED, True
    site.converter.output = output
    out["ready"] = upload_pdf(page, site.base)
    assert site.run_worker() == out["ready"]
    site.converter.output = fake_output()

    out["gone"] = upload_pdf(page, site.base)
    assert site.run_worker() == out["gone"]
    now = datetime.now(UTC)
    with session_scope(site.ctx.factory) as session:
        gone = session.get(Job, out["gone"])
        assert gone is not None
        gone.created_at, gone.retain_until = now - timedelta(hours=25), now - timedelta(hours=1)
        running = Job(
            user_id=user_id(site, ALICE),
            status="running",
            filename="Kinematics slides.pdf",
            page_count=3,
            pages_done=1,
            retain_until=now + timedelta(hours=24),
            lease_until=now + timedelta(days=1),
            created_at=now,
        )
        session.add(running)
        session.flush()
        out["running"] = running.id
    context.close()
    return out


def strict_violations(page, axe_source: str) -> list[dict]:
    """Every rule at serious and above, then WCAG 2.2 AA and best practice at moderate and
    above (as test_web_browser.py does for the other pages)."""
    found = axe_violations(page, axe_source)
    results = page.evaluate(
        "async (tags) => await axe.run(document, "
        "{runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']})",
        WCAG_TAGS,
    )
    return found + [v for v in results["violations"] if v["impact"] in STRICT_IMPACTS]


PAGES = {
    "/app": ALICE,
    "/help/posting": None,
    "/guides/ada-title-ii-stem-course-materials": None,
    "/guides/readablestem-mathpix-latexml": None,
    "/jobs/{decide}/feedback": ALICE,
    "/jobs/{ready}": ALICE,
    "/jobs/{decide}/delete?from=app": ALICE,
    "/jobs/{gone}": ALICE,
    "/admin": ADMIN,
    "/": None,
    "/limits": None,
    "/privacy": None,
    "/status": None,
}


def open_page(browser, site, documents, path: str, *, width: int = 1280, **options):
    context = browser.new_context(
        viewport={"width": width, "height": 800 if width > 400 else 640}, **options
    )
    page = context.new_page()
    who = PAGES.get(path)
    if who:
        sign_in(page, site.base, who)
    page.goto(site.base + path.format(**documents))
    return context, page


@pytest.mark.parametrize("width", [1280, 320])
@pytest.mark.parametrize("path", list(PAGES))
def test_pages_pass_axe_at_desktop_and_phone_widths(
    site, browser, documents, axe_source, path, width
):
    context, page = open_page(browser, site, documents, path, width=width, bypass_csp=True)
    violations = strict_violations(page, axe_source)
    context.close()
    assert not violations, describe(violations)


@pytest.mark.parametrize("spacing", [False, True], ids=["plain", "text-spacing"])
@pytest.mark.parametrize("path", list(PAGES))
def test_pages_reflow_at_320_css_pixels(site, browser, documents, path, spacing):
    """WCAG 1.4.10: nothing scrolls sideways at 320 CSS px, with a long title in the list
    and with the 1.4.12 text-spacing override. Wide admin tables scroll in their own
    focusable box, not the page."""
    context, page = open_page(browser, site, documents, path, width=320, bypass_csp=True)
    if spacing:
        page.add_style_tag(content=TEXT_SPACING)
    width = page.evaluate(
        "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    context.close()
    assert width[0] <= width[1], f"{path}: {width[0]} px wide in a {width[1]} px view"


def test_on_a_phone_each_document_is_a_block_and_no_badge_is_cut(site, browser, documents):
    context, page = open_page(browser, site, documents, "/app", width=390)
    row = page.locator(f"#doc-{documents['decide']}").locator("xpath=ancestor::tr")
    assert row.evaluate("el => getComputedStyle(el).display") == "block"
    badge = row.locator(".status-badge")
    assert badge.evaluate("el => getComputedStyle(el).borderTopLeftRadius") == "6.4px"
    label = row.locator("td[data-label='Status']")
    assert label.evaluate("el => getComputedStyle(el, '::before').content").startswith('"Status')
    context.close()


@pytest.mark.parametrize(
    "path",
    [
        "/app",
        "/jobs/{decide}/feedback",
        "/help/posting",
        "/guides/ada-title-ii-stem-course-materials",
        "/guides/readablestem-mathpix-latexml",
    ],
)
def test_pages_without_javascript_have_no_serious_axe_violations(
    site, browser, documents, axe_source, path
):
    violations = no_js_violations(browser, site, path.format(**documents), axe_source)
    assert not violations, describe(violations)


# --------------------------------------------------------------------------- times


def test_times_show_in_the_readers_time_zone_and_say_utc_without_javascript(
    site, browser, documents
):
    context, page = open_page(
        browser, site, documents, "/app", timezone_id="America/Chicago", locale="en-US"
    )
    deleted = page.locator(f"#doc-{documents['decide']}").locator("xpath=ancestor::tr")
    shown = deleted.locator("td[data-label='Deleted automatically'] time").inner_text()
    context.close()
    assert "UTC" not in shown and re.search(r"\b(CDT|CST)\b", shown), shown
    assert re.search(r"\(in (2[0-4]|1\d) hours\)", shown), shown

    context, page = open_page(browser, site, documents, "/app", java_script_enabled=False)
    plain = page.locator("time.local-time").first.inner_text()
    context.close()
    assert plain.endswith("UTC"), plain


# --------------------------------------------------------------------------- upload


def test_the_upload_form_refuses_a_wrong_file_before_sending_it(site, browser):
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    chooser = page.get_by_label("PDF file")
    chooser.set_input_files(
        files=[{"name": "slides.pptx", "mimeType": "application/zip", "buffer": b"PK.."}]
    )
    check = page.locator("#pdf-check")
    assert "slides.pptx is not a PDF." in check.inner_text()
    assert chooser.get_attribute("aria-invalid") == "true"
    assert "pdf-check-error" in (chooser.get_attribute("aria-describedby") or "")
    page.get_by_role("button", name="Upload and convert").click()
    assert page.url == f"{site.base}/app"  # nothing was sent
    assert page.evaluate("document.activeElement.id") == "pdf"

    big = make_pdf(1) + b"%" + b"x" * (1024 * 1024)  # max_upload_mb=1 in tests
    chooser.set_input_files(
        files=[{"name": "big.pdf", "mimeType": "application/pdf", "buffer": big}]
    )
    assert "big.pdf is larger than 1 MB." in check.inner_text()

    chooser.set_input_files(
        files=[{"name": "notes.pdf", "mimeType": "application/pdf", "buffer": make_pdf(1)}]
    )
    assert check.inner_text() == "" and chooser.get_attribute("aria-invalid") is None
    # Hold the form back to read what it says while it sends.
    page.evaluate("document.addEventListener('submit', e => e.preventDefault())")
    page.get_by_role("button", name="Upload and convert").click()
    assert page.locator("#upload-status").inner_text() == (
        "Uploading notes.pdf. Keep this tab open."
    )
    button = page.get_by_role("button", name="Upload and convert")
    assert button.get_attribute("aria-disabled") == "true"
    context.close()


@pytest.mark.parametrize("width", [1280, 320])
def test_a_used_up_page_limit_says_when_uploads_open_again(site, browser, axe_source, width):
    """UX review MF20: with no page left today, a notice replaces a form that could only
    fail. Carol, so no other test's uploads are refused."""
    carol = "carol@example.org"
    site.allow(carol)
    context = browser.new_context(viewport={"width": width, "height": 800}, bypass_csp=True)
    page = context.new_page()
    sign_in(page, site.base, carol)
    with session_scope(site.ctx.factory) as session:
        session.merge(QuotaDay(user_id=user_id(site, carol), day=utc_today(), pages=20))
    page.goto(f"{site.base}/app")
    notice = page.locator("#upload-closed")
    assert "You have converted all 20 pages you can today." in notice.inner_text()
    assert "Uploads open again at" in notice.inner_text()
    assert page.locator("#pdf").count() == 0
    violations = strict_violations(page, axe_source)
    context.close()
    assert not violations, describe(violations)


# --------------------------------------------------------------------------- feedback


def test_the_questions_appear_after_a_download_and_save(site, browser):
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    job_id = upload_pdf(page, site.base)
    assert site.run_worker() == job_id
    page.goto(f"{site.base}/jobs/{job_id}")
    questions = page.locator("#feedback")
    assert questions.is_hidden()
    # Decisions are open, so the job page's download asks first (/draft, job-page work);
    # once a download is recorded the questions show on the job page.
    page.get_by_role("link", name=re.compile(r"^Download a draft")).click()
    with page.expect_download() as download:
        page.get_by_role("link", name="Download the draft anyway").click()
    assert download.value.suggested_filename == f"secret-heading-kinematics-{job_id[:6]}.html"
    page.goto(f"{site.base}/jobs/{job_id}")
    assert questions.is_visible()
    page.get_by_label("Not sure").check()
    page.get_by_label("About how many minutes did this save you?").fill("lots")
    page.get_by_role("button", name="Send answers").click()
    # An error is explained on the questions' own page, with what was typed kept.
    assert page.locator(".error-summary").is_visible()
    assert page.get_by_label("Not sure").is_checked()
    page.get_by_label("About how many minutes did this save you?").fill("40")
    page.get_by_role("button", name="Send answers").click()
    page.wait_for_url(f"{site.base}/jobs/{job_id}")
    assert "Thank you. Your answers were saved." in page.locator("main").inner_text()
    assert page.locator("#feedback").count() == 0
    context.close()


def test_the_questions_work_without_javascript(site, browser):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    job_id = upload_pdf(page, site.base)
    assert site.run_worker() == job_id
    page.goto(f"{site.base}/jobs/{job_id}/feedback")
    page.get_by_label("Yes").check()
    page.get_by_role("button", name="Send answers").click()
    assert "Thank you" in page.locator("main").inner_text()
    context.close()


# --------------------------------------------------------------------------- the list


def test_the_list_refreshes_in_place_and_keeps_focus(site, browser):
    """While a document converts, htmx swaps the list when something changed; the link
    that had focus has it again afterwards, and the tab title follows the count."""
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, BOB)
    job_id = upload_pdf(page, site.base)
    page.goto(f"{site.base}/app")
    assert page.title() == "My documents (0 of 1 finished) | ReadableSTEM"
    delete = page.locator(f"#del-{job_id}")
    delete.focus()
    assert site.run_worker() == job_id
    page.wait_for_function("() => document.title === 'My documents | ReadableSTEM'", timeout=15000)
    assert "decisions to make" in page.locator("#documents").inner_text()
    assert page.evaluate("document.activeElement.id") == f"del-{job_id}"
    assert page.locator("#documents[hx-get]").count() == 0  # nothing left to wait for
    context.close()
