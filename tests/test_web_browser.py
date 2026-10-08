"""The app in a real browser (Playwright Chromium): axe-core, CSP, htmx, and no-JS use.

Run with: uv run pytest -m browser tests/test_web_browser.py
Needs `uv run playwright install chromium`. axe-core is downloaded once from the npm
registry (integrity-checked) into .cache/axe-core/; the tests skip if it cannot be.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import socket
import tarfile
import threading
import time
from pathlib import Path

import pytest

from fixtures.web.support import ADMIN, ALICE, WebEnv, make_pdf

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

AXE_VERSION = "4.14.0"
AXE_TARBALL = f"https://registry.npmjs.org/axe-core/-/axe-core-{AXE_VERSION}.tgz"
AXE_INTEGRITY = (
    "sha512-9WTZxEjsZ7b13TH8JPmbV2z8CHbl80/2hm3XPEG4JgNdQLK81IBRXmSxHfMA"
    "OkSqQeRxT/0dwNDz2GOm3zzpcQ=="
)
AXE_CACHE = (
    Path(__file__).resolve().parents[1] / ".cache" / "axe-core" / f"axe-{AXE_VERSION}.min.js"
)
BLOCKING_IMPACTS = {"serious", "critical"}


@pytest.fixture(scope="module")
def axe_source() -> str:
    if AXE_CACHE.is_file():
        return AXE_CACHE.read_text(encoding="utf-8")
    import httpx

    try:
        tarball = httpx.get(AXE_TARBALL, timeout=30, follow_redirects=True).content
    except httpx.HTTPError as exc:
        pytest.skip(f"axe-core download failed: {type(exc).__name__}")
    digest = "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode()
    assert digest == AXE_INTEGRITY, "axe-core tarball does not match its npm integrity"
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        member = archive.extractfile("package/axe.min.js")
        assert member is not None
        source = member.read().decode("utf-8")
    AXE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    AXE_CACHE.write_text(source, encoding="utf-8")
    return source


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """The app served by uvicorn on a free port (same process, so WP3/WP4 stubs that a
    review action calls can be monkeypatched)."""
    import uvicorn

    from a11y_stem.pipeline import assemble

    patch = pytest.MonkeyPatch()
    patch.setattr(assemble, "render_html", lambda **kw: "<html><body>re-rendered</body></html>")
    env = WebEnv(tmp_path_factory.mktemp("browser"))
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
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover - browsers not installed
            pytest.skip(f"Chromium unavailable ({type(exc).__name__}); run playwright install")
        yield chromium
        chromium.close()


def sign_in(page, base: str, email: str) -> None:
    page.goto(f"{base}/dev-login")
    page.get_by_label("Email address").fill(email)
    page.get_by_role("button", name="Sign in").click()


def upload_pdf(page, base: str, pages: int = 1) -> str:
    page.goto(f"{base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": "notes.pdf", "mimeType": "application/pdf", "buffer": make_pdf(pages)}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{base}/jobs/*")
    return page.url.rsplit("/", 1)[-1]


@pytest.fixture(scope="module")
def finished_job(site, browser) -> str:
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    job_id = upload_pdf(page, site.base)
    assert site.run_worker() == job_id
    context.close()
    return job_id


def axe_violations(page, axe_source: str) -> list[dict]:
    page.add_script_tag(content=axe_source)
    results = page.evaluate("async () => await axe.run(document, {resultTypes: ['violations']})")
    return [v for v in results["violations"] if v["impact"] in BLOCKING_IMPACTS]


def no_js_violations(browser, site, path: str, axe_source: str) -> list[dict]:
    """axe on a page as a browser without JavaScript sees it.

    With scripting off, <noscript> content is live markup, but axe cannot run there (its
    timers never fire). So the page is loaded with JavaScript off, its DOM is taken with
    the <noscript> wrappers removed and the scripts dropped, and axe checks that copy in a
    normal page on the same origin (so the stylesheet still applies)."""
    nojs = browser.new_context(java_script_enabled=False)
    page = nojs.new_page()
    sign_in(page, site.base, ALICE)
    response = page.goto(site.base + path)
    assert response is not None and response.status == 200
    html = page.content()
    nojs.close()
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S)
    html = html.replace("<noscript>", "").replace("</noscript>", "")
    context = browser.new_context(bypass_csp=True, java_script_enabled=True)
    page = context.new_page()
    page.goto(site.base + "/privacy")
    page.set_content(html)
    violations = axe_violations(page, axe_source)
    context.close()
    return violations


def describe(violations: list[dict]) -> str:
    return "\n".join(
        f"{v['id']} ({v['impact']}): {v['help']} -> {[n['target'] for n in v['nodes']][:3]}"
        for v in violations
    )


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/app",
        "/jobs/{id}",
        "/jobs/{id}/review",
        "/privacy",
        "/terms",
        "/limits",
        "/demo",
        "/login",
        "/dev-login",
        "/admin",
    ],
)
def test_pages_have_no_serious_or_critical_axe_violations(
    site, browser, finished_job, axe_source, path
):
    # bypass_csp only so the test can inject axe; the CSP itself is tested below.
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    sign_in(page, site.base, ADMIN if path == "/admin" else ALICE)
    response = page.goto(site.base + path.replace("{id}", finished_job))
    assert response is not None and response.status == 200
    violations = axe_violations(page, axe_source)
    context.close()
    assert not violations, describe(violations)


@pytest.fixture
def queued_job(site, browser):
    """A job left waiting in the queue (no worker runs), deleted afterwards so later tests
    find only their own jobs in the queue."""
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    job_id = upload_pdf(page, site.base)
    yield job_id
    page.goto(f"{site.base}/jobs/{job_id}")
    page.get_by_role("button", name="Delete now").click()
    context.close()


def test_an_unfinished_job_page_has_no_serious_axe_violations(
    site, browser, queued_job, axe_source
):
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/jobs/{queued_job}")
    assert "Waiting" in page.locator("#job-status").inner_text()
    violations = axe_violations(page, axe_source)
    context.close()
    assert not violations, describe(violations)


def test_an_unfinished_job_page_without_javascript_has_no_serious_axe_violations(
    site, browser, queued_job, axe_source
):
    """Without JavaScript the page used to reload itself every 5 s: axe "meta-refresh",
    critical (WCAG 2.2.1, F41). Reloading is now only on request."""
    violations = no_js_violations(browser, site, f"/jobs/{queued_job}", axe_source)
    assert not violations, describe(violations)


def test_error_states_have_no_serious_axe_violations(site, browser, axe_source):
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": "x.pdf", "mimeType": "application/pdf", "buffer": b"not a pdf"}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    assert page.locator(".error-summary").is_visible()
    violations = axe_violations(page, axe_source)
    page.goto(f"{site.base}/no-such-page")
    violations += axe_violations(page, axe_source)
    context.close()
    assert not violations, describe(violations)


def test_pages_load_htmx_under_the_csp_without_violations(site, browser, finished_job):
    context = browser.new_context()
    context.add_init_script(
        "document.addEventListener('securitypolicyviolation', e => "
        "(window.__csp = window.__csp || []).push(e.violatedDirective + ' ' + e.blockedURI))"
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    sign_in(page, site.base, ALICE)
    for path in ("/", "/app", f"/jobs/{finished_job}", f"/jobs/{finished_job}/review"):
        page.goto(site.base + path)
        assert page.evaluate("typeof window.htmx") == "object", path
        assert page.evaluate("window.__csp || []") == [], path
    context.close()
    assert not [e for e in errors if "Content Security Policy" in e], errors


def test_review_page_renders_mathml(site, browser, finished_job):
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/jobs/{finished_job}/review")
    box = page.locator("#fix-p1-e3 .mathml-preview math").bounding_box()
    assert box is not None and box["width"] > 0
    context.close()


def test_skip_link_is_the_first_tab_stop_and_becomes_visible(site, browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{site.base}/")
    page.keyboard.press("Tab")
    focused = page.evaluate("document.activeElement.textContent.trim()")
    assert focused == "Skip to main content"
    box = page.locator(".skip-link").bounding_box()
    assert box is not None and box["y"] >= 0
    outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    assert outline == "solid"  # visible focus indicator
    context.close()


def test_status_updates_live_with_htmx_then_shows_results(site, browser):
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    job_id = upload_pdf(page, site.base)
    assert "Waiting" in page.locator("#job-status").inner_text()
    assert site.run_worker() == job_id
    # htmx polls every 3 s; once finished, HX-Refresh reloads the page with the results.
    page.wait_for_selector("#results-heading", timeout=15000)
    assert "Finished" in page.locator("#job-status").inner_text()
    context.close()


def test_everything_works_without_javascript(site, browser):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    job_id = upload_pdf(page, site.base)
    assert page.locator('meta[http-equiv="refresh"]').count() == 0  # no unrequested reloads
    page.get_by_role("link", name="Reload this page every 15 seconds").click()
    assert page.locator('meta[http-equiv="refresh"]').count() == 1  # the user opted in
    assert page.get_by_role("link", name="Stop reloading this page").is_visible()
    assert site.run_worker() == job_id
    page.reload()
    assert page.locator("#results-heading").is_visible()
    assert page.locator('meta[http-equiv="refresh"]').count() == 0
    page.get_by_role("link", name="Review fixes").click()
    page.locator("#fix-p1-e4").get_by_role("button", name="Reject").click()
    assert "Rejected" in page.locator("#review-progress").inner_text()
    context.close()
