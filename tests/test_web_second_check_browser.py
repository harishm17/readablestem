"""The second automated check's note on the review screen, in a real browser.

An accepted figure the check flagged is listed under "Optional checks" with the plain
sentence from web/messages.py, never under "Needs your decision"; the page has zero
serious or critical axe-core violations at 1280 and 320 CSS px and does not scroll
sideways on a phone. Run with: uv run pytest -m browser tests/test_web_second_check_browser.py

Set A11Y_STEM_SCREENSHOTS=/some/dir to also save screenshots of the page there.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from a11y_stem.core.types import (
    Check,
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    FigureFix,
    Fix,
    Kind,
    Status,
    Verification,
)
from a11y_stem.ops.canary import REQUESTS_FILE
from a11y_stem.web.messages import CHECK_MESSAGES
from fixtures.web.support import ALICE, WebEnv, make_pdf
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import axe_violations, describe, free_port, sign_in
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review_browser import NARROW, WIDE, open_page, sideways_overflow, unfold_everything

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

NOTE = CHECK_MESSAGES[("figure.second_check", "second_check_mismatch")]


def second_check_output() -> ConversionOutput:
    """Two accepted figures on one page: p1-e1 flagged by the second check (optional
    note), p1-e2 not; and one equation waiting for a decision."""
    png = base64.b64decode(json.loads(REQUESTS_FILE.read_text())["images"]["bars"])
    elements = [
        Element(id="p1-e0", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=0, text="F = ma"),
        Element(id="p1-e1", kind=Kind.FIGURE, page=0, bbox=(0, 1, 1, 2), order=1,
                caption="Figure 1. Results of a class vote."),
        Element(id="p1-e2", kind=Kind.FIGURE, page=0, bbox=(0, 2, 1, 3), order=2),
    ]  # fmt: skip
    flagged = Verification(
        check="figure_alt", probabilities={"ok": 0.2, "factual_error": 0.9}, flagged=True
    )
    clean = Verification(
        check="figure_alt", probabilities={"ok": 0.97, "factual_error": 0.0}, flagged=False
    )
    fixes = [
        Fix(element_id="p1-e0", kind=Kind.EQUATION, status=Status.FAILED,
            reason="invalid_latex", review="required"),
        Fix(element_id="p1-e1", kind=Kind.FIGURE, status=Status.ACCEPTED, confidence=0.98,
            figure=FigureFix(figure_type="chart", alt="Bar chart of votes: B has 9."),
            checks=[Check(name="figure.second_check", passed=False, severity="warning",
                          code="second_check_mismatch")],
            review="optional", verifications=[flagged]),
        Fix(element_id="p1-e2", kind=Kind.FIGURE, status=Status.ACCEPTED, confidence=0.98,
            figure=FigureFix(figure_type="chart", alt="Bar chart of votes: B has 5."),
            checks=[Check(name="figure.second_check", passed=True, severity="warning")],
            review="none", verifications=[clean]),
    ]  # fmt: skip
    result = DocumentResult(
        pipeline_version="test",
        title="Second check",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(pages=1, cost_usd=0.01, latency_ms=5, model_calls=3),
        html="<!doctype html><html lang='en'><head><title>t</title></head><body></body></html>",
    )
    return ConversionOutput(result=result, crops={e.id: png for e in elements})


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    import uvicorn

    from a11y_stem.pipeline import assemble

    patch = pytest.MonkeyPatch()
    patch.setattr(assemble, "render_html", lambda **kw: "<html><body>re-rendered</body></html>")
    env = WebEnv(tmp_path_factory.mktemp("second-check-browser"), per_user_daily_page_quota=100)
    env.converter.output = second_check_output()
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


@pytest.fixture(scope="module")
def job(site, browser) -> str:
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": "votes.pdf", "mimeType": "application/pdf", "buffer": make_pdf(1)}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    assert site.run_worker() == job_id
    return job_id


def _screenshot(page, name: str) -> None:
    target = os.environ.get("A11Y_STEM_SCREENSHOTS")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / name), full_page=True)


@pytest.mark.parametrize("viewport", [WIDE, NARROW], ids=["1280px", "320px"])
def test_the_note_is_an_optional_check_with_no_axe_violations(
    site, browser, job, axe_source, viewport
):
    context, page = open_page(browser, site, viewport=viewport)
    page.goto(f"{site.base}/jobs/{job}/review/list")
    decisions = page.locator("#needs-decision-title").inner_text()
    assert decisions == "Needs your decision (1)"  # the equation only, not the figure
    optional = page.locator("#optional-checks")
    assert NOTE in (optional.text_content() or "")  # folded: in the page, not shown yet
    lead = " ".join(optional.locator("p").first.inner_text().split())
    assert "something small" not in lead  # the note is not called small
    assert "a second automated check found a possible mismatch" in lead
    assert "worth a look" in lead
    folded = axe_violations(page, axe_source)
    unfold_everything(page)
    assert NOTE in optional.inner_text()  # unfolded, the item's summary line says it
    unfolded = axe_violations(page, axe_source)
    width = sideways_overflow(page)
    _screenshot(page, f"second-check-review-{viewport['width']}px.png")
    # The Split View lists it among the optional checks, with its short line.
    page.goto(f"{site.base}/jobs/{job}/review")
    split_text = page.locator("main").text_content() or ""
    split = axe_violations(page, axe_source)
    context.close()
    assert "Worth a look: a second automated check doubts the alt text." in split_text
    assert not folded, describe(folded)
    assert not unfolded, describe(unfolded)
    assert not split, describe(split)
    if viewport is NARROW:
        assert width == 0
    for word in ("verified", "checked for accuracy", "compl" + "iant"):
        assert word not in NOTE.lower()


def test_the_job_page_counts_the_note_as_optional(site, browser, job, axe_source):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}")
    text = page.locator("main").inner_text()
    violations = axe_violations(page, axe_source)
    _screenshot(page, "second-check-job.png")
    context.close()
    assert "1 need your decision" in text or "1 needs your decision" in text
    assert "Optional checks (accepted; nothing required, 1 worth a look)" in text
    assert "nothing to do" not in text
    assert not violations, describe(violations)
