"""The public status page says when it was checked, what the AI service line is based on,
and offers a way to check again (templates/status.html)."""

from __future__ import annotations

import re

import pytest

from fixtures.web.support import WebEnv
from readablestem.web.provider_health import HEALTH


@pytest.fixture(autouse=True)
def _clean_health():
    HEALTH.reset()
    yield
    HEALTH.reset()


def status_text(page: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


def get_status(tmp_path, path="/status"):
    env = WebEnv(tmp_path)
    with env.running() as client:
        return client.get(path).text


def test_some_failures_without_an_outage_are_said_plainly(tmp_path):
    HEALTH.record("ok")
    HEALTH.record("api_error")
    text = status_text(get_status(tmp_path))
    assert "Mixed: 1 of 2 requests in the last 10 minutes failed" in text


def test_automatic_refresh_is_off_until_asked_for_and_can_be_stopped(tmp_path):
    off = get_status(tmp_path / "a")
    assert 'http-equiv="refresh"' not in off
    assert 'href="/status?refresh=1"' in off
    on = get_status(tmp_path / "b", "/status?refresh=1")
    assert '<meta http-equiv="refresh" content="60">' in on
    assert "This page refreshes itself every minute" in status_text(on)
    assert '<a href="/status">stop refreshing</a>' in on
