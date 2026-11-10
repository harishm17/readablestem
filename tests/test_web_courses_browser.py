"""Courses and course notation in a real browser (Playwright Chromium), with
FEATURE_NOTATION_MEMORY on:

- axe-core with the WCAG 2.2 AA and best-practice rules, failing on moderate and above,
  on every courses page (empty, with entries, an entry's edit page, the delete
  question, each form with an error), the upload form with its course choice, the job
  page naming the course, and the review screen with the course line and the
  "remember" offer;
- no sideways scrolling at 320 CSS px, also with the WCAG 1.4.12 text-spacing override;
- the whole flow works without JavaScript: add a course and a symbol, convert for it,
  use bold omega on the review screen and remember it for the course.

Run with: uv run pytest -m browser tests/test_web_courses_browser.py
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv
from test_web_browser import WCAG_TAGS, free_port, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

STRICT = {"moderate", "serious", "critical"}
LONG_COURSE = "Physics_1_Mechanics_and_Waves_Fall_Term_Section_Two_Teaching_Assistants_Copy"


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    import uvicorn

    env = WebEnv(
        tmp_path_factory.mktemp("courses-browser"),
        per_user_daily_page_quota=1000,
        feature_notation_memory=True,
    )
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


def open_page(browser, site, *, javascript: bool = True, viewport=None):
    options = {"viewport": viewport} if viewport else {}
    context = browser.new_context(bypass_csp=True, java_script_enabled=javascript, **options)
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


def add_course(page, base: str, name: str) -> str:
    page.goto(f"{base}/courses")
    page.get_by_label("Course name").fill(name)
    page.get_by_role("button", name="Add course").click()
    page.wait_for_url(f"{base}/courses/*")
    return page.url.rsplit("/", 1)[-1]


def add_symbol(page, symbol: str, meaning: str = "A vector", note: str = "") -> None:
    page.get_by_label("Symbol", exact=True).fill(symbol)
    page.get_by_label("What it means").select_option(label=meaning)
    if note:
        page.get_by_label("Note (optional)").fill(note)
    page.get_by_role("button", name="Add symbol").click()
    page.wait_for_load_state()


def convert_for(page, site, course_name: str) -> str:
    page.goto(f"{site.base}/app")
    page.locator("#pdf").set_input_files(
        files=[{"name": "vectors.pdf", "mimeType": "application/pdf", "buffer": review_pdf()}]
    )
    page.locator("#group-convert > summary").click()  # the groups are one line until opened
    page.get_by_label("Course (optional)").select_option(label=course_name)
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    assert site.run_worker() == job_id
    return job_id


@pytest.fixture(scope="module")
def course(site, browser) -> dict[str, str]:
    """A course with two symbols and a finished job for it."""
    context, page = open_page(browser, site)
    course_id = add_course(page, site.base, LONG_COURSE)
    add_symbol(page, "bold d", note="displacement")
    add_symbol(page, r"\mathbb{R}", "A set", "the real numbers")
    entry = page.get_by_role("link", name="Edit or remove bold d").get_attribute("href")
    job_id = convert_for(page, site, LONG_COURSE)
    context.close()
    assert entry is not None
    return {"id": course_id, "entry": entry, "job": job_id}


def states(page, site, course: dict[str, str]):
    """Every page and state to check, as (name, action) pairs run in order."""
    base = site.base
    yield "courses list", lambda: page.goto(f"{base}/courses")
    yield "course page", lambda: page.goto(f"{base}/courses/{course['id']}")
    yield "entry edit", lambda: page.goto(base + course["entry"])
    yield "delete question", lambda: page.goto(f"{base}/courses/{course['id']}/delete")
    yield "upload form", lambda: page.goto(f"{base}/app")
    yield "job page", lambda: page.goto(f"{base}/jobs/{course['job']}")
    yield "review card", lambda: page.goto(f"{base}/jobs/{course['job']}/review")

    def empty_name():
        page.goto(f"{base}/courses")
        page.get_by_role("button", name="Add course").click()
        page.locator(".error-summary").wait_for()

    yield "course name missing", empty_name

    def bad_symbol():
        page.goto(f"{base}/courses/{course['id']}")
        page.get_by_label("Symbol", exact=True).fill("the speed thing")
        page.get_by_role("button", name="Add symbol").click()
        page.locator(".error-summary").wait_for()

    yield "symbol not understood", bad_symbol


def test_without_javascript_a_course_symbol_and_a_remembered_letter(site, browser, axe_source):
    context, page = open_page(browser, site, javascript=False)
    add_course(page, site.base, "No script course")
    add_symbol(page, "bold v")
    assert page.get_by_role("row", name="bold v").is_visible()
    job_id = convert_for(page, site, "No script course")
    page.goto(f"{site.base}/jobs/{job_id}/review")
    # Decision 1 is a bold omega read plain: the one-click fix, then the offer.
    page.get_by_role("button", name="Use bold ω in equation 1").click()
    remember = page.get_by_role("button", name="Remember for No script course: bold ω is a vector")
    remember.wait_for()
    remember.click()
    page.wait_for_url(f"{site.base}/jobs/{job_id}/review?item=*")
    assert (
        "Remembered for No script course: bold ω is a vector."
        in page.locator("#decision-notice").inner_text()
    )
    page.goto(f"{site.base}/courses")
    page.get_by_role("link", name="No script course", exact=True).click()
    row = page.get_by_role("row", name="bold ω")
    assert "From a review" in row.inner_text()
    context.close()


# --------------------------------------------------------------------------- suggestions (T3-12)


def with_notation(output):
    """The review document with notation in the equation that `v = d / t` was."""
    for fix in output.result.fixes:
        if fix.equation is not None and fix.equation.latex == "v = d / t":
            fix.equation.latex = r"\mathbf{d} \in \mathbb{R}^3, \ \vec{v} = \hat{x}"
    return output


# --------------------------------------------------------------------------- the record (T3-6)
