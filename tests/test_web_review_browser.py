"""The review screen in a real browser (Playwright Chromium).

- axe-core in every state a reviewer can put the page in: fixes needing review, failed
  and not converted, decided ones folded away and unfolded, just edited, just rejected,
  after a bulk accept, and a refused edit (zero serious or critical violations);
- every control has a label and every button says which fix or group it acts on;
- status is written in words, not shown by colour alone;
- no sideways scrolling at 320 CSS px (WCAG 1.4.10), even with a file name that has no
  break points and an equation wider than the screen;
- decisions and bulk accept work without JavaScript and land where the reviewer expects.

Run with: uv run pytest -m browser tests/test_web_review_browser.py
Uses the rich three-page document from test_web_review.py (every status in every group)
and the axe-core download and helpers from test_web_browser.py.
"""

from __future__ import annotations

import re
import threading
import time
from collections import Counter
from collections.abc import Iterator

import pytest

from a11y_stem.core.types import ConversionOutput
from a11y_stem.web.views import FIX_STATUS_LABELS
from fixtures.web.support import ALICE, WebEnv, make_pdf
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import axe_violations, describe, free_port, sign_in
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review import rich_output

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

# Underscores are not break opportunities: without overflow-wrap this file name alone is
# about 900 px wide in the <h1>.
LONG_NAME = "Physics_Unit_2_Kinematics_Lecture_Notes_With_Worked_Examples_Fall_2026_FINAL_v3.pdf"
NARROW = {"width": 320, "height": 640}
WIDE = {"width": 1280, "height": 900}
STATUS_WORDS = set(FIX_STATUS_LABELS.values())  # "Needs review", "Rejected: original kept", ...


def wide_equation() -> str:
    """a_1 + a_2 + ... + a_40: far wider than a phone screen."""
    terms = "<mo>+</mo>".join(f"<msub><mi>a</mi><mn>{i}</mn></msub>" for i in range(1, 41))
    return f"<math><mrow>{terms}</mrow></math>"


def review_output() -> ConversionOutput:
    output = rich_output()
    fix = output.result.fix_for("p1-e1")
    assert fix is not None and fix.equation is not None
    fix.equation.mathml = wide_equation()
    return output


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    """The app served by uvicorn on a free port, converting every upload into the rich
    document. Only the HTML assembler is stubbed; review edits go through the real
    validators and speech."""
    import uvicorn

    from a11y_stem.pipeline import assemble

    patch = pytest.MonkeyPatch()
    patch.setattr(assemble, "render_html", lambda **kw: "<html><body>re-rendered</body></html>")
    env = WebEnv(tmp_path_factory.mktemp("review-browser"), per_user_daily_page_quota=1000)
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
    patch.undo()


@pytest.fixture
def job(site, browser) -> str:
    """A finished job of the rich document, uploaded under LONG_NAME. A fresh one per test,
    because the tests change its review state."""
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": LONG_NAME, "mimeType": "application/pdf", "buffer": make_pdf(1)}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    assert site.run_worker() == job_id
    return job_id


def open_page(browser, site, *, viewport=WIDE, javascript: bool = True):
    # bypass_csp only so axe can be injected; test_web_browser.py checks the CSP itself.
    context = browser.new_context(
        bypass_csp=True, viewport=viewport, java_script_enabled=javascript
    )
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    return context, page


def review_url(site, job: str) -> str:
    return f"{site.base}/jobs/{job}/review"


def unfold_everything(page) -> None:
    page.evaluate("document.querySelectorAll('details').forEach(d => { d.open = true; })")


def sideways_overflow(page) -> int:
    """How many CSS px the page is wider than the viewport (0 when it reflows)."""
    return page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


def walk_states(page, site, job: str, check) -> None:
    """Put the review page through every state a reviewer can reach, calling
    `check(state_name)` in each. Edits use the real validators."""
    url = review_url(site, job)
    page.goto(url)
    check("needs review, failed, not converted, decided folded")
    unfold_everything(page)
    check("decided unfolded")

    page.get_by_label("LaTeX for equation 3").fill("\\frac{a}{b}")
    page.get_by_role("button", name="Save edit to equation 3").click()
    page.wait_for_url(f"{url}#item-*")
    assert "Your edit passed the required checks" in page.locator("#review-progress").inner_text()
    check("just edited")

    page.get_by_role("button", name="Reject figure 1", exact=True).click()
    page.wait_for_url(f"{url}#item-*")
    check("just rejected")

    page.get_by_label("LaTeX for equation 1").fill("\\input{secret}")
    page.get_by_role("button", name="Save edit to equation 1").click()
    assert page.locator(".error-summary").is_visible()
    assert page.title().startswith("Error: ")
    check("refused edit")

    page.goto(url)
    page.get_by_role("button", name="Accept all 3 remaining headings").click()
    page.wait_for_url(f"{url}#group-headings")
    page.get_by_label("Accept every equation proposal at or above").select_option("60")
    page.get_by_role("button", name="Accept these equations").click()
    page.wait_for_url(f"{url}#group-equations")
    check("after bulk accept")
    unfold_everything(page)
    check("after bulk accept, unfolded")


# --------------------------------------------------------------------------- axe


