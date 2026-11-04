"""Browser checks for the sharing page, the comparison guide on a phone, the contents of the long
guides and the status page: axe (WCAG 2.2 AA tags, light and dark), the size of the role rows,
the keyboard, Copy link, and no sideways scroll at 320 px."""

from __future__ import annotations

import pytest

from test_web_browser import (
    ALICE,
    axe_violations,
    browser,
    describe,
    finished_job,
    sign_in,
    site,
)
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest

pytestmark = pytest.mark.browser
_ = (browser, finished_job, site)

PHONE = {"width": 390, "height": 844}
SMALL = {"width": 320, "height": 640}
WIDE = {"width": 1280, "height": 900}


def open_as(browser, site, path: str, *, scheme="light", viewport=WIDE, signed_in=True, **opts):
    context = browser.new_context(bypass_csp=True, color_scheme=scheme, viewport=viewport, **opts)
    page = context.new_page()
    if signed_in:
        sign_in(page, site.base, ALICE)
    response = page.goto(site.base + path)
    assert response is not None and response.status == 200
    return context, page


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("viewport", [WIDE, PHONE], ids=["wide", "phone"])
def test_the_share_page_passes_axe(site, browser, finished_job, axe_source, scheme, viewport):
    context, page = open_as(
        browser, site, f"/jobs/{finished_job}/share", scheme=scheme, viewport=viewport
    )
    page.get_by_role("button", name="Copy link").wait_for()  # script has shown it
    violations = axe_violations(page, axe_source)
    context.close()
    assert not violations, describe(violations)


def test_each_role_row_is_a_whole_row_target_of_at_least_44_px(site, browser, finished_job):
    context, page = open_as(browser, site, f"/jobs/{finished_job}/share", viewport=PHONE)
    for role in ("view", "review"):
        row = page.locator(f'label.role-row[for="share-role-{role}"]')
        box = row.bounding_box()
        assert box and box["height"] >= 44 and box["width"] >= 300
        # A click on the description (not on the radio) selects the role.
        row.locator(".role-help").click()
        assert page.locator(f"#share-role-{role}").is_checked()
    # Name and description are announced by the radio itself.
    radio = page.locator("#share-role-review")
    assert radio.evaluate("e => e.labels[0].textContent").count("Review") >= 1
    context.close()


def test_the_role_radios_work_from_the_keyboard(site, browser, finished_job):
    context, page = open_as(browser, site, f"/jobs/{finished_job}/share")
    page.locator("#share-email").focus()
    page.keyboard.press("Tab")  # into the radio group: the checked one (View)
    assert page.evaluate("document.activeElement.id") == "share-role-view"
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.id") == "share-role-review"
    assert page.locator("#share-role-review").is_checked()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.textContent.trim()") == "Share"
    context.close()


def test_copy_link_confirms_in_a_status_line_and_leaves_the_address_selectable(
    site, browser, finished_job
):
    context, page = open_as(
        browser,
        site,
        f"/jobs/{finished_job}/share",
        permissions=["clipboard-read", "clipboard-write"],
    )
    button = page.get_by_role("button", name="Copy link")
    button.focus()
    page.keyboard.press("Enter")
    status = page.locator("#share-copy-state")
    page.wait_for_function("document.getElementById('share-copy-state').textContent !== ''")
    assert status.inner_text() == "Link copied."
    assert status.get_attribute("role") == "status"
    assert (
        page.evaluate("navigator.clipboard.readText()")
        == page.locator("#share-address").input_value()
    )
    assert page.evaluate("document.activeElement === document.querySelector('[data-copy-target]')")
    context.close()


def test_without_script_the_address_is_a_plain_selectable_field(site, browser, finished_job):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    page.goto(site.base + "/dev-login")
    page.get_by_label("Email address").fill(ALICE)
    page.get_by_role("button", name="Sign in").click()
    page.goto(f"{site.base}/jobs/{finished_job}/share")
    assert page.locator("#share-address").is_visible()
    assert not page.get_by_role("button", name="Copy link").is_visible()  # hidden until script
    context.close()


