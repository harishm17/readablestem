"""Microsoft (Entra ID) sign-in and sign-in identities, against a fake identity platform
(tests/fixtures/web/oidc.py: keys made here, tokens signed here, nothing leaves the
machine).

What it pins down: only tokens Microsoft signed for our client id, issued by an allowed
tenant to this browser's sign-in (nonce) and still valid get anyone in; the address in a
token is trusted only from an allowed tenant and only to link a new identity to an
allowlisted user; after that the identity alone decides; Google sign-in is unchanged but
its identity is recorded; and sessions end exactly as before.
"""

from __future__ import annotations

import logging
import time

import pytest
from sqlalchemy import select

from a11y_stem.observability.logging import FIELDS_ATTR
from a11y_stem.web.auth import make_oauth
from a11y_stem.web.db import AuthIdentity, User, session_scope
from a11y_stem.web.identities import GOOGLE_ISSUER
from a11y_stem.web.microsoft import (
    TokenRefused,
    email_claims,
    verify_id_token,
)
from fixtures.web.oidc import (
    CLIENT_ID,
    CLIENT_SECRET,
    JWKS_URL,
    OTHER_TENANT,
    TENANT,
    FakeMicrosoft,
    issuer,
    new_key,
)
from fixtures.web.support import ALICE, BOB, WebEnv, csrf_from, make_settings

MS_SETTINGS = {
    "ms_client_id": CLIENT_ID,
    "ms_client_secret": CLIENT_SECRET,
    "ms_allowed_tenants": [TENANT],
    "base_url": "https://beta.example.org",
}
GOOGLE_SETTINGS = {"google_client_id": "google-client", "google_client_secret": "google-secret"}


@pytest.fixture
def ms(tmp_path, monkeypatch):
    """Microsoft and Google sign-in both set up; Google answered by a stub, as in
    test_web_auth.py."""
    env = WebEnv(tmp_path, dev_login_enabled=False, **MS_SETTINGS, **GOOGLE_SETTINGS)
    env.fake = FakeMicrosoft(ALICE)
    env.fake.install(env.ctx)
    env.google_info = {"email": ALICE, "email_verified": True, "sub": "google-sub-alice"}
    google = env.ctx.sign_in_client("google")

    async def create_authorization_url(redirect_uri=None, **kwargs):
        return {"url": "https://accounts.google.com/o/oauth2/v2/auth?s=g", "state": "g"}

    async def authorize_access_token(request, **kwargs):
        return {"userinfo": env.google_info}

    monkeypatch.setattr(google, "create_authorization_url", create_authorization_url)
    monkeypatch.setattr(google, "authorize_access_token", authorize_access_token)
    with env.running() as client:
        env.browser = client
        yield env


def start(env, browser=None):
    """Press "Sign in with your university account": the redirect to Microsoft."""
    browser = browser or env.browser
    token = csrf_from(browser.get("/login").text)
    response = browser.post("/login/microsoft", data={"csrf_token": token})
    assert response.status_code == 302, response.text
    return response.headers["location"]


def ms_sign_in(env, browser=None):
    """The whole round trip; returns the callback's response."""
    browser = browser or env.browser
    return browser.get(env.fake.authorize(start(env, browser)))


def google_sign_in(env, browser=None):
    return (browser or env.browser).get("/auth/callback?code=c&state=g")


def identities(env) -> list[tuple[str, str, str, str | None, str]]:
    with session_scope(env.ctx.factory) as session:
        rows = session.scalars(select(AuthIdentity).order_by(AuthIdentity.provider))
        return [(r.provider, r.issuer, r.subject, r.tenant, r.user_id) for r in rows]


def user_id(env, email: str) -> str | None:
    with session_scope(env.ctx.factory) as session:
        return session.scalar(select(User.id).where(User.email == email))


def emails(env) -> list[str]:
    with session_scope(env.ctx.factory) as session:
        return sorted(session.scalars(select(User.email)))


def signed_in(browser) -> bool:
    return browser.get("/app").status_code == 200


# --------------------------------------------------------------------------- the page


