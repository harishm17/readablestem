"""Static files: hashed names, long cache lifetimes, and the font as a WOFF2."""

from __future__ import annotations

import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from fixtures.web.support import make_settings
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


def test_the_url_carries_the_first_twelve_hex_digits_of_the_files_hash():
    assert assets.static_url("app.css") == f"/static/app.{digest('app.css')}.css"
    assert assets.static_url("app.js") == f"/static/app.{digest('app.js')}.js"
    assert assets.static_url("no-such-file.css") == "/static/no-such-file.css"  # a 404 to show


def test_pages_ask_for_the_hashed_names(client):
    page = client.get("/privacy").text
    assert f'href="/static/app.{digest("app.css")}.css"' in page
    assert f'src="/static/app.{digest("app.js")}.js"' in page
    assert 'src="/static/vendor/htmx-2.0.11.min.js"' in page  # versioned in its name already


def test_a_hashed_file_is_cached_for_a_year_and_never_revalidated(client):
    url = assets.static_url("app.css")
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.content == (STATIC / "app.css").read_bytes()
    assert response.headers["content-type"].startswith("text/css")
    assert "no-store" not in response.headers["cache-control"]


def test_a_hash_that_is_not_the_files_is_not_found(client):
    assert client.get("/static/app.000000000000.css").status_code == 404
    assert client.get("/static/other.1a2b3c4d5e6f.css").status_code == 404


def test_the_plain_name_is_still_served_and_revalidated_with_an_etag(client):
    first = client.get("/static/app.css")
    assert first.status_code == 200 and first.headers["cache-control"] == "no-cache"
    again = client.get("/static/app.css", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["cache-control"] == "no-cache"


def test_vendored_files_carry_their_version_so_they_are_cached_for_a_year(client):
    for path in ("vendor/htmx-2.0.11.min.js", "vendor/fonts/latinmodern-math-1.959.woff2"):
        response = client.get(f"/static/{path}")
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_the_font_goes_out_as_woff2_at_about_half_the_size(client):
    response = client.get("/static/vendor/fonts/latinmodern-math-1.959.woff2")
    assert response.headers["content-type"] == "font/woff2"
    assert response.content[:4] == b"wOF2" and len(response.content) < 400_000
    css = client.get(assets.static_url("app.css")).text
    assert re.search(
        r'url\("/static/vendor/fonts/latinmodern-math-1\.959\.woff2"\) format\("woff2"\)', css
    )


def test_nothing_else_loses_its_no_store_and_traversal_is_still_refused(client):
    assert client.get("/privacy").headers["cache-control"] == "no-store"
    assert client.get("/static/../config.py").status_code in {400, 404}
    assert client.get("/static/%2e%2e/config.py").status_code in {400, 404}


def test_the_hash_follows_the_file_and_is_read_again_only_when_it_changes(tmp_path):
    (tmp_path / "x.css").write_text("a { color: red }")
    first = assets.content_hash("x.css", root=tmp_path)
    assert first == hashlib.sha256(b"a { color: red }").hexdigest()[:12]
    assert assets.content_hash("x.css", root=tmp_path) == first
    (tmp_path / "x.css").write_text("a { color: blue }, and longer")
    assert assets.content_hash("x.css", root=tmp_path) != first
    assert assets.content_hash("../x.css", root=tmp_path / "sub") is None  # outside the root
    assert assets.content_hash("missing.css", root=tmp_path) is None
