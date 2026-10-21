"""Open sign-up through Google and Microsoft (their callbacks, with the providers stubbed).

Google is answered by a stub of Authlib's client, as in tests/test_web_auth.py; Microsoft by
the fake identity platform of tests/fixtures/web/oidc.py, so the real token checks run.
What these pin down: an account is made only for a verified identity; the Google or Microsoft
identity is recorded with it; a blocked person, a full day and the per-address rule turn a
sign-in away without storing anything; and allowlist mode is as it was.
"""

from __future__ import annotations

import pytest
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


def test_an_unverified_google_address_gets_no_account_in_open_mode(tmp_path, monkeypatch):
    env = google_env(tmp_path, monkeypatch, signup_mode="open")
    with env.running() as client:
        before = users(env)
        for unverified in (False, "false", None):
            env.userinfo = {"email": BOB, "email_verified": unverified}
            response = callback(client)
            assert response.status_code == 403 and "not verified" in response.text
        assert user(env, BOB) is None and users(env) == before
        assert client.get("/app").status_code == 303


def test_google_without_an_address_gets_no_account(tmp_path, monkeypatch):
    env = google_env(tmp_path, monkeypatch, signup_mode="open")
    with env.running() as client:
        env.userinfo = {"email_verified": True}
        assert callback(client).status_code == 403
        assert users(env) == 1


def test_allowlist_mode_still_refuses_a_verified_stranger_and_stores_nothing(tmp_path, monkeypatch):
    env = google_env(tmp_path, monkeypatch, signup_mode="allowlist")
    with env.running() as client:
        response = callback(client)
        assert response.status_code == 403 and "You are not on the beta list yet" in response.text
        assert user(env, ALICE) is None and identities(env) == []


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


def test_google_sign_ups_stop_when_the_days_accounts_are_taken(tmp_path, monkeypatch):
    env = google_env(tmp_path, monkeypatch, signup_mode="open", max_new_accounts_per_day=1)
    with env.running() as client:
        assert callback(client).status_code == 303
        env.userinfo = {"email": BOB, "email_verified": True, "sub": "g-bob"}
        full = callback(env.client())
        assert full.status_code == 503 and "Sign-ups are full for today" in full.text
        assert user(env, BOB) is None
        assert [i[2] for i in identities(env)] == ["g-alice"]  # nothing recorded for the refused


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


def test_in_allowlist_mode_a_microsoft_stranger_is_refused_and_nothing_is_stored(tmp_path):
    env, fake = microsoft_env(tmp_path, signup_mode="allowlist")
    with env.running() as client:
        response = ms_sign_in(env, fake, client)
        assert response.status_code == 403 and "not on the list for this beta" in response.text
        assert user(env, ALICE) is None and identities(env) == []


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


@pytest.mark.parametrize("mode", ["allowlist", "open"])
def test_a_microsoft_token_without_an_address_gets_no_account(tmp_path, mode):
    env, fake = microsoft_env(tmp_path / mode, signup_mode=mode)
    fake.claims.pop("email", None)
    fake.claims.pop("preferred_username", None)
    with env.running() as client:
        response = ms_sign_in(env, fake, client)
        assert response.status_code == 403 and "No email address" in response.text
        assert users(env) == 1 and identities(env) == []


# --------------------------------------------------------------------------- Microsoft: trust


def refusal(response) -> str:
    return " ".join(response.text.split())


@pytest.mark.parametrize(
    "label, claims",
    [
        ("absent", {}),
        ("false", {"xms_edov": False}),
        ("sign-in name only", {"xms_edov": False, "email": None}),
    ],
)
def test_an_address_the_directory_does_not_vouch_for_makes_no_account(tmp_path, label, claims):
    """nOAuth: with open sign-up an email claim anyone can put in a directory would make an
    account, or take one over, for any address. Only `xms_edov` true counts."""
    env, fake = microsoft_env(tmp_path, signup_mode="open")
    fake.claims.pop("xms_edov", None)
    for key, value in claims.items():
        if value is None:
            fake.claims.pop(key, None)
        else:
            fake.claims[key] = value
    with env.running() as client:
        response = ms_sign_in(env, fake, client)
        text = refusal(response)
        assert response.status_code == 403
        assert "Please sign in with Google" in text
        assert "Sign in with your Google account instead" in text
        assert user(env, ALICE) is None and identities(env) == []
        with session_scope(env.ctx.factory) as session:
            assert signups_today(session, utc_today()) == 0
        assert client.get("/app").status_code == 303


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


def test_an_open_tier_account_can_link_a_verified_microsoft_identity(tmp_path):
    env, fake = microsoft_env(tmp_path, signup_mode="open")
    with env.running():
        with session_scope(env.ctx.factory) as session:
            session.add(User(email=ALICE, is_allowed=True, access_tier=access.OPEN))
        response = ms_sign_in(env, fake, env.client())
        assert response.status_code == 303
        assert [i[0] for i in identities(env)] == ["microsoft"]
        with session_scope(env.ctx.factory) as session:
            assert signups_today(session, utc_today()) == 0  # no new account


def test_an_identity_linked_before_sign_up_opened_keeps_signing_in(tmp_path):
    """Linking is refused for invited people only the first time: the identity decides
    afterwards, whatever the token's address says."""
    from readablestem.web.identities import MICROSOFT, Identity, link

    env, fake = microsoft_env(tmp_path, signup_mode="open")
    with env.running():
        env.allow(ALICE, admin=True)
        alice = user(env, ALICE)
        assert alice is not None
        issuer = f"https://login.microsoftonline.com/{TENANT}/v2.0"
        with session_scope(env.ctx.factory) as session:
            link(session, alice.id, Identity(MICROSOFT, issuer, fake.claims["sub"], tenant=TENANT))
        fake.claims["xms_edov"] = False  # even an unverified token signs a linked identity in
        assert ms_sign_in(env, fake, env.client()).status_code == 303


def test_in_allowlist_mode_the_microsoft_rules_are_as_they_were(tmp_path):
    """No `xms_edov`, an invited user: still linked by address (the pilot's way in)."""
    env, fake = microsoft_env(tmp_path, signup_mode="allowlist")
    fake.claims.pop("xms_edov")
    with env.running() as client:
        env.allow(ALICE)
        assert ms_sign_in(env, fake, client).status_code == 303
        assert [i[0] for i in identities(env)] == ["microsoft"]


def test_the_verified_address_is_the_email_claim_with_a_verified_domain_only():
    from readablestem.web.microsoft import verified_email

    claims = {"email": "a@uni.edu", "preferred_username": "b@uni.edu"}
    assert verified_email(claims) is None  # absent: not known to be verified
    for yes in (True, "true", "True", "1"):
        assert verified_email(claims | {"xms_edov": yes}) == "a@uni.edu"
    for no in (False, "false", "False", "0", None, 0):
        assert verified_email(claims | {"xms_edov": no}) is None
    assert verified_email({"preferred_username": "b@uni.edu", "xms_edov": True}) is None
    assert verified_email({"email": 7, "xms_edov": True}) is None
