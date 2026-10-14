"""The review screen in a real browser (Playwright Chromium).

- axe-core in every state a reviewer can put the page in: fixes needing review, failed
  and not converted, decided ones folded away and unfolded, just edited, just rejected,
  after more decisions, and a refused edit (zero serious or critical violations);
- every control has a label and every button says which fix or group it acts on;
- status is written in words, not shown by colour alone;
- no sideways scrolling at 320 CSS px (WCAG 1.4.10), even with a file name that has no
  break points and an equation wider than the screen;
- at 320 CSS px, an error summary link shows the field with its label (app.js);
- decisions work without JavaScript and land where the reviewer expects; there is no
  bulk accept.

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

from fixtures.web.support import ALICE, WebEnv, make_pdf
from readablestem.core.types import ConversionOutput
from readablestem.web.routes.review import (
    BOLD_TEXT_WORDS,
    OTHER_WORDS,
    PLAIN_TEXT_WORDS,
    REJECT_WORDS,
)
from readablestem.web.views import FIX_STATUS_LABELS, REVIEWER_ACCEPTED_LABEL
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
# "Needs review", "Kept as an image by you", ..., "Accepted by you"
REJECTED = [*REJECT_WORDS.values(), BOLD_TEXT_WORDS, PLAIN_TEXT_WORDS, OTHER_WORDS]
STATUS_WORDS = {*FIX_STATUS_LABELS.values(), REVIEWER_ACCEPTED_LABEL, *(w.state for w in REJECTED)}


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

    from readablestem.pipeline import assemble

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
    return f"{site.base}/jobs/{job}/review/list"


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

    page.get_by_role("button", name="Keep without a description (figure 1)", exact=True).click()
    page.wait_for_url(f"{url}#item-*")
    check("just rejected")

    page.get_by_label("LaTeX for equation 1").fill("\\input{secret}")
    page.get_by_role("button", name="Save edit to equation 1").click()
    assert page.locator(".error-summary").is_visible()
    assert page.title().startswith("Error: ")
    check("refused edit")

    page.goto(url)
    page.get_by_role("button", name="Accept equation 1", exact=True).click()
    page.wait_for_url(f"{url}#item-*")
    page.goto(url)
    page.get_by_role("button", name="Accept heading 2", exact=True).click()
    page.wait_for_url(f"{url}#item-*")
    check("after more decisions")
    unfold_everything(page)
    check("after more decisions, unfolded")


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
    assert page.get_by_role("link", name="4 equations").is_visible()
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
    page.add_style_tag(content="h1, .lede, .doc-file { overflow-wrap: normal !important; }")
    assert sideways_overflow(page) > 300
    # The wide equation scrolls inside its own box instead of widening the page. (It was
    # accepted above, so it is folded away: unfold it.)
    page.goto(review_url(site, job))
    unfold_everything(page)
    preview = page.locator("#fix-p1-e1 .mathml-preview")
    assert preview.evaluate("e => e.scrollWidth > e.clientWidth")
    assert sideways_overflow(page) == 0
    context.close()


ZOOM_400 = {"width": 320, "height": 256}  # 1280 x 1024 at 400%


def follow_summary_link(page, control_id: str) -> dict[str, tuple[float, float]]:
    """Follow the error summary's link to `control_id` from the top of the page; the
    (top, bottom) in the window of the field's label, hint, error message and control."""
    page.evaluate("window.scrollTo(0, 0)")
    page.locator(f'.error-summary a[href="#{control_id}"]').click()
    assert page.evaluate("document.activeElement.id") == control_id
    prefix, element_id = control_id.split("-", 1)
    parts = {}
    for part, selector in (
        ("label", f'label[for="{control_id}"]'),
        ("hint", f"#{prefix}-hint-{element_id}"),
        ("error", f"#{prefix}-error-{element_id}"),
        ("control", f"#{control_id}"),
    ):
        box = page.locator(selector).bounding_box()
        assert box is not None, part
        parts[part] = (box["y"], box["y"] + box["height"])
    return parts


