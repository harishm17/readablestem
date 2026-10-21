"""Open sign-up in a real browser: the new and changed pages pass axe-core (WCAG 2.2 AA and
best practice, moderate and above), reflow at 320 CSS pixels, and deleting an account works
without JavaScript.

Run with: uv run pytest -m browser tests/test_web_open_signup_browser.py
Uses the server, axe-core and Chromium set-up of tests/test_web_browser.py.
"""

from __future__ import annotations

import threading
import time

import pytest

from fixtures.web.support import ADMIN, BOB, WebEnv, make_pdf
from readablestem.web.auth import set_blocked
from readablestem.web.db import session_scope
from test_web_browser import (
    STRICT_IMPACTS,
    TEXT_SPACING,
    WCAG_TAGS,
    describe,
    free_port,
    sign_in,
)
from test_web_browser import (
    axe_source as axe_source,  # fixture, re-exported for pytest
)
from test_web_browser import (
    browser as browser,  # fixture, re-exported for pytest
)

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

CAROL = "carol@example.org"
BLOCKED = "blocked@example.org"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    import uvicorn

    env = WebEnv(
        tmp_path_factory.mktemp("open-browser"),
        signup_mode="open",
        max_new_accounts_per_day=50,
        open_tier_daily_page_quota=5,
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
    env.allow(BOB)
    yield env
    server.should_exit = True
    thread.join(timeout=10)


def axe_violations(page, axe_source: str) -> list[dict]:
    page.add_script_tag(content=axe_source)
    results = page.evaluate(
        "async (tags) => await axe.run(document, "
        "{runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']})",
        WCAG_TAGS,
    )
    return [v for v in results["violations"] if v["impact"] in STRICT_IMPACTS]


@pytest.mark.parametrize("path", ["/", "/login", "/privacy", "/terms", "/limits"])
def test_the_public_pages_in_open_mode_pass_axe(site, browser, axe_source, path):
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    response = page.goto(site.base + path)
    assert response is not None and response.status == 200
    violations = axe_violations(page, axe_source)
    context.close()
    assert not violations, describe(violations)


@pytest.mark.parametrize("path", ["/account", "/account/delete", "/admin", "/app"])
def test_the_account_and_admin_pages_pass_axe(site, browser, axe_source, path):
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    sign_in(page, site.base, CAROL)  # makes the account (open tier)
    if path == "/admin":
        context.close()
        context = browser.new_context(bypass_csp=True)
        page = context.new_page()
        _make_a_blocked_person(site)
        sign_in(page, site.base, ADMIN)
    response = page.goto(site.base + path)
    assert response is not None and response.status == 200
    violations = axe_violations(page, axe_source)
    context.close()
    assert not violations, describe(violations)


def _make_a_blocked_person(site) -> None:
    """An open-tier account that an admin blocked, so /admin shows the blocked table."""
    import httpx

    with httpx.Client(base_url=site.base, follow_redirects=False) as client:
        page = client.get("/dev-login").text
        token = page.split('name="csrf_token" value="')[1].split('"')[0]
        client.post("/dev-login", data={"csrf_token": token, "email": BLOCKED})
    with session_scope(site.ctx.factory) as session:
        set_blocked(session, BLOCKED, blocked=True)


def test_the_refusal_pages_pass_axe(site, browser, axe_source):
    _make_a_blocked_person(site)
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    sign_in(page, site.base, BLOCKED)
    assert "This account cannot be used" in page.content()
    blocked = axe_violations(page, axe_source)
    context.close()
    assert not blocked, describe(blocked)

    # The day's accounts used up: a calm page. Make the count full, then try a new address.
    from readablestem.web.db import SignupDay

    with session_scope(site.ctx.factory) as session:
        session.merge(SignupDay(day=_today(), accounts=site.settings.max_new_accounts_per_day))
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    sign_in(page, site.base, "late@example.org")
    assert "Sign-ups are full for today" in page.content()
    full = axe_violations(page, axe_source)
    context.close()
    with session_scope(site.ctx.factory) as session:
        session.merge(SignupDay(day=_today(), accounts=0))
    assert not full, describe(full)


def _today():
    from readablestem.web.database import utc_today

    return utc_today()


@pytest.mark.parametrize("spacing", [False, True], ids=["plain", "text-spacing"])
@pytest.mark.parametrize("path", ["/account", "/account/delete", "/login"])
def test_the_new_pages_reflow_at_320_css_pixels(site, browser, path, spacing):
    context = browser.new_context(viewport={"width": 320, "height": 256}, bypass_csp=True)
    page = context.new_page()
    if path != "/login":
        sign_in(page, site.base, CAROL)
    page.goto(site.base + path)
    if spacing:
        page.add_style_tag(content=TEXT_SPACING)
    width = page.evaluate(
        "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    context.close()
    assert width[0] <= width[1], f"{path}: content is {width[0]} px wide in a {width[1]} px view"


def test_deleting_an_account_works_without_javascript(site, browser):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    sign_in(page, site.base, "leaving@example.org")
    page.goto(site.base + "/account")
    page.get_by_role("link", name="Delete my account").click()
    assert page.get_by_role("heading", name="Delete your account?").is_visible()
    page.get_by_role("link", name="No, keep it").click()
    assert page.url.endswith("/account")
    page.get_by_role("link", name="Delete my account").click()
    page.get_by_role("button", name="Yes, delete my account").click()
    page.wait_for_url(site.base + "/")
    assert "Your account and everything in it have been deleted." in page.content()
    # Signed out: the documents page sends the person to sign in.
    page.goto(site.base + "/app")
    assert "/login" in page.url
    context.close()


def test_a_new_account_sees_its_limit_on_the_upload_page(site, browser):
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, "uploader@example.org")
    page.goto(site.base + "/app")
    assert "a new account can convert 5 pages a day" in " ".join(page.content().split())
    page.get_by_label("PDF files").set_input_files(
        files=[{"name": "big.pdf", "mimeType": "application/pdf", "buffer": make_pdf(5)}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(site.base + "/jobs/*")
    page.goto(site.base + "/app")
    assert "You have converted all 5 pages a new account can convert today" in " ".join(
        page.content().split()
    )
    context.close()
