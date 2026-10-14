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

from a11y_stem.core.types import ConversionOutput, Link, Notice
from a11y_stem.models.fake import FakeVisionClient
from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv
from fixtures.web.table_doc import table_pdf
from test_web_browser import WCAG_TAGS, describe, free_port, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_edit_tables import clean_output
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


@pytest.fixture(scope="module")
def table_site(tmp_path_factory) -> Iterator[WebEnv]:
    env = WebEnv(tmp_path_factory.mktemp("edit-tables-browser"), per_user_daily_page_quota=1000)
    env.converter.output = clean_output()
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


@pytest.fixture
def table_job(table_site, browser) -> str:
    return new_job(table_site, browser, table_pdf())


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


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_refused_edits_pass_axe(doc_site, browser, doc_job, axe_source, viewport):
    context, page = open_page(browser, doc_site, viewport=viewport)
    check = checker(page, axe_source, viewport)
    url = f"{doc_site.base}/jobs/{doc_job}/review?page=1&tools=1&change=p1-e1"
    page.goto(url)
    page.get_by_label(re.compile("^Words of this")).fill("Nothing about the equation here.")
    page.get_by_role("button", name=re.compile("^Save the words")).click()
    page.locator(".error-summary").wait_for()
    check("text that left an equation out")
    page.goto(f"{doc_site.base}/jobs/{doc_job}/review?page=1&tools=1&change={PARAGRAPH}")
    page.get_by_label("Make this a heading, at level").select_option("4")
    page.get_by_role("button", name=re.compile("^Make it a heading")).click()
    page.locator(".error-summary").wait_for()
    check("a level that skips")
    page.goto(
        f"{doc_site.base}/jobs/{doc_job}/review?page=1&tools=1&add=picture&left=10&top=55&right=50&bottom=70"
    )
    page.get_by_role("button", name="Add the picture").click()
    page.locator(".error-summary").wait_for()
    check("a picture without alt text")
    context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_notices_and_a_left_out_page_pass_axe(doc_site, browser, doc_job, axe_source, viewport):
    context, page = open_page(browser, doc_site, viewport=viewport)
    check = checker(page, axe_source, viewport)
    base = f"{doc_site.base}/jobs/{doc_job}"
    page.goto(f"{base}")
    check("the job page with notices that name their link")
    page.goto(f"{base}/review?page=1&note=link_text_unclear:{PARAGRAPH}")
    check("the panel of a notice about a link")
    page.goto(f"{base}/review/list")
    check("the list view with notes and the page changes")
    page.goto(f"{base}/review?page=2&tools=1")
    page.get_by_role("button", name="Leave page 2 out").click()
    page.wait_for_url(re.compile(r"tools=1"))
    check("a page left out")
    context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_describing_and_the_new_table_controls_pass_axe(
    table_site, browser, table_job, axe_source, viewport
):
    context, page = open_page(browser, table_site, viewport=viewport)
    check = checker(page, axe_source, viewport)
    base = f"{table_site.base}/jobs/{table_job}/review"
    page.goto(base)
    check("a table decision with 'accept all clean tables'")
    assert "Accept all 2 clean tables" in page.content()
    page.goto(f"{base}?item=p1-e3")
    check("a table kept as an image: describe it, not a table")
    page.goto(f"{base}?item=p1-e1&cell=r3c1")
    check("a maths cell: read it with the model")
    page.goto(f"{base}/list")
    check("the list view: clean tables, describe, not a table")
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


def test_a_paragraph_becomes_a_heading_and_an_undo_takes_it_back(doc_site, browser, doc_job):
    context, page = open_page(browser, doc_site)
    page.goto(f"{doc_site.base}/jobs/{doc_job}/review?page=1&tools=1&change={PARAGRAPH}")
    page.get_by_label("Make this a heading, at level").select_option("2")
    page.get_by_role("button", name=re.compile("^Make it a heading")).click()
    page.wait_for_load_state("networkidle")
    assert "It is now a heading, level 2." in page.inner_text("#decision-notice")
    page.get_by_role("button", name=re.compile("^Undo")).click()
    page.wait_for_load_state("networkidle")
    assert "Undone" in page.inner_text("#decision-notice")
    context.close()


def test_a_picture_is_added_and_is_in_the_file(doc_site, browser, doc_job):
    context, page = open_page(browser, doc_site)
    page.goto(f"{doc_site.base}/jobs/{doc_job}/review?page=1&tools=1")
    page.get_by_label("Left edge").fill("10")
    page.get_by_label("Top edge").fill("55")
    page.get_by_label("Right edge").fill("50")
    page.get_by_label("Bottom edge").fill("70")
    page.get_by_role("button", name="Show the part I described").click()
    page.wait_for_load_state("networkidle")
    image = page.locator(".region-preview img")
    image.wait_for()
    assert image.evaluate("img => img.naturalWidth") > 0  # the crop was drawn
    page.get_by_label("Alt text for this picture").fill("A cart rolling down a short ramp.")
    page.get_by_role("button", name="Add the picture").click()
    page.wait_for_load_state("networkidle")
    assert "The picture was added" in page.inner_text("#decision-notice")
    file = page.request.get(f"{doc_site.base}/jobs/{doc_job}/view").text()
    assert 'alt="A cart rolling down a short ramp."' in file
    context.close()


def test_choosing_a_notice_from_the_job_page_opens_its_place(doc_site, browser, doc_job):
    context, page = open_page(browser, doc_site)
    page.goto(f"{doc_site.base}/jobs/{doc_job}")
    page.get_by_role("link", name=re.compile("^See it on the page")).first.click()
    page.wait_for_load_state("networkidle")
    assert "Worth checking" in page.inner_text("#decision-title")
    assert "here" in page.inner_text("#decision")
    context.close()


# --------------------------------------------------------------------------- no JavaScript


def test_everything_new_works_without_javascript(doc_site, browser, doc_job):
    context, page = open_page(browser, doc_site, javascript=False)
    base = f"{doc_site.base}/jobs/{doc_job}/review"
    page.goto(f"{base}?page=1&tools=1&change={PARAGRAPH}")
    page.get_by_label(re.compile("^Words of this")).fill("Plain words, no script.")
    page.get_by_role("button", name=re.compile("^Save the words")).click()
    assert "The text was changed." in page.inner_text("#decision-notice")
    page.goto(f"{base}?page=2&tools=1")
    page.get_by_role("button", name="Leave page 2 out").click()
    assert "left out" in page.inner_text("#decision-notice")
    page.get_by_role("button", name="Put page 2 back").click()
    assert "back in the student file" in page.inner_text("#decision-notice")
    context.close()
