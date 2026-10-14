"""The jobs work in a real browser (Playwright Chromium): "Try again" on a stopped document
and on one converted during an outage, the card of a revised version and its upload form,
the page for choosing which pages of a large file to convert, uploading several files and
the course page with its documents, and the sample document.

axe-core (WCAG 2.2 AA and best practice, moderate and above) runs at 1280 and 375 CSS px
in each state, and each page must not scroll sideways at 320 px. Set A11Y_SHOTS to a
directory to also write a screenshot of each state.

Run with: uv run pytest -m browser tests/test_web_jobs_browser.py
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select

from a11y_stem.core.types import ConversionOutput, Status
from a11y_stem.web.db import Job, PendingUpload, QuotaDay, session_scope
from fixtures.web.support import (
    ALICE,
    WebEnv,
    csrf_from,
    fake_output,
    job_id_from,
    login,
    make_pdf,
    token,
)
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_browser import describe, free_port, sign_in
from test_web_carry import first_output, revised_output
from test_web_review_browser import sideways_overflow
from test_web_review_split_browser import open_page, strict_axe

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

WIDE = {"width": 1280, "height": 900}
PHONE = {"width": 375, "height": 800}
NARROW = {"width": 320, "height": 640}
SIZES = pytest.mark.parametrize("viewport", [WIDE, PHONE], ids=["1280", "375"])


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    import uvicorn

    env = WebEnv(
        tmp_path_factory.mktemp("jobs-browser"),
        per_user_daily_page_quota=1000,
        feature_notation_memory=True,
        max_pages_per_job=5,
        max_large_upload_mb=5,
        max_source_pages=40,
    )
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
    env.api = env.client()  # the same app, in this process: makes the states quickly
    login(env.api, ALICE)
    yield env
    env.api.close()
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(autouse=True)
def clean_slate(site: WebEnv) -> Iterator[None]:
    """Every test starts with nothing: the caps on documents waiting would otherwise fill
    up over the module's tests."""
    from a11y_stem.ops.retention import delete_jobs

    with session_scope(site.ctx.factory) as session:
        delete_jobs(session, list(session.scalars(select(Job.id))))
        session.execute(PendingUpload.__table__.delete())
        session.execute(QuotaDay.__table__.delete())
    yield


def shoot(page, name: str, viewport: dict) -> None:
    folder = os.environ.get("A11Y_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}-{viewport['width']}.png"), full_page=True)


def clean(page, axe_source: str, what: str) -> None:
    violations = strict_axe(page, axe_source)
    assert not violations, f"{what}: {describe(violations)}"


def convert(site: WebEnv, output: ConversionOutput | None = None, **form) -> str:
    """Upload through the app's own routes and run the worker once; returns the job id."""
    site.converter.output = output or fake_output()
    site.converter.raises = None
    response = site.api.post(
        "/jobs",
        data={"csrf_token": token(site.api)} | form,
        files={"pdf": ("notes.pdf", make_pdf(1), "application/pdf")},
    )
    job_id = job_id_from(response)
    site.run_worker()
    return job_id


def outage_output() -> ConversionOutput:
    """The small document converted while the AI service was down: every model item failed."""
    output = fake_output()
    for fix in output.result.fixes:
        if fix.kind.value in ("equation", "figure"):
            fix.status, fix.reason, fix.equation, fix.figure = (
                Status.FAILED,
                "model_api_error",
                None,
                None,
            )
            fix.checks, fix.review = [], "required"
    return output


def look(site: WebEnv, browser, axe_source: str, path: str, viewport: dict, name: str, what: str):
    """Open `path` as the signed-in person and check it with axe; returns (context, page)."""
    context, page = open_page(browser, site, viewport=viewport)
    page.goto(f"{site.base}{path}")
    page.wait_for_load_state("networkidle")
    clean(page, axe_source, what)
    shoot(page, name, viewport)
    return context, page


# --------------------------------------------------------------------------- try again


