"""Microsoft (Entra ID) sign-in: the Authlib client, the code exchange and our own ID-token
checks.

Work and school accounts sign in through one multi-tenant app registration at the
"organizations" authority of the Microsoft identity platform v2. Authlib runs the
authorisation code flow (state, nonce, PKCE with S256) and the token request. The ID token
is checked here rather than by Authlib, because the discovery document's issuer is a
template, "https://login.microsoftonline.com/{tenantid}/v2.0", that no real token matches
as written.

`verify_id_token` accepts a token only when:

- its RS256 signature verifies with the Microsoft key its `kid` names, from the JWKS (an
  unknown `kid` makes the caller fetch the JWKS again, once: Microsoft rotates its keys);
- `aud` is our client id;
- `tid` is a directory id and `iss` is exactly "https://login.microsoftonline.com/{tid}/v2.0"
  (and agrees with the key's own issuer template when the key carries one);
- `nonce` is the one this browser's sign-in started with, so a token from another sign-in
  cannot be replayed;
- `exp`, `nbf` and `iat` hold, with two minutes of clock skew;
- and, checked last so that only an otherwise valid token gets the "not set up" page,
  `tid` is one of MS_ALLOWED_TENANTS.

nOAuth: an email claim is only as good as the directory that issued it, and anyone can
create a directory and put any address in it. So addresses are read only from allowed
tenants, an email claim whose domain owner the directory has not verified (`xms_edov`
false) is ignored, and the address is used once, to link a new identity to an allowlisted
user (`web/identities.py`). After that the person is recognised by issuer and subject.

Nothing from the token is logged: refusals carry a reason code only.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, TypeGuard

from joserfc import jws, jwt
from joserfc.errors import JoseError
from joserfc.jwk import RSAKey
from starlette.requests import Request

from readablestem.config import Settings

MS_LOGIN = "https://login.microsoftonline.com"
MS_AUTHORITY = f"{MS_LOGIN}/organizations/v2.0"
MS_METADATA_URL = f"{MS_AUTHORITY}/.well-known/openid-configuration"
MS_SCOPE = "openid profile email"
MS_ISSUER = MS_LOGIN + "/{tid}/v2.0"
MS_ALGORITHM = "RS256"
CLOCK_SKEW_S = 120
MAX_SUBJECT_CHARS = 255  # auth_identities.subject

# A directory (tenant) id as Microsoft writes it in `tid`: a lower-case GUID.
_TENANT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# `xms_edov` values meaning "the email domain's owner is not verified".
_UNVERIFIED = (False, "false", "False", "0")
# Refusal reasons that mean the sign-in did not finish (logged as login_failed), rather
# than a token we turned down (login_refused).
FLOW_FAILURES = frozenset({"authorization_error", "state_mismatch", "no_id_token"})


class TokenRefused(Exception):
    """The sign-in is refused. `reason` is a content-free code for the logs; `tenant` is
    set for "tenant_not_allowed" only (a directory id from a token that passed every
    other check, shown to the person so they can pass it on)."""

    def __init__(self, reason: str, *, tenant: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.tenant = tenant


class UnknownSigningKey(TokenRefused):
    """No key in the JWKS has the token's `kid` (yet): fetch the JWKS again, once."""

    def __init__(self) -> None:
        super().__init__("unknown_key")


@dataclass(frozen=True)
class MicrosoftAccount:
    """What a verified ID token says about the person."""

    issuer: str  # https://login.microsoftonline.com/{tid}/v2.0
    subject: str  # `sub`
    tenant: str  # `tid`
    # Addresses to match against the allowlist, most trusted first (untrusted text, not
    # yet normalised): the email claim unless unverified, then preferred_username.
    email_claims: tuple[str, ...]
    name: str | None


def register_microsoft(oauth: Any, settings: Settings) -> None:
    """Add the "microsoft" client to an Authlib registry (`web.auth.make_oauth`).

    PKCE (S256) on top of the client secret; the secret goes in the token request's body,
    which Microsoft accepts for every secret it issues. `prompt=select_account` lets
    someone signed in to Microsoft with a personal or another organisation's account pick
    their university account instead of being signed in with the wrong one.
    """
    assert settings.ms_client_secret is not None  # ms_sign_in_enabled
    oauth.register(
        name="microsoft",
        client_id=settings.ms_client_id,
        client_secret=settings.ms_client_secret.get_secret_value(),
        server_metadata_url=MS_METADATA_URL,
        authorize_params={"prompt": "select_account"},
        client_kwargs={
            "scope": MS_SCOPE,
            "code_challenge_method": "S256",
            "token_endpoint_auth_method": "client_secret_post",
        },
    )


# --------------------------------------------------------------------------- the flow


async def exchange_code(request: Request, client: Any) -> tuple[str, str]:
    """Finish the authorisation code flow: returns (ID token, the sign-in's nonce).

    The state must be one this browser's session started (Authlib keeps it there, and it
    works once); the code goes back with the PKCE verifier. Raises `TokenRefused` when
    Microsoft reports an error, the state is unknown or no ID token comes back, and
    Authlib's `OAuthError` when the token request is refused.
    """
    params = request.query_params
    if params.get("error"):
        # error_description is Microsoft's text about this person's sign-in: not kept.
        raise TokenRefused("authorization_error")
    state = params.get("state") or ""
    data = await client.framework.get_state_data(request.session, state) if state else None
    if state:
        await client.framework.clear_state_data(request.session, state)
    code = params.get("code")
    if not isinstance(data, dict) or not code:
        raise TokenRefused("state_mismatch")
    nonce, verifier = data.get("nonce"), data.get("code_verifier")
    if not isinstance(nonce, str) or not isinstance(verifier, str):
        raise TokenRefused("state_mismatch")
    token = await client.fetch_access_token(
        redirect_uri=data.get("redirect_uri"), code=code, code_verifier=verifier
    )
    id_token = token.get("id_token") if isinstance(token, Mapping) else None
    if not isinstance(id_token, str):
        raise TokenRefused("no_id_token")
    return id_token, nonce


