"""Sign-in, allowlist, sessions and CSRF (offline: dev login and a stubbed Google client)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from a11y_stem.web.app import create_app
from a11y_stem.web.db import User, session_scope
from a11y_stem.web.security import SecurityHeadersMiddleware
from fixtures.web.support import (
    ADMIN,
    ALICE,
    BOB,
    WebEnv,
    csrf_from,
    login,
    make_settings,
)

JOB = "0" * 32
PROTECTED_GETS = [
    "/app",
    f"/jobs/{JOB}",
    f"/jobs/{JOB}/status",
    f"/jobs/{JOB}/download",
    f"/jobs/{JOB}/view",
    f"/jobs/{JOB}/crops/p1-e1.png",
    f"/jobs/{JOB}/review",
    "/admin",
]


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.browser = client
        yield env


def all_routes(routes):
    """Flatten included routers (FastAPI keeps them as wrapper routes)."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from all_routes(inner.routes)
        elif hasattr(route, "methods"):
            yield route


def users(env) -> list[str]:
    with session_scope(env.ctx.factory) as session:
        return list(session.scalars(select(User.email)))


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_protected_pages_send_anonymous_visitors_to_sign_in(web, path):
    response = web.browser.get(path)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("path", ["/", "/demo", "/privacy", "/terms", "/limits", "/login"])
def test_public_pages_need_no_sign_in(web, path):
    assert web.browser.get(path).status_code == 200


def test_people_not_on_the_allowlist_get_a_polite_403_and_are_not_stored(web):
    response = login(web.browser, ALICE)
    assert response.status_code == 403
    assert "not on the beta list" in response.text
    assert "help@example.org" in response.text  # settings.contact_email
    assert ALICE not in users(web)
    assert web.browser.get("/app").status_code == 303


def test_allowlisted_user_signs_in_and_session_is_rotated(web):
    web.allow(ALICE)
    before = csrf_from(web.browser.get("/dev-login").text)
    response = login(web.browser, ALICE)
    assert response.status_code == 303 and response.headers["location"] == "/app"
    page = web.browser.get("/app")
    assert page.status_code == 200
    assert csrf_from(page.text) != before  # new CSRF token after sign-in
    with session_scope(web.ctx.factory) as session:
        user = session.scalar(select(User).where(User.email == ALICE))
        assert user is not None and user.last_login_at is not None


def test_emails_are_case_insensitive(web):
    web.allow(ALICE)
    assert login(web.browser, "  Alice@Example.ORG ").status_code == 303


def test_admin_emails_from_settings_are_seeded_as_allowed_admins(web):
    with session_scope(web.ctx.factory) as session:
        admin = session.scalar(select(User).where(User.email == ADMIN))
        assert admin is not None and admin.is_allowed and admin.is_admin
    login(web.browser, ADMIN)
    assert web.browser.get("/admin").status_code == 200


def test_session_cookie_is_httponly_lax_and_lasts_seven_days(web):
    web.allow(ALICE)
    response = login(web.browser, ALICE)
    cookie = response.headers["set-cookie"].lower()
    assert "a11y_stem_session=" in cookie
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "max-age=604800" in cookie
    assert "secure" not in cookie  # https_only only in production


def prod_settings(tmp_path, **overrides):
    values = {
        "env": "prod",
        "dev_login_enabled": False,
        "database_url": "postgresql+psycopg://u:p@127.0.0.1:1/never",
        "base_url": "https://beta.example.org",
        "session_secret": "s" * 40,
        "google_client_id": "client-id",
        "google_client_secret": "client-secret",
        "openai_api_key": "sk-test",
    }
    return make_settings(tmp_path, **(values | overrides))


def test_production_cookie_is_secure_and_hsts_is_on(tmp_path):
    app = create_app(prod_settings(tmp_path))  # no lifespan: nothing connects to the database
    options = {m.cls: m.kwargs for m in app.user_middleware}
    assert options[SessionMiddleware]["https_only"] is True
    assert options[SessionMiddleware]["max_age"] == 7 * 24 * 3600
    assert options[SecurityHeadersMiddleware]["hsts"] is True


def test_production_refuses_to_start_without_its_limits(tmp_path):
    from a11y_stem.config import ConfigError

    with pytest.raises(ConfigError):
        create_app(make_settings(tmp_path, env="prod", dev_login_enabled=False))


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({"session_secret": "", "cron_token": ""}, "SESSION_SECRET, CRON_TOKEN"),
        ({"cron_token": "   "}, "CRON_TOKEN"),
        ({"google_client_secret": ""}, "GOOGLE_CLIENT_SECRET"),
        ({"openai_api_key": ""}, "OPENAI_API_KEY"),
        ({"session_secret": "short"}, "SESSION_SECRET must be at least 32"),
    ],
)
def test_production_refuses_empty_or_weak_secrets(tmp_path, overrides, named):
    """Blank secrets (`SecretStr("")`, whitespace) are refused by the spine's
    `check_production`; a weak session secret passes it and is refused by the web app."""
    from a11y_stem.config import ConfigError

    settings = prod_settings(tmp_path, **overrides)
    with pytest.raises(ConfigError, match=named) as refused:
        create_app(settings)
    assert "s" * 40 not in str(refused.value) and "sk-test" not in str(refused.value)


