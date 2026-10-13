"""The table card in a real browser (Playwright Chromium), on fixtures.web.table_doc:
table 1 a proposal a check doubts (decision 1), table 2 kept as an image with a grid read
from the PDF (2), table 3 kept as an image with nothing else (3), an optional table and
an accepted one twelve columns wide.

- axe-core with the WCAG 2.2 AA and best-practice rules, failing on moderate and above,
  at 1280 and 320 CSS px, in every state of the card (each decision, a cell's form open,
  a refused cell, a stale form refused, headers changed, the wide table, the list view);
- no sideways scrolling of the page at 320 CSS px in any state, also with the WCAG 1.4.12
  text-spacing override: a wide table scrolls inside its own named, focusable box;
- the keyboard reaches everything in reading order, and choosing and changing a cell
  works from the keyboard alone (focus lands in the cell's field, then on the decision's
  heading after Save cell);
- the three table decisions can be made without JavaScript.

Run with: uv run pytest -m browser tests/test_web_review_tables_browser.py
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator

import pytest

from fixtures.web.support import ALICE, WebEnv
from fixtures.web.table_doc import table_output, table_pdf
from test_web_browser import TEXT_SPACING, WCAG_TAGS, describe, free_port, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review_browser import sideways_overflow, unfold_everything

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

DESKTOP = {"width": 1280, "height": 900}
NARROW = {"width": 320, "height": 640}
STRICT = {"moderate", "serious", "critical"}


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    """The app on a free port, converting every upload into the table document."""
    import uvicorn

    env = WebEnv(tmp_path_factory.mktemp("tables-browser"), per_user_daily_page_quota=1000)
    env.converter.output = table_output()
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
    """A fresh finished job (the tests change its review state)."""
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": "motion.pdf", "mimeType": "application/pdf", "buffer": table_pdf()}]
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
    """Every state of the table card, calling `check(name)` in each."""
    url = f"{site.base}/jobs/{job}/review"
    page.goto(url)
    page.wait_for_load_state("networkidle")
    check("decision 1: a proposal to check")
    page.goto(f"{url}?item=p1-e1&cell=r3c1")
    check("a maths cell's form open")
    page.get_by_label("LaTeX in row 4, columns 2 to 3").fill(r"\notacommand{v}")
    page.get_by_role("button", name=re.compile("^Save cell")).click()
    page.locator(".error-summary").wait_for()
    check("a refused cell")
    page.goto(url)
    page.locator(".table-edit summary").click()
    page.get_by_label("The first row is a header").check()
    page.get_by_role("button", name="Save headers of table 1").click()
    page.wait_for_url(f"{url}?item=p1-e1")
    check("headers changed, still on the table")
    # A form from before that change (the version it carries is now old).
    page.goto(f"{url}?item=p1-e1")
    page.evaluate("document.querySelectorAll('input[name=version]').forEach(i => i.value = '0')")
    page.get_by_role("button", name="Accept table 1").click()
    page.locator(".error-summary").wait_for()
    check("a stale form refused")
    page.goto(f"{url}?item=p1-e3")
    check("decision 2: kept as an image, a grid to use")
    page.goto(f"{url}?item=p2-e0")
    check("decision 3: kept as an image")
    page.goto(f"{url}?item=p2-e2")
    check("the wide table")
    page.goto(f"{url}/list")
    check("the list view")


@pytest.mark.parametrize("viewport", [DESKTOP, NARROW], ids=["1280px", "320px"])
def test_every_state_passes_wcag22_aa_and_best_practice(site, browser, job, axe_source, viewport):
    context, page = open_page(browser, site, viewport=viewport)
    found: dict[str, list[dict]] = {}

    def check(state: str) -> None:
        found[state] = strict_axe(page, axe_source)
        unfold_everything(page)
        found[state + " (unfolded)"] = strict_axe(page, axe_source)

    walk(page, site, job, check)
    context.close()
    assert len(found) == 18
    failures = {state: describe(v) for state, v in found.items() if v}
    assert not failures, "\n".join(f"[{s}]\n{d}" for s, d in failures.items())


@pytest.mark.parametrize("spacing", [False, True], ids=["plain", "text-spacing"])
def test_no_state_scrolls_sideways_at_320_css_px(site, browser, job, spacing):
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


@pytest.mark.parametrize("viewport", [DESKTOP, NARROW], ids=["1280px", "320px"])
def test_a_wide_table_scrolls_in_its_own_box(site, browser, job, viewport):
    context, page = open_page(browser, site, viewport=viewport)
    page.goto(f"{site.base}/jobs/{job}/review?item=p2-e2")
    box = page.get_by_role("group", name="Proposed table 5")
    assert box.get_attribute("tabindex") == "0"
    scroll, client = box.evaluate("e => [e.scrollWidth, e.clientWidth]")
    assert scroll > client  # twelve columns: wider than the card at both sizes
    # From the keyboard: focus the box, arrow right, and it has scrolled.
    box.focus()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    deadline = time.monotonic() + 3
    while box.evaluate("e => e.scrollLeft") == 0 and time.monotonic() < deadline:
        time.sleep(0.05)  # the browser may scroll smoothly
    assert box.evaluate("e => e.scrollLeft") > 0
    assert sideways_overflow(page) == 0
    # The cells keep whole words (the card breaks long words anywhere, the table must
    # not): every one-word cell is on one line.
    lines = box.locator("td, th").evaluate_all(
        """cells => cells
          .filter(c => c.innerText.trim() && !/\\s/.test(c.innerText.trim()))
          .map(c => { const r = document.createRange(); r.selectNodeContents(c);
                      return r.getClientRects().length; })"""
    )
    assert len(lines) == 22 and set(lines) == {1}, lines
    context.close()


def focused(page) -> str:
    """What has focus, in words: its role-ish tag and accessible text."""
    return page.evaluate(
        """() => {
          const e = document.activeElement;
          const name = e.getAttribute('aria-label') || e.innerText || e.value || '';
          return `${e.tagName.toLowerCase()}:${name.trim().split('\\n')[0]}`;
        }"""
    )


def tab_until(page, stop: str, limit: int = 40) -> list[str]:
    seen = []
    for _ in range(limit):
        page.keyboard.press("Tab")
        seen.append(focused(page))
        if seen[-1].startswith(stop):
            return seen
    raise AssertionError(f"never reached {stop!r}: {seen}")


@pytest.mark.parametrize("javascript", [True, False], ids=["js", "no-js"])
def test_the_keyboard_reaches_everything_in_reading_order(site, browser, job, javascript):
    context, page = open_page(browser, site, javascript=javascript)
    page.goto(f"{site.base}/jobs/{job}/review")
    page.locator("#decision-title").focus()
    seen = tab_until(page, "button:Keep as image")
    stops = [s for s in seen if not s.startswith("a:Decision") and s != "a:"]
    assert stops[:6] == [
        "a:Show on the page",
        "div:Proposed table 1",
        "summary:Change the table",
        "summary:The grid read from the PDF",
        "button:Accept",
        "button:Keep as image",
    ], seen
    # Open "Change the table" from the keyboard: the cell list, then the header boxes.
    page.locator("#decision-title").focus()
    tab_until(page, "summary:Change the table")
    page.keyboard.press("Enter")
    inside = tab_until(page, "button:Save headers")
    assert [s.split(":")[0] for s in inside] == [
        "select", "button", "input", "input", "button",
    ], inside  # fmt: skip
    context.close()


@pytest.mark.parametrize("javascript", [True, False], ids=["js", "no-js"])
def test_a_cell_is_changed_from_the_keyboard_alone(site, browser, job, javascript):
    context, page = open_page(browser, site, javascript=javascript)
    url = f"{site.base}/jobs/{job}/review"
    page.goto(url)
    page.locator("#decision-title").focus()
    tab_until(page, "summary:Change the table")
    page.keyboard.press("Enter")
    tab_until(page, "select:")
    page.keyboard.press("ArrowDown")  # row 1, column 2
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")  # row 2, column 2: "10"
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")  # Change this cell
    page.wait_for_url(re.compile(r".*cell=r1c1$"))
    _wait_focus(page, "cell-p1-e1")  # autofocus: the field of the chosen cell
    page.keyboard.press("Control+A")
    page.keyboard.type("12")
    page.keyboard.press("Tab")
    assert focused(page).startswith("button:Save cell")
    page.keyboard.press("Enter")
    page.wait_for_url(f"{url}?item=p1-e1")
    _wait_focus(page, "decision-title")
    assert "Table 1: Row 2, column 2 changed." in page.get_by_role("status").inner_text()
    assert page.get_by_role("group", name="Proposed table 1").locator("td", has_text="12").count()
    context.close()


def _wait_focus(page, element_id: str, timeout_s: float = 5.0) -> None:
    """`autofocus` applies at the next rendering opportunity after load."""
    deadline = time.monotonic() + timeout_s
    while (now := page.evaluate("document.activeElement.id")) != element_id:
        if time.monotonic() > deadline:
            raise AssertionError(f"focus is on #{now!r}, not #{element_id}")
        time.sleep(0.05)


def test_the_table_decisions_can_be_made_without_javascript(site, browser, job):
    context, page = open_page(browser, site, viewport=NARROW, javascript=False)
    url = f"{site.base}/jobs/{job}/review"
    page.goto(url)
    assert page.locator("#decision-title").inner_text().startswith("Decision 1 of 3")
    page.get_by_role("button", name="Accept table 1").click()
    page.wait_for_url(f"{url}?item=p1-e3")
    page.get_by_role("button", name="Use the grid read from the PDF for table 2").click()
    page.wait_for_url(f"{url}?item=p1-e3")  # stays on the table it changed
    assert "Decided: Edited by you" in page.locator("#decision").inner_text()
    page.get_by_role("link", name="Go on to decision 3").click()
    page.get_by_role("button", name="Keep as image (table 3)").click()
    page.wait_for_url(url)
    assert page.locator("#decision-title").inner_text() == "Ready"  # a table stays an image
    context.close()
    statuses = {f["element_id"]: f["status"] for f in site.job(job).result_json["fixes"]}
    assert statuses["p1-e1"] == "accepted"
    assert statuses["p1-e3"] == "edited"
    assert statuses["p2-e0"] == "rejected"


def test_the_shortcuts_reach_the_table_card(site, browser, job):
    """With shortcuts on, E opens "Change the table" at the cell list; A accepts."""
    context, page = open_page(browser, site)
    url = f"{site.base}/jobs/{job}/review"
    page.goto(url)
    page.locator("#shortcuts summary").click()
    page.get_by_label("Use keyboard shortcuts on this screen").check()
    page.locator("#decision-title").focus()
    page.keyboard.press("e")
    assert page.evaluate("document.activeElement.id") == "cell-pick-p1-e1"
    assert page.locator(".table-edit").evaluate("d => d.open")
    page.locator("#decision-title").focus()
    page.keyboard.press("a")
    page.wait_for_url(f"{url}?item=p1-e3")
    assert "Table 1: Accepted." in page.get_by_role("status").inner_text()
    context.close()


def test_choosing_a_cell_keeps_no_draft_and_one_tables_draft_stays_with_it(site, browser, job):
    """The cell list is navigation: picking a cell and saving it leaves no "not saved
    yet" line, and nothing carries over to another table. A typed cell's draft belongs
    to that cell's form only."""
    context, page = open_page(browser, site)
    url = f"{site.base}/jobs/{job}/review"
    page.goto(f"{url}?item=p1-e1")
    page.locator("summary", has_text="Change the table").click()
    page.locator("#cell-pick-p1-e1").select_option("r1c1")
    page.get_by_role("button", name="Change this cell").click()
    page.wait_for_url(re.compile(r".*cell=r1c1$"))
    page.locator("#cell-p1-e1").fill("10 m")
    page.get_by_role("button", name=re.compile("^Save cell")).click()
    page.wait_for_url(f"{url}?item=p1-e1")
    assert "Row 2, column 2 changed" in page.get_by_role("status").inner_text()
    assert page.locator(".kept-draft").count() == 0
    drafts = "Object.keys(sessionStorage).filter(k => k.includes('draft'))"
    assert page.evaluate(drafts) == []
    # A cell picked on table 1, then table 2 opened: its box stays closed.
    page.locator("summary", has_text="Change the table").click()
    page.locator("#cell-pick-p1-e1").select_option("r2c0")
    page.goto(f"{url}?item=p2-e1")
    assert page.locator("details.table-edit[open]").count() == 0
    assert page.locator(".kept-draft").count() == 0
    context.close()
