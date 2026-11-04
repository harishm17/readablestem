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


def test_the_status_page_says_when_it_was_checked_and_links_to_check_again(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        response = client.get("/status")
    page = response.text
    assert response.status_code == 200
    checked = re.search(
        r'id="status-checked">Checked at <time class="local-time" datetime="(.*?)"', page
    )
    assert checked and checked.group(1).endswith("Z")
    assert '<a href="/status">Check again</a>' in page
    window = " ".join(re.sub(r"<[^>]+>", " ", page[page.index('id="status-window"') :]).split())
    assert "requests to the model in the last 10 minutes" in window
    assert "does not test that the service is up" in window
    assert "The page changes only when you check again or turn on refreshing" in window
    assert "state right now" not in page


def get_status(tmp_path, path="/status"):
    env = WebEnv(tmp_path)
    with env.running() as client:
        return client.get(path).text


def test_no_recent_requests_is_not_reported_as_no_problems(tmp_path):
    page = get_status(tmp_path)
    text = status_text(page)
    assert "No recent requests; availability untested" in text
    assert "No problems seen" not in text


def test_answered_requests_are_reported_with_their_count_and_time(tmp_path):
    for _ in range(3):
        HEALTH.record("ok")
    page = get_status(tmp_path)
    assert "Recent requests were answered: 3 in the last 10 minutes, the last at" in status_text(
        page
    )
    assert "availability untested" not in page


def test_some_failures_without_an_outage_are_said_plainly(tmp_path):
    HEALTH.record("ok")
    HEALTH.record("api_error")
    text = status_text(get_status(tmp_path))
    assert "Mixed: 1 of 2 requests in the last 10 minutes failed" in text


def test_the_retention_prose_is_not_on_the_status_page(tmp_path):
    text = status_text(get_status(tmp_path))
    assert "hours after they were last worked on" not in text
    assert 'href="/limits"' in get_status(tmp_path / "again")


def test_automatic_refresh_is_off_until_asked_for_and_can_be_stopped(tmp_path):
    off = get_status(tmp_path / "a")
    assert 'http-equiv="refresh"' not in off
    assert 'href="/status?refresh=1"' in off
    on = get_status(tmp_path / "b", "/status?refresh=1")
    assert '<meta http-equiv="refresh" content="60">' in on
    assert "This page refreshes itself every minute" in status_text(on)
    assert '<a href="/status">stop refreshing</a>' in on