def test_the_university_button_comes_first_and_google_second(ms):
    page = ms.browser.get("/login").text
    assert page.index("Sign in with your university account (Microsoft)") < page.index(
        "Sign in with Google"
    )
    assert 'action="/login/microsoft"' in page and 'action="/login"' in page
    assert "your university's Microsoft account, or with the Google account" in page
    assert "Sign in with your university account (Microsoft)" in ms.browser.get("/").text


def test_without_microsoft_settings_only_google_is_offered(tmp_path):
    env = WebEnv(tmp_path, dev_login_enabled=False, **GOOGLE_SETTINGS)
    with env.running() as client:
        page = client.get("/login").text
        assert "Sign in with Google" in page and "Microsoft" not in page
        assert 'class="button">Sign in with Google' in page  # still the main button
        token = csrf_from(page)
        assert client.post("/login/microsoft", data={"csrf_token": token}).status_code == 503
        assert client.get("/auth/microsoft/callback?code=c&state=s").status_code == 404


def test_microsoft_alone_offers_no_google_button(tmp_path):
    env = WebEnv(tmp_path, dev_login_enabled=False, **MS_SETTINGS)
    with env.running() as client:
        page = client.get("/login").text
        assert "Sign in with your university account (Microsoft)" in page
        assert "Sign in with Google" not in page and "Google account" not in page
        token = csrf_from(page)
        assert client.post("/login", data={"csrf_token": token}).status_code == 503
        assert client.get("/auth/callback").status_code == 404


def test_half_configured_microsoft_is_not_offered(tmp_path):
    """No tenant listed: off, even with a client id and secret (the setting refuses to
    start only when the secret is set without tenants, in check_production)."""
    settings = make_settings(tmp_path, ms_client_id=CLIENT_ID)
    assert make_oauth(settings) is None


def test_the_button_goes_to_microsoft_with_pkce_state_and_nonce(ms):
    location = start(ms)
    callback = ms.fake.authorize(location)  # asserts S256 PKCE, state, nonce, scopes
    assert "redirect_uri=https%3A%2F%2Fbeta.example.org%2Fauth%2Fmicrosoft%2Fcallback" in location
    assert callback.startswith("/auth/microsoft/callback?code=")


# --------------------------------------------------------------------------- good sign-in


def test_an_allowlisted_university_account_signs_in_and_is_linked(ms):
    ms.allow(ALICE)
    response = ms_sign_in(ms)
    assert response.status_code == 303 and response.headers["location"] == "/app"
    assert signed_in(ms.browser)
    alice = user_id(ms, ALICE)
    assert identities(ms) == [("microsoft", issuer(TENANT), "sub-alice-pairwise", TENANT, alice)]
    with session_scope(ms.ctx.factory) as session:
        user = session.get(User, alice)
        assert user is not None and user.name == "Alice Example" and user.last_login_at


def test_the_secret_and_the_verifier_go_only_to_the_token_endpoint(ms):
    ms.allow(ALICE)
    location = start(ms)
    assert CLIENT_SECRET not in location and "code_verifier" not in location
    assert ms.browser.get(ms.fake.authorize(location)).status_code == 303


def test_a_university_account_links_to_the_existing_google_user(ms):
    ms.allow(ALICE)
    assert google_sign_in(ms).status_code == 303
    alice = user_id(ms, ALICE)
    assert identities(ms) == [("google", GOOGLE_ISSUER, "google-sub-alice", None, alice)]
    other = ms.client()
    assert ms_sign_in(ms, other).status_code == 303
    assert {row[0]: row[4] for row in identities(ms)} == {"google": alice, "microsoft": alice}
    assert emails(ms) == sorted([ALICE, "admin@example.org"])  # nobody new
    other.close()


def test_a_linked_identity_signs_in_whatever_address_the_token_now_carries(ms):
    """The address is used once, to link. Afterwards a changed address (or one that is
    another allowlisted person's) does not move the identity."""
    ms.allow(ALICE)
    ms.allow(BOB)
    assert ms_sign_in(ms).status_code == 303
    alice = user_id(ms, ALICE)
    for changed in ({"email": BOB, "preferred_username": BOB}, {"email": None}):
        ms.fake.claims |= changed
        browser = ms.client()
        assert ms_sign_in(ms, browser).status_code == 303
        assert browser.get("/app").status_code == 200
        assert ALICE in browser.get("/app").text  # signed in as Alice, not Bob
        browser.close()
    assert [row[4] for row in identities(ms)] == [alice]  # one identity, reused


