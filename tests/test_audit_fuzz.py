"""Launch audit: junk in every parameter of every route never produces a server error.

A 500 is an unhandled case; on this app it can also hide a path that skips a check. For each
route, as its owner and with valid tokens, the query and form fields the app reads are filled
with awkward values (empty, NUL, enormous numbers, path tricks, lone surrogates, huge
strings, markup).
"""

from __future__ import annotations

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
    ("GET", "/healthz/ops"),  # 503 until the backup and cron have reported
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
