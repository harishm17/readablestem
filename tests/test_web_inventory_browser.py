"""The Symbols screen in a real browser (Playwright Chromium).

- axe-core (WCAG 2.2 AA and best practice, target size included) on the screen with every
  list of places folded and unfolded, at 1440 and 390 CSS px, and on the screen after a change;
- a symbol is changed from the keyboard alone: its choice, the list of places, the tick that
  leaves one out, the button, and then Undo;
- every control is a target of at least 44 x 44 CSS px, also at 320 CSS px, with no sideways
  scrolling;
- forced-colors: each symbol's form and its button keep a visible border.

Run with: uv run pytest -m browser tests/test_web_inventory_browser.py
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from fixtures.web.review_doc import review_pdf
from fixtures.web.support import ALICE, WebEnv
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_browser import describe, free_port, sign_in
from test_web_inventory import handwritten_output
from test_web_review_browser import sideways_overflow
from test_web_review_split_browser import (
    DESKTOP,
    LONG_NAME,
    NARROW,
    PHONE,
    open_page,
    strict_axe,
    wait_images,
)

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    import uvicorn

    env = WebEnv(tmp_path_factory.mktemp("symbols-browser"), per_user_daily_page_quota=1000)
    env.converter.output = handwritten_output()
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
        files=[{"name": LONG_NAME, "mimeType": "application/pdf", "buffer": review_pdf()}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    assert site.run_worker() == job_id
    return job_id


def open_screen(page, site, job: str, *, unfold: bool = False) -> None:
    page.goto(f"{site.base}/jobs/{job}/symbols?view=split")
    page.locator(".symbol-form").first.wait_for()
    if unfold:
        page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    wait_images(page)


@pytest.mark.parametrize("unfold", [False, True], ids=["folded", "unfolded"])
@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440px", "390px"])
def test_the_screen_passes_axe(site, browser, job, axe_source, viewport, unfold):
    context, page = open_page(browser, site, viewport=viewport)
    open_screen(page, site, job, unfold=unfold)
    violations = strict_axe(page, axe_source)
    context.close()
    assert not violations, describe(violations)


def tab_to(page, matches: str, limit: int = 80) -> None:
    """Press Tab until the focused element matches the selector."""
    for _ in range(limit):
        page.keyboard.press("Tab")
        if page.evaluate("(sel) => !!document.activeElement?.matches(sel)", matches):
            return
    pytest.fail(f"Tab never reached {matches}")


def test_a_symbol_is_changed_from_the_keyboard_alone_and_undone(site, browser, job, axe_source):
    context, page = open_page(browser, site, viewport=DESKTOP)
    open_screen(page, site, job)
    page.locator("h1").focus()
    # The choice for small x: nothing is chosen before the person chooses.
    tab_to(page, "#to-sym-x")
    assert page.input_value("#to-sym-x") == ""
    page.keyboard.press("ArrowDown")  # the first symbol of the family: capital X
    assert page.input_value("#to-sym-x") == "X"
    # The places of x, opened with Enter on their summary, and the first one left out.
    tab_to(page, "summary")
    page.keyboard.press("Enter")
    tab_to(page, "input[name=exclude]")
    page.keyboard.press("Space")
    assert page.evaluate("document.activeElement.checked") is True
    tab_to(page, "button[data-role=relabel]")
    page.keyboard.press("Enter")
    page.wait_for_url("**/symbols?view=split")
    notice = page.locator(".symbols-notice [role=status]")
    notice.wait_for()
    assert "Changed small x to capital X in 1 equation." in notice.inner_text()
    # The one place left out is still a small x; the other two are now capital X.
    page.locator("#sym-x-title").scroll_into_view_if_needed()
    assert "Read 1 time, in 1 equation." in page.locator("#sym-x-title + p").inner_text()
    assert strict_axe(page, axe_source) == []
    # Undo is the next thing in the notice, reached from the keyboard.
    page.locator("h1").focus()
    tab_to(page, "button[data-role=undo]")
    page.keyboard.press("Enter")
    page.wait_for_url("**/symbols?view=split")
    page.locator(".symbols-notice [role=status]").wait_for()
    assert "Undone." in page.locator(".symbols-notice [role=status]").inner_text()
    assert "Read 3 times, in 2 equations." in page.locator("#sym-x-title + p").inner_text()
    context.close()


@pytest.mark.parametrize("viewport", [DESKTOP, NARROW], ids=["1440px", "320px"])
def test_every_control_is_a_44px_target_and_nothing_scrolls_sideways(site, browser, job, viewport):
    context, page = open_page(browser, site, viewport=viewport)
    open_screen(page, site, job, unfold=True)
    selectors = [
        "select",
        "main summary",
        "button[data-role=relabel]",
        ".place-keep",
        ".place-where a",
    ]
    for selector in selectors:
        found = page.locator(selector).all()
        assert found, selector
        for box in (item.bounding_box() for item in found):
            assert box is not None and box["width"] >= 44 and box["height"] >= 44, (selector, box)
    # the tick itself is a target of its own: at least 24 CSS px (WCAG 2.2, 2.5.8)
    for box in (c.bounding_box() for c in page.locator("input[name=exclude]").all()):
        assert box is not None and box["width"] >= 24 and box["height"] >= 24, box
    assert sideways_overflow(page) == 0
    context.close()


def test_forced_colors_keep_the_forms_and_buttons_visible(site, browser, job):
    context, page = open_page(browser, site, viewport=DESKTOP, forced_colors="active")
    open_screen(page, site, job)
    for selector in (".symbol-form", "button[data-role=relabel]"):
        width = page.evaluate(
            "(sel) => getComputedStyle(document.querySelector(sel)).borderTopWidth", selector
        )
        assert float(width.removesuffix("px")) >= 1, (selector, width)
    underline = page.evaluate(
        "() => (document.querySelector('.symbol-form details').open = true,"
        " getComputedStyle(document.querySelector('mark.said-diff')).textDecorationLine)"
    )
    assert "underline" in underline  # the marked symbol does not rely on its colour
    context.close()


def test_the_count_beside_change_all_follows_the_places_left_out(site, browser, job):
    context, page = open_page(browser, site, viewport=DESKTOP)
    open_screen(page, site, job, unfold=True)
    form = page.locator("form.symbol-form", has=page.locator(".symbol-affect")).first
    line = form.locator(".symbol-affect")
    before = int(line.locator("[data-affect-count]").inner_text())
    left_before = int(line.locator("[data-affect-left]").inner_text())
    assert before >= 1 and "left as they are" in line.inner_text()
    form.locator("input[name=exclude]").first.check()
    assert int(line.locator("[data-affect-count]").inner_text()) == before - 1
    assert int(line.locator("[data-affect-left]").inner_text()) == left_before + 1
    context.close()