def test_the_same_subject_in_another_tenant_is_another_identity(ms):
    ms.ctx.settings.ms_allowed_tenants = [TENANT, OTHER_TENANT]
    ms.allow(ALICE)
    assert ms_sign_in(ms).status_code == 303
    ms.fake.claims["tid"] = OTHER_TENANT
    assert ms_sign_in(ms, ms.client()).status_code == 303
    assert sorted(row[3] for row in identities(ms)) == sorted([TENANT, OTHER_TENANT])


def test_without_an_email_claim_the_sign_in_name_is_used(ms):
    ms.allow(ALICE)
    ms.fake.claims["email"] = None
    assert ms_sign_in(ms).status_code == 303


# --------------------------------------------------------------------------- refused


def test_an_account_from_another_tenant_is_told_its_organisation_is_not_set_up(ms, caplog):
    """nOAuth: any organisation can put any address in its own directory. Alice is on the
    allowlist, but a token from a tenant we have not allowed must not reach her account."""
    caplog.set_level(logging.INFO)
    ms.allow(ALICE)
    ms.fake.claims["tid"] = OTHER_TENANT
    response = ms_sign_in(ms)
    assert response.status_code == 403
    assert "Your organisation is not set up for this beta" in response.text
    assert OTHER_TENANT in response.text and "help@example.org" in response.text
    assert "sign in with Google" in response.text
    assert refusals(caplog) == ["tenant_not_allowed"]
    assert identities(ms) == [] and not signed_in(ms.browser)


def test_a_wrong_tenant_never_creates_anyone(ms):
    ms.fake.claims |= {"tid": OTHER_TENANT, "email": BOB}
    assert ms_sign_in(ms).status_code == 403
    assert BOB not in emails(ms) and identities(ms) == []


def test_an_address_not_on_the_allowlist_gets_the_polite_page_and_nothing_is_kept(ms):
    ms.fake.claims |= {"email": BOB, "preferred_username": BOB}
    response = ms_sign_in(ms)
    assert response.status_code == 403
    assert "not on the beta list" in response.text and BOB in response.text
    assert BOB not in emails(ms) and identities(ms) == []


def test_an_email_whose_domain_owner_is_unverified_is_not_trusted(ms):
    """xms_edov false: the directory has not verified the email domain's owner. The
    sign-in name is used instead (here someone else's), so Alice is not linked."""
    ms.allow(ALICE)
    ms.fake.claims |= {"xms_edov": False, "preferred_username": "mallory@uni.example.edu"}
    response = ms_sign_in(ms)
    assert response.status_code == 403 and "mallory@uni.example.edu" in response.text
    assert identities(ms) == []


def test_no_address_at_all_is_refused(ms):
    ms.allow(ALICE)
    ms.fake.claims |= {"email": None, "preferred_username": "not an address"}
    response = ms_sign_in(ms)
    assert response.status_code == 403 and "did not share an email address" in response.text
    assert identities(ms) == []


def test_someone_removed_from_the_allowlist_cannot_sign_in_with_a_linked_identity(ms):
    from a11y_stem.web.auth import set_allowed

    ms.allow(ALICE)
    assert ms_sign_in(ms).status_code == 303
    with session_scope(ms.ctx.factory) as session:
        set_allowed(session, ALICE, allowed=False)
    response = ms_sign_in(ms, ms.client())
    assert response.status_code == 403 and "not on the beta list" in response.text


