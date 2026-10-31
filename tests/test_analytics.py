"""Product analytics (web/analytics.py): the /m/ proxy to PostHog, the loader, the funnel
markers, and the promises around them.

The upstream is mocked (`analytics.new_client`); nothing here reaches PostHog. The loader's
own behaviour in a browser (skips on other hosts, opt-out, init options, no CSP violation)
is in tests/test_analytics_browser.py.
"""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest

from fixtures.web.support import ALICE, WebEnv, csrf_from, job_id_from, login, make_pdf, upload
from readablestem.web import analytics
from readablestem.web.security import APP_CSP

STATIC = analytics.ASSET_HOST
API = analytics.API_HOST


@pytest.fixture
def upstream(monkeypatch):
    """Replace the proxy's HTTP client; `seen` collects the requests it would have sent."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "boom.invalid":
            raise httpx.ConnectError("down")
        if request.url.path.endswith("/array.js"):
            return httpx.Response(
                200, content=b"/* array */", headers={"content-type": "text/plain"}
            )
        if "/config" in request.url.path:
            return httpx.Response(200, json={"supportedCompression": ["gzip"]})
        return httpx.Response(
            200,
            content=b'{"status": 1}',
            headers={"content-type": "application/json", "set-cookie": "ph=1; Path=/"},
        )

    monkeypatch.setattr(
        analytics,
        "new_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return seen


@pytest.fixture
def on(tmp_path):
    env = WebEnv(tmp_path, analytics_enabled=True)
    with env.running() as client:
        env.allow(ALICE)
        env.browser = client
        yield env


@pytest.fixture
def off(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.browser = client
        yield env


# --------------------------------------------------------------------------- the flag


def test_analytics_is_off_by_default():
    from readablestem.config import Settings

    assert Settings(_env_file=None).analytics_enabled is False  # type: ignore[call-arg]


def test_off_there_is_no_proxy_no_loader_and_no_marker(off, upstream):
    client = off.browser
    assert client.get("/m/static/array.js").status_code == 404
    assert client.post("/m/i/v0/e/", content=b"{}").status_code in {404, 405}
    page = client.get("/").text
    assert "analytics" not in page and "data-ph-" not in page
    assert upstream == []


def test_on_the_pages_load_the_loader_from_this_origin(on):
    page = on.browser.get("/").text
    match = re.search(r'<script src="(/static/analytics\.[0-9a-f]{12}\.js)" defer></script>', page)
    assert match, "base.html should include the first-party loader"
    response = on.browser.get(match.group(1))
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "immutable" in response.headers["cache-control"]


# --------------------------------------------------------------------------- the CSP


def test_the_policy_stays_strict_and_names_no_outside_host(on):
    directives = dict(part.strip().split(" ", 1) for part in APP_CSP.split(";"))
    assert directives["script-src"] == "'self'"
    assert directives["default-src"] == "'self'"
    assert "connect-src" not in directives  # falls back to default-src 'self'
    assert "posthog" not in APP_CSP and "unsafe" not in APP_CSP
    for path in ("/", "/privacy", "/m/static/array.js"):
        response = on.browser.get(path)
        assert response.headers["content-security-policy"] == APP_CSP, path


def test_the_loader_names_no_host_but_this_one_and_posthogs_dashboard(on):
    source = on.browser.get("/static/analytics.js").text
    hosts = set(re.findall(r"https?://[A-Za-z0-9.-]+", source))
    # api_host is location.origin + "/m"; ui_host is a link target, never requested.
    assert hosts == {"https://us.posthog.com"}


def test_the_loader_sets_the_required_options(on):
    source = on.browser.get("/static/analytics.js").text
    for needle in (
        'api_host: location.origin + "/m"',
        'ui_host: "https://us.posthog.com"',
        'defaults: "2026-08-30"',
        'person_profiles: "identified_only"',
        "autocapture: false",
        "capture_pageview: true",
        "capture_pageleave: true",
        "disable_session_recording: true",
        "enable_heatmaps: false",
        "capture_dead_clicks: false",
        "capture_exceptions: false",
        "disable_surveys: true",
        "capture_performance: false",
        "advanced_disable_feature_flags: true",
        "register({ site: SITE })",
        'var SITE = "readablestem"',
    ):
        assert needle in source, needle
    assert ".identify(" not in source  # nobody is identified
    assert "hm_notrack" in source and "notrack" in source


# --------------------------------------------------------------------------- the proxy


def test_static_files_come_from_the_assets_host_and_may_be_cached(on, upstream):
    response = on.browser.get("/m/static/array.js?v=1.2")
    assert response.status_code == 200 and response.content == b"/* array */"
    assert str(upstream[0].url) == f"{STATIC}/static/array.js?v=1.2"
    assert response.headers["cache-control"] == analytics.ASSET_CACHE
    assert response.headers["access-control-allow-origin"] == "*"
    assert "set-cookie" not in response.headers


def test_the_site_config_is_never_cached_and_gets_the_browsers_origin(on, upstream):
    path = f"/m/array/{analytics.TOKEN}/config.js"
    response = on.browser.get(path, headers={"Origin": "https://readablestem.com", "Cookie": "a=b"})
    assert response.status_code == 200
    request = upstream[0]
    assert str(request.url) == f"{STATIC}/array/{analytics.TOKEN}/config.js"
    assert request.headers["origin"] == "https://readablestem.com"
    assert "cookie" not in request.headers
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == "https://readablestem.com"
    assert response.headers["content-type"].startswith("application/javascript")
    json_config = on.browser.get(f"/m/array/{analytics.TOKEN}/config")
    assert json_config.headers["content-type"].startswith("application/json")
    assert json_config.headers["cache-control"] == "no-store"


def test_events_go_to_the_api_host_without_cookies_and_with_the_callers_address(on, upstream):
    body = b'{"api_key": "x", "event": "e"}'
    response = on.browser.post(
        "/m/i/v0/e/?ip=0&_=1",
        content=body,
        headers={
            "Content-Type": "application/json",
            "Cookie": "readablestem_session=secret",
            "Authorization": "Bearer phc_abc",
            "X-Forwarded-For": "198.51.100.7",
            "Referer": "https://readablestem.com/jobs/abc",
        },
    )
    assert response.status_code == 200
    request = upstream[0]
    assert str(request.url) == f"{API}/i/v0/e/?ip=0&_=1"
    assert request.method == "POST" and request.content == body
    assert request.headers["content-type"] == "application/json"
    assert request.headers["authorization"] == "Bearer phc_abc"
    assert request.headers["x-forwarded-for"] == "198.51.100.7"
    for dropped in ("cookie", "referer"):
        assert dropped not in request.headers
    assert "set-cookie" not in response.headers  # PostHog's cookie is not passed to the browser
    assert response.headers["cache-control"] == "no-store"


def test_only_a_phc_bearer_token_is_forwarded(on, upstream):
    on.browser.post("/m/e/", content=b"{}", headers={"Authorization": "Bearer sk-other"})
    assert "authorization" not in upstream[0].headers
    on.browser.post("/m/e/", content=b"{}", headers={"Authorization": "Basic abc"})
    assert "authorization" not in upstream[1].headers


def test_the_proxy_reaches_only_posthog_hosts(on, upstream):
    for path in ("/m/static/../x", "/m/static/%2e%2e/x", "/m/a%5C..%5Cb", "/m//evil.example/x"):
        on.browser.get(path)
    for request in upstream:
        assert request.url.host in {"us.i.posthog.com", "us-assets.i.posthog.com"}
        assert ".." not in request.url.path


def test_assets_are_read_only_and_an_upstream_failure_is_a_502(on, upstream, monkeypatch):
    assert on.browser.post("/m/static/array.js", content=b"x").status_code == 405
    assert on.browser.post(f"/m/array/{analytics.TOKEN}/config", content=b"x").status_code == 405
    monkeypatch.setattr(analytics, "API_HOST", "https://boom.invalid")
    response = on.browser.post("/m/i/v0/e/", content=b"{}")
    assert response.status_code == 502 and response.headers["cache-control"] == "no-store"


def test_the_proxy_needs_no_sign_in_and_adds_no_session_cookie(on, upstream):
    response = on.browser.get("/m/static/array.js")
    assert response.status_code == 200
    assert "set-cookie" not in response.headers


# --------------------------------------------------------------------------- funnel events


def markers(html: str) -> list[tuple[str, dict]]:
    found = re.findall(
        r'<span hidden data-ph-event="([a-z_]+)"[^>]*data-ph-props=\'([^\']*)\'', html
    )
    return [(name, json.loads(props)) for name, props in found]


def sign_in(env: WebEnv, email: str = ALICE):
    client = env.browser
    return login(client, email), client


def test_a_sign_in_is_announced_once_on_the_next_page(on):
    response, client = sign_in(on)
    assert response.status_code == 303
    first = client.get("/app").text
    assert markers(first) == [("login_completed", {"provider": "dev"})]
    assert markers(client.get("/app").text) == []  # shown once


def test_a_new_account_is_announced_as_a_signup(tmp_path):
    env = WebEnv(tmp_path, analytics_enabled=True, signup_mode="open")
    with env.running() as client:
        env.browser = client
        login(client, "new.person@example.org")
        names = [name for name, _ in markers(client.get("/app").text)]
        assert names == ["signup_completed"]
        client.post("/logout", data={"csrf_token": csrf_from(client.get("/app").text)})
        login(client, "new.person@example.org")
        assert [n for n, _ in markers(client.get("/app").text)] == ["login_completed"]


def test_an_upload_carries_counts_and_no_file_name(on):
    _, client = sign_in(on)
    client.get("/app")  # the sign-in marker
    response = upload(client, make_pdf(2), filename="Midterm-grades-SECRET.pdf")
    job = job_id_from(response)
    page = client.get(f"/jobs/{job}")
    found = markers(page.text)
    assert found == [("document_uploaded", {"page_count": 2, "source": "pdf"})]


def test_a_refused_upload_announces_nothing(on):
    _, client = sign_in(on)
    client.get("/app")
    response = upload(client, b"not a pdf")
    assert response.status_code >= 400
    assert markers(response.text) == [] and markers(client.get("/app").text) == []


def test_a_finished_job_announces_its_conversion_once_per_job(on):
    _, client = sign_in(on)
    job = job_id_from(upload(client, make_pdf(2)))
    client.get(f"/jobs/{job}")  # the upload marker
    on.run_worker()
    page = client.get(f"/jobs/{job}").text
    done = re.findall(
        r'data-ph-event="conversion_completed" data-ph-once="conv-([0-9a-f]{32})" '
        r"data-ph-props='([^']*)'",
        page,
    )
    assert [d[0] for d in done] == [job]
    props = json.loads(done[0][1])
    assert set(props) == {"page_count", "duration_s", "status", "sample"}
    assert props["page_count"] == 2 and props["status"] == "done" and props["sample"] is False
    assert isinstance(props["duration_s"], int)
    # the polled fragment carries it too, so a page that was open while it finished sends it
    fragment = client.get(f"/jobs/{job}/status", headers={"HX-Request": "true"}).text
    assert "conversion_completed" in fragment


def test_nothing_is_announced_with_analytics_off(off):
    client = off.browser
    login(client, ALICE)
    job = job_id_from(upload(client, make_pdf(1)))
    off.run_worker()
    assert 'data-ph-event="conversion_completed"' not in client.get(f"/jobs/{job}").text
    assert 'data-ph-event="login_completed"' not in client.get("/app").text


def test_the_preview_links_are_tagged_as_result_opened(on):
    _, client = sign_in(on)
    job = job_id_from(upload(client, make_pdf(1)))
    on.run_worker()
    page = client.get(f"/jobs/{job}").text
    assert f'<a href="/jobs/{job}/view" data-ph-event="result_opened">' in page


def test_partial_pages_leave_the_queue_for_the_next_full_page(on):
    _, client = sign_in(on)
    client.get("/app/documents", headers={"HX-Request": "true"})
    assert [n for n, _ in markers(client.get("/app").text)] == ["login_completed"]


def test_properties_must_be_short_plain_values():
    settings = SimpleNamespace(analytics_enabled=True)
    app = SimpleNamespace(state=SimpleNamespace(ctx=SimpleNamespace(settings=settings)))
    request = SimpleNamespace(app=app, session={}, headers={})
    analytics.queue_event(request, "x", provider="google", page_count=3, ok=True)
    assert request.session[analytics.SESSION_KEY][0]["props"]["page_count"] == 3
    with pytest.raises(ValueError):
        analytics.queue_event(request, "x", label="a" * 40)
    with pytest.raises(ValueError):
        analytics.queue_event(request, "x", label=["a list"])


def test_visitor_ids_are_checked():
    assert analytics.visitor_id("0199f1c2-7a3e-7b8a-9d21-5c3f0a9e4b11") == (
        "0199f1c2-7a3e-7b8a-9d21-5c3f0a9e4b11"
    )
    for bad in (None, "", "short", "has space in it", "x" * 65, "a<script>b1234", "é" * 10):
        assert analytics.visitor_id(bad) is None


def test_a_direct_demo_visit_with_a_visitor_id_is_noted_server_side(on, upstream):
    visitor = "0199f1c2-7a3e-7b8a-9d21-5c3f0a9e4b11"
    response = on.browser.get(f"/demo?hm_id={visitor}")
    assert response.status_code == 200
    sent = [r for r in upstream if r.url.path == "/i/v0/e/"]
    assert len(sent) == 1
    payload = httpx.Response(200, content=sent[0].content).json()
    assert payload["event"] == "demo_opened" and payload["distinct_id"] == visitor
    assert payload["properties"]["site"] == "readablestem"
    assert payload["properties"]["$geoip_disable"] is True
    on.browser.get("/demo")  # no id: nothing is sent
    on.browser.get("/demo?hm_id=bad id")
    assert len([r for r in upstream if r.url.path == "/i/v0/e/"]) == 1


def test_demo_visits_are_not_noted_with_analytics_off(off, upstream):
    off.browser.get("/demo?hm_id=0199f1c2-7a3e-7b8a-9d21-5c3f0a9e4b11")
    assert upstream == []


# --------------------------------------------------------------------------- generated files


def test_the_loader_is_absent_from_everything_the_tool_produces(on, upstream):
    """The converted document says "No cookies or scripts are used"; that stays true."""
    _, client = sign_in(on)
    job = job_id_from(upload(client, make_pdf(2)))
    on.run_worker()
    outputs: dict[str, str] = {}
    outputs["view"] = client.get(f"/jobs/{job}/view").text
    outputs["download"] = client.get(f"/jobs/{job}/download").text
    outputs["demo"] = client.get("/demo").text
    canvas = client.get(f"/jobs/{job}/download/canvas")
    cartridge = client.get(f"/jobs/{job}/download/cartridge")
    for name, response in (("canvas", canvas), ("cartridge", cartridge)):
        assert response.status_code == 200, name
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            for member in archive.namelist():
                if member.endswith((".html", ".htm", ".xml", ".js")):
                    outputs[f"{name}:{member}"] = archive.read(member).decode("utf-8", "replace")
    assert len(outputs) > 4
    for name, text in outputs.items():
        # JSON-LD is data for search engines, not a script that runs.
        lowered = re.sub(
            r'<script type="application/ld\+json">.*?</script>', "", text, flags=re.S
        ).lower()
        assert "<script" not in lowered, name
        for needle in ("posthog", "analytics", "data-ph-", "/m/static", "/m/i/"):
            assert needle not in lowered, (name, needle)
    # the app's pages around them do carry it
    assert "analytics" in client.get(f"/jobs/{job}").text


def test_the_converted_document_keeps_the_sandbox_policy(on):
    _, client = sign_in(on)
    job = job_id_from(upload(client, make_pdf(1)))
    on.run_worker()
    from readablestem.web.security import VIEW_CSP

    assert client.get(f"/jobs/{job}/view").headers["content-security-policy"] == VIEW_CSP
    assert client.get("/demo").headers["content-security-policy"] == VIEW_CSP
