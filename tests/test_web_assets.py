"""Static files: hashed names, long cache lifetimes, and the font as a WOFF2."""

from __future__ import annotations

import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from fixtures.web.support import ALICE, WebEnv, csrf_from, login, make_settings
from readablestem.web import assets
from readablestem.web.app import create_app

STATIC = assets.STATIC_DIR


@pytest.fixture
def client(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app, follow_redirects=False) as browser:
        yield browser


def digest(name: str) -> str:
    return hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:12]


def test_per_user_pages_stay_no_store(client):
    assert client.get("/login").headers["cache-control"] == "no-store"
    assert client.get("/status").headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/", "/limits", "/privacy"])
def test_a_public_page_answers_a_repeat_visit_with_a_304(client, path):
    first = client.get(path)
    assert first.status_code == 200 and first.headers["cache-control"] == "public, no-cache"
    assert "csrf_token" not in first.text
    again = client.get(path, headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    assert client.get(path, headers={"If-None-Match": '"another"'}).status_code == 200


def test_a_signed_in_visitor_gets_no_cached_public_page(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        response = client.get("/")
        assert response.headers["cache-control"] == "no-store" and "etag" not in response.headers


def test_fixed_files_outside_static_answer_a_304(client):
    for path in ("/favicon.ico", "/apple-touch-icon.png", "/demo/source.pdf"):
        first = client.get(path)
        assert first.status_code == 200, path
        again = client.get(path, headers={"If-None-Match": first.headers["etag"]})
        assert again.status_code == 304 and again.content == b"", path


def test_text_is_compressed_but_never_a_page_that_carries_the_csrf_token(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        page = client.get("/limits", headers={"Accept-Encoding": "gzip"})
        assert (
            page.headers["content-encoding"] == "gzip" and "Accept-Encoding" in page.headers["vary"]
        )
        assert int(page.headers["content-length"]) < len(page.content) / 2  # a text page packs well
        assert (
            "content-encoding"
            not in client.get("/limits", headers={"Accept-Encoding": "identity"}).headers
        )
        # A conditional request still works against the compressed copy's weak tag.
        again = client.get(
            "/limits", headers={"Accept-Encoding": "gzip", "If-None-Match": page.headers["etag"]}
        )
        assert again.status_code == 304
        # Files that are already compressed are sent as they are.
        font = next((STATIC / "vendor" / "fonts").glob("*.woff2")).name
        sent = client.get(f"/static/vendor/fonts/{font}", headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in sent.headers
        # A signed-in page holds the token (the sign-out form), so it is sent plain.
        env.allow(ALICE)
        login(client, ALICE)
        mine = client.get("/app", headers={"Accept-Encoding": "gzip"})
        assert csrf_from(mine.text) and "content-encoding" not in mine.headers


def test_nothing_else_loses_its_no_store_and_traversal_is_still_refused(client):
    assert client.get("/jobs/abc").headers["cache-control"] == "no-store"
    assert client.get("/static/../config.py").status_code in {400, 404}
    assert client.get("/static/%2e%2e/config.py").status_code in {400, 404}
    # A name with a null byte, shaped like a hashed one, never reaches the file system.
    assert client.get("/static/a%00b.0123456789ab.css").status_code in {400, 404}
    assert client.get("/static/%2e%2e/%2e%2e/etc/passwd.0123456789ab.txt").status_code in {400, 404}


def test_the_manifest_and_the_root_icon_answer_and_name_icons_that_exist(client):
    manifest = client.get("/site.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    body = manifest.json()
    assert body["start_url"] == "/"
    tokens = (STATIC / "tokens.css").read_text()
    assert body["theme_color"] == re.search(r"--bg: (#[0-9a-f]{6});", tokens).group(1)
    for icon in body["icons"]:
        got = client.get(icon["src"])
        assert got.status_code == 200 and got.headers["content-type"] == icon["type"], icon
    touch = client.get("/apple-touch-icon.png")
    assert touch.status_code == 200 and touch.headers["content-type"] == "image/png"
    assert touch.content == (STATIC / "apple-touch-icon.png").read_bytes()
    svg = client.get("/static/icon.svg")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    page = client.get("/privacy").text
    assert 'rel="manifest" href="/site.webmanifest"' in page