def refusals(caplog) -> list[str]:
    """The reason codes of the sign-in refusals and failures logged so far."""
    return [
        getattr(r, FIELDS_ATTR)["reason"]
        for r in caplog.records
        if r.getMessage() in {"login_refused", "login_failed"}
    ]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"aud": "another-app"}, "wrong_audience"),  # a token for another app
        ({"aud": [CLIENT_ID, "another-app"]}, "wrong_audience"),
        ({"iss": issuer(OTHER_TENANT)}, "bad_issuer"),  # issuer and tenant disagree
        ({"iss": "https://login.microsoftonline.com/{tenantid}/v2.0"}, "bad_issuer"),
        ({"iss": "https://sts.windows.net/" + TENANT + "/"}, "bad_issuer"),  # v1.0
        ({"tid": "organizations"}, "bad_issuer"),  # not a directory id
        ({"tid": None}, "bad_issuer"),
        ({"exp": int(time.time()) - 600}, "expired"),
        ({"exp": None}, "expired"),
        ({"exp": "tomorrow"}, "expired"),
        ({"nbf": int(time.time()) + 600}, "not_yet_valid"),
        ({"iat": int(time.time()) + 600}, "not_yet_valid"),
        ({"nonce": None}, "bad_nonce"),
        ({"nonce": "nonce-of-an-earlier-sign-in"}, "bad_nonce"),  # a replayed token
        ({"nonce": "nonce-é"}, "bad_nonce"),
        ({"sub": None}, "no_subject"),
        ({"sub": "x" * 256}, "no_subject"),  # too long to store
    ],
)
def test_tokens_that_fail_a_check_are_refused(ms, caplog, change, reason):
    caplog.set_level(logging.INFO)
    ms.allow(ALICE)
    ms.fake.overrides = change
    response = ms_sign_in(ms)
    assert response.status_code == 400
    assert "Sign-in did not finish" in response.text
    assert refusals(caplog) == [reason]
    assert identities(ms) == [] and not signed_in(ms.browser)


def test_a_token_signed_with_another_key_is_refused(ms, caplog):
    caplog.set_level(logging.INFO)
    ms.allow(ALICE)
    ms.fake.signing_key = new_key("key-1")  # same key id, different key
    assert ms_sign_in(ms).status_code == 400
    assert refusals(caplog) == ["bad_signature"] and identities(ms) == []


def test_an_unknown_key_fetches_the_keys_once_more_then_refuses(ms, caplog):
    caplog.set_level(logging.INFO)
    ms.allow(ALICE)
    ms.fake.signing_key = new_key("key-unknown")
    assert ms_sign_in(ms).status_code == 400
    assert ms.fake.fetched.count(JWKS_URL) == 2
    assert refusals(caplog) == ["unknown_key"]


def test_a_rotated_key_is_picked_up(ms):
    ms.allow(ALICE)
    assert ms_sign_in(ms).status_code == 303
    rotated = new_key("key-2")
    ms.fake.published = [ms.fake.key, rotated]
    ms.fake.signing_key = rotated
    assert ms_sign_in(ms, ms.client()).status_code == 303
    assert ms.fake.fetched.count(JWKS_URL) == 2  # cached, then fetched again once


def test_a_token_from_another_sign_in_is_refused(ms, caplog):
    """The nonce: the token endpoint hands back a token minted for an earlier sign-in of
    the same browser (a replay); it does not match the sign-in in progress."""
    caplog.set_level(logging.INFO)
    ms.allow(ALICE)
    ms.fake.authorize(start(ms))  # an earlier sign-in, never finished
    location = start(ms)
    ms.fake.overrides = {"nonce": ms.fake.nonces[0]}
    assert ms.browser.get(ms.fake.authorize(location)).status_code == 400
    assert refusals(caplog) == ["bad_nonce"]


def test_a_callback_works_once(ms, caplog):
    caplog.set_level(logging.INFO)
    ms.allow(ALICE)
    callback = ms.fake.authorize(start(ms))
    assert ms.browser.get(callback).status_code == 303
    other = ms.client()
    for _ in range(2):  # the state lived in the first browser's session, and is spent
        assert ms.browser.get(callback).status_code == 400
        assert other.get(callback).status_code == 400
    assert refusals(caplog) == ["state_mismatch"] * 4
    other.close()


def test_a_callback_in_another_browser_is_refused(ms):
    """The state is kept in the browser's own session, so a callback link sent to
    someone else (login CSRF) does not sign them in."""
    ms.allow(ALICE)
    callback = ms.fake.authorize(start(ms))
    other = ms.client()
    assert other.get(callback).status_code == 400 and not signed_in(other)
    other.close()


