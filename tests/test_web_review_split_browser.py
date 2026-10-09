"""The review screen in a real browser (Playwright Chromium), on the real-shaped document
of fixtures.web.review_doc: a bold omega read plain (decision 1), a figure (2), a heading
group (3), an optional note.

- axe-core with the WCAG 2.2 AA and best-practice rules (target size included), failing
  on moderate and above, at 1440 and 390 CSS px, in every state: each decision, an
  optional check opened, accepted items shown, a refused edit, everything decided;
- no sideways scrolling of the page at 320 CSS px in any state, also with the WCAG 1.4.12
  text-spacing override; the page image scrolls inside its own named region instead;
- every mark on the page image is a link at least 24 x 24 CSS px;
- the whole review can be completed without JavaScript, and after each decision focus is
  on the next decision's heading (with and without JavaScript);
- keyboard shortcuts are off until turned on and never act while typing;
- in forced-colors mode the marks keep colours that show on the (white) page image.

Run with: uv run pytest -m browser tests/test_web_review_split_browser.py
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator

import pytest

from fixtures.web.review_doc import GROUP, dense_output, dense_pdf, review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv
from test_web_browser import TEXT_SPACING, WCAG_TAGS, describe, free_port, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review_browser import sideways_overflow, unfold_everything

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
NARROW = {"width": 320, "height": 640}
STRICT = {"moderate", "serious", "critical"}
LONG_NAME = "Physics_Unit_2_Vectors_and_Scalars_Teacher_Edition_With_Worked_Examples_FINAL.pdf"


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    """The app on a free port, converting every upload into the review document. Nothing
    is stubbed: real validators, assembler and page renderer."""
    import uvicorn

    env = WebEnv(tmp_path_factory.mktemp("split-browser"), per_user_daily_page_quota=1000)
    env.converter.output = review_output()
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
        files=[{"name": LONG_NAME, "mimeType": "application/pdf", "buffer": review_pdf()}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    assert site.run_worker() == job_id
    return job_id


def open_page(browser, site, *, viewport=DESKTOP, javascript: bool = True, **options):
    # bypass_csp only so axe can be injected; test_web_browser.py checks the CSP itself.
    context = browser.new_context(
        bypass_csp=True, viewport=viewport, java_script_enabled=javascript, **options
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


def wait_images(page) -> None:
    page.wait_for_load_state("networkidle")


def walk(page, site, job: str, check) -> None:
    """Every state of the review screen, calling `check(name)` in each."""
    url = f"{site.base}/jobs/{job}/review"
    page.goto(url)
    wait_images(page)
    check("decision 1: equation")
    page.goto(f"{url}?item=p1-e5")
    check("optional check opened")
    page.goto(f"{url}?accepted=1")
    check("accepted shown")
    page.goto(url)
    page.get_by_label("LaTeX for equation 1").fill(r"\notacommand{x}")
    page.get_by_role("button", name="Save edit to equation 1").click()
    page.locator(".error-summary").wait_for()
    check("refused edit")
    page.goto(url)
    page.get_by_role("button", name="Accept anyway equation 1").click()
    page.wait_for_url(f"{url}?item=p1-e6")
    wait_images(page)
    check("decision 2: figure")
    page.get_by_role("button", name="Accept figure 1").click()
    page.wait_for_url(f"{url}?item={GROUP}")
    wait_images(page)
    check("decision 3: heading group")
    page.get_by_role("button", name=re.compile("^Make bold text")).click()
    page.wait_for_url(url)
    check("all decided")


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440px", "390px"])
def test_every_state_passes_wcag22_aa_and_best_practice(site, browser, job, axe_source, viewport):
    context, page = open_page(browser, site, viewport=viewport)
    found: dict[str, list[dict]] = {}

    def check(state: str) -> None:
        found[state] = strict_axe(page, axe_source)
        unfold_everything(page)
        found[state + " (unfolded)"] = strict_axe(page, axe_source)

    walk(page, site, job, check)
    context.close()
    assert len(found) == 14
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


def test_the_page_image_is_a_named_focusable_region_that_scrolls_on_a_phone(site, browser, job):
    context, page = open_page(browser, site, viewport=NARROW)
    page.goto(f"{site.base}/jobs/{job}/review")
    wait_images(page)
    region = page.get_by_role("region", name="Page 1 image with 5 marks")
    assert region.get_attribute("tabindex") == "0"
    sizes = region.evaluate("e => [e.scrollWidth, e.clientWidth]")
    assert sizes[0] > sizes[1]  # wider than the screen: it scrolls inside the region
    loaded = page.locator(".page-image").evaluate("img => img.naturalWidth")
    assert loaded > 1000  # rendered from the upload
    # The selected mark was scrolled into view inside the region (app.js).
    assert mark_in_region(page), page.evaluate(REGION_GEOMETRY)
    context.close()


REGION_GEOMETRY = """() => {
  const region = document.querySelector('.page-scroll');
  const r = region.getBoundingClientRect();
  const m = document.querySelector('.box.is-current').getBoundingClientRect();
  return {top: m.top - r.top - region.clientTop, left: m.left - r.left - region.clientLeft,
          width: m.width, height: m.height, visibleWidth: region.clientWidth,
          visibleHeight: region.clientHeight, scrollTop: region.scrollTop,
          windowY: window.scrollY};
}"""


def mark_in_region(page) -> bool:
    """The selected mark lies inside the page region's visible box, both ways; a mark
    bigger than the box (a figure at 400% zoom) starts at its edge."""
    g = page.evaluate(REGION_GEOMETRY)

    def inside(start: float, size: float, visible: float) -> bool:
        if size > visible:
            return abs(start) <= 0.5
        return start >= -0.5 and start + size <= visible + 0.5

    return inside(g["top"], g["height"], g["visibleHeight"]) and inside(
        g["left"], g["width"], g["visibleWidth"]
    )


@pytest.mark.parametrize(
    "viewport",
    [NARROW, {"width": 320, "height": 180}, {"width": 844, "height": 390}, PHONE],
    ids=["320x640", "320x180 (400% zoom)", "844x390 (landscape phone)", "390x844"],
)
@pytest.mark.parametrize("item", ["", "?item=p1-e6", f"?item={GROUP}&page=2"])
def test_stacked_the_selected_mark_is_inside_the_page_region(site, browser, job, viewport, item):
    """Stacked, the page region starts below the window. app.js used to add a window term
    to the region's scroll, which scrolled it to its very end: the mark sat above the
    visible part. It must be inside, and the window must not move."""
    context, page = open_page(browser, site, viewport=viewport)
    page.goto(f"{site.base}/jobs/{job}/review{item}")
    wait_images(page)
    assert mark_in_region(page), page.evaluate(REGION_GEOMETRY)
    assert page.evaluate("window.scrollY") == 0
    # Scrolling the window down to the region shows the mark (nothing scrolls it away).
    page.locator(".page-scroll").scroll_into_view_if_needed()
    assert mark_in_region(page), page.evaluate(REGION_GEOMETRY)
    context.close()


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE, NARROW], ids=["1440", "390", "320"])
def test_every_mark_is_a_link_of_at_least_24_css_px(site, browser, job, viewport):
    context, page = open_page(browser, site, viewport=viewport)
    page.goto(f"{site.base}/jobs/{job}/review?accepted=1")
    wait_images(page)
    sizes = page.eval_on_selector_all(
        ".box",
        "els => els.map(e => [e.id, e.tagName, e.getBoundingClientRect().width, "
        "e.getBoundingClientRect().height])",
    )
    context.close()
    assert len(sizes) == 6
    small = [s for s in sizes if s[1] != "A" or s[2] < 24 or s[3] < 24]
    assert not small, small


def test_marks_are_named_and_link_to_their_decision(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}/review")
    page.get_by_role("link", name="2: Figure 1, needs your decision (2 of 3)").click()
    page.wait_for_url(re.compile(r"item=p1-e6"))
    assert "Decision 2 of 3" in page.locator("#decision-title").inner_text()
    page.get_by_role("link", name=re.compile(r"^3: Heading 2")).first.click()
    assert "Decision 3 of 3" in page.locator("#decision-title").inner_text()
    context.close()


@pytest.mark.parametrize("javascript", [False, True], ids=["no-js", "js"])
def test_focus_moves_to_the_next_decisions_heading_after_each_decision(
    site, browser, job, javascript
):
    context, page = open_page(browser, site, javascript=javascript)
    url = f"{site.base}/jobs/{job}/review"
    page.goto(url)
    page.get_by_role("button", name="Accept anyway equation 1").click()
    page.wait_for_url(f"{url}?item=p1-e6")
    _wait_focus(page, "decision-title")
    status = page.get_by_role("status")
    assert "Equation 1: Accepted. 2 decisions left." in status.inner_text()
    described = page.locator("#decision-title").get_attribute("aria-describedby")
    assert described == "decision-notice"
    context.close()


def _wait_focus(page, element_id: str, timeout_s: float = 5.0) -> None:
    """`autofocus` applies at the next rendering opportunity after load (poll from Python:
    the no-JS run cannot wait in the page)."""
    deadline = time.monotonic() + timeout_s
    while (focused := page.evaluate("document.activeElement.id")) != element_id:
        if time.monotonic() > deadline:
            raise AssertionError(f"focus is on #{focused!r}, not #{element_id}")
        time.sleep(0.05)


def test_the_whole_review_can_be_done_without_javascript(site, browser, job):
    context, page = open_page(browser, site, viewport=PHONE, javascript=False)
    url = f"{site.base}/jobs/{job}/review"
    page.goto(f"{site.base}/jobs/{job}")
    page.get_by_role("link", name=re.compile("^Review fixes")).click()
    assert page.locator("#decision-title").inner_text().startswith("Decision 1 of 3")
    # Decision 1: the omega must be bold; the suggested fix is one button.
    page.get_by_role("button", name="Use bold ω in equation 1").click()
    page.wait_for_url(f"{url}?item=p1-e6")
    assert (
        "Equation 1: Your edit passed the required checks"
        in page.get_by_role("status").inner_text()
    )
    # Decision 2: write better alt text.
    page.get_by_label("Alt text for figure 1").fill("A car drives along a road from a house.")
    page.get_by_role("button", name="Save edit to figure 1").click()
    page.wait_for_url(f"{url}?item={GROUP}")
    # Decision 3: the group, one button for all three.
    page.get_by_role("button", name=re.compile("^Keep as headings at level 2")).click()
    page.wait_for_url(url)
    assert page.locator("#decision-title").inner_text() == "All decisions made"
    assert page.get_by_role("link", name="Download the accessible HTML").is_visible()
    assert page.locator(".page-scroll .box-required").count() == 0
    # The optional checks still open without scripts.
    page.locator("#optional-summary").click()
    page.get_by_role("link", name="Equation 2, page 1").click()
    assert page.locator("#decision-title").inner_text().startswith("Optional check")
    context.close()


def test_typing_a_fix_then_pressing_accept_keeps_the_fix_without_javascript(site, browser, job):
    """Accept and Save edit share a form; Accept used to drop what was typed and accept
    the proposal the screen flagged. Without JavaScript nothing restyles the buttons, so
    the server must refuse it."""
    context, page = open_page(browser, site, javascript=False)
    url = f"{site.base}/jobs/{job}/review"
    page.goto(url)
    field = page.get_by_label("LaTeX for equation 1")
    field.fill(r"\boldsymbol{\omega}")
    page.get_by_role("button", name="Accept anyway equation 1").click()
    summary = page.locator(".error-summary")
    assert "You changed the LaTeX." in summary.inner_text()
    assert page.get_by_label("LaTeX for equation 1").input_value() == r"\boldsymbol{\omega}"
    # Save edit is now the main (filled) button; Accept is not.
    save, accept = (
        page.get_by_role("button", name=n).evaluate("e => getComputedStyle(e).backgroundColor")
        for n in ("Save edit to equation 1", "Accept anyway equation 1")
    )
    assert save == "rgb(26, 79, 160)" and accept != save
    page.get_by_role("button", name="Save edit to equation 1").click()
    page.wait_for_url(f"{url}?item=p1-e6")
    assert (
        "Equation 1: Your edit passed the required checks"
        in page.get_by_role("status").inner_text()
    )
    context.close()
    result = site.job(job).result_json
    fix = next(f for f in result["fixes"] if f["element_id"] == "p1-e2")
    assert (fix["status"], fix["equation"]["latex"]) == ("edited", r"\boldsymbol{\omega}")


@pytest.fixture
def dense_job(site, browser) -> Iterator[str]:
    """A finished job of the dense handout page (fixtures.web.review_doc.dense_output)."""
    site.converter.output = dense_output()
    try:
        context = browser.new_context()
        page = context.new_page()
        sign_in(page, site.base, ALICE)
        page.goto(f"{site.base}/app")
        page.get_by_label("PDF file").set_input_files(
            files=[{"name": "dense.pdf", "mimeType": "application/pdf", "buffer": dense_pdf()}]
        )
        page.get_by_role("button", name="Upload and convert").click()
        page.wait_for_url(f"{site.base}/jobs/*")
        job_id = page.url.rsplit("/", 1)[-1]
        context.close()
        assert site.run_worker() == job_id
        yield job_id
    finally:
        site.converter.output = review_output()


OVERLAPS = """() => {
  const rects = [...document.querySelectorAll('.page-frame .box')].map(
    b => [b.id, b.getBoundingClientRect()]);
  const out = [];
  for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
    const [a, ra] = rects[i], [b, rb] = rects[j];
    const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
    const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
    if (w > 0.5 && h > 0.5) out.push([a, b, Math.round(w), Math.round(h)]);
  }
  return out;
}"""


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440px", "390px"])
def test_a_dense_page_with_accepted_shown_keeps_every_target_whole(
    site, browser, dense_job, axe_source, viewport
):
    """An inline equation on every line, accepted ones shown: 24 px marks centred on each
    overlapped, so a click in the overlap went to the wrong item (axe: target-size,
    "partially obscured"). Now no two clickable areas overlap and axe passes."""
    context, page = open_page(browser, site, viewport=viewport)
    page.goto(f"{site.base}/jobs/{dense_job}/review?accepted=1")
    wait_images(page)
    assert page.locator(".page-frame .box").count() == 18
    assert page.evaluate(OVERLAPS) == []
    sizes = page.eval_on_selector_all(
        ".page-frame .box",
        "els => els.map(e => [e.id, e.getBoundingClientRect().width, "
        "e.getBoundingClientRect().height])",
    )
    assert not [s for s in sizes if s[1] < 24 or s[2] < 24], sizes
    found = strict_axe(page, axe_source)
    assert not found, describe(found)
    context.close()


def test_a_refused_image_is_asked_for_once_more_then_explained(site, browser, job):
    """Renders are capped per person (429 beyond two at a time): app.js asks once more
    after a pause, and only then says the page image could not be shown."""
    context, page = open_page(browser, site)
    calls: list[str] = []

    def once(route):
        calls.append(route.request.url)
        if len(calls) == 1:
            route.fulfill(status=429, body="Too many images at once.")
        else:
            route.continue_()

    page.route("**/pages/1.png", once)
    page.goto(f"{site.base}/jobs/{job}/review")
    page.wait_for_function("document.querySelector('.page-image').naturalWidth > 0")
    assert len(calls) == 2
    assert page.locator(".page-failed").is_hidden()
    page.unroute("**/pages/1.png")

    refused: list[str] = []
    page.route(
        "**/pages/1.png",
        lambda route: (refused.append(route.request.url), route.fulfill(status=429, body="")),
    )
    page.goto(f"{site.base}/jobs/{job}/review")
    page.locator(".page-failed").wait_for(state="visible", timeout=8000)
    assert len(refused) == 2
    context.close()


def test_keyboard_shortcuts_are_opt_in_and_never_fire_while_typing(site, browser, job):
    context, page = open_page(browser, site)
    url = f"{site.base}/jobs/{job}/review"
    page.goto(url)
    page.locator("main").focus()
    page.keyboard.press("n")
    assert page.url == url  # off by default
    page.locator("#shortcuts summary").click()
    page.get_by_label("Use keyboard shortcuts on this screen").check()
    field = page.get_by_label("LaTeX for equation 1")  # open: the meaning changes
    field.focus()
    page.keyboard.type("n a p")
    assert page.url == url and "n a p" in field.input_value()
    page.locator("#decision-title").focus()
    page.keyboard.press("n")
    page.wait_for_url(re.compile(r"item=p1-e6"))
    page.keyboard.press("p")
    page.wait_for_url(re.compile(r"item=p1-e2"))
    page.locator("#decision-title").focus()
    page.keyboard.press("e")
    assert page.evaluate("document.activeElement.id") == "latex-p1-e2"
    context.close()


def test_forced_colors_keep_the_marks_visible_on_the_page_image(site, browser, job):
    context, page = open_page(browser, site, forced_colors="active", color_scheme="dark")
    page.goto(f"{site.base}/jobs/{job}/review")
    wait_images(page)
    colours = page.evaluate(
        """() => ['.box-required:not(.is-current)', '.box.is-current', '.box-optional'].map(
             s => getComputedStyle(document.querySelector(s + ' .box-frame')).borderTopColor)"""
    )
    assert colours == ["rgb(122, 75, 0)", "rgb(26, 79, 160)", "rgb(75, 90, 117)"], colours
    page.locator(".box-optional").focus()
    outline = page.evaluate(
        "(() => { const s = getComputedStyle(document.activeElement); "
        "return [s.outlineStyle, s.outlineColor]; })()"
    )
    assert outline == ["solid", "rgb(11, 61, 145)"], outline
    context.close()


def test_a_flagged_symbol_is_outlined_in_the_proposal(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}/review")
    outline = page.locator(".cmp-math .flagged").evaluate(
        "e => [getComputedStyle(e).outlineStyle, e.getBoundingClientRect().width]"
    )
    assert outline[0] == "solid" and outline[1] > 0
    size = page.locator(".cmp-math").evaluate("e => parseFloat(getComputedStyle(e).fontSize)")
    width = page.locator(".cmp-image").evaluate("e => e.getBoundingClientRect().width")
    # Same size: the proposal's font is the equation's 11 pt at the crop's scale.
    element_width_pt = 8.71 + 2 * 4 * 11  # the omega's box plus 4 font sizes each side
    assert size == pytest.approx(11 * width / element_width_pt, rel=0.05)
    context.close()
