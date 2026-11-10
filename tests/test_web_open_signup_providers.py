"""Open sign-up through Google and Microsoft (their callbacks, with the providers stubbed).

Google is answered by a stub of Authlib's client, as in tests/test_web_auth.py; Microsoft by
the fake identity platform of tests/fixtures/web/oidc.py, so the real token checks run.
What these pin down: an account is made only for a verified identity; the Google or Microsoft
identity is recorded with it; a blocked person, a full day and the per-address rule turn a
sign-in away without storing anything; and allowlist mode is as it was.
"""

from __future__ import annotations

from sqlalchemy import func, select

from fixtures.web.oidc import CLIENT_ID, CLIENT_SECRET, OTHER_TENANT, TENANT, FakeMicrosoft
from fixtures.web.support import ALICE, BOB, WebEnv, csrf_from
from readablestem.web import access
from readablestem.web.auth import set_blocked
from readablestem.web.database import utc_today
from readablestem.web.db import AuthIdentity, User, session_scope
from readablestem.web.identities import GOOGLE_ISSUER
from readablestem.web.quota import signups_today

GOOGLE = {
    "dev_login_enabled": False,
    "google_client_id": "client-id",
    "google_client_secret": "client-secret",
    "base_url": "https://beta.example.org",
}
MS = {
    "ms_client_id": CLIENT_ID,
    "ms_client_secret": CLIENT_SECRET,
    "ms_allowed_tenants": [TENANT],
}


def google_env(tmp_path, monkeypatch, **overrides) -> WebEnv:
    env = WebEnv(tmp_path, **GOOGLE | overrides)
    google = env.ctx.oauth.google
    env.userinfo = {"email": ALICE, "email_verified": True, "name": "Alice", "sub": "g-alice"}

    async def authorize_access_token(request, **kwargs):
        return {"userinfo": env.userinfo}

    monkeypatch.setattr(google, "authorize_access_token", authorize_access_token)
    return env


def callback(client):
    return client.get("/auth/callback?code=c&state=abc")


def user(env, email: str) -> User | None:
    with session_scope(env.ctx.factory) as session:
        return session.scalar(select(User).where(User.email == email))


def identities(env) -> list[tuple[str, str, str]]:
    with session_scope(env.ctx.factory) as session:
        rows = session.scalars(select(AuthIdentity).order_by(AuthIdentity.provider))
        return [(r.provider, r.issuer, r.subject) for r in rows]


