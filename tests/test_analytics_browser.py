"""Product analytics, in a real browser: the loader's own rules (web/static/analytics.js).

The app runs with ANALYTICS_ENABLED and a mock PostHog behind /m/ (a stand-in array.js that
records what the stub was asked to do). Chromium maps readablestem.com to the test server so
the production-host check can pass; on the plain 127.0.0.1 address the loader must stay
silent. Checked: skips on other hosts, in automated browsers and after ?notrack=1; the init
options; ?hm_id= and ?ref= handling and the address-bar clean-up; the funnel markers and the
/demo click; and that the strict policy produces no violation with all of it running.

Run with: uv run pytest -m browser tests/test_analytics_browser.py
"""

from __future__ import annotations

import json
import socket
import threading
import time

import httpx
import pytest

from fixtures.web.support import ALICE, WebEnv
from readablestem.web import analytics

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

# Stands in for PostHog's array.js: records the stub's init call and every queued call.
FAKE_ARRAY = b"""
window.__arrayLoaded = true;
window.__init = JSON.stringify(window.posthog._i[0][1],
  function (k, v) { return typeof v === 'function' ? 'fn' : v; });
window.__token = window.posthog._i[0][0];
"""
NOT_AUTOMATED = "Object.defineProperty(navigator, 'webdriver', { get: () => false });"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    import uvicorn

    patch = pytest.MonkeyPatch()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/array.js"):
            return httpx.Response(
                200, content=FAKE_ARRAY, headers={"content-type": "text/javascript"}
            )
        return httpx.Response(200, json={"status": 1})

    patch.setattr(
        analytics, "new_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    env = WebEnv(tmp_path_factory.mktemp("analytics-browser"), analytics_enabled=True)
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
    env.port = port
    env.local = f"http://127.0.0.1:{port}"
    env.prod = f"http://readablestem.com:{port}"
    env.allow(ALICE)
    yield env
    server.should_exit = True
    thread.join(timeout=10)
    patch.undo()


@pytest.fixture(scope="module")
def chromium(site):
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                args=["--host-resolver-rules=MAP readablestem.com 127.0.0.1"]
            )
        except Exception as exc:  # pragma: no cover - browsers not installed
            pytest.skip(f"Chromium unavailable ({type(exc).__name__}); run playwright install")
        yield browser
        browser.close()


class Visit:
    """One browser context with the requests and console messages it produced."""

    def __init__(self, chromium, *, automated: bool = False) -> None:
        self.context = chromium.new_context()
        if not automated:
            self.context.add_init_script(NOT_AUTOMATED)
        self.page = self.context.new_page()
        self.requests: list[str] = []
        self.console: list[str] = []
        self.page.on("request", lambda r: self.requests.append(r.url))
        self.page.on("console", lambda m: self.console.append(m.text))
        self.page.on("pageerror", lambda e: self.console.append(str(e)))

    def goto(self, url: str) -> None:
        self.page.goto(url)
        self.page.wait_for_load_state("networkidle")

    def value(self, expression: str):
        return self.page.evaluate(expression)

    @property
    def calls(self) -> list:
        return self.value("window.posthog ? JSON.parse(JSON.stringify([...window.posthog])) : null")

    def proxied(self) -> list[str]:
        return [url for url in self.requests if "/m/" in url]

    def close(self) -> None:
        self.context.close()


@pytest.fixture
def visit(chromium):
    made: list[Visit] = []

    def start(**kwargs) -> Visit:
        visitor = Visit(chromium, **kwargs)
        made.append(visitor)
        return visitor

    yield start
    for visitor in made:
        visitor.close()


def policy_violations(visitor: Visit) -> list[str]:
    return [m for m in visitor.console if "Content Security Policy" in m or "Refused to" in m]


def test_it_stays_silent_on_any_other_host(site, visit):
    visitor = visit()
    visitor.goto(site.local + "/")
    assert visitor.value("typeof window.posthog") == "undefined"
    assert visitor.proxied() == []


def test_it_stays_silent_in_an_automated_browser(site, visit):
    visitor = visit(automated=True)
    visitor.goto(site.prod + "/")
    assert visitor.value("navigator.webdriver") is True
    assert visitor.value("typeof window.posthog") == "undefined"
    assert visitor.proxied() == []