def test_production_never_falls_back_to_a_random_session_secret(tmp_path):
    from a11y_stem.config import ConfigError
    from a11y_stem.web.app import _session_secret

    with pytest.raises(ConfigError):
        _session_secret(prod_settings(tmp_path, session_secret=None))
    assert len(_session_secret(make_settings(tmp_path, session_secret=None))) >= 32  # dev/test


def test_dev_login_is_404_unless_enabled(tmp_path):
    env = WebEnv(tmp_path, env="dev", dev_login_enabled=False)
    with env.running() as client:
        assert client.get("/dev-login").status_code == 404
        # The enabled-check runs before anything else, so even a valid form gets 404.
        assert client.post("/dev-login", data={"email": ADMIN}).status_code == 404
        assert "/dev-login" not in client.get("/login").text


def test_dev_login_rejects_malformed_email(web):
    token = csrf_from(web.browser.get("/dev-login").text)
    response = web.browser.post("/dev-login", data={"csrf_token": token, "email": "nope"})
    assert response.status_code == 422
    assert 'href="#email"' in response.text  # error summary links to the field
    assert "<title>Error: " in response.text


def test_logout_is_post_only_with_csrf(web):
    web.allow(ALICE)
    login(web.browser, ALICE)
    assert web.browser.get("/logout").status_code == 405
    assert web.browser.post("/logout", data={}).status_code == 403
    token = csrf_from(web.browser.get("/app").text)
    response = web.browser.post("/logout", data={"csrf_token": token})
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert web.browser.get("/app").status_code == 303


def test_removing_someone_from_the_allowlist_ends_their_access_immediately(web):
    web.allow(ALICE)
    login(web.browser, ALICE)
    assert web.browser.get("/app").status_code == 200
    from a11y_stem.web.auth import set_allowed

    with session_scope(web.ctx.factory) as session:
        set_allowed(session, ALICE, allowed=False)
    assert web.browser.get("/app").status_code == 303


def copy_of(web, browser):
    """Another browser holding a copy of `browser`'s session cookie."""
    other = web.client()
    for name, value in browser.cookies.items():
        other.cookies.set(name, value)
    return other


def sign_out(browser) -> None:
    token = csrf_from(browser.get("/app").text)
    assert browser.post("/logout", data={"csrf_token": token}).status_code == 303


def test_signing_out_ends_copied_sessions_too(web):
    """The cookie is signed, not stored server-side: clearing it in one browser used to
    leave every copy of it (another device, a stolen cookie) valid for 7 days."""
    web.allow(ALICE)
    login(web.browser, ALICE)
    copied = copy_of(web, web.browser)
    assert copied.get("/app").status_code == 200
    sign_out(web.browser)
    assert copied.get("/app").status_code == 303  # sent to sign in
    login(web.browser, ALICE)  # signing in again works, and only the new session counts
    assert web.browser.get("/app").status_code == 200
    assert copied.get("/app").status_code == 303
    copied.close()


def test_a_session_from_before_removal_stays_ended_when_allowed_back(web):
    from a11y_stem.web.auth import set_allowed

    web.allow(ALICE)
    login(web.browser, ALICE)
    copied = copy_of(web, web.browser)
    with session_scope(web.ctx.factory) as session:
        set_allowed(session, ALICE, allowed=False)
    web.allow(ALICE)
    assert copied.get("/app").status_code == 303
    copied.close()


def test_signing_out_does_not_touch_other_people(web):
    web.allow(ALICE)
    web.allow(BOB)
    login(web.browser, ALICE)
    bob = web.client()
    login(bob, BOB)
    sign_out(web.browser)
    assert bob.get("/app").status_code == 200
    bob.close()


def test_admin_page_is_admins_only(web):
    web.allow(ALICE)
    login(web.browser, ALICE)
    response = web.browser.get("/admin")
    assert response.status_code == 403
    assert "Admins only" in response.text


# --------------------------------------------------------------------------- CSRF


