"""The ways to sign in on this server, worded once.

Every page that names the sign-in methods (the sign-in page, the legal pages, the landing
page, the share page, the admin page, llms.txt) reads these two sentences' parts, so a
method that is switched off is named nowhere and one that is on is named everywhere.
"""

from __future__ import annotations


def _join(names: list[str]) -> str:
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + " or " + names[-1]


def sign_in_words(*, google: bool, microsoft: bool, email_link: bool) -> str:
    """Every way a person can sign in: "Google", "Google or Microsoft", "Google, Microsoft
    or an emailed sign-in link". Empty when none is on (development sign-in only), so no
    page names a provider that is not there."""
    return _join(
        [
            name
            for name, on in (
                ("Google", google),
                ("Microsoft", microsoft),
                ("an emailed sign-in link", email_link),
            )
            if on
        ]
    )


def account_making_words(*, google: bool, microsoft: bool) -> str:
    """The methods whose first use makes an account when sign-up is open: "Google or
    Microsoft". The emailed link only signs in an account that exists (`web.email_login`)."""
    return _join([name for name, on in (("Google", google), ("Microsoft", microsoft)) if on])