@SIZES
def test_a_stopped_document_offers_try_again_and_passes_axe(site, browser, axe_source, viewport):
    site.converter.raises = RuntimeError("boom")
    response = site.api.post(
        "/jobs",
        data={"csrf_token": token(site.api)},
        files={"pdf": ("notes.pdf", make_pdf(1), "application/pdf")},
    )
    job_id = job_id_from(response)
    site.run_worker()
    site.converter.raises = None
    context, page = look(
        site, browser, axe_source, f"/jobs/{job_id}", viewport, "retry-failed", "stopped"
    )
    expect(page.get_by_role("button", name="Try again")).to_be_visible()
    assert not sideways_overflow(page)
    context.close()


def test_try_again_works_from_the_keyboard_and_queues_the_document(site, browser):
    site.converter.raises = RuntimeError("boom")
    job_id = job_id_from(
        site.api.post(
            "/jobs",
            data={"csrf_token": token(site.api)},
            files={"pdf": ("notes.pdf", make_pdf(1), "application/pdf")},
        )
    )
    site.run_worker()
    site.converter.raises = None
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job_id}")
    button = page.get_by_role("button", name="Try again")
    button.focus()
    page.keyboard.press("Enter")
    page.wait_for_url(f"{site.base}/jobs/{job_id}")
    expect(page.locator("[role=status]").first).to_contain_text("queued again")
    context.close()
    with session_scope(site.ctx.factory) as session:
        assert session.get(Job, job_id).status in {"queued", "running", "done"}


@SIZES
def test_a_conversion_during_an_outage_says_so_and_offers_the_missing_parts(
    site, browser, axe_source, viewport
):
    job_id = convert(site, outage_output())
    context, page = look(
        site, browser, axe_source, f"/jobs/{job_id}", viewport, "retry-outage", "outage"
    )
    expect(page.get_by_role("button", name="Convert the missing parts")).to_be_visible()
    expect(page.locator("#job-status")).to_contain_text("Nothing is wrong with your PDF")
    assert not sideways_overflow(page)
    context.close()


@SIZES
def test_the_status_page_names_the_ai_service(site, browser, axe_source, viewport):
    from a11y_stem.web.provider_health import HEALTH, MIN_CALLS

    for _ in range(MIN_CALLS):
        HEALTH.record("api_error")
    try:
        context, page = look(
            site, browser, axe_source, "/status", viewport, "status-down", "status"
        )
        expect(page.locator("dl")).to_contain_text("Not answering since")
        context.close()
    finally:
        HEALTH.reset()


# --------------------------------------------------------------------------- revised versions


@SIZES
def test_the_card_of_a_revised_version_and_its_upload_form_pass_axe(
    site, browser, axe_source, viewport
):
    first = convert(site, first_output())
    client = site.api
    client.post(
        f"/jobs/{first}/review/p1-e3",
        data={"csrf_token": token(client), "view": "list", "action": "accept"},
    )
    context, page = look(
        site, browser, axe_source, f"/app?revises={first}", viewport, "revise-form", "revision form"
    )
    expect(page.get_by_role("heading", name="Upload a revised version")).to_be_visible()
    context.close()

    site.converter.output = revised_output()
    second = job_id_from(
        client.post(
            "/jobs",
            data={"csrf_token": token(client), "revises": first},
            files={"pdf": ("notes-v2.pdf", make_pdf(1), "application/pdf")},
        )
    )
    site.run_worker()
    context, page = look(
        site, browser, axe_source, f"/jobs/{second}", viewport, "revise-card", "carry card"
    )
    expect(page.locator("#carry")).to_contain_text("carried over")
    assert not sideways_overflow(page)
    context.close()


# --------------------------------------------------------------------------- several files


def send_many(site: WebEnv, files: list[tuple[str, bytes]], **fields):
    return site.api.post(
        "/jobs",
        data={"csrf_token": token(site.api)} | fields,
        files=[("pdf", (name, data, "application/pdf")) for name, data in files],
    )


@SIZES
def test_the_upload_form_for_several_files_passes_axe(site, browser, axe_source, viewport):
    context, page = look(site, browser, axe_source, "/app", viewport, "upload-form", "upload form")
    expect(page.get_by_label("PDF files")).to_be_visible()
    assert page.get_by_label("PDF files").get_attribute("multiple") is not None
    assert not sideways_overflow(page)
    context.close()