def test_an_error_from_microsoft_shows_a_retry_page(ms, caplog):
    caplog.set_level(logging.INFO)
    start(ms)
    response = ms.browser.get(
        "/auth/microsoft/callback?error=access_denied&error_description=AADSTS65001+consent"
    )
    assert response.status_code == 400
    assert "Microsoft did not complete the sign-in" in response.text
    assert "IT team" in response.text and "AADSTS" not in response.text
    assert refusals(caplog) == ["authorization_error"]
    assert "AADSTS" not in caplog.text


def test_an_it_admin_back_from_the_approval_link_is_thanked_not_signed_in(ms):
    approved = ms.browser.get(
        f"/auth/microsoft/callback?admin_consent=True&tenant={OTHER_TENANT}&scope=openid"
    )
    assert approved.status_code == 303 and approved.headers["location"] == "/login"
    page = ms.browser.get("/login").text
    assert "Microsoft has sent you back from the approval step" in page
    assert OTHER_TENANT not in page and not signed_in(ms.browser)
    declined = ms.browser.get(
        "/auth/microsoft/callback?admin_consent=True&error=consent_required"
        "&error_description=AADSTS65004"
    )
    assert declined.headers["location"] == "/login"
    page = ms.browser.get("/login").text
    assert "did not finish the approval" in page and "AADSTS" not in page
    assert identities(ms) == [] and ms.fake.fetched == []  # nothing asked of Microsoft


def test_a_refused_code_shows_a_retry_page(ms, caplog):
    caplog.set_level(logging.INFO)
    ms.allow(ALICE)
    location = start(ms)
    callback = ms.fake.authorize(location)
    ms.fake.codes.clear()  # Microsoft no longer knows the code
    assert ms.browser.get(callback).status_code == 400
    assert refusals(caplog) == ["oauth_error"]


def test_microsoft_out_of_reach_gives_a_plain_page(ms):
    from authlib.integrations.httpx_client._compat import httpx2

    def unreachable(request):
        raise httpx2.ConnectError("down")

    ms.ctx.sign_in_client("microsoft").client_kwargs["transport"] = httpx2.MockTransport(
        unreachable
    )
    token = csrf_from(ms.browser.get("/login").text)
    response = ms.browser.post("/login/microsoft", data={"csrf_token": token})
    assert response.status_code == 503 and "Microsoft did not answer" in response.text


# --------------------------------------------------------------------------- Google


def test_google_sign_in_records_the_google_identity_once(ms):
    ms.allow(ALICE)
    for _ in range(2):
        assert google_sign_in(ms, ms.client()).status_code == 303
    assert identities(ms) == [
        ("google", GOOGLE_ISSUER, "google-sub-alice", None, user_id(ms, ALICE))
    ]


def test_google_access_is_still_by_verified_email(ms):
    """A recorded Google identity changes nobody's access. The same Google account, now
    with Bob's verified address, signs in as Bob, by email as before, and the identity
    stays with Alice; once Bob is removed from the allowlist it is refused."""
    from a11y_stem.web.auth import set_allowed

    ms.allow(ALICE)
    ms.allow(BOB)
    assert google_sign_in(ms).status_code == 303
    ms.google_info = {"email": BOB, "email_verified": True, "sub": "google-sub-alice"}
    bob = ms.client()
    assert google_sign_in(ms, bob).status_code == 303
    assert BOB in bob.get("/app").text  # by email, as before
    assert [row[4] for row in identities(ms)] == [user_id(ms, ALICE)]  # left where it was
    with session_scope(ms.ctx.factory) as session:
        set_allowed(session, BOB, allowed=False)
    assert google_sign_in(ms, ms.client()).status_code == 403
    bob.close()


def test_google_sign_ins_off_the_allowlist_record_nothing(ms):
    ms.google_info = {"email": BOB, "email_verified": True, "sub": "google-sub-bob"}
    assert google_sign_in(ms).status_code == 403
    assert identities(ms) == [] and BOB not in emails(ms)


