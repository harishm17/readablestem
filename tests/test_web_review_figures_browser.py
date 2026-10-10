"""The figure card with a long description and a chart data table, in a real browser
(Playwright Chromium): axe-core with the WCAG 2.2 AA and best-practice rules at 1440 and
390 CSS px (the card as shown, and after a refused edit), no sideways scrolling at 320
CSS px (the table scrolls in its own focusable box), and "Add the data table" then
"Remove the data table" done without JavaScript.

Run with: uv run pytest -m browser tests/test_web_review_figures_browser.py
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from fixtures.web.review_doc import review_pdf
from fixtures.web.support import ALICE, WebEnv
from test_web_browser import TEXT_SPACING, describe, free_port, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review_browser import sideways_overflow, unfold_everything
from test_web_review_figures import described_output
from test_web_review_split_browser import DESKTOP, NARROW, PHONE, open_page, strict_axe

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    """The app on a free port, converting every upload into the review document with a
    described figure. Real validators, assembler and page renderer."""
    import uvicorn

    env = WebEnv(tmp_path_factory.mktemp("figure-browser"), per_user_daily_page_quota=1000)
    env.converter.output = described_output()
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
        files=[{"name": "figures.pdf", "mimeType": "application/pdf", "buffer": review_pdf()}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    assert site.run_worker() == job_id
    return job_id


def figure_states(page, url: str, check) -> None:
    page.goto(f"{url}?item=p1-e6")
    page.wait_for_load_state("networkidle")
    check("figure card")
    page.get_by_label("Longer description for figure 1").fill("<b>Too short</b>")
    page.get_by_role("button", name="Save edit to figure 1").click()
    page.locator(".error-summary").wait_for()
    check("refused description")
    page.goto(f"{url}?item=p1-e6")
    page.get_by_role("button", name="Add the data table to figure 1").click()
    page.get_by_label("Remove the data table (keep the descriptions)").wait_for()
    check("table added")


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440px", "390px"])
def test_the_figure_card_passes_wcag22_aa_and_best_practice(
    site, browser, job, axe_source, viewport
):
    context, page = open_page(browser, site, viewport=viewport)
    found: dict[str, list[dict]] = {}

    def check(state: str) -> None:
        found[state] = strict_axe(page, axe_source)
        unfold_everything(page)
        found[state + " (unfolded)"] = strict_axe(page, axe_source)

    figure_states(page, f"{site.base}/jobs/{job}/review", check)
    context.close()
    assert len(found) == 6
    failures = {state: describe(v) for state, v in found.items() if v}
    assert not failures, "\n".join(f"[{s}]\n{d}" for s, d in failures.items())


@pytest.mark.parametrize("spacing", [False, True], ids=["plain", "text-spacing"])
def test_the_figure_card_does_not_scroll_sideways_at_320_css_px(site, browser, job, spacing):
    context, page = open_page(browser, site, viewport=NARROW)
    widths: dict[str, int] = {}

    def check(state: str) -> None:
        if spacing:
            page.add_style_tag(content=TEXT_SPACING)
        unfold_everything(page)
        widths[state] = sideways_overflow(page)

    figure_states(page, f"{site.base}/jobs/{job}/review", check)
    table = page.get_by_role("group", name="Data read from Figure 1; the values are approximate")
    assert table.get_attribute("tabindex") == "0"  # a keyboard user can scroll it
    context.close()
    assert widths == dict.fromkeys(widths, 0), widths


def test_add_then_remove_the_data_table_without_javascript(site, browser, job):
    context, page = open_page(browser, site, viewport=PHONE, javascript=False)
    url = f"{site.base}/jobs/{job}/review"
    page.goto(f"{url}?item=p1-e6")
    page.get_by_role("button", name="Add the data table to figure 1").click()
    page.wait_for_url(f"{url}?item=p1-e6")
    result = site.job(job).result_json
    fix = next(f for f in result["fixes"] if f["element_id"] == "p1-e6")
    assert fix["figure"]["data_table_added"] is True and fix["status"] == "needs_review"
    page.get_by_label("Remove the data table (keep the descriptions)").check()
    page.get_by_role("button", name="Save edit to figure 1").click()
    page.wait_for_url(f"{url}?item=hg-p1-e3")
    context.close()
    result = site.job(job).result_json
    fix = next(f for f in result["fixes"] if f["element_id"] == "p1-e6")
    assert fix["status"] == "edited"
    assert fix["figure"]["data_table"] is None and fix["figure"]["long_description"]