def test_on_the_production_host_it_loads_through_m_and_inits_minimally(site, visit):
    visitor = visit()
    visitor.goto(site.prod + "/")
    assert visitor.value("window.__arrayLoaded") is True
    assert visitor.proxied() == [f"{site.prod}/m/static/array.js"]
    assert all(u.startswith(site.prod) for u in visitor.requests)  # nothing from another host
    assert visitor.value("window.__token") == analytics.TOKEN
    options = json.loads(visitor.value("window.__init"))
    assert options["api_host"] == f"{site.prod}/m"
    assert options["ui_host"] == "https://us.posthog.com"
    assert options["defaults"] == "2026-08-30"
    assert options["person_profiles"] == "identified_only"
    assert options["persistence"] == "localStorage"
    for off in (
        "autocapture",
        "capture_dead_clicks",
        "capture_exceptions",
        "capture_performance",
        "enable_heatmaps",
    ):
        assert options[off] is False, off
    for on in ("capture_pageview", "capture_pageleave", "disable_session_recording"):
        assert options[on] is True, on
    assert options["disable_surveys"] is True
    assert options["advanced_disable_feature_flags"] is True
    assert options["bootstrap"] == {}
    assert policy_violations(visitor) == []


def test_opting_out_with_notrack_sticks_until_it_is_undone(site, visit):
    visitor = visit()
    visitor.goto(site.prod + "/?notrack=1")
    assert visitor.value("typeof window.posthog") == "undefined"
    visitor.goto(site.prod + "/privacy")
    assert visitor.value("typeof window.posthog") == "undefined"
    assert visitor.proxied() == []
    visitor.goto(site.prod + "/?notrack=0")
    assert visitor.value("window.__arrayLoaded") is True


def test_a_link_from_the_personal_site_hands_over_its_id_and_is_cleaned(site, visit):
    ident = "0199f1c2-7a3e-7b8a-9d21-5c3f0a9e4b11"
    visitor = visit()
    visitor.goto(f"{site.prod}/?hm_id={ident}&ref=Wedge&utm_source=x&keep=1")
    options = json.loads(visitor.value("window.__init"))
    assert options["bootstrap"] == {"distinctID": ident}
    visitor.page.wait_for_timeout(
        2200
    )  # the clean-up runs 1.5 s after load (waitForFunction would eval)
    assert visitor.value("location.search") == "?keep=1"


def test_an_implausible_id_is_not_used(site, visit):
    visitor = visit()
    visitor.goto(site.prod + "/?hm_id=<script>alert(1)</script>")
    assert json.loads(visitor.value("window.__init"))["bootstrap"] == {}


def sign_in(visitor: Visit, site: WebEnv) -> None:
    visitor.goto(site.prod + "/dev-login")
    visitor.page.get_by_label("Email address").fill(ALICE)
    visitor.page.get_by_role("button", name="Sign in").click()
    visitor.page.wait_for_load_state("networkidle")


def test_a_sign_in_is_sent_once_with_only_its_category(site, visit):
    visitor = visit()
    sign_in(visitor, site)
    sent = [c for c in visitor.calls if c[0] == "capture"]
    assert sent == [["capture", "login_completed", {"provider": "dev"}]]
    visitor.page.reload()
    visitor.page.wait_for_load_state("networkidle")
    assert [c for c in visitor.calls if c[0] == "capture"] == []
    assert policy_violations(visitor) == []


def test_a_click_on_the_demo_link_is_sent(site, visit):
    visitor = visit()
    visitor.goto(site.prod + "/")
    visitor.page.evaluate("document.addEventListener('click', (e) => e.preventDefault())")
    visitor.page.locator("a[href='/demo']").first.click()
    sent = [c for c in visitor.calls if c[0] == "capture"]
    assert sent == [["capture", "demo_opened", {}, {"transport": "sendBeacon"}]]


def test_markers_with_odd_properties_are_cleaned(site, visit):
    visitor = visit()
    visitor.goto(site.prod + "/")
    visitor.page.evaluate(
        """() => {
          const span = document.createElement('span');
          span.hidden = true;
          span.setAttribute('data-ph-event', 'conversion_completed');
          span.setAttribute('data-ph-once', 'conv-test');
          span.setAttribute('data-ph-props', JSON.stringify({
            page_count: 3, status: 'done', file_name: 'x'.repeat(200), Title: 'no',
            nested: {a: 1}, sample: false }));
          document.body.append(span);
          document.dispatchEvent(new Event('htmx:afterSettle'));
          document.dispatchEvent(new Event('htmx:afterSettle'));
        }"""
    )
    sent = [c for c in visitor.calls if c[0] == "capture"]
    assert sent == [
        ["capture", "conversion_completed", {"page_count": 3, "status": "done", "sample": False}]
    ]
