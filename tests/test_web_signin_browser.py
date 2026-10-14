"""The sign-in fallback pages in a real browser (Playwright Chromium): the emailed link
round trip, and axe-core at 1280 and 375 CSS px on the pages T3-10 adds.

Run with: uv run pytest -m browser tests/test_web_signin_browser.py
"""

from __future__ import annotations

import re
import threading
import time

import pytest

from a11y_stem.web import linked_emails
from a11y_stem.web.db import session_scope
from fixtures.web.support import ADMIN, ALICE, OutboxSender, WebEnv
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_browser import describe, free_port
from test_web_ux_browser import strict_violations

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

PERSONAL = "alice.personal@gmail.com"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    import uvicorn

    env = WebEnv(
        tmp_path_factory.mktemp("signin-browser"),
        email_login_enabled=True,
        email_sender="outbox",
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
    env.allow(ADMIN, admin=True)
    with session_scope(env.ctx.factory) as session:
        linked_emails.link(session, ALICE, PERSONAL)
    yield env
    server.should_exit = True
    thread.join(timeout=10)


def request_link(page, site, address: str = ALICE) -> None:
    page.goto(f"{site.base}/login")
    page.get_by_label("Or get a sign-in link by email").fill(address)
    page.get_by_role("button", name="Email me a sign-in link").click()
    page.wait_for_url(f"{site.base}/login/email")


def emailed_link(site) -> str:
    found = re.search(r"/login/email/confirm\?token=[A-Za-z0-9_-]+", OutboxSender.sent[-1].body)
    assert found, "no link in the message"
    return site.base + found.group(0)


@pytest.mark.parametrize("javascript", [True, False], ids=["js", "no-js"])
def test_the_emailed_link_round_trip(site, browser, javascript):
    OutboxSender.reset()
    context = browser.new_context(java_script_enabled=javascript)
    page = context.new_page()
    request_link(page, site)
    assert page.get_by_role("heading", name="Check your email").is_visible()
    page.goto(emailed_link(site))
    assert page.get_by_role("heading", name="Finish signing in").is_visible()
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{site.base}/app")
    assert page.get_by_role("heading", level=1).first.is_visible()
    other = browser.new_context().new_page()
    other.goto(emailed_link(site))  # used: the same link now says so
    assert other.get_by_role("heading", name="This link does not work").is_visible()
    context.close()


def open_signed_in(browser, site, path: str, width: int, who: str | None):
    context = browser.new_context(
        viewport={"width": width, "height": 800 if width > 400 else 700}, bypass_csp=True
    )
    page = context.new_page()
    if who:
        page.goto(f"{site.base}/dev-login")  # dev login is on for this app too
        page.get_by_label("Email address").fill(who)
        page.get_by_role("button", name="Sign in").click()
    page.goto(site.base + path)
    return context, page


@pytest.mark.parametrize("width", [1280, 375])
@pytest.mark.parametrize(
    "page_name", ["login", "sent", "bad-address", "confirm", "dead-link", "admin"]
)
def test_the_new_pages_pass_axe(site, browser, axe_source, page_name, width):
    OutboxSender.reset()
    if page_name == "admin":
        context, page = open_signed_in(browser, site, "/admin", width, ADMIN)
    else:
        context = browser.new_context(
            viewport={"width": width, "height": 800 if width > 400 else 700}, bypass_csp=True
        )
        page = context.new_page()
        if page_name == "login":
            page.goto(f"{site.base}/login")
        elif page_name == "sent":
            request_link(page, site)
        elif page_name == "bad-address":
            page.goto(f"{site.base}/login")
            page.get_by_label("Or get a sign-in link by email").fill("not-an-address")
            page.get_by_role("button", name="Email me a sign-in link").click()
        elif page_name == "confirm":
            request_link(page, site)
            page.goto(emailed_link(site))
        else:
            page.goto(f"{site.base}/login/email/confirm?token={'a' * 43}")
    violations = strict_violations(page, axe_source)
    scroll = page.evaluate(
        "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    context.close()
    assert not violations, describe(violations)
    assert scroll[0] <= scroll[1], f"{page_name}: {scroll[0]} px wide in a {scroll[1]} px view"


def test_the_admin_page_lists_the_linked_account(site, browser):
    context, page = open_signed_in(browser, site, "/admin", 1280, ADMIN)
    assert page.get_by_text(PERSONAL).first.is_visible()
    assert page.get_by_role("button", name=f"Unlink {PERSONAL} from {ALICE}").is_visible()
    context.close()
