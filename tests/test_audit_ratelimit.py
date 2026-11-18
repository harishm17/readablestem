"""Launch audit: per-address rate limits on sign-in, upload and the cron endpoint.

Before this, nothing limited how fast a script could try sign-ins or post uploads (every
upload that is not a PDF still costs a probe process).
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from fixtures.web.support import (
    ALICE,
    BOB,
    WebEnv,
    csrf_from,
    login,
    make_pdf,
    token,
    upload,
)
from readablestem.web.abuse import RateLimiter, Rule, client_ip


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def request_with(headers: dict[str, str], peer: str = "10.0.0.9") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 1234)})


# --------------------------------------------------------------------------- the limiter


def test_allows_up_to_the_limit_then_says_how_long_to_wait():
    clock = Clock()
    limiter = RateLimiter(rules={"r": Rule(3, 60)}, clock=clock)
    assert [limiter.check("r", "a") for _ in range(3)] == [None, None, None]
    wait = limiter.check("r", "a")
    assert wait is not None and 1 <= wait <= 61
    assert limiter.check("r", "b") is None  # another address is not affected


def test_the_window_slides_and_refused_attempts_do_not_extend_it():
    clock = Clock()
    limiter = RateLimiter(rules={"r": Rule(2, 60)}, clock=clock)
    limiter.check("r", "a")
    clock.now += 30
    limiter.check("r", "a")
    for _ in range(50):  # hammering while blocked changes nothing
        assert limiter.check("r", "a") is not None
    clock.now += 31  # the first attempt is now 61 s old
    assert limiter.check("r", "a") is None
    assert limiter.check("r", "a") is not None


def test_memory_is_bounded_when_keys_are_made_up():
    clock = Clock()
    limiter = RateLimiter(rules={"r": Rule(5, 60)}, clock=clock, max_keys=100)
    for n in range(10_000):
        limiter.check("r", f"key-{n}")
    assert len(limiter._hits) <= 100


# --------------------------------------------------------------------------- the address


def test_the_address_is_the_one_the_trusted_proxy_wrote_not_one_the_client_wrote():
    # Cloud Run appends the address it saw to whatever the client sent.
    spoofed = request_with({"x-forwarded-for": "6.6.6.6, 203.0.113.7"})
    assert client_ip(spoofed, trusted_hops=1) == "203.0.113.7"
    behind_balancer = request_with({"x-forwarded-for": "6.6.6.6, 203.0.113.7, 130.211.0.1"})
    assert client_ip(behind_balancer, trusted_hops=2) == "203.0.113.7"
    assert client_ip(spoofed, trusted_hops=0) == "10.0.0.9"  # no proxy: the header is ignored
    replaced = request_with({"x-forwarded-for": "203.0.113.7"})  # a host that replaces the header
    assert client_ip(replaced, trusted_hops=1) == "203.0.113.7"


def test_a_header_that_is_not_an_address_falls_back_to_the_peer():
    assert client_ip(request_with({"x-forwarded-for": "not-an-ip"}), 1) == "10.0.0.9"
    assert client_ip(request_with({}), 1) == "10.0.0.9"
    assert client_ip(request_with({"x-forwarded-for": "1.2.3.4"}), 2) == "10.0.0.9"  # too short


def test_ipv6_clients_share_a_limit_per_64_bit_prefix():
    one = request_with({"x-forwarded-for": "2001:db8:1:2:aaaa::1"})
    two = request_with({"x-forwarded-for": "2001:db8:1:2:bbbb::9"})
    other = request_with({"x-forwarded-for": "2001:db8:1:3::1"})
    assert client_ip(one, 1) == client_ip(two, 1) != client_ip(other, 1)


# --------------------------------------------------------------------------- the routes


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, rate_limits_enabled=True)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        env.browser = client
        yield env


def test_sign_in_attempts_are_limited_per_address(web):
    rule = web.ctx.limiter.rules["login_dev"]
    page = web.browser.get("/dev-login").text
    statuses = [
        web.browser.post(
            "/dev-login", data={"csrf_token": csrf_from(page), "email": "nobody@example.org"}
        ).status_code
        for _ in range(rule.limit + 3)
    ]
    assert statuses[: rule.limit] == [403] * rule.limit  # not on the list
    assert statuses[rule.limit :] == [429] * 3
    refused = web.browser.post("/dev-login", data={"csrf_token": csrf_from(page), "email": ALICE})
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) > 0
    assert "Too many sign-in attempts" in refused.text
    assert web.browser.get("/app").status_code == 303  # still signed out


def test_a_refused_form_is_not_an_attempt_and_the_wait_page_says_what_and_where_next(web):
    """The sign-in rule is counted after the form's CSRF check, so an expired form does not
    use an attempt; when the allowance is used up the page names it and links back."""
    rule = web.ctx.limiter.rules["login_dev"]
    page = web.browser.get("/dev-login").text
    for _ in range(rule.limit + 3):
        expired = web.browser.post("/dev-login", data={"csrf_token": "old", "email": ALICE})
        assert expired.status_code == 403  # the expired form, not the limit
    good = {"csrf_token": csrf_from(page), "email": "nobody@example.org"}
    assert web.browser.post("/dev-login", data=good).status_code == 403  # still has attempts
    for _ in range(rule.limit - 1):
        web.browser.post("/dev-login", data=good)
    limited = web.browser.post("/dev-login", data=good)
    assert limited.status_code == 429
    assert "sign-in attempts" in limited.text and 'href="/login"' in limited.text


def test_google_and_microsoft_callbacks_are_limited(web):
    rule = web.ctx.limiter.rules["callback"]
    codes = [
        web.browser.get("/auth/callback?code=x&state=y").status_code for _ in range(rule.limit + 2)
    ]
    assert codes[-1] == 429 and 429 not in codes[: rule.limit]


def test_refused_uploads_do_not_use_the_per_person_allowance(web):
    """A refused file gives its count back (the per-person rule counts accepted files), so a
    person fixing a refused file is not stopped early; the per-address rule stays on requests,
    so a script sending refused files in a loop is still stopped."""
    login(web.browser, ALICE)
    person = web.ctx.limiter.rules["upload_user"]
    csrf = token(web.browser)
    refused = [upload(web.browser, b"not a pdf", csrf=csrf).status_code for _ in range(3)]
    assert refused == [415] * 3
    good = [upload(web.browser, make_pdf(1), csrf=csrf).status_code for _ in range(person.limit)]
    assert good == [303] * person.limit  # today the 3 refusals take 3 of the 15
    assert upload(web.browser, make_pdf(1), csrf=csrf).status_code == 429
    bob = web.client()
    login(bob, BOB)
    assert upload(bob, b"not a pdf").status_code == 415
    bob.close()


def test_refused_requests_in_a_loop_are_still_stopped_per_address(web):
    login(web.browser, ALICE)
    address = web.ctx.limiter.rules["upload"]
    csrf = token(web.browser)
    statuses = [
        upload(web.browser, b"not a pdf", csrf=csrf).status_code for _ in range(address.limit + 2)
    ]
    assert statuses[: address.limit] == [415] * address.limit
    assert statuses[address.limit :] == [429, 429]


def test_the_zip_routes_share_one_per_person_allowance(web):
    """A zip builds every ready document's files, so the four zip routes count against one
    rule per person: the wait comes back in `Retry-After`, and another person is not held."""
    login(web.browser, ALICE)
    rule = web.ctx.limiter.rules["zip_user"]
    first = [web.browser.get("/app/download").status_code for _ in range(rule.limit - 1)]
    assert first == [409] * (rule.limit - 1)  # nothing is ready: the build was still asked for
    assert web.browser.head("/app/download").status_code == 200  # a HEAD builds nothing: free
    assert web.browser.get("/account/export").status_code == 200  # the last one allowed
    refused = web.browser.get("/app/download?format=docx")
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) > 0
    assert "Too many zip downloads" in refused.text
    assert web.browser.get("/account/export").status_code == 429
    bob = web.client()
    login(bob, BOB)
    assert bob.get("/app/download").status_code == 409
    bob.close()


def test_a_normal_afternoon_is_never_limited(web):
    """Several uploads and page loads by one person stay inside every allowance."""
    login(web.browser, ALICE)
    for _ in range(6):
        assert upload(web.browser, make_pdf(1)).status_code == 303
        assert web.browser.get("/app").status_code == 200


def test_the_cron_endpoint_limits_wrong_tokens(web):
    path = "/internal/cron/aggregate-and-purge"
    rule = web.ctx.limiter.rules["cron"]
    codes = [
        web.browser.post(path, headers={"X-Cron-Token": "guess"}).status_code
        for _ in range(rule.limit + 2)
    ]
    assert codes[: rule.limit] == [403] * rule.limit and codes[-1] == 429


def test_production_does_not_start_with_the_limits_off(tmp_path):
    from fixtures.web.support import make_settings
    from readablestem.config import ConfigError
    from readablestem.web.app import create_app

    settings = make_settings(
        tmp_path,
        env="prod",
        dev_login_enabled=False,
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/never",
        base_url="https://beta.example.org",
        session_secret="s" * 40,
        google_client_id="client-id",
        google_client_secret="client-secret",
        openai_api_key="sk-test",
        rate_limits_enabled=False,
    )
    with pytest.raises(ConfigError, match="RATE_LIMITS_ENABLED"):
        create_app(settings)