def test_choosing_too_many_files_is_said_at_once_and_nothing_is_sent(site, browser):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/app")
    files = [
        {"name": f"f{n}.pdf", "mimeType": "application/pdf", "buffer": make_pdf(1)}
        for n in range(11)
    ]
    page.get_by_label("PDF files").set_input_files(files=files)
    expect(page.locator("#pdf-check")).to_contain_text("You chose 11 files. Upload 10 or fewer")
    page.get_by_role("button", name="Upload and convert").click()
    expect(page).to_have_url(f"{site.base}/app")
    expect(page.locator("#pdf-check")).to_contain_text("You chose 11 files")
    context.close()


@SIZES
def test_the_results_of_an_upload_of_several_files_pass_axe(site, browser, axe_source, viewport):
    # Written to a page of its own by the browser: upload through the form, not the API.
    context, page = open_page(browser, site, viewport=viewport)
    page.goto(f"{site.base}/app")
    files = [
        {"name": "week1.pdf", "mimeType": "application/pdf", "buffer": make_pdf(1)},
        {"name": "week2.pdf", "mimeType": "application/pdf", "buffer": make_pdf(2)},
        {"name": "scan.pdf", "mimeType": "application/pdf", "buffer": make_pdf(12)},
        {"name": "notes.docx", "mimeType": "application/pdf", "buffer": b"PK not a pdf"},
    ]
    page.get_by_label("PDF files").set_input_files(files=files)
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_selector("h1")
    expect(page.get_by_role("heading", level=1)).to_contain_text("3 of 4 files uploaded")
    expect(page.get_by_role("heading", name="Choose which pages to convert")).to_be_visible()
    expect(page.get_by_role("heading", name="Not uploaded")).to_be_visible()
    clean(page, axe_source, "results of several files")
    shoot(page, "upload-results", viewport)
    assert not sideways_overflow(page)
    context.close()


# --------------------------------------------------------------------------- large files


def keep_big(site: WebEnv, pages: int = 12, name: str = "chapter2.pdf") -> str:
    response = send_many(site, [(name, make_pdf(pages))])
    assert response.status_code == 303, response.text[:200]
    return response.headers["location"].rsplit("/", 1)[-1]


@SIZES
def test_the_page_for_choosing_pages_passes_axe_in_each_state(site, browser, axe_source, viewport):
    upload_id = keep_big(site)
    context, page = look(
        site, browser, axe_source, f"/uploads/{upload_id}", viewport, "choose-pages", "chooser"
    )
    expect(page.get_by_label("Pages for each part")).to_have_value("1-4\n5-8\n9-12")
    assert not sideways_overflow(page)
    # An error: the summary links to the box, which keeps what was typed.
    page.get_by_label("Pages for each part").fill("1-9")
    page.get_by_role("button", name="Convert these parts").click()
    expect(page.locator(".error-summary")).to_be_visible()
    expect(page.get_by_label("Pages for each part")).to_have_value("1-9")
    clean(page, axe_source, "chooser with an error")
    shoot(page, "choose-pages-error", viewport)
    # My documents lists the file waiting, with a link to choose its pages.
    page.goto(f"{site.base}/app")
    expect(page.get_by_role("link", name="Choose the pages of chapter2.pdf")).to_be_visible()
    clean(page, axe_source, "my documents with a large file waiting")
    shoot(page, "app-pending", viewport)
    page.goto(f"{site.base}/uploads/{upload_id}")
    # Discarding asks first.
    page.get_by_role("button", name="Discard this file").click()
    expect(page.get_by_role("heading", name="Discard chapter2.pdf?")).to_be_visible()
    clean(page, axe_source, "discard confirmation")
    shoot(page, "discard-confirm", viewport)
    context.close()


def test_the_parts_are_chosen_and_started_from_the_keyboard(site, browser):
    upload_id = keep_big(site, 8, "keyboard.pdf")
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/uploads/{upload_id}")
    box = page.get_by_label("Pages for each part")
    box.focus()
    page.keyboard.press("Control+A")
    page.keyboard.type("1-4\n5-8")
    page.keyboard.press("Tab")  # the shrink box
    page.keyboard.press("Tab")  # the button
    page.keyboard.press("Enter")
    page.wait_for_url(f"{site.base}/app")
    expect(page.locator("[role=status]").first).to_contain_text("2 parts are converting")
    context.close()