def test_the_share_page_rows_hold_in_forced_colours(site, browser, finished_job):
    context, page = open_as(
        browser, site, f"/jobs/{finished_job}/share", forced_colors="active", viewport=PHONE
    )
    checked = page.locator("label.role-row:has(#share-role-view)")
    outline = checked.evaluate("e => getComputedStyle(e).outlineStyle")
    border = checked.evaluate("e => getComputedStyle(e).borderTopStyle")
    assert outline == "solid" and border == "solid"
    context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_comparison_guide_stacks_on_a_phone_and_keeps_its_table_wide(
    site, browser, axe_source, scheme
):
    path = "/guides/readablestem-mathpix-latexml"
    context, page = open_as(browser, site, path, scheme=scheme, viewport=PHONE, signed_in=False)
    assert not page.locator("table.compare-table").is_visible()
    assert page.locator(".compare-stack").is_visible()
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0
    violations = axe_violations(page, axe_source)
    assert not violations, describe(violations)
    page.set_viewport_size(WIDE)
    assert page.locator("table.compare-table").is_visible()
    assert not page.locator(".compare-stack").is_visible()
    violations = axe_violations(page, axe_source)
    context.close()
    assert not violations, describe(violations)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_phone_comparison_topics_are_keyboard_disclosures(site, browser, axe_source, scheme):
    path = "/guides/readablestem-mathpix-latexml"
    context, page = open_as(browser, site, path, scheme=scheme, viewport=PHONE, signed_in=False)
    topics = page.locator("details.compare-topic")
    assert topics.count() == 9
    assert page.locator("details.compare-topic[open]").count() == 0  # collapsed by default
    first = topics.first
    summary = first.locator("summary")
    box = summary.bounding_box()
    assert box and box["height"] >= 44
    # Closed: the topic and ReadableSTEM's answer show; the other tools do not.
    assert "Takes in" in summary.inner_text() and "STEM course PDF" in summary.inner_text()
    assert "Source" not in summary.inner_text()
    assert not first.locator("dt", has_text="Mathpix").is_visible()
    # Keyboard: focus the summary, Enter opens, Space closes.
    summary.focus()
    page.keyboard.press("Enter")
    assert first.get_attribute("open") is not None
    for tool in ("ReadableSTEM", "Mathpix", "Aelira", "Chatbots", "LaTeXML"):
        assert first.locator("dt", has_text=tool).is_visible()
    assert first.locator("dd a", has_text="Source").first.is_visible()
    assert not first.locator(".compare-lead").is_visible()
    violations = axe_violations(page, axe_source)  # open
    assert not violations, describe(violations)
    page.keyboard.press("Space")
    assert first.get_attribute("open") is None
    # Tab moves on to the next topic's summary, not into closed content.
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.closest('details') !== null")
    assert page.evaluate("document.activeElement.tagName") == "SUMMARY"
    violations = axe_violations(page, axe_source)  # closed
    context.close()
    assert not violations, describe(violations)


@pytest.mark.parametrize(
    "path",
    [
        "/guides/ada-title-ii-stem-course-materials",
        "/guides/readablestem-mathpix-latexml",
        "/help/posting",
        "/for-accessibility-offices",
    ],
)
def test_the_contents_links_move_focus_to_their_headings_and_fit_320_px(
    site, browser, axe_source, path
):
    # Buttons and links transition colour for 120 ms unless the reader asks for reduced motion
    # (controls.css): with it, axe cannot read a mid-way colour after the scheme switch.
    context, page = open_as(
        browser, site, path, viewport=SMALL, signed_in=False, reduced_motion="reduce"
    )
    links = page.locator('nav.toc[aria-label="On this page"] a')
    assert links.count() >= 6
    for index in range(links.count()):
        assert links.nth(index).bounding_box()["height"] >= 24
    last = links.nth(links.count() - 1)
    anchor = last.get_attribute("href")
    last.click()
    assert page.evaluate("location.hash") == anchor
    target = page.locator(anchor)
    box = target.bounding_box()
    assert box and -2 <= box["y"] < 400  # scrolled into view
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        violations = axe_violations(page, axe_source)
        assert not violations, describe(violations)
    context.close()


def test_the_status_page_passes_axe_and_check_again_reloads_it(site, browser, axe_source):
    for scheme in ("light", "dark"):
        context, page = open_as(browser, site, "/status", scheme=scheme, signed_in=False)
        violations = axe_violations(page, axe_source)
        assert not violations, describe(violations)
        first = page.locator("#status-checked time").get_attribute("datetime")
        assert page.locator("#status-checked time").inner_text()
        page.get_by_role("link", name="Check again").click()
        page.wait_for_load_state()
        assert page.url.endswith("/status")
        assert page.locator("#status-checked time").get_attribute("datetime") >= first
        context.close()