def test_every_state_changing_route_rejects_a_missing_csrf_token(web):
    """Enumerates the app's POST routes so a new one cannot skip the check unnoticed."""
    web.allow(ALICE)
    login(web.browser, ALICE)
    posts = sorted(
        route.path for route in all_routes(web.app.routes) if "POST" in (route.methods or ())
    )
    assert posts == [
        "/admin/allowlist",
        "/admin/allowlist/remove",
        # Courses and their notation (FEATURE_NOTATION_MEMORY): CSRF before the flag.
        "/courses",
        "/courses/{course_id}/delete",
        "/courses/{course_id}/entries",
        "/courses/{course_id}/entries/{entry_id}",
        "/courses/{course_id}/entries/{entry_id}/delete",
        "/courses/{course_id}/remember",
        "/courses/{course_id}/rename",
        "/courses/{course_id}/suggestions",
        "/dev-login",
        "/internal/cron/aggregate-and-purge",  # token-authenticated, no session
        "/jobs",
        "/jobs/{job_id}/contact",  # the student file's contact line (T1-17)
        "/jobs/{job_id}/delete",
        "/jobs/{job_id}/edit/{element_id}",  # the words of a paragraph; a heading made (T3-5)
        "/jobs/{job_id}/feedback",
        "/jobs/{job_id}/keep",  # keep 7 more days (T1-8)
        "/jobs/{job_id}/language",
        "/jobs/{job_id}/retry",  # try a stopped document again (T3-1)
        "/jobs/{job_id}/pages/{number}/add",  # a picture or an equation the tool missed (T3-5)
        "/jobs/{job_id}/pages/{number}/omit",  # a page left out (T3-16)
        "/jobs/{job_id}/pages/{number}/restore",
        "/jobs/{job_id}/review/{element_id}",
        "/jobs/{job_id}/review/{element_id}/check",  # changes nothing, but sends LaTeX
        "/jobs/{job_id}/tables/accept-clean",  # the clean tables, in one write (T3-11)
        "/jobs/{job_id}/tables/{element_id}/read-cell",  # a maths cell read by the model (T3-11)
        "/jobs/{job_id}/title",  # the title students hear (T1-7)
        "/jobs/{job_id}/undo",
        "/login",
        "/login/microsoft",
        "/logout",
        "/sample",  # opens the recorded sample document (T3-9)
        "/uploads/{upload_id}",  # choose the pages of a large file (T3-3)
        "/uploads/{upload_id}/delete",
    ]
    for path in posts:
        url = path.replace("{job_id}", JOB).replace("{element_id}", "p1-e1")
        url = url.replace("{course_id}", JOB).replace("{entry_id}", JOB).replace("{number}", "1")
        url = url.replace("{upload_id}", JOB)
        if path == "/jobs":
            response = web.browser.post(
                url, data={"csrf_token": "forged"}, files={"pdf": ("a.pdf", b"%PDF-1.4", "x")}
            )
        else:
            response = web.browser.post(url, data={"csrf_token": "forged"})
        assert response.status_code == 403, path


def test_csrf_failure_page_explains_what_to_do(web):
    response = web.browser.post("/login", data={})
    assert response.status_code == 403
    assert "This form has expired" in response.text


# --------------------------------------------------------------------------- Google


@pytest.fixture
def google(tmp_path, monkeypatch):
    env = WebEnv(
        tmp_path,
        dev_login_enabled=False,
        google_client_id="client-id",
        google_client_secret="client-secret",
        base_url="https://beta.example.org",
    )
    google = env.ctx.oauth.google
    sent: dict = {}

    async def create_authorization_url(redirect_uri=None, **kwargs):
        sent["redirect_uri"] = redirect_uri
        return {
            "url": "https://accounts.google.com/o/oauth2/v2/auth?state=abc",
            "state": "abc",
            "nonce": "n",
        }

    monkeypatch.setattr(google, "create_authorization_url", create_authorization_url)
    env.userinfo = {"email": ALICE, "email_verified": True, "name": "Alice"}
    env.sent = sent

    async def authorize_access_token(request, **kwargs):
        if env.userinfo is None:
            from authlib.integrations.starlette_client import OAuthError

            raise OAuthError(error="access_denied")
        return {"userinfo": env.userinfo}

    monkeypatch.setattr(google, "authorize_access_token", authorize_access_token)
    with env.running() as client:
        env.browser = client
        yield env


def test_sign_in_button_redirects_to_google_with_our_callback(google):
    token = csrf_from(google.browser.get("/login").text)
    response = google.browser.post("/login", data={"csrf_token": token})
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.com/")
    assert google.sent["redirect_uri"] == "https://beta.example.org/auth/callback"


def test_google_sign_in_for_allowlisted_verified_email(google):
    google.allow(ALICE)
    response = google.browser.get("/auth/callback?code=c&state=abc")
    assert response.status_code == 303 and response.headers["location"] == "/app"
    assert google.browser.get("/app").status_code == 200
    with session_scope(google.ctx.factory) as session:
        user = session.scalar(select(User).where(User.email == ALICE))
        assert user is not None and user.name == "Alice"


def test_google_sign_in_requires_a_verified_email(google):
    google.allow(ALICE)
    google.userinfo = {"email": ALICE, "email_verified": False}
    response = google.browser.get("/auth/callback?code=c&state=abc")
    assert response.status_code == 403
    assert "not verified" in response.text
    assert google.browser.get("/app").status_code == 303


def test_google_sign_in_not_on_allowlist(google):
    google.userinfo = {"email": BOB, "email_verified": True}
    response = google.browser.get("/auth/callback?code=c&state=abc")
    assert response.status_code == 403
    assert BOB not in users(google)


def test_google_error_shows_a_retry_page(google):
    google.userinfo = None
    response = google.browser.get("/auth/callback?error=access_denied")
    assert response.status_code == 400
    assert "Sign-in did not finish" in response.text


def test_without_google_credentials_sign_in_button_is_hidden(web):
    page = web.browser.get("/login").text
    assert "Sign in with Google" not in page
    assert "/dev-login" in page
    assert web.browser.get("/auth/callback").status_code == 404
