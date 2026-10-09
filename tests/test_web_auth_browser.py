"""The sign-in pages with Microsoft set up, in a real browser (Playwright Chromium).

Run with: uv run pytest -m browser tests/test_web_auth_browser.py

The round trip runs under the app's real CSP: the "Sign in with your university account"
form is answered with a redirect to login.microsoftonline.com, which Chromium follows only
because `form-action` names that host. This Chromium resolves that host to a closed local
port, so the redirect is attempted (and checked against the CSP) but nothing leaves the
machine; the test then does what Microsoft would, with the fake identity platform
(tests/fixtures/web/oidc.py), and opens the callback in the same browser. The app's own
requests (discovery, keys, token) reach the fake through Authlib's transport.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from fixtures.web.oidc import (
    AUTHORIZE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    OTHER_TENANT,
    TENANT,
    FakeMicrosoft,
)
from fixtures.web.support import ALICE, WebEnv
from test_web_browser import TEXT_SPACING, WCAG_TAGS, axe_violations, describe, free_port
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

UNIVERSITY_BUTTON = "Sign in with your university account (Microsoft)"
STRICT = {"moderate", "serious", "critical"}
# Port 9 (discard) on this machine: closed, so the connection is refused at once.
OFFLINE_MICROSOFT = "--host-resolver-rules=MAP login.microsoftonline.com 127.0.0.1:9"
CSP_LISTENER = (
    "document.addEventListener('securitypolicyviolation', e => "
    "(window.__csp = window.__csp || []).push(e.violatedDirective + ' ' + e.blockedURI))"
)


@pytest.fixture(scope="module")
def browser():
    """Chromium that cannot reach Microsoft (see the module docstring)."""
    with sync_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch(args=[OFFLINE_MICROSOFT])
        except Exception as exc:  # pragma: no cover - browsers not installed
            pytest.skip(f"Chromium unavailable ({type(exc).__name__}); run playwright install")
        yield chromium
        chromium.close()


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    """Google and Microsoft sign-in set up, Microsoft answered by the fake."""
    import uvicorn

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = WebEnv(
        tmp_path_factory.mktemp("auth-browser"),
        dev_login_enabled=False,
        google_client_id="google-client",
        google_client_secret="google-secret",
        ms_client_id=CLIENT_ID,
        ms_client_secret=CLIENT_SECRET,
        ms_allowed_tenants=[TENANT],
        base_url=base,
    )
    env.fake = FakeMicrosoft(ALICE)
    env.fake.install(env.ctx)
    server = uvicorn.Server(
        uvicorn.Config(env.app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    env.base = base
    env.allow(ALICE)
    yield env
    server.should_exit = True
    thread.join(timeout=10)


def university_sign_in(page, site, *, tenant: str = TENANT) -> None:
    """Press the button; Chromium follows the redirect to Microsoft (unreachable here);
    then come back to the callback as Microsoft would after the person signs in."""
    page.goto(f"{site.base}/login")
    with (
        page.expect_event("framenavigated", lambda frame: frame.url.startswith("chrome-error:")),
        page.expect_event(
            "requestfailed", lambda request: request.url.startswith(AUTHORIZE_URL)
        ) as sent,
    ):
        page.get_by_role("button", name=UNIVERSITY_BUTTON).click()
    site.fake.claims["tid"] = tenant
    try:
        callback = site.fake.authorize(sent.value.url)
        page.goto(site.base + callback)
    finally:
        site.fake.claims["tid"] = TENANT


@pytest.mark.parametrize("javascript", [True, False], ids=["js", "no-js"])
def test_the_university_round_trip_works_under_the_csp(site, browser, javascript):
    context = browser.new_context(java_script_enabled=javascript)
    context.add_init_script(CSP_LISTENER)
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    refused: list[str] = []
    page.on("requestfailed", lambda request: refused.append(request.failure or ""))
    university_sign_in(page, site)
    assert page.url == f"{site.base}/app"
    assert f"Signed in as {ALICE}" in page.content()
    if javascript:
        assert page.evaluate("window.__csp || []") == []
    context.close()
    assert not [e for e in errors if "Content Security Policy" in e], errors
    # The redirect was sent (and refused by the closed port), not blocked by the CSP.
    assert refused and all("CSP" not in failure for failure in refused), refused


def test_another_organisation_gets_a_plain_page(site, browser):
    context = browser.new_context()
    page = context.new_page()
    university_sign_in(page, site, tenant=OTHER_TENANT)
    assert page.title().startswith("Your organisation is not set up for this beta")
    assert OTHER_TENANT in page.locator("main").inner_text()
    context.close()


def open_sign_in_page(page, site, path: str) -> None:
    if path == "org":
        university_sign_in(page, site, tenant=OTHER_TENANT)
    else:
        page.goto(site.base + path)


@pytest.mark.parametrize("path", ["/login", "/", "org"])
def test_sign_in_pages_pass_wcag22_aa_at_moderate_and_above(site, browser, axe_source, path):
    # bypass_csp only so axe can be injected; the round trip above runs under the CSP.
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    open_sign_in_page(page, site, path)
    assert page.get_by_role("button", name=UNIVERSITY_BUTTON).count() == (path != "org")
    serious = axe_violations(page, axe_source)
    results = page.evaluate(
        "async (tags) => await axe.run(document, "
        "{runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']})",
        WCAG_TAGS,
    )
    context.close()
    strict = [v for v in results["violations"] if v["impact"] in STRICT]
    assert not serious, describe(serious)
    assert not strict, describe(strict)


@pytest.mark.parametrize("spacing", [False, True], ids=["plain", "text-spacing"])
@pytest.mark.parametrize("path", ["/login", "org"])
def test_sign_in_pages_reflow_at_320_css_pixels(site, browser, path, spacing):
    context = browser.new_context(viewport={"width": 320, "height": 256}, bypass_csp=True)
    page = context.new_page()
    open_sign_in_page(page, site, path)
    if spacing:
        page.add_style_tag(content=TEXT_SPACING)
    width = page.evaluate(
        "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    context.close()
    assert width[0] <= width[1], f"{path}: content is {width[0]} px wide in a {width[1]} px view"


def test_the_buttons_are_reachable_by_keyboard_in_order(site, browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{site.base}/login")
    names = []
    for _ in range(12):
        page.keyboard.press("Tab")
        name = page.evaluate(
            "document.activeElement.tagName === 'BUTTON' ? "
            "document.activeElement.textContent.trim() : null"
        )
        if name:
            names.append(name)
    context.close()
    assert names[:2] == [UNIVERSITY_BUTTON, "Sign in with Google"]
