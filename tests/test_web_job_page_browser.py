"""The job page in a real browser (Playwright Chromium): every state passes axe (WCAG 2.2
AA and best practice, moderate and above) at 1280 and 320 CSS px, reflows at 320 px with
the text-spacing override, updates its steps with htmx without touching the live region,
and works without JavaScript (the draft question, the language control).

Run with: uv run pytest -m browser tests/test_web_job_page_browser.py
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from datetime import timedelta

import pytest

from a11y_stem.core.types import ConversionOutput, Notice, SourceFacts, Status
from a11y_stem.web.db import Job, JobStatus, session_scope, utcnow
from fixtures.web.support import ALICE, PNG, WebEnv, fake_output, make_pdf
from test_web_browser import TEXT_SPACING, WCAG_TAGS, describe, free_port, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

WIDE = {"width": 1280, "height": 900}
NARROW = {"width": 320, "height": 640}
STRICT = {"moderate", "serious", "critical"}
LONG_NAME = "Physics_Unit_2_Vectors_and_Scalars_Teacher_Edition_With_Worked_Examples_FINAL.pdf"


def card_output() -> ConversionOutput:
    """The fake result with what the job page shows from a real one: source facts, a
    notice of each kind, an equation whose text layer loses its power, a detected
    language."""
    output = fake_output()
    result = output.result
    result.elements[3].text = "x2 + 1 = 0 and a long run of maths to wrap: " + "abcdefgh" * 12
    result.elements[3].reasons = ["superscript"]
    result.source_facts = SourceFacts(
        tagged=False, images=1, equations=3, equations_flattened=1, bold_maths_letters=2, links=1
    )
    result.lang_source = "detected"
    result.notices = [
        Notice(code="lang_detected"),
        Notice(code="footnote", page=0, count=2, review="none"),
        Notice(code="colour_only", page=0, element_id="p1-e4"),
    ]
    output.crops["p1-e3"] = PNG
    return output


def ready_output() -> ConversionOutput:
    output = card_output()
    for fix in output.result.fixes:
        if fix.pending:
            fix.status = Status.REJECTED if fix.status is Status.ABSTAINED else Status.ACCEPTED
            fix.reviewed = True
    return output


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    import uvicorn

    from a11y_stem.pipeline import assemble

    patch = pytest.MonkeyPatch()
    # The language control re-renders the file; the fake result's file is a stub.
    patch.setattr(assemble, "render_html", lambda **kw: f'<html lang="{kw["lang"]}"></html>')
    env = WebEnv(tmp_path_factory.mktemp("job-browser"), per_user_daily_page_quota=1000)
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


def upload(site, browser, *, javascript: bool = True) -> str:
    context = browser.new_context(java_script_enabled=javascript)
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
    return job_id


def finished(site, browser, output: ConversionOutput) -> str:
    site.converter.output = output
    job_id = upload(site, browser)
    assert site.run_worker() == job_id
    return job_id


def running(site, browser) -> str:
    job_id = upload(site, browser)
    set_progress(site, job_id, equations=(14, 21))
    return job_id


def set_progress(site, job_id: str, **stages) -> None:
    progress = {
        "stages": {
            "layout": {"stage": "layout", "done": 1, "total": 1,
                       "counts": {"equation": 21, "figure": 7, "heading": 52}},
            **{
                name: {"stage": name, "done": done, "total": total, "counts": {}}
                for name, (done, total) in stages.items()
            },
        }
    }  # fmt: skip
    with session_scope(site.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.status = JobStatus.RUNNING
        job.started_at = utcnow()
        job.lease_until = utcnow() + timedelta(minutes=5)
        job.attempts = 1
        job.progress_json = progress


def finish(site, job_id: str) -> None:
    with session_scope(site.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.status = JobStatus.QUEUED
        job.progress_json = None
    assert site.run_worker() == job_id


@pytest.fixture(scope="module")
def jobs(site, browser) -> dict[str, str]:
    """One job per state of the page."""
    return {
        "decisions": finished(site, browser, card_output()),
        "ready": finished(site, browser, ready_output()),
        "running": running(site, browser),
    }


def open_page(browser, site, *, viewport=WIDE, javascript: bool = True):
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


PAGES = [
    ("decisions", "/jobs/{id}"),
    ("decisions", "/jobs/{id}/draft"),
    ("ready", "/jobs/{id}"),
    ("running", "/jobs/{id}"),
]


@pytest.mark.parametrize("viewport", [WIDE, NARROW], ids=["1280", "320"])
@pytest.mark.parametrize(("state", "path"), PAGES, ids=[f"{s}{p[9:]}" for s, p in PAGES])
def test_every_state_passes_axe(site, browser, jobs, axe_source, state, path, viewport):
    context, page = open_page(browser, site, viewport=viewport)
    response = page.goto(site.base + path.replace("{id}", jobs[state]))
    assert response is not None and response.status == 200
    violations = strict_axe(page, axe_source)
    context.close()
    assert not violations, describe(violations)


@pytest.mark.parametrize("spacing", [False, True], ids=["plain", "text-spacing"])
@pytest.mark.parametrize(("state", "path"), PAGES, ids=[f"{s}{p[9:]}" for s, p in PAGES])
def test_every_state_reflows_at_320_css_px(site, browser, jobs, state, path, spacing):
    """WCAG 1.4.10 and 1.4.12: no sideways scrolling of the page (the compare table may
    scroll inside its own box)."""
    context, page = open_page(browser, site, viewport=NARROW)
    page.goto(site.base + path.replace("{id}", jobs[state]))
    if spacing:
        page.add_style_tag(content=TEXT_SPACING)
    width = page.evaluate(
        "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    context.close()
    assert width[0] <= width[1], f"{state} {path}: {width[0]} px wide in a {width[1]} px view"


def test_the_steps_update_without_touching_the_live_region(site, browser, jobs):
    job_id = upload(site, browser)
    set_progress(site, job_id, equations=(1, 21))
    context = browser.new_context(viewport=WIDE)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/jobs/{job_id}")
    region = page.locator("#job-status-region")
    assert region.get_attribute("role") == "status"
    before = region.inner_text()
    assert "Reading the equations" in before
    page.evaluate("document.querySelector('#job-status').dataset.mark = 'old'")
    set_progress(site, job_id, equations=(9, 21))
    page.locator("#job-progress").get_by_text("Equations: 9 of 21").wait_for(timeout=15000)
    # Same step: the status box was not swapped (a screen reader hears nothing new).
    assert page.evaluate("document.querySelector('#job-status').dataset.mark") == "old"
    assert region.inner_text() == before
    site.converter.output = card_output()  # 3 decisions open
    finish(site, job_id)
    page.locator("#job-status").get_by_role("link", name="Start the review").wait_for(timeout=15000)
    assert page.title() == f"Converted: {LONG_NAME} | a11y-stem"
    assert page.locator("#job-progress li").count() == 0
    context.close()


def test_the_draft_and_the_language_work_without_javascript(site, browser, jobs):
    site.converter.output = card_output()
    job_id = upload(site, browser, javascript=False)
    assert site.run_worker() == job_id
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/jobs/{job_id}")
    page.get_by_role("link", name="Download a draft (3 decisions open)").click()
    assert page.get_by_role("heading", level=1).inner_text().startswith("Download a draft of")
    assert page.get_by_role("link", name="Download the draft anyway").is_visible()
    page.get_by_role("link", name="Review 3 decisions first").click()
    assert "/review" in page.url
    page.goto(f"{site.base}/jobs/{job_id}")
    page.get_by_label("Language of this document").select_option("de")
    page.get_by_role("button", name="Confirm this language").click()
    assert "The language is now German." in page.locator(".notice").first.inner_text()
    assert "German (you chose it)" in page.locator("#language-heading + p").inner_text()
    context.close()


def test_the_finish_line_downloads_in_one_click(site, browser, jobs):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/jobs/{jobs['ready']}")
    link = page.locator("#ready").get_by_role("link", name="Download the HTML file")
    assert link.get_attribute("href") == f"/jobs/{jobs['ready']}/download"
    with page.expect_download() as download:
        link.click()
    assert download.value.suggested_filename.endswith("-accessible.html")
    context.close()
