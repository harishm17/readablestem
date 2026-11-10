"""The review edits in a real browser (Playwright Chromium): T3-5 (the page tools: change the
words, make a line a heading, add a picture or an equation the tool missed), T3-7 (notices
that name their link and open their place), T3-11 (clean tables, a grid that is not a table, a
maths cell read) and T3-16 (describe it in words, a page left out).

- axe-core with the WCAG 2.2 AA and best-practice rules, failing on moderate and above, at
  1280 and 375 CSS px, in every state the new controls have (open, refused, confirming);
- the page never scrolls sideways at 375 CSS px, also with the WCAG 1.4.12 text-spacing override;
- everything works from the keyboard alone, and without JavaScript;
- the added picture is in the downloaded file; the HTML is not made on the click.

Run with: uv run pytest -m browser tests/test_web_edit_browser.py
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator

import pytest

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv
from readablestem.core.types import ConversionOutput, Link, Notice
from readablestem.models.fake import FakeVisionClient
from test_web_browser import WCAG_TAGS, describe, free_port, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review_browser import sideways_overflow, unfold_everything

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

DESKTOP = {"width": 1280, "height": 900}
NARROW = {"width": 375, "height": 700}
STRICT = {"moderate", "serious", "critical"}
PARAGRAPH = "p1-e4"
VIEWPORTS = [pytest.param(DESKTOP, id="1280px"), pytest.param(NARROW, id="375px")]


def doc_output() -> ConversionOutput:
    """The review document with a link that says nothing, and its notice."""
    output = review_output()
    paragraph = next(e for e in output.result.elements if e.id == PARAGRAPH)
    paragraph.text = "Open the simulation here to practise vectors and scalars."
    start = paragraph.text.index("here")
    paragraph.links = [Link(kind="uri", start=start, end=start + 4, href="https://example.org/s")]
    output.result.notices.append(Notice(code="link_text_unclear", page=0, element_id=PARAGRAPH))
    return output


def serve(env: WebEnv) -> Callable[[], None]:
    import uvicorn

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

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=10)

    return stop


def model(env: WebEnv) -> None:
    env.ctx.client_factory = lambda settings, budget: FakeVisionClient(
        budget=budget,
        defaults={"equation": {"is_equation": True, "latex": "x^2 + 1", "confidence": 0.99}},
    )


@pytest.fixture(scope="module")
def doc_site(tmp_path_factory) -> Iterator[WebEnv]:
    env = WebEnv(tmp_path_factory.mktemp("edit-browser"), per_user_daily_page_quota=1000)
    env.converter.output = doc_output()
    model(env)
    stop = serve(env)
    yield env
    stop()


def new_job(site: WebEnv, browser, pdf: bytes) -> str:
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": "notes.pdf", "mimeType": "application/pdf", "buffer": pdf}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    assert site.run_worker() == job_id
    return job_id


@pytest.fixture
def doc_job(doc_site, browser) -> str:
    return new_job(doc_site, browser, review_pdf())


def open_page(browser, site, *, viewport=DESKTOP, javascript: bool = True):
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


def checker(page, axe_source: str, viewport: dict):
    """`check(name)`: axe clean, and no sideways scrolling, in the state the page is in."""

    def check(name: str) -> None:
        page.wait_for_load_state("networkidle")
        unfold_everything(page)
        violations = strict_axe(page, axe_source)
        assert not violations, f"{name} at {viewport['width']}px:\n{describe(violations)}"
        wide = sideways_overflow(page)
        assert wide <= 0, f"{name}: {wide} px wider than a {viewport['width']} px screen"

    return check


# --------------------------------------------------------------------------- accessibility


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_the_page_tools_pass_axe_in_every_state(doc_site, browser, doc_job, axe_source, viewport):
    context, page = open_page(browser, doc_site, viewport=viewport)
    check = checker(page, axe_source, viewport)
    url = f"{doc_site.base}/jobs/{doc_job}/review"
    page.goto(f"{url}?page=1&tools=1")
    check("the tools open: text list, add, leave out")
    page.goto(f"{url}?page=1&tools=1&change=p1-e1")
    check("a paragraph open to change")
    page.goto(f"{url}?page=1&tools=1&change={PARAGRAPH}")
    check("a paragraph that can be a heading")
    page.goto(f"{url}?page=1&tools=1&change=p1-e0")
    check("a heading open to change")
    page.goto(f"{url}?page=1&tools=1&add=picture&left=10&top=55&right=50&bottom=70")
    check("a picture: the region cut out, alt text to write")
    page.goto(f"{url}?page=1&tools=1&add=equation&left=10&top=55&right=50&bottom=70")
    check("an equation: the region cut out")
    page.goto(f"{url}?page=1&tools=1&add=picture&left=60&top=55&right=50&bottom=70")
    check("a region that is not one")
    context.close()


# --------------------------------------------------------------------------- the keyboard


def test_the_words_of_a_paragraph_are_changed_from_the_keyboard_alone(doc_site, browser, doc_job):
    context, page = open_page(browser, doc_site)
    page.goto(f"{doc_site.base}/jobs/{doc_job}/review?page=1&tools=1")
    link = page.get_by_role("link", name=re.compile("Open the simulation"))
    link.focus()
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    box = page.get_by_label(re.compile("^Words of this"))
    box.focus()
    page.keyboard.press("Control+a")
    page.keyboard.type("Open the simulation to practise vectors, scalars and tensors.")
    page.keyboard.press("Tab")  # to the button that saves it
    assert page.evaluate("document.activeElement.textContent").strip().startswith("Save the words")
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    assert "The text was changed." in page.inner_text("#decision-notice")
    # The notice has focus's heading; the changed words are in the student file.
    page.goto(f"{doc_site.base}/jobs/{doc_job}/view")
    assert "tensors" in page.content()
    context.close()


# --------------------------------------------------------------------------- no JavaScript
