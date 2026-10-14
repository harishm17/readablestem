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
    rule = web.ctx.limiter.rules["login"]
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
    assert "Too many attempts" in refused.text
    assert web.browser.get("/app").status_code == 303  # still signed out


def test_another_address_is_not_locked_out(web):
    rule = web.ctx.limiter.rules["login"]
    page = web.browser.get("/dev-login").text
    for _ in range(rule.limit + 1):
        web.browser.post("/dev-login", data={"csrf_token": csrf_from(page), "email": "x@e.org"})
    other = web.client()
    page = other.get("/dev-login").text
    response = other.post(
        "/dev-login",
        data={"csrf_token": csrf_from(page), "email": ALICE},
        headers={"X-Forwarded-For": "198.51.100.4"},  # a different address behind the proxy
    )
    assert response.status_code == 303
    other.close()


def test_google_and_microsoft_callbacks_are_limited(web):
    rule = web.ctx.limiter.rules["callback"]
    codes = [
        web.browser.get("/auth/callback?code=x&state=y").status_code for _ in range(rule.limit + 2)
    ]
    assert codes[-1] == 429 and 429 not in codes[: rule.limit]


def test_uploads_are_limited_per_person_and_a_rejected_upload_counts(web):
    login(web.browser, ALICE)
    rule = web.ctx.limiter.rules["upload_user"]
    csrf = token(web.browser)
    statuses = [
        upload(web.browser, b"not a pdf", csrf=csrf).status_code for _ in range(rule.limit + 2)
    ]
    assert statuses[: rule.limit] == [415] * rule.limit
    assert statuses[rule.limit :] == [429, 429]
    bob = web.client()
    login(bob, BOB)
    assert upload(bob, b"not a pdf").status_code == 415  # Bob has his own allowance
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
