"""Pages kept as images in a real browser (Playwright Chromium): the job page, the review
screen and the list view of a course PDF with typed, handwritten and scanned pages
(tests/fixtures/handwriting/mixed_course.pdf, synthetic), converted by the real pipeline
with the offline model client.

- axe-core with the WCAG 2.2 AA and best-practice rules, failing on moderate and above,
  at 1440 and 390 CSS px, before and after the pages are kept;
- no sideways scrolling at 320 CSS px, also with the WCAG 1.4.12 text-spacing override;
- the group decision works without JavaScript and lands on the next decision.

Run with: uv run pytest -m browser tests/test_page_types_browser.py
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from a11y_stem.config import Settings
from a11y_stem.models import OFFLINE_DEFAULTS
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline.run import convert_pdf
from fixtures.web.support import ALICE, WebEnv
from test_web_browser import TEXT_SPACING, WCAG_TAGS, describe, free_port, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review_browser import sideways_overflow, unfold_everything

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

COURSE = Path(__file__).parent / "fixtures" / "handwriting" / "mixed_course.pdf"
DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
NARROW = {"width": 320, "height": 640}
STRICT = {"moderate", "serious", "critical"}


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    """The app on a free port; every upload becomes the real conversion of the course PDF."""
    import uvicorn

    env = WebEnv(tmp_path_factory.mktemp("page-types-browser"), per_user_daily_page_quota=1000)
    env.converter.output = convert_pdf(
        COURSE.read_bytes(),
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        client=FakeVisionClient(defaults=copy.deepcopy(OFFLINE_DEFAULTS)),
    )
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


@pytest.fixture
def job(site, browser) -> str:
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": "course.pdf", "mimeType": "application/pdf", "buffer": COURSE.read_bytes()}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    assert site.run_worker() == job_id
    return job_id


def open_page(browser, site, *, viewport=DESKTOP, javascript: bool = True):
    # bypass_csp only so axe can be injected; test_web_browser.py checks the CSP itself.
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


def walk(page, site, job: str, check) -> None:
    """The job page, the review screen's two page decisions, the list view, and all of
    them again once the pages are kept."""
    review = f"{site.base}/jobs/{job}/review"
    page.goto(f"{site.base}/jobs/{job}")
    check("job page")
    page.goto(review)
    page.wait_for_load_state("networkidle")
    check("decision 1: handwritten pages")
    page.goto(f"{review}/list")
    check("list view")
    page.goto(review)
    page.get_by_role("button", name="Keep the page images, marked “Not converted”").click()
    page.wait_for_url(f"{review}?item=pg-p4-e0")
    page.wait_for_load_state("networkidle")
    check("decision 2: scanned page")
    page.get_by_role("button", name="Keep the page image, marked “Not converted”").click()
    page.wait_for_url(review)
    check("all decided")
    page.goto(f"{review}?item=pg-p2-e0")
    check("a decided page group")
    page.goto(f"{site.base}/jobs/{job}")
    check("job page, decided")


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440px", "390px"])
def test_kept_pages_pass_wcag22_aa_and_best_practice(site, browser, job, axe_source, viewport):
    context, page = open_page(browser, site, viewport=viewport)
    found: dict[str, list[dict]] = {}

    def check(state: str) -> None:
        unfold_everything(page)
        found[state] = strict_axe(page, axe_source)

    walk(page, site, job, check)
    context.close()
    assert len(found) == 7
    failures = {state: describe(v) for state, v in found.items() if v}
    assert not failures, "\n".join(f"[{s}]\n{d}" for s, d in failures.items())


@pytest.mark.parametrize("spacing", [False, True], ids=["plain", "text-spacing"])
def test_kept_pages_reflow_at_320_css_px(site, browser, job, spacing):
    context, page = open_page(browser, site, viewport=NARROW)
    widths: dict[str, int] = {}

    def check(state: str) -> None:
        if spacing:
            page.add_style_tag(content=TEXT_SPACING)
        unfold_everything(page)
        widths[state] = sideways_overflow(page)

    walk(page, site, job, check)
    context.close()
    assert widths == dict.fromkeys(widths, 0), widths


def test_the_page_decision_works_without_javascript(site, browser, job):
    context, page = open_page(browser, site, javascript=False)
    review = f"{site.base}/jobs/{job}/review"
    page.goto(review)
    assert page.get_by_role("heading", name="Decision 1 of 2").is_visible()
    page.get_by_role("button", name="Keep the page images, marked “Not converted”").click()
    page.wait_for_url(f"{review}?item=pg-p4-e0")
    status = page.locator(".decision-status, [role=status]").first.inner_text()
    assert "Kept 2 as images" in status
    page.get_by_role("button", name="Keep the page image, marked “Not converted”").click()
    page.wait_for_url(review)
    assert "All decisions made" in page.content()
    context.close()