def test_the_page_for_choosing_pages_works_without_javascript(site, browser):
    upload_id = keep_big(site, 8, "nojs.pdf")
    context, page = open_page(browser, site, javascript=False)
    page.goto(f"{site.base}/uploads/{upload_id}")
    page.get_by_role("button", name="Convert these parts").click()
    page.wait_for_url(f"{site.base}/app")
    expect(page.get_by_text("2 parts are converting")).to_be_visible()
    context.close()


@SIZES
def test_a_part_of_a_larger_file_and_its_siblings_pass_axe(site, browser, axe_source, viewport):
    upload_id = keep_big(site, 12, "parts.pdf")
    site.api.post(
        f"/uploads/{upload_id}",
        data={"csrf_token": token(site.api, f"/uploads/{upload_id}"), "parts": "1-4\n5-8"},
    )
    with session_scope(site.ctx.factory) as session:
        ids = list(
            session.scalars(
                select(Job.id).where(Job.part_group == upload_id).order_by(Job.created_at)
            )
        )
    context, page = look(
        site, browser, axe_source, f"/jobs/{ids[1]}", viewport, "part-page", "part"
    )
    expect(page.locator("#job-parts")).to_contain_text("pages 5-8")
    expect(page.get_by_role("navigation", name="Parts of this file")).to_be_visible()
    context.close()


# --------------------------------------------------------------------------- the course page


@SIZES
def test_the_course_page_lists_its_documents_with_download_all(site, browser, axe_source, viewport):
    made = site.api.post(
        "/courses",
        data={
            "csrf_token": csrf_from(site.api.get("/courses").text),
            "name": f"Physics {time.monotonic_ns()}",
        },
    )
    assert made.status_code == 303
    course_id = made.headers["location"].rsplit("/", 1)[-1]
    first = job_id_from(send_many(site, [("a.pdf", make_pdf(1))], course=course_id))
    site.converter.output = fake_output()
    site.run_worker()
    for element_id, action in (("p1-e3", "accept"), ("p1-e4", "accept"), ("p1-e5", "reject")):
        site.api.post(
            f"/jobs/{first}/review/{element_id}",
            data={"csrf_token": token(site.api), "view": "list", "action": action},
        )
    context, page = look(
        site, browser, axe_source, f"/courses/{course_id}", viewport, "course-page", "course page"
    )
    expect(page.get_by_role("heading", name="Documents in this course")).to_be_visible()
    expect(
        page.get_by_role("link", name=re.compile("^Download all 1 ready document"))
    ).to_be_visible()
    assert not sideways_overflow(page)
    context.close()


# --------------------------------------------------------------------------- the sample


def fresh_person(site: WebEnv) -> str:
    """A signed-in person with nothing yet: a new email, allowed, for the sample."""
    email = f"sample{time.monotonic_ns()}@example.org"
    site.allow(email)
    return email


@SIZES
def test_my_documents_offers_the_sample_first_and_it_opens_and_passes_axe(
    site, browser, axe_source, viewport
):
    email = fresh_person(site)
    context = browser.new_context(bypass_csp=True, viewport=viewport)
    page = context.new_page()
    sign_in(page, site.base, email)
    page.goto(f"{site.base}/app")
    expect(page.get_by_role("heading", name="Try a sample first")).to_be_visible()
    clean(page, axe_source, "my documents with the sample offered")
    shoot(page, "sample-offered", viewport)
    page.get_by_role("button", name="Open the sample document").click()
    page.wait_for_url(re.compile(r"/jobs/[0-9a-f]{32}$"))
    expect(page.locator("#sample-note")).to_contain_text("This is a sample document, not your file")
    clean(page, axe_source, "the sample's job page")
    shoot(page, "sample-job", viewport)
    assert not sideways_overflow(page)
    page.get_by_role("link", name=re.compile("^Review 2 decisions")).click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("Decision 1 of 2")).to_be_visible()
    clean(page, axe_source, "the sample's review screen")
    shoot(page, "sample-review", viewport)
    context.close()