async def verified_account(
    client: Any, id_token: str, *, nonce: str, settings: Settings, now: float
) -> MicrosoftAccount:
    """`verify_id_token` with Microsoft's current keys, fetching them again once when the
    token names a key we have not seen."""

    def verify(jwks: Mapping[str, Any]) -> MicrosoftAccount:
        return verify_id_token(
            id_token,
            jwks=jwks,
            client_id=settings.ms_client_id or "",
            nonce=nonce,
            allowed_tenants=settings.ms_allowed_tenants,
            now=now,
        )

    try:
        return verify(await client.fetch_jwk_set())
    except UnknownSigningKey:
        return verify(await client.fetch_jwk_set(force=True))


# --------------------------------------------------------------------------- the checks


def verify_id_token(
    raw: str,
    *,
    jwks: Mapping[str, Any],
    client_id: str,
    nonce: str,
    allowed_tenants: Collection[str],
    now: float,
    leeway: int = CLOCK_SKEW_S,
) -> MicrosoftAccount:
    """Check a Microsoft ID token (see the module docstring) or raise `TokenRefused`."""
    try:
        header = jws.extract_compact(raw.encode("ascii")).headers()
    except (JoseError, ValueError, UnicodeError):
        raise TokenRefused("malformed") from None
    if header.get("alg") != MS_ALGORITHM:
        raise TokenRefused("bad_algorithm")
    jwk = _signing_key(jwks, header.get("kid"))
    try:
        key = RSAKey.import_key(dict(jwk))
        claims = jwt.decode(raw, key, algorithms=[MS_ALGORITHM]).claims
    except (JoseError, ValueError):
        raise TokenRefused("bad_signature") from None

    aud = claims.get("aud")
    if not client_id or (aud != client_id and aud != [client_id]):
        raise TokenRefused("wrong_audience")
    tenant = claims.get("tid")
    if not isinstance(tenant, str) or not _TENANT_ID.fullmatch(tenant):
        raise TokenRefused("bad_issuer")
    issuer = MS_ISSUER.format(tid=tenant)
    if claims.get("iss") != issuer:
        raise TokenRefused("bad_issuer")
    key_issuer = jwk.get("issuer")
    if isinstance(key_issuer, str) and key_issuer.replace("{tenantid}", tenant) != issuer:
        raise TokenRefused("bad_issuer")
    sent = claims.get("nonce")
    if (
        not nonce
        or not isinstance(sent, str)
        or not hmac.compare_digest(sent.encode(), nonce.encode())
    ):
        raise TokenRefused("bad_nonce")
    _check_times(claims, now=now, leeway=leeway)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not 0 < len(subject) <= MAX_SUBJECT_CHARS:
        raise TokenRefused("no_subject")
    if tenant not in allowed_tenants:
        raise TokenRefused("tenant_not_allowed", tenant=tenant)
    name = claims.get("name")
    return MicrosoftAccount(
        issuer=issuer,
        subject=subject,
        tenant=tenant,
        email_claims=email_claims(claims),
        name=name if isinstance(name, str) and name.strip() else None,
    )


def email_claims(claims: Mapping[str, Any]) -> tuple[str, ...]:
    """The addresses a token offers for the allowlist, most trusted first.

    The `email` claim, unless the directory says the owner of its domain is not verified
    (`xms_edov`, an optional claim; Microsoft also drops unverified email claims for
    multi-tenant apps by default), then `preferred_username`, the sign-in name, whose
    domain is one the directory has verified. The caller uses the first that is an
    address."""
    found: list[str] = []
    if claims.get("xms_edov") not in _UNVERIFIED and isinstance(claims.get("email"), str):
        found.append(claims["email"])
    if isinstance(claims.get("preferred_username"), str):
        found.append(claims["preferred_username"])
    return tuple(found)


def _signing_key(jwks: Mapping[str, Any], kid: object) -> Mapping[str, Any]:
    if not isinstance(kid, str) or not kid:
        raise TokenRefused("malformed")
    keys = jwks.get("keys")
    for jwk in keys if isinstance(keys, list) else []:
        if (
            isinstance(jwk, Mapping)
            and jwk.get("kid") == kid
            and jwk.get("kty") == "RSA"
            and jwk.get("use") in (None, "sig")
        ):
            return jwk
    raise UnknownSigningKey()


def _check_times(claims: Mapping[str, Any], *, now: float, leeway: int) -> None:
    exp, nbf, iat = claims.get("exp"), claims.get("nbf"), claims.get("iat")
    if not _is_time(exp) or now >= exp + leeway:
        raise TokenRefused("expired")
    if nbf is not None and (not _is_time(nbf) or nbf > now + leeway):
        raise TokenRefused("not_yet_valid")
    if not _is_time(iat) or iat > now + leeway:
        raise TokenRefused("not_yet_valid")


def _is_time(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)
