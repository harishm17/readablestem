"""Static files: hashed names, long cache lifetimes, and the font as a WOFF2."""

from __future__ import annotations

import hashlib

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


def test_per_user_pages_stay_no_store(client):
    for path in ("/", "/login", "/limits"):
        assert client.get(path).headers["cache-control"] == "no-store", path


def test_nothing_else_loses_its_no_store_and_traversal_is_still_refused(client):
    assert client.get("/privacy").headers["cache-control"] == "no-store"
    assert client.get("/static/../config.py").status_code in {400, 404}
    assert client.get("/static/%2e%2e/config.py").status_code in {400, 404}