def test_a_first_sign_in_that_loses_a_race_finds_the_link(ms, monkeypatch):
    """Two first sign-ins at once: the one whose insert hits the unique key (the other
    linked the identity meanwhile) is tried again and finds the link (Postgres, for
    real: test_web_auth_postgres.py)."""
    from a11y_stem.web import identities as store

    ms.allow(ALICE)
    alice = user_id(ms, ALICE)
    identity = store.Identity("microsoft", issuer(TENANT), "sub-raced", TENANT)
    with session_scope(ms.ctx.factory) as session:
        store.link(session, alice, identity)  # the other sign-in got there first
    looked = []
    real = store.user_with

    def not_yet_on_the_first_look(session, wanted):
        looked.append(wanted)
        return None if len(looked) == 1 else real(session, wanted)

    monkeypatch.setattr(store, "user_with", not_yet_on_the_first_look)
    result = store.microsoft_sign_in(ms.ctx.factory, identity, ALICE, None)
    assert result.user_id == alice and len(looked) == 2
    assert [row[4] for row in identities(ms)] == [alice]


# --------------------------------------------------------------------------- sessions


def test_signing_out_ends_microsoft_sessions_and_their_copies(ms):
    ms.allow(ALICE)
    assert ms_sign_in(ms).status_code == 303
    copied = ms.client()
    for name, value in ms.browser.cookies.items():
        copied.cookies.set(name, value)
    assert signed_in(copied)
    token = csrf_from(ms.browser.get("/app").text)
    assert ms.browser.post("/logout", data={"csrf_token": token}).status_code == 303
    assert not signed_in(copied) and not signed_in(ms.browser)
    assert ms_sign_in(ms).status_code == 303 and signed_in(ms.browser)
    assert not signed_in(copied)
    copied.close()


def test_removal_ends_a_microsoft_session_and_it_stays_ended(ms):
    from a11y_stem.web.auth import set_allowed

    ms.allow(ALICE)
    assert ms_sign_in(ms).status_code == 303
    with session_scope(ms.ctx.factory) as session:
        set_allowed(session, ALICE, allowed=False)
    assert not signed_in(ms.browser)
    ms.allow(ALICE)
    assert not signed_in(ms.browser)
    assert ms_sign_in(ms).status_code == 303 and signed_in(ms.browser)


def test_the_session_is_rotated_at_microsoft_sign_in(ms):
    ms.allow(ALICE)
    before = csrf_from(ms.browser.get("/login").text)
    assert ms_sign_in(ms).status_code == 303
    assert csrf_from(ms.browser.get("/app").text) != before


# --------------------------------------------------------------------------- privacy


def test_nothing_from_the_tokens_reaches_the_logs(ms, caplog):
    caplog.set_level(logging.DEBUG)
    ms.allow(ALICE)
    ms_sign_in(ms)
    ms.fake.claims |= {"tid": OTHER_TENANT, "email": BOB}
    ms_sign_in(ms, ms.client())
    ms.fake.claims |= {"tid": TENANT}
    ms_sign_in(ms, ms.client())  # Bob: not on the allowlist
    ms.fake.overrides = {"aud": "another-app"}
    ms_sign_in(ms, ms.client())
    logged = "\n".join(f"{r.getMessage()} {r.args} {r.__dict__}" for r in caplog.records)
    assert "login_refused" in logged and "tenant_not_allowed" in logged
    for private in (ALICE, BOB, TENANT, OTHER_TENANT, "sub-alice-pairwise", "Alice Example"):
        assert private not in logged, private
    assert CLIENT_SECRET not in logged


# --------------------------------------------------------------------------- launch check


def test_launch_check_wants_microsoft_in_the_privacy_notice_once_it_is_on(tmp_path, monkeypatch):
    from a11y_stem.ops import launch

    templates = tmp_path / "templates"
    templates.mkdir()
    privacy = templates / "privacy.html"
    privacy.write_text("<p>You sign in with Google.</p>")
    monkeypatch.setattr(launch, "TEMPLATES_DIR", templates)
    on = make_settings(tmp_path, **MS_SETTINGS)

    def flagged(settings) -> bool:
        return any(
            "Microsoft sign-in is on" in item for item in launch.launch_blockers(settings=settings)
        )

    assert flagged(on)
    assert not flagged(make_settings(tmp_path)) and not flagged(None)
    privacy.write_text("<p>You sign in with Google or with your university's Microsoft.</p>")
    assert not flagged(on)