@pytest.mark.parametrize(
    ("viewport", "field_fits"), [(NARROW, True), (ZOOM_400, False)], ids=["320x640", "320x256"]
)
def test_an_error_summary_link_shows_the_field_with_its_label_at_320_css_px(
    site, browser, job, viewport, field_fits
):
    """At 320 CSS px a field's label, hint and error message wrap to about 300 px, more
    than the CSS scroll-margin-top: following a summary link focused the control with
    its label 160 px above the window. app.js scrolls the whole field into view. When
    the field is taller than the window (400% zoom) the focused control stays in view,
    with as much of the field as fits above it.

    Its own context, without bypass_csp: the Content-Security-Policy must allow app.js.
    """
    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    url = review_url(site, job)
    page.goto(url)
    page.get_by_label("Level for heading 2").select_option("6")
    page.get_by_role("button", name="Save edit to heading 2").click()
    fields = {"heading level": follow_summary_link(page, "level-p1-e3")}
    page.goto(url)
    page.get_by_label("LaTeX for equation 1").fill("\\input{secret}")
    page.get_by_role("button", name="Save edit to equation 1").click()
    fields["LaTeX"] = follow_summary_link(page, "latex-p1-e1")
    context.close()

    height = viewport["height"]
    for name, parts in fields.items():
        top, bottom = parts["control"]
        assert top >= 0 and bottom <= height, (name, parts)  # focus is in view
        if field_fits:
            assert all(y0 >= 0 and y1 <= height for y0, y1 in parts.values()), (name, parts)
        else:
            assert parts["label"][0] < 0, (name, parts)  # the field really is too tall
            assert bottom >= height - 16, (name, parts)  # all that fits above the control


# --------------------------------------------------------------------------- names


def test_every_button_names_the_fix_or_group_it_acts_on(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(review_url(site, job))
    unfold_everything(page)
    names = re.findall(r'- button "([^"]+)"', page.locator("main").aria_snapshot())
    context.close()
    duplicates = [name for name, n in Counter(names).items() if n > 1]
    assert not duplicates, duplicates
    # "Reject" is named for what it does (UX review MF14), with the item it acts on.
    rejects = {
        "equation 1": "Keep as image",
        "equation 2": "Keep as image",
        "figure 1": "Keep without a description",
    }
    for item in ("equation 1", "equation 2", "figure 1", "heading 2", "heading 5"):
        reject = rejects.get(item, r"Make (bold|plain) text")
        assert f"Save edit to {item}" in names
        assert [n for n in names if re.fullmatch(rf"{reject} \({item}\)", n)], (item, names)
    assert "Accept equation 1" in names
    assert "Accept equation 2" not in names  # failed: nothing to accept
    assert not [n for n in names if n.startswith(("Accept all", "Accept these"))]  # no bulk
    assert not {"Accept", "Reject", "Save edit"} & set(names)  # never a bare verb
    assert not [n for n in names if n.startswith("Reject")]


def test_every_control_has_a_visible_label(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(review_url(site, job))
    for label in (
        "LaTeX for equation 1",
        "Alt text for figure 1",
        "Decorative: it adds nothing a reader needs, so it gets no alt text",
        "Level for heading 2",
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
    # 6 LaTeX boxes, 4 alt text boxes, 4 "decorative" boxes, 5 heading levels and the
    # "Describe it in words" box of the one equation kept as an image (T3-16).
    assert (controls, unlabelled) == (20, 0)


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


def test_decisions_work_without_javascript(site, browser, job):
    context, page = open_page(browser, site, javascript=False)
    url = review_url(site, job)
    page.goto(url)
    assert page.get_by_role("button", name=re.compile("^Accept (all|these)")).count() == 0
    page.locator("#fix-p3-e1").get_by_role(
        "button", name="Keep without a description (figure 2)"
    ).click()
    assert page.url == f"{url}#item-p3-e2"
    context.close()