def users(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return int(session.scalar(select(func.count()).select_from(User)) or 0)


# --------------------------------------------------------------------------- Google


def test_a_verified_google_account_gets_an_open_tier_account_at_the_first_sign_in(
    tmp_path, monkeypatch
):
    env = google_env(tmp_path, monkeypatch, signup_mode="open")
    with env.running() as client:
        response = callback(client)
        assert response.status_code == 303 and response.headers["location"] == "/app"
        row = user(env, ALICE)
        assert row is not None and row.access_tier == access.OPEN and row.name == "Alice"
        assert row.is_allowed and not row.is_admin
        assert identities(env) == [("google", GOOGLE_ISSUER, "g-alice")]
        assert client.get("/app").status_code == 200
        # The second sign-in is the same account, and counts as no new one.
        again = env.client()
        assert callback(again).status_code == 303
        assert users(env) == 2  # the seeded admin and Alice
        with session_scope(env.ctx.factory) as session:
            assert signups_today(session, utc_today()) == 1


def test_google_without_an_address_gets_no_account(tmp_path, monkeypatch):
    env = google_env(tmp_path, monkeypatch, signup_mode="open")
    with env.running() as client:
        env.userinfo = {"email_verified": True}
        assert callback(client).status_code == 403
        assert users(env) == 1


def test_a_blocked_google_account_is_refused_in_open_mode(tmp_path, monkeypatch):
    env = google_env(tmp_path, monkeypatch, signup_mode="open")
    with env.running() as client:
        callback(client)
        with session_scope(env.ctx.factory) as session:
            set_blocked(session, ALICE, blocked=True)
        again = env.client()
        response = callback(again)
        assert response.status_code == 403 and "This account cannot be used" in response.text
        assert users(env) == 2 and identities(env) == [("google", GOOGLE_ISSUER, "g-alice")]


def test_the_per_address_rule_applies_to_the_google_callback_too(tmp_path, monkeypatch):
    env = google_env(tmp_path, monkeypatch, signup_mode="open", rate_limits_enabled=True)
    with env.running():
        codes = []
        for n in range(6):
            env.userinfo = {"email": f"p{n}@example.org", "email_verified": True, "sub": f"s{n}"}
            codes.append(callback(env.client()).status_code)
        assert codes == [303] * 5 + [429]
        assert user(env, "p5@example.org") is None


# --------------------------------------------------------------------------- Microsoft


def microsoft_env(tmp_path, **overrides) -> tuple[WebEnv, FakeMicrosoft]:
    env = WebEnv(tmp_path, **GOOGLE | MS | overrides)
    fake = FakeMicrosoft(ALICE)
    fake.claims["xms_edov"] = True  # the directory vouches for the address (open sign-up needs it)
    fake.install(env.ctx)
    return env, fake


def ms_sign_in(env, fake, client):
    token = csrf_from(client.get("/login").text)
    started = client.post("/login/microsoft", data={"csrf_token": token})
    assert started.status_code == 302, started.text
    return client.get(fake.authorize(started.headers["location"]))


def test_a_microsoft_account_of_an_allowed_tenant_gets_an_open_tier_account(tmp_path):
    env, fake = microsoft_env(tmp_path, signup_mode="open")
    with env.running() as client:
        response = ms_sign_in(env, fake, client)
        assert response.status_code == 303, response.text
        row = user(env, ALICE)
        assert row is not None and row.access_tier == access.OPEN
        assert [i[0] for i in identities(env)] == ["microsoft"]
        assert client.get("/app").status_code == 200
        # The identity alone signs the person in next time, and makes nothing.
        again = env.client()
        assert ms_sign_in(env, fake, again).status_code == 303
        assert users(env) == 2
        with session_scope(env.ctx.factory) as session:
            assert signups_today(session, utc_today()) == 1


def test_microsoft_blocked_and_full_days_are_refused_without_storing_anything(tmp_path):
    env, fake = microsoft_env(tmp_path, signup_mode="open", max_new_accounts_per_day=1)
    with env.running() as client:
        assert ms_sign_in(env, fake, client).status_code == 303
        with session_scope(env.ctx.factory) as session:
            set_blocked(session, ALICE, blocked=True)
        blocked = ms_sign_in(env, fake, env.client())
        assert blocked.status_code == 403 and "This account cannot be used" in blocked.text

        fake.claims["sub"] = "someone-else"
        fake.claims["email"] = BOB
        full = ms_sign_in(env, fake, env.client())
        assert full.status_code == 503 and "Sign-ups are full for today" in full.text
        assert user(env, BOB) is None and len(identities(env)) == 1


def test_a_microsoft_token_from_another_directory_still_makes_no_account(tmp_path):
    env, fake = microsoft_env(tmp_path, signup_mode="open")
    fake.claims["tid"] = OTHER_TENANT
    with env.running() as client:
        response = ms_sign_in(env, fake, client)
        assert response.status_code == 403
        assert user(env, ALICE) is None and identities(env) == []
        with session_scope(env.ctx.factory) as session:
            assert signups_today(session, utc_today()) == 0


# --------------------------------------------------------------------------- Microsoft: trust


def refusal(response) -> str:
    return " ".join(response.text.split())


def test_an_unverified_address_cannot_take_over_an_open_account(tmp_path):
    env, fake = microsoft_env(tmp_path, signup_mode="open")
    with env.running():
        with session_scope(env.ctx.factory) as session:  # a Google sign-up made this account
            session.add(User(email=ALICE, is_allowed=True, access_tier=access.OPEN))
        fake.claims["xms_edov"] = False
        response = ms_sign_in(env, fake, env.client())
        assert response.status_code == 403 and "Please sign in with Google" in refusal(response)
        assert identities(env) == []


def test_a_first_microsoft_sign_in_does_not_link_to_an_invited_account_or_an_admin(tmp_path):
    env, fake = microsoft_env(tmp_path, signup_mode="open")
    with env.running():
        env.allow(ALICE)  # invited
        response = ms_sign_in(env, fake, env.client())
        assert response.status_code == 403 and "Please sign in with Google" in refusal(response)
        assert identities(env) == []
        env.allow(ALICE, admin=True)
        assert ms_sign_in(env, fake, env.client()).status_code == 403
        assert identities(env) == []
        # An open-tier admin is refused too: the check is on the role, not only the tier.
        with session_scope(env.ctx.factory) as session:
            row = session.scalar(select(User).where(User.email == ALICE))
            assert row is not None
            row.access_tier = access.OPEN
        assert ms_sign_in(env, fake, env.client()).status_code == 403
        assert identities(env) == []
