"""A fake Microsoft identity platform for the sign-in tests (tests/test_web_auth_*.py).

It answers Authlib's discovery, key and token requests in process (an httpx
MockTransport set on the "microsoft" client), and signs ID tokens with an RSA key made
here, so the real sign-in code runs end to end with nothing leaving the machine:
authorisation URL with state, nonce and PKCE; the token request with the code verifier
and client secret; our own ID-token checks.

Tests change what the next token says with `claims` (who the person is) and `overrides`
(anything else, applied last: `{"aud": "another-app"}`), and swap `signing_key` or
`published` to test signatures and key rotation.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from authlib.integrations.httpx_client._compat import httpx2
from joserfc import jwt
from joserfc.jwk import RSAKey

from a11y_stem.web.microsoft import MS_METADATA_URL

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"
CLIENT_ID = "00000000-aaaa-4bbb-8ccc-000000000001"
CLIENT_SECRET = "ms-secret-should-never-print"
AUTHORIZE_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
JWKS_URL = "https://login.microsoftonline.com/organizations/discovery/v2.0/keys"
ISSUER_TEMPLATE = "https://login.microsoftonline.com/{tenantid}/v2.0"


def issuer(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{tenant}/v2.0"


def new_key(kid: str) -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": kid, "use": "sig"})


def s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class FakeMicrosoft:
    def __init__(self, email: str, *, subject: str = "sub-alice-pairwise") -> None:
        self.key = new_key("key-1")
        self.signing_key = self.key  # what the next ID token is signed with
        self.published = [self.key]  # what the keys endpoint serves
        self.claims: dict[str, Any] = {
            "tid": TENANT,
            "sub": subject,
            "oid": "33333333-3333-4333-8333-333333333333",
            "email": email,
            "preferred_username": email,
            "name": "Alice Example",
        }
        self.overrides: dict[str, Any] = {}
        self.codes: dict[str, dict[str, str]] = {}  # code -> what it was issued for
        self.fetched: list[str] = []  # every URL the app asked for
        self.nonces: list[str] = []  # nonces of the sign-ins started, in order

    def transport(self) -> Any:
        return httpx2.MockTransport(self.handle)

    def install(self, ctx: Any) -> None:
        """Answer the app's "microsoft" client from here."""
        ctx.sign_in_client("microsoft").client_kwargs["transport"] = self.transport()

    # ------------------------------------------------------------------ endpoints

    def handle(self, request: Any) -> Any:
        url = str(request.url).split("?", 1)[0]
        self.fetched.append(url)
        if url == MS_METADATA_URL:
            return httpx2.Response(200, json=self.metadata())
        if url == JWKS_URL:
            keys = [k.as_dict(private=False) | {"issuer": ISSUER_TEMPLATE} for k in self.published]
            return httpx2.Response(200, json={"keys": keys})
        if url == TOKEN_URL and request.method == "POST":
            return self.token(dict(parse_qsl(request.content.decode())))
        return httpx2.Response(404)

    def metadata(self) -> dict[str, Any]:
        return {
            "issuer": ISSUER_TEMPLATE,
            "authorization_endpoint": AUTHORIZE_URL,
            "token_endpoint": TOKEN_URL,
            "jwks_uri": JWKS_URL,
            "response_types_supported": ["code", "id_token", "code id_token"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "scopes_supported": ["openid", "profile", "email", "offline_access"],
        }

    def token(self, form: dict[str, str]) -> Any:
        grant = self.codes.pop(form.get("code", ""), None)  # a code works once
        if (
            grant is None
            or form.get("grant_type") != "authorization_code"
            or form.get("client_id") != CLIENT_ID
            or form.get("client_secret") != CLIENT_SECRET
            or form.get("redirect_uri") != grant["redirect_uri"]
            or s256(form.get("code_verifier", "")) != grant["challenge"]
        ):
            return httpx2.Response(400, json={"error": "invalid_grant"})
        body = {
            "token_type": "Bearer",
            "scope": "openid profile email",
            "expires_in": 3600,
            "access_token": "fake-access-token",
            "id_token": self.id_token(grant["nonce"]),
        }
        return httpx2.Response(200, json=body)

    # ------------------------------------------------------------------ the person's side

    def authorize(self, location: str) -> str:
        """What Microsoft does once the person has signed in: checks the request the app
        sent the browser to, and gives back the callback path with a one-time code."""
        assert location.startswith(AUTHORIZE_URL + "?"), location
        query = dict(parse_qsl(urlsplit(location).query))
        assert query["client_id"] == CLIENT_ID
        assert query["response_type"] == "code"
        assert set(query["scope"].split()) == {"openid", "profile", "email"}
        assert query["code_challenge_method"] == "S256" and len(query["code_challenge"]) == 43
        assert query["prompt"] == "select_account"
        assert query["state"] and query["nonce"]
        code = secrets.token_urlsafe(16)
        self.codes[code] = {
            "nonce": query["nonce"],
            "challenge": query["code_challenge"],
            "redirect_uri": query["redirect_uri"],
        }
        self.nonces.append(query["nonce"])
        callback = urlsplit(query["redirect_uri"])
        return f"{callback.path}?code={code}&state={query['state']}"

    def id_token(self, nonce: str) -> str:
        now = int(time.time())
        claims = {
            "ver": "2.0",
            "aud": CLIENT_ID,
            "iss": issuer(self.claims["tid"]),
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
            "nonce": nonce,
        }
        claims |= self.claims
        claims |= self.overrides
        claims = {name: value for name, value in claims.items() if value is not None}
        header = {"alg": "RS256", "kid": self.signing_key.kid, "typ": "JWT"}
        return jwt.encode(header, claims, self.signing_key)
