"""The review screen fixes from the UX review in a real browser (Playwright Chromium), on
the real-shaped document of fixtures.web.review_doc (decision 1 the bold omega read plain,
2 a figure, 3 the "Teacher Support" heading group).

- axe-core (WCAG 2.2 AA and best practice, moderate and above) at 1280 and 320 CSS px in
  each new state: the two readings with the differing symbol marked (BW12), the edit
  checked while typing (MF3), "Use my edit anyway" offered, the notice with Undo, your
  decisions listed apart, a write from an out-of-date tab refused (MF12), Undo done, the
  list view's new sections, the page after the session ended (MF19) and a kept draft put
  back; none of them scrolls sideways at 320 CSS px, also with the text-spacing override;
- the keyboard flow (BW11): the decision in five Tab stops or fewer from the top, focus on
  the next decision after one, Undo one Shift+Tab away and focus back after it;
- the edit checked while typing: debounced, said in the field's live region;
- the stale guard, Undo and "Use my edit anyway" without JavaScript;
- what was typed survives the session ending, sign-in comes back to the decision, and the
  draft is forgotten once saved or on Sign out.

Run with: uv run pytest -m browser tests/test_web_review_ux_browser.py
"""

from __future__ import annotations

import re
import time

import pytest

from a11y_stem.web.db import Job, session_scope
from fixtures.web.review_doc import GROUP
from fixtures.web.support import ALICE
from test_web_browser import TEXT_SPACING, describe, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review_browser import sideways_overflow, unfold_everything
from test_web_review_split_browser import _wait_focus, open_page, strict_axe, wait_images
from test_web_review_split_browser import job as job  # fixture, re-exported for pytest
from test_web_review_split_browser import site as site  # fixture, re-exported for pytest

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

WIDE = {"width": 1280, "height": 800}
NARROW = {"width": 320, "height": 640}
ALT = "A car drives along a road from a house to a school."


def set_speech(site, job: str, element_id: str, speech: str) -> None:
    """Give a stored proposal the reading the pipeline would (the fixture stores its LaTeX
    there), so the card has two readings to compare."""
    with session_scope(site.ctx.factory) as session:
        row = session.get(Job, job)
        assert row is not None and row.result_json is not None
        data = dict(row.result_json)
        data["fixes"] = [
            fix | {"equation": fix["equation"] | {"speech": speech}}
            if fix["element_id"] == element_id
            else fix
            for fix in data["fixes"]
        ]
        row.result_json = data


def sign_out_elsewhere(browser, site) -> None:
    """Sign out on another device: every session of the account ends."""
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.get_by_role("button", name="Sign out").click()
    page.wait_for_url(f"{site.base}/")
    context.close()


def walk(browser, site, job: str, page, check) -> None:
    """Every new state, calling `check(page, name)` in each (JavaScript on)."""
    url = f"{site.base}/jobs/{job}/review"
    set_speech(site, job, "p1-e2", "omega")
    page.goto(url)
    wait_images(page)
    check(page, "readings, the differing symbol marked")
    field = page.get_by_label("LaTeX for equation 1")
    field.fill(r"{\omega}")
    page.locator("#live-check-p1-e2 .live-results").wait_for()
    check(page, "checked while typing")
    page.get_by_role("button", name="Save edit to equation 1").click()
    page.locator(".error-summary").wait_for()
    check(page, "use my edit anyway offered")
    page.get_by_role("button", name="Use my edit anyway for equation 1").click()
    page.wait_for_url(f"{url}?item=p1-e6")
    wait_images(page)
    check(page, "notice with undo")
    page.locator("#decided-summary").click()
    check(page, "your decisions")
    # A second tab drawn now; the first one decides; the second one is out of date.
    other = page.context.new_page()
    other.goto(f"{url}?item=p1-e6")
    page.get_by_role("button", name="Accept figure 1").click()
    page.wait_for_url(f"{url}?item={GROUP}")
    other.get_by_role("button", name="Accept figure 1").click()
    other.locator(".stale-notice").wait_for()
    check(other, "stale write refused")
    other.close()
    page.get_by_role("button", name="Undo this decision").click()
    page.wait_for_url(f"{url}?item=p1-e6")
    check(page, "undone")
    page.goto(f"{url}/list")
    check(page, "list view")
    page.goto(f"{url}?item=p1-e6")
    page.get_by_label("Alt text for figure 1").fill(ALT)
    sign_out_elsewhere(browser, site)
    page.get_by_role("button", name="Save edit to figure 1").click()
    page.get_by_role("heading", name="Please sign in again").wait_for()
    check(page, "session ended")
    page.get_by_role("link", name="Development sign-in").click()
    page.get_by_label("Email address").fill(ALICE)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{url}?item=p1-e6")
    page.locator(".kept-draft").wait_for()
    check(page, "draft put back")


