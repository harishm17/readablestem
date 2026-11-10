"""The choices for a doubtful symbol in a real browser (Playwright Chromium).

- axe-core (WCAG 2.2 AA and best practice, target size included) on the card with choices, at
  1440 and 390 CSS px;
- the choices are reached and pressed from the keyboard alone, in the page's tab order (before
  the edit box), and nothing is selected before one is pressed;
- every choice is at least 44 x 44 CSS px, also at 320 CSS px, with no sideways scrolling;
- pressing one saves it as an edit and the notice that follows is a status region (announced);
- forced-colors and reduced-motion: the buttons keep a visible border and have no transition.

Run with: uv run pytest -m browser tests/test_web_review_chips_browser.py
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
from test_web_review_browser import sideways_overflow
from test_web_review_chips import ID, handwritten_output
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

    env = WebEnv(tmp_path_factory.mktemp("chips-browser"), per_user_daily_page_quota=1000)
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
    page.locator("#pdf").set_input_files(
        files=[{"name": LONG_NAME, "mimeType": "application/pdf", "buffer": review_pdf()}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    assert site.run_worker() == job_id
    return job_id


def open_card(page, site, job: str) -> None:
    page.goto(f"{site.base}/jobs/{job}/review?item={ID}")
    wait_images(page)
    page.locator(".chips").wait_for()


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440px", "390px"])
def test_the_card_with_choices_passes_axe(site, browser, job, axe_source, viewport):
    context, page = open_page(browser, site, viewport=viewport)
    open_card(page, site, job)
    violations = strict_axe(page, axe_source)
    context.close()
    assert not violations, describe(violations)


def test_the_choices_are_operated_from_the_keyboard_and_nothing_is_preselected(site, browser, job):
    context, page = open_page(browser, site, viewport=DESKTOP)
    open_card(page, site, job)
    chip = page.get_by_role("button", name="Use small x in equation 3")
    assert chip.count() == 1
    assert chip.get_attribute("aria-pressed") is None
    # Tab order: the choice comes before the edit box and the decision buttons.
    order = page.evaluate(
        """() => [...document.querySelectorAll(
            '#decision button, #decision summary, #decision textarea')]
            .map(e => e.dataset.role || e.tagName.toLowerCase())"""
    )
    assert order.index("chip") < order.index("save") < order.index("reject")
    # It is the group's only control, named by what it means, and described by how it would
    # be read aloud.
    described = chip.get_attribute("aria-describedby")
    assert "read aloud roughly as" in page.locator(f"#{described}").inner_text().lower()
    group = page.get_by_role("group", name="Where the proposal above has")
    assert group.count() == 1
    # Keyboard only: focus it by Tab from the heading, press Enter.
    page.locator("#decision-title").focus()
    for _ in range(40):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement?.dataset?.role") == "chip":
            break
    else:
        pytest.fail("the choice was never reached with Tab")
    assert page.evaluate("document.activeElement.textContent").strip().startswith("Use small x")
    page.keyboard.press("Enter")
    page.wait_for_load_state("load")
    # The notice after a decision is a status region (announced), and the choice was saved.
    notice = page.locator('#decision-notice[role="status"]')
    notice.wait_for()
    assert "was saved" in notice.inner_text()
    assert page.get_by_role("button", name="Undo").count() == 1  # and it can be taken back
    context.close()
    # (Space works the same: it is a button)


def test_pressing_space_also_chooses(site, browser, job):
    context, page = open_page(browser, site, viewport=DESKTOP)
    open_card(page, site, job)
    page.get_by_role("button", name="Use small x in equation 3").focus()
    page.keyboard.press("Space")
    page.locator('#decision-notice[role="status"]').wait_for()
    context.close()


@pytest.mark.parametrize("viewport", [DESKTOP, NARROW], ids=["1440px", "320px"])
def test_every_choice_is_a_44px_target_and_the_card_does_not_scroll_sideways(
    site, browser, job, viewport
):
    context, page = open_page(browser, site, viewport=viewport)
    open_card(page, site, job)
    for box in (b.bounding_box() for b in page.locator("button[data-role=chip]").all()):
        assert box is not None and box["width"] >= 44 and box["height"] >= 44, box
    assert sideways_overflow(page) == 0
    context.close()


def test_forced_colors_keep_a_visible_border_and_reduced_motion_removes_transitions(
    site, browser, job
):
    context, page = open_page(browser, site, viewport=DESKTOP, forced_colors="active")
    open_card(page, site, job)
    border = page.evaluate(
        "getComputedStyle(document.querySelector('button[data-role=chip]')).borderTopWidth"
    )
    assert float(border.removesuffix("px")) >= 2
    context.close()
    context, page = open_page(browser, site, viewport=DESKTOP, reduced_motion="reduce")
    open_card(page, site, job)
    duration = page.evaluate(
        "getComputedStyle(document.querySelector('button[data-role=chip]')).transitionDuration"
    )
    assert duration in ("0s", "0s, 0s")
    context.close()
