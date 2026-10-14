"""Launch audit: junk in every parameter of every route never produces a server error.

A 500 is an unhandled case; on this app it can also hide a path that skips a check. For each
route, as its owner and with valid tokens, the query and form fields the app reads are filled
with awkward values (empty, NUL, enormous numbers, path tricks, lone surrogates, huge
strings, markup).
"""

from __future__ import annotations

import pytest

from fixtures.web.support import csrf_from
from test_audit_authz import ROUTES, fill, site  # noqa: F401  (site is a fixture)

JUNK = [
    "",
    " ",
    "\x00",
    "-1",
    "9" * 400,
    "0" * 33,
    "../../etc/passwd",
    "%00%2e%2e%2f",
    "😀‮",
    "A" * 5000,
    "<script>alert(1)</script>",
    "null",
    "[]",
    "{}",
]
FIELDS = [
    "v", "auto", "item", "q", "view", "page", "filter", "group", "kind", "status", "from",
    "action", "alt", "latex", "long_description", "name", "symbol", "meaning", "note", "lang",
    "confirm", "email", "admin", "keep", "course", "saved_minutes", "will_post", "version",
    "result_version", "bulk", "ids", "csrf_token", "remove_data_table", "text", "caption",
]  # fmt: skip

# /readyz answers 503 on purpose when the worker or the provider is not set up (as in these
# tests); it reads no parameter, and its own tests cover it (test_web_health.py).
SKIP = {
    ("POST", "/jobs"),
    ("POST", "/internal/cron/aggregate-and-purge"),
    ("GET", "/readyz"),
}


def test_no_route_answers_junk_with_a_server_error(site):  # noqa: F811
    client = site.alice
    csrf = csrf_from(client.get("/app").text)
    failures = []
    for (method, template), who in ROUTES.items():
        if (method, template) in SKIP or who == "admin" or template.startswith("/auth/"):
            continue
        if template in {"/logout", "/login", "/login/microsoft", "/login/email"}:
            continue  # signing out / starting sign-in are covered elsewhere
        for junk in JUNK:
            fields = dict.fromkeys(FIELDS, junk) | {"csrf_token": csrf}
            for path in (fill(site, template),):
                if method == "GET":
                    short = {k: v[:40] for k, v in fields.items()}  # the client caps a URL
                    response = client.get(path, params=short)
                else:
                    response = client.post(path, data=fields)
                if response.status_code >= 500:
                    failures.append((method, template, junk[:12], response.status_code))
    assert failures == []


@pytest.mark.parametrize("number", ["0", "-1", "99999999999999999999", "1.5", "abc", "%00"])
def test_page_image_numbers_that_are_not_pages_are_404s(site, number):  # noqa: F811
    response = site.alice.get(f"/jobs/{site.ids['job_id']}/pages/{number}.png")
    assert response.status_code in {404, 422, 400}, response.status_code


@pytest.mark.parametrize("element", ["..", "a" * 200, "p1-e3%2f..%2f", "p1 e3", "%00"])
def test_element_ids_that_are_not_ids_are_404s(site, element):  # noqa: F811
    for path in ("crops", "regions"):
        response = site.alice.get(f"/jobs/{site.ids['job_id']}/{path}/{element}.png")
        assert response.status_code in {404, 400, 422}, (path, element, response.status_code)