@pytest.mark.parametrize("viewport", [WIDE, NARROW], ids=["1280px", "320px"])
def test_every_new_state_passes_wcag22_aa_and_best_practice(
    site, browser, job, axe_source, viewport
):
    context, page = open_page(browser, site, viewport=viewport)
    found: dict[str, list[dict]] = {}

    def check(p, state: str) -> None:
        found[state] = strict_axe(p, axe_source)
        unfold_everything(p)
        found[state + " (unfolded)"] = strict_axe(p, axe_source)

    walk(browser, site, job, page, check)
    context.close()
    assert len(found) == 20
    failures = {state: describe(v) for state, v in found.items() if v}
    assert not failures, "\n".join(f"[{s}]\n{d}" for s, d in failures.items())


@pytest.mark.parametrize("spacing", [False, True], ids=["plain", "text-spacing"])
def test_no_new_state_scrolls_sideways_at_320_css_px(site, browser, job, spacing):
    context, page = open_page(browser, site, viewport=NARROW)
    widths: dict[str, int] = {}

    def check(p, state: str) -> None:
        if spacing:
            p.add_style_tag(content=TEXT_SPACING)
        unfold_everything(p)
        widths[state] = sideways_overflow(p)

    walk(browser, site, job, page, check)
    context.close()
    assert widths == dict.fromkeys(widths, 0), widths


def _wait_active(page, class_name: str, timeout_s: float = 5.0) -> None:
    """Wait for focus on the element with this class (polled from Python, as
    `_wait_focus`, so it works without JavaScript too)."""
    deadline = time.monotonic() + timeout_s
    script = f"document.activeElement.classList.contains({class_name!r})"
    while not page.evaluate(script):
        if time.monotonic() > deadline:
            raise AssertionError(f"focus is not on .{class_name}: {focused(page)!r}")
        time.sleep(0.05)


def focused(page) -> str:
    return page.evaluate(
        "(() => { const e = document.activeElement; "
        "return (e.id ? '#' + e.id + ' ' : '') + e.textContent.trim().replace(/\\s+/g, ' '); })()"
    )


@pytest.mark.parametrize("javascript", [True, False], ids=["js", "no-js"])
def test_the_keyboard_reaches_the_decision_and_follows_it(site, browser, job, javascript):
    context, page = open_page(browser, site, viewport=WIDE, javascript=javascript)
    url = f"{site.base}/jobs/{job}/review"
    page.goto(url)
    page.keyboard.press("Tab")
    assert focused(page) == "Skip to main content"
    page.keyboard.press("Tab")
    assert focused(page) == "Skip to the decision"
    page.keyboard.press("Enter")
    assert focused(page).startswith("#decision-title Decision 1 of 3")
    tabs = 2
    while not focused(page).startswith("Use bold ω"):
        page.keyboard.press("Tab")
        tabs += 1
        assert tabs <= 5, f"more than 5 Tab stops to the decision (at {focused(page)!r})"
    page.keyboard.press("Enter")
    page.wait_for_url(f"{url}?item=p1-e6")
    _wait_focus(page, "decision-title")
    assert "Equation 1: Your edit passed the required checks" in (
        page.locator("#decision-notice").inner_text()
    )
    assert page.title().startswith("Decision 2 of 3: Review")
    # Undo is just before the heading: one Shift+Tab.
    page.keyboard.press("Shift+Tab")
    assert focused(page) == "Undo this decision"
    page.keyboard.press("Enter")
    page.wait_for_url(f"{url}?item=p1-e2")
    _wait_focus(page, "decision-title")
    assert "Undone. It is waiting for your decision again." in (
        page.locator("#decision-notice").inner_text()
    )
    context.close()


def test_the_edit_is_checked_while_typing_and_said_briefly(site, browser, job):
    context, page = open_page(browser, site, viewport=WIDE)
    checks: list[str] = []
    page.on("request", lambda r: checks.append(r.url) if r.url.endswith("/check") else None)
    page.goto(f"{site.base}/jobs/{job}/review")
    field = page.get_by_label("LaTeX for equation 1")
    status = page.locator("#live-status-p1-e2")
    assert status.get_attribute("role") == "status"
    field.click()
    field.press("End")
    page.keyboard.type(" + 1", delay=40)  # several keys inside the pause: one request
    expect(status).to_have_text(
        "Your LaTeX differs from the PDF where it changes the meaning. "
        "The 2 problems are listed below."
    )
    assert len(checks) == 1
    results = page.locator("#live-check-p1-e2")
    assert "Differs from the PDF:" in results.inner_text()
    field.fill(r"\boldsymbol{\omega}")
    expect(status).to_have_text("Your LaTeX can be saved: it matches the PDF's own text.")
    expect(results.locator(".speech")).to_have_text("Read aloud roughly as: “bold omega”")
    assert "Your edit" in results.inner_text()
    field.fill(r"\frac{")
    expect(status).to_contain_text("Your LaTeX cannot be saved yet.")
    # The field is described by the live line, so its state is also read on focus.
    assert "live-status-p1-e2" in (field.get_attribute("aria-describedby") or "")
    context.close()