@pytest.mark.parametrize("viewport", [WIDE, NARROW], ids=["1280px", "320px"])
def test_every_review_state_has_no_serious_or_critical_axe_violations(
    site, browser, job, axe_source, viewport
):
    context, page = open_page(browser, site, viewport=viewport)
    found: dict[str, list[dict]] = {}

    def check(state: str) -> None:
        found[state] = axe_violations(page, axe_source)

    walk_states(page, site, job, check)
    context.close()
    assert len(found) == 7
    failures = {state: describe(v) for state, v in found.items() if v}
    assert not failures, "\n".join(f"[{s}]\n{d}" for s, d in failures.items())


def test_job_page_breakdown_has_no_serious_or_critical_axe_violations(
    site, browser, job, axe_source
):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}")
    assert page.get_by_role("link", name="3 equations").is_visible()
    violations = axe_violations(page, axe_source)
    context.close()
    assert not violations, describe(violations)


# --------------------------------------------------------------------------- reflow


def test_no_sideways_scrolling_at_320_css_px(site, browser, job):
    context, page = open_page(browser, site, viewport=NARROW)
    widths: dict[str, int] = {}
    page.goto(f"{site.base}/jobs/{job}")
    widths["job page"] = sideways_overflow(page)

    def check(state: str) -> None:
        widths[state] = sideways_overflow(page)

    walk_states(page, site, job, check)
    assert widths == dict.fromkeys(widths, 0), widths

    # The long file name really does need the wrapping: without it, the page scrolls.
    page.goto(f"{site.base}/jobs/{job}")
    page.add_style_tag(content="h1, .lede { overflow-wrap: normal !important; }")
    assert sideways_overflow(page) > 300
    # The wide equation scrolls inside its own box instead of widening the page. (It was
    # accepted in bulk above, so it is folded away: unfold it.)
    page.goto(review_url(site, job))
    unfold_everything(page)
    preview = page.locator("#fix-p1-e1 .mathml-preview")
    assert preview.evaluate("e => e.scrollWidth > e.clientWidth")
    assert sideways_overflow(page) == 0
    context.close()


# --------------------------------------------------------------------------- names


def test_every_button_names_the_fix_or_group_it_acts_on(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(review_url(site, job))
    unfold_everything(page)
    names = re.findall(r'- button "([^"]+)"', page.locator("main").aria_snapshot())
    context.close()
    duplicates = [name for name, n in Counter(names).items() if n > 1]
    assert not duplicates, duplicates
    for item in ("equation 1", "equation 2", "figure 1", "heading 2", "heading 5"):
        assert f"Save edit to {item}" in names and f"Reject {item}" in names
    assert "Accept equation 1" in names
    assert "Accept equation 2" not in names  # failed: nothing to accept
    assert {"Accept all 3 remaining headings", "Accept these equations"} <= set(names)
    assert not {"Accept", "Reject", "Save edit"} & set(names)  # never a bare verb


def test_every_control_has_a_visible_label(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(review_url(site, job))
    for label in (
        "LaTeX for equation 1",
        "Alt text for figure 1",
        "Decorative: it adds nothing a reader needs, so it gets no alt text",
        "Level for heading 2",
        "Accept every equation proposal at or above",
        "Accept every figure proposal at or above",
    ):
        control = page.get_by_label(label).first
        assert control.is_visible(), label
    controls, unlabelled = page.evaluate(
        """() => {
          const all = [...document.querySelectorAll(
            'main input:not([type=hidden]), main select, main textarea')];
          return [all.length, all.filter(c => c.labels.length === 0).length];
        }"""
    )
    context.close()
    # 6 LaTeX boxes, 4 alt text boxes, 4 "decorative" boxes, 5 heading levels and one
    # bulk threshold per group.
    assert (controls, unlabelled) == (22, 0)


def test_status_is_written_in_words_not_only_colour(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(review_url(site, job))
    unfold_everything(page)
    cards = page.locator("article.fix-card")
    assert cards.count() == 15
    words = [
        cards.nth(i).locator(".status-badge").inner_text().strip() for i in range(cards.count())
    ]
    context.close()
    assert set(words) <= STATUS_WORDS and len(set(words)) >= 5, words


# --------------------------------------------------------------------------- flow


def test_a_decision_lands_on_the_next_fix_with_a_link_back(site, browser, job):
    context, page = open_page(browser, site)
    url = review_url(site, job)
    page.goto(url)
    page.get_by_role("button", name="Accept equation 1", exact=True).click()
    page.wait_for_url(f"{url}#item-p2-e1")
    # The landing item has focus, so the next Tab reaches the link back to the change.
    assert page.evaluate("document.activeElement.id") == "item-p2-e1"
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.textContent") == "Equation 1 on page 1"
    page.keyboard.press("Enter")
    page.wait_for_url(f"{url}#item-p1-e1")
    changed = page.locator("#item-p1-e1")
    assert changed.is_visible() and "Just changed: Accepted." in changed.inner_text()
    context.close()


def test_bulk_accept_and_decisions_work_without_javascript(site, browser, job):
    context, page = open_page(browser, site, javascript=False)
    url = review_url(site, job)
    page.goto(url)
    page.get_by_role("button", name="Accept all 3 remaining headings").click()
    assert page.url == f"{url}#group-headings"
    group = page.locator("#group-headings")
    assert "Accepted 3 headings." in group.inner_text()
    assert group.get_by_role("button", name=re.compile("^Accept all")).count() == 0
    page.locator("#fix-p3-e1").get_by_role("button", name="Reject figure 2").click()
    assert page.url == f"{url}#item-p3-e2"
    context.close()
