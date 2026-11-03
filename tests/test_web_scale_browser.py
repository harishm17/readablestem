"""Review at scale in a real browser (Playwright Chromium): accept many at once, the decision
filter, "Report this item", "Leave this page out of the HTML", the finish notice on the job
page, the guides list, and the larger targets.

- every new control is used from the keyboard alone (Tab, Enter, typing in a field);
- axe-core (WCAG 2.2 AA and best practice, moderate and above) at 1280 and 320 CSS px on each
  new page or state;
- forced-colors and reduced-motion: the controls stay visible and nothing animates;
- target sizes: Undo is 44 px, the marks are 24 px or more and 44 px where the page has room;
- the finish notice works with a stubbed Notification API and does nothing without it.

Run with: uv run pytest -m browser tests/test_web_scale_browser.py
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator

import pytest

from fixtures.web.review_doc import review_pdf
from fixtures.web.support import ALICE, WebEnv
from test_web_browser import TEXT_SPACING, describe, free_port, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review_browser import sideways_overflow
from test_web_review_bulk_accept import kept_page_output, scaled_output
from test_web_review_split_browser import _wait_focus, strict_axe

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

WIDE = {"width": 1280, "height": 800}
NARROW = {"width": 320, "height": 640}


def output():
    """Equations and figures in every bulk state, and a scanned page kept as an image."""
    out = scaled_output()
    kept = kept_page_output()
    extra_ids = {"p2-e9"}
    out.result.elements += [e for e in kept.result.elements if e.id in extra_ids]
    out.result.fixes += [f for f in kept.result.fixes if f.element_id in extra_ids]
    out.crops["p2-e9"] = kept.crops["p2-e9"]
    return out


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    import uvicorn

    env = WebEnv(tmp_path_factory.mktemp("scale-browser"), per_user_daily_page_quota=1000)
    env.converter.output = output()
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


def upload(site, browser) -> str:
    """Upload the document from a browser; the job is queued until `site.run_worker()`."""
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": "notes.pdf", "mimeType": "application/pdf", "buffer": review_pdf()}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    return job_id


@pytest.fixture
def job(site, browser) -> str:
    job_id = upload(site, browser)
    assert site.run_worker() == job_id
    return job_id


def open_page(browser, site, *, viewport=WIDE, **options):
    context = browser.new_context(bypass_csp=True, viewport=viewport, **options)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    return context, page


def tab_to(page, locator, limit: int = 40) -> int:
    """Press Tab from the top of the page until `locator` has focus; the count of presses."""
    handle = locator.element_handle()
    # A click on the page's heading puts the keyboard's starting point there (a link to
    # #item-... would leave it far down the page).
    page.locator("h1").first.click()
    for presses in range(1, limit + 1):
        page.keyboard.press("Tab")
        if page.evaluate("(el) => el === document.activeElement", handle):
            return presses
    raise AssertionError(f"Tab did not reach the control in {limit} presses")


def statuses(site, job_id: str) -> dict[str, str]:
    row = site.job(job_id)
    return {f["element_id"]: f["status"] for f in row.result_json["fixes"]}


# --------------------------------------------------------------------------- keyboard


def test_accept_all_equations_from_the_keyboard_then_undo(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}/review/list")
    link = page.get_by_role("link", name=re.compile("Accept all remaining equations whose checks"))
    assert "(2)" in link.inner_text()
    tab_to(page, link.first)
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/accept/equations"))
    expect = sync_api.expect
    expect(page.get_by_role("heading", level=1)).to_have_text(
        "Accept all remaining equations whose checks passed"
    )
    assert page.locator(".bulk-preview > li").count() == 2
    accept = page.get_by_role("button", name="Accept all 2 equations")
    presses = tab_to(page, accept)
    assert presses <= 20
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/review/list"))
    assert statuses(site, job)["p1-e2"] == statuses(site, job)["p2-e7"] == "accepted"
    notice = page.locator(".notice[role=status]").filter(has_text="Accepted 2 equations")
    expect(notice.first).to_be_visible()
    # Undo is a button the keyboard reaches, and one press takes both back.
    undo = page.locator('[data-role="undo"]')
    tab_to(page, undo)
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    after = statuses(site, job)
    assert after["p1-e2"] == after["p2-e7"] == "needs_review"
    context.close()


def test_cancel_is_a_link_back_and_changes_nothing(site, browser, job):
    context, page = open_page(browser, site)
    before = statuses(site, job)
    page.goto(f"{site.base}/jobs/{job}/accept/figures?view=list")
    cancel = page.get_by_role("link", name="Cancel")
    tab_to(page, cancel)
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/review/list"))
    assert statuses(site, job) == before
    context.close()


def test_the_decision_filter_is_used_by_keyboard_and_moves_focus_to_the_result(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}/review/list")
    kind = page.get_by_label("Kind")
    tab_to(page, kind.first)
    page.keyboard.type("Figures")  # a select answers typed letters
    page.keyboard.press("Tab")
    page.keyboard.type("2")
    page.keyboard.press("Enter")  # submits the GET form
    page.wait_for_url(re.compile(r"kind=figures&page=2"))
    _wait_focus(page, "filter-result")
    text = page.locator("#filter-result").inner_text()
    assert text.startswith("Showing 4 of") and "figures on page 2" in text
    assert page.locator("#group-equations").count() == 0
    # The way back is a link.
    page.get_by_role("link", name="Show all decisions").focus()
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/review/list#needs-decision"))
    assert page.locator("#filter-result").count() == 0
    context.close()


def test_leave_this_page_out_from_the_card_by_keyboard_and_undo(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}/review?item=pg-p2-e9")
    button = page.get_by_role("button", name="Leave this page out of the HTML")
    tab_to(page, button)
    page.keyboard.press("Enter")
    notice = page.locator("#decision-notice")
    notice.wait_for(timeout=10000)
    assert "page-tools" not in page.url
    assert site.job(job).result_json.get("omitted"), "the page was left out"
    assert "Page 2" in notice.inner_text()
    undo = page.locator('[data-role="undo"]')
    tab_to(page, undo)
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"tools=1"), timeout=10000)
    assert not site.job(job).result_json.get("omitted")
    context.close()


def test_report_this_item_is_a_link_with_a_full_name(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}/review?item=p1-e6")
    link = page.locator('[data-role="report-item"]')
    href = link.get_attribute("href") or ""
    assert href.startswith("mailto:") and job in href and "p1-e6" in href.replace("%20", " ")
    name = link.inner_text()
    assert "Report this item" in name and "Figure 1" in name
    tab_to(page, link)  # reachable by Tab
    context.close()


# --------------------------------------------------------------------------- finish notice

NOTIFICATION_STUB = """
(() => {
  window.__notes = [];
  window.__asked = 0;
  document.hasFocus = () => false;
  class Stub {
    constructor(title, options) { window.__notes.push({ title, body: options && options.body }); }
    close() {}
  }
  Stub.permission = "default";
  Stub.requestPermission = (callback) => {
    window.__asked += 1;
    Stub.permission = "granted";
    if (callback) callback("granted");
    return Promise.resolve("granted");
  };
  window.Notification = Stub;
})();
"""


def test_finish_notice_opt_in_title_and_announcement(site, browser):
    job_id = upload(site, browser)
    context, page = open_page(browser, site)
    page.add_init_script(NOTIFICATION_STUB)
    page.goto(f"{site.base}/jobs/{job_id}")
    assert page.evaluate("window.__asked") == 0  # nothing asked on load
    button = page.get_by_role("button", name="Notify me when it finishes")
    expect(button).to_be_visible()
    tab_to(page, button)
    page.keyboard.press("Enter")
    expect(page.locator("#notify-state")).to_contain_text("You will get a notice")
    assert page.evaluate("window.__asked") == 1
    assert site.run_worker() == job_id
    finished = page.locator("#job-status[data-finished]")
    finished.wait_for(timeout=15000)
    wanted = f"{finished.get_attribute('data-title')} | ReadableSTEM"
    page.wait_for_function("(t) => document.title === t", arg=wanted, timeout=5000)
    assert job_id not in page.title()
    region = page.locator("#job-status-region")
    expect(region).to_contain_text(finished.get_attribute("data-title").split(":")[0])
    # The button had focus; it is hidden now, so focus moved to the status box.
    assert page.evaluate("document.activeElement.id") == "job-status-region"
    assert region.get_attribute("role") == "status"
    notes = page.evaluate("window.__notes")
    assert len(notes) == 1 and notes[0]["title"] == "ReadableSTEM"
    assert notes[0]["body"] == finished.get_attribute("data-summary")
    assert page.locator("#notify-box").is_hidden()
    context.close()


def test_without_the_notification_api_nothing_is_offered_and_the_title_still_changes(site, browser):
    job_id = upload(site, browser)
    context, page = open_page(browser, site)
    page.add_init_script("delete window.Notification;")
    page.goto(f"{site.base}/jobs/{job_id}")
    assert page.locator("#notify-box").is_hidden()
    assert site.run_worker() == job_id
    page.locator("#job-status[data-finished]").wait_for(timeout=15000)
    assert page.title().startswith(page.locator("#job-status").get_attribute("data-title"))
    context.close()


def test_a_blocked_browser_is_not_offered_the_button(site, browser):
    job_id = upload(site, browser)
    context, page = open_page(browser, site)
    page.add_init_script(
        "window.Notification = class { static permission = 'denied'; "
        "static requestPermission() {} };"
    )
    page.goto(f"{site.base}/jobs/{job_id}")
    assert page.locator("#notify-box").is_hidden()
    assert site.run_worker() == job_id
    context.close()


def test_a_job_that_was_already_done_says_nothing(site, browser, job):
    context, page = open_page(browser, site)
    page.add_init_script(NOTIFICATION_STUB)
    page.goto(f"{site.base}/jobs/{job}")
    assert page.locator("#notify-box").count() == 0
    assert page.evaluate("window.__notes.length") == 0
    context.close()


# --------------------------------------------------------------------------- axe and layout


def states(site, job: str):
    base = f"{site.base}/jobs/{job}"
    return {
        "bulk equations": f"{base}/accept/equations?view=list",
        "bulk figures": f"{base}/accept/figures?view=split",
        "list with offers": f"{base}/review/list",
        "list filtered": f"{base}/review/list?kind=equations&page=2",
        "list no match": f"{base}/review/list?kind=headings&page=1",
        "decision with report link": f"{base}/review?item=p1-e6",
        "kept page card": f"{base}/review?item=pg-p2-e9",
        "kept page card in the list": f"{base}/review/list#group-pages",
        "job page": base,
        "guides": f"{site.base}/guides",
        "landing": f"{site.base}/",
        "my documents": f"{site.base}/app",
    }


@pytest.mark.parametrize("viewport", [WIDE, NARROW], ids=["1280px", "320px"])
def test_new_pages_pass_wcag22_aa_and_best_practice_without_sideways_scrolling(
    site, browser, job, axe_source, viewport
):
    context, page = open_page(browser, site, viewport=viewport)
    found: dict[str, list[dict]] = {}
    overflow: dict[str, int] = {}
    for name, url in states(site, job).items():
        page.goto(url)
        page.wait_for_load_state("networkidle")
        found[name] = strict_axe(page, axe_source)
        overflow[name] = sideways_overflow(page)
    context.close()
    failures = {name: describe(v) for name, v in found.items() if v}
    assert not failures, "\n".join(f"[{s}]\n{d}" for s, d in failures.items())
    assert {name: o for name, o in overflow.items() if o > 0} == {}


def test_in_progress_job_page_with_the_notify_button_passes_axe(site, browser, axe_source):
    job_id = upload(site, browser)
    context, page = open_page(browser, site)
    page.add_init_script(NOTIFICATION_STUB)
    page.goto(f"{site.base}/jobs/{job_id}")
    expect(page.locator("#notify-box")).to_be_visible()
    assert not strict_axe(page, axe_source)
    page.get_by_role("button", name="Notify me when it finishes").click()
    expect(page.locator("#notify-state")).to_contain_text("You will get a notice")
    assert not strict_axe(page, axe_source)
    context.close()
    site.run_worker()


def test_text_spacing_override_does_not_clip_the_new_controls(site, browser, job):
    context, page = open_page(browser, site, viewport=NARROW)
    for url in (
        f"{site.base}/jobs/{job}/review/list",
        f"{site.base}/jobs/{job}/accept/equations?view=list",
    ):
        page.goto(url)
        page.add_style_tag(content=TEXT_SPACING)
        assert sideways_overflow(page) <= 0
    context.close()


def test_forced_colors_and_reduced_motion_keep_the_controls_visible_and_still(site, browser, job):
    context, page = open_page(browser, site, forced_colors="active", reduced_motion="reduce")
    page.goto(f"{site.base}/jobs/{job}/review/list")
    assert page.evaluate("matchMedia('(forced-colors: active)').matches")
    assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
    for selector in (".bulk-list a", ".decision-filter button", ".decision-filter select"):
        box = page.locator(selector).first
        expect(box).to_be_visible()
        border = box.evaluate("(el) => getComputedStyle(el).borderTopWidth")
        assert border != "0px", selector
        assert box.evaluate("(el) => getComputedStyle(el).transitionDuration") in {"0s", "0.01ms"}
    group = page.locator(".bulk-offers")
    assert group.evaluate("(el) => getComputedStyle(el).borderTopWidth") != "0px"
    context.close()


def test_undo_is_a_44_px_target_and_the_marks_are_24_px_or_more(site, browser, job):
    context, page = open_page(browser, site, viewport={"width": 1440, "height": 900})
    page.goto(f"{site.base}/jobs/{job}/review?item=p1-e6")
    page.get_by_role("button", name="Accept figure 1").click()
    page.wait_for_load_state("networkidle")
    undo = page.locator('[data-role="undo"]').bounding_box()
    assert undo and undo["height"] >= 44 and undo["width"] >= 44
    sizes = page.evaluate(
        """() => [...document.querySelectorAll('.page-frame .box')].map((el) => {
             const r = el.getBoundingClientRect(); return [r.width, r.height]; })"""
    )
    assert sizes, "marks on the page"
    assert all(w >= 23.9 and h >= 23.9 for w, h in sizes)
    assert any(w >= 43.9 and h >= 43.9 for w, h in sizes), "a mark with room is a 44 px target"
    context.close()