def test_stale_undo_and_anyway_work_without_javascript(site, browser, job):
    context, page = open_page(browser, site, javascript=False)
    url = f"{site.base}/jobs/{job}/review"
    page.goto(url)
    other = context.new_page()
    other.goto(url)
    # Out of date: the other tab decides first.
    other.get_by_role("button", name="Use bold ω in equation 1").click()
    page.get_by_role("button", name="Accept anyway equation 1").click()
    notice = page.locator(".stale-notice")
    assert "Your choice was not saved" in notice.inner_text()
    assert "Equation 1 is now: edited by you." in notice.inner_text()
    _wait_active(page, "stale-notice")  # it takes focus, like the error summary
    page.get_by_role("link", name="Reload this page").click()
    assert page.locator("#decision-title").inner_text().startswith("Decision 1 of 3")
    # Undo the other tab's decision from that tab, then use a plain omega anyway here.
    other.get_by_role("button", name="Undo this decision").click()
    other.close()
    page.goto(url)
    page.get_by_label("LaTeX for equation 1").fill(r"{\omega}")
    page.get_by_role("button", name="Save edit to equation 1").click()
    page.get_by_role("button", name="Use my edit anyway for equation 1").click()
    page.wait_for_url(f"{url}?item=p1-e6")
    assert "although it differs from the PDF" in page.locator("#decision-notice").inner_text()
    context.close()
    fix = next(f for f in site.job(job).result_json["fixes"] if f["element_id"] == "p1-e2")
    assert (fix["status"], fix["equation"]["latex"]) == ("edited", r"{\omega}")


def test_typed_text_survives_the_session_ending(site, browser, job):
    context, page = open_page(browser, site)
    url = f"{site.base}/jobs/{job}/review?item=p1-e6"
    page.goto(url)
    page.get_by_label("Alt text for figure 1").fill(ALT)
    sign_out_elsewhere(browser, site)
    page.get_by_role("button", name="Save edit to figure 1").click()
    page.get_by_role("heading", name="Please sign in again").wait_for()
    hint = page.get_by_text("What you typed is kept in this browser tab")
    expect(hint).to_be_visible()
    page.get_by_role("link", name="Development sign-in").click()
    page.get_by_label("Email address").fill(ALICE)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(url)  # back on the same decision
    field = page.get_by_label("Alt text for figure 1")
    assert field.input_value() == ALT
    note = page.locator(".kept-draft")
    assert note.inner_text() == "What you typed before is back here. It is not saved yet."
    assert note.get_attribute("id") in (field.get_attribute("aria-describedby") or "")
    # Saved: the draft is forgotten.
    page.get_by_role("button", name="Save edit to figure 1").click()
    page.wait_for_url(re.compile(rf"item={GROUP}"))
    page.goto(url)
    assert page.locator(".kept-draft").count() == 0
    assert page.get_by_label("Alt text for figure 1").input_value() == ALT
    context.close()


def test_signing_out_in_the_tab_forgets_its_drafts(site, browser, job):
    context, page = open_page(browser, site)
    url = f"{site.base}/jobs/{job}/review?item=p1-e6"
    page.goto(url)
    page.get_by_label("Alt text for figure 1").fill(ALT)
    stored = page.evaluate("Object.keys(sessionStorage).filter(k => k.includes('draft'))")
    assert stored == [f"a11y-stem.draft:/jobs/{job}/review/p1-e6"]
    page.get_by_role("button", name="Sign out").click()
    page.wait_for_url(f"{site.base}/")
    assert page.evaluate("Object.keys(sessionStorage).filter(k => k.includes('draft'))") == []
    sign_in(page, site.base, ALICE)
    page.goto(url)
    assert page.locator(".kept-draft").count() == 0
    assert page.get_by_label("Alt text for figure 1").input_value() != ALT
    context.close()


def test_a_draft_comes_back_in_the_list_view_too(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}/review/list")
    page.get_by_label("Alt text for figure 1").fill(ALT)
    page.reload()
    started = time.monotonic()
    page.locator(".kept-draft").wait_for()
    assert time.monotonic() - started < 5
    assert page.get_by_label("Alt text for figure 1").input_value() == ALT
    context.close()