def test_launch_check_reads_the_sign_in_pages(tmp_path, monkeypatch):
    from a11y_stem.ops import launch

    (tmp_path / "org_not_set_up.html").write_text("<p>TODO(Harish): wording</p>")
    monkeypatch.setattr(launch, "TEMPLATES_DIR", tmp_path)
    assert "web/templates/org_not_set_up.html: still has a TODO(Harish) note" in (
        launch.launch_blockers()
    )


# --------------------------------------------------------------------------- the checks


def test_email_claims_prefer_a_verified_email_then_the_sign_in_name():
    assert email_claims({"email": "a@x.org", "preferred_username": "b@x.org"}) == (
        "a@x.org",
        "b@x.org",
    )
    for unverified in (False, "false", "0"):
        claims = {"email": "a@x.org", "preferred_username": "b@x.org", "xms_edov": unverified}
        assert email_claims(claims) == ("b@x.org",)
    assert email_claims({"email": "a@x.org", "xms_edov": True}) == ("a@x.org",)
    assert email_claims({"email": 3, "preferred_username": None}) == ()


def test_verify_id_token_is_pure_and_strict():
    fake = FakeMicrosoft(ALICE)
    jwks = {"keys": [fake.key.as_dict(private=False)]}
    raw = fake.id_token("n-1")
    now = time.time()
    checks = {"client_id": CLIENT_ID, "nonce": "n-1", "allowed_tenants": [TENANT], "now": now}
    account = verify_id_token(raw, jwks=jwks, **checks)
    assert (account.issuer, account.subject, account.tenant) == (
        issuer(TENANT),
        "sub-alice-pairwise",
        TENANT,
    )
    assert account.email_claims == (ALICE, ALICE) and account.name == "Alice Example"
    for refused_with, reason in (
        ({"now": now + 3600 + 121}, "expired"),
        ({"nonce": "n-2"}, "bad_nonce"),
        ({"client_id": "someone-else"}, "wrong_audience"),
        ({"allowed_tenants": [OTHER_TENANT]}, "tenant_not_allowed"),
        ({"allowed_tenants": []}, "tenant_not_allowed"),
    ):
        with pytest.raises(TokenRefused) as refused:
            verify_id_token(raw, jwks=jwks, **(checks | refused_with))
        assert refused.value.reason == reason
    with pytest.raises(TokenRefused) as refused:
        verify_id_token(raw, jwks=jwks, **(checks | {"allowed_tenants": []}))
    assert refused.value.tenant == TENANT  # shown on the "not set up" page
    for junk in ("", "a.b.c", "not-a-token", raw + "x"):
        with pytest.raises(TokenRefused):
            verify_id_token(junk, jwks=jwks, **checks)


def test_a_key_must_agree_with_its_own_issuer():
    """Microsoft's keys say which issuer they sign for; a key for one tenant only
    cannot vouch for another tenant's token."""
    fake = FakeMicrosoft(ALICE)
    jwk = fake.key.as_dict(private=False) | {"issuer": issuer(OTHER_TENANT)}
    checks = {"client_id": CLIENT_ID, "nonce": "n", "allowed_tenants": [TENANT]}
    with pytest.raises(TokenRefused) as refused:
        verify_id_token(fake.id_token("n"), jwks={"keys": [jwk]}, now=time.time(), **checks)
    assert refused.value.reason == "bad_issuer"


def test_only_rs256_is_accepted():
    from joserfc import jwt
    from joserfc.jwk import OctKey

    fake = FakeMicrosoft(ALICE)
    secret = OctKey.generate_key(256, parameters={"kid": "key-1"})
    claims = {"aud": CLIENT_ID, "tid": TENANT, "iss": issuer(TENANT), "nonce": "n", "sub": "s"}
    forged = jwt.encode({"alg": "HS256", "kid": "key-1"}, claims, secret)
    with pytest.raises(TokenRefused) as refused:
        verify_id_token(
            forged,
            jwks={"keys": [fake.key.as_dict(private=False)]},
            client_id=CLIENT_ID,
            nonce="n",
            allowed_tenants=[TENANT],
            now=time.time(),
        )
    assert refused.value.reason == "bad_algorithm"
