"""Every number a person reads about the upload limits comes from the settings.

The limits were raised (100 pages, 50 MB; 500 pages and 100 MB kept for choosing pages; a new
account's day 150 pages) and the pages that mention them are templates, so a number written
into one would go on saying the old limit. These tests give the settings values no default
has, and look for them in every page that talks about limits, and for the old defaults in none.
"""

from __future__ import annotations

import re
from html import unescape

import pytest

from fixtures.web.support import ALICE, WebEnv, login, make_pdf, token
from readablestem.web import ranges

ODD = {
    "max_pages_per_job": 77,
    "max_upload_mb": 66,
    "max_large_upload_mb": 88,
    "max_source_pages": 333,
    "open_tier_daily_page_quota": 111,
    "per_user_daily_page_quota": 222,
    "signup_mode": "open",
}
# The numbers the old defaults put in these pages.
OLD = ("30 pages", "20 MB", "300 pages", "30 MB", "20 pages a day", "90 pages", "1-30")


def flat(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, **ODD)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.alice = client
        yield env


def test_the_limits_page_names_the_configured_limits(web):
    text = flat(web.alice.get("/limits").text)
    assert "Up to 77 pages and 66 MB per upload." in text
    assert "up to 333 pages and 88 MB, is kept" in text
    assert "can convert 111 pages and start" in text
    assert "an invited account can convert 222 pages a day" in text
    for old in OLD:
        assert old not in text, old


def test_the_upload_form_names_the_configured_limits(web):
    text = flat(web.alice.get("/app").text)
    # the site copy (content/site-rewrite) states each limit once, from the settings
    assert "up to 77 pages and 66 MB." in text
    assert "up to 333 pages, 88 MB) are kept" in text
    for old in OLD:
        assert old not in text, old
    page = web.alice.get("/app").text
    assert 'data-max-mb="66"' in page and 'data-limit-mb="88"' in page
    assert f'data-max-bytes="{66 * 1024 * 1024}"' in page


def test_the_landing_and_sign_in_pages_name_the_configured_quota(web):
    landing = flat(web.client().get("/").text)
    assert "Up to 77 pages at a time" in landing
    assert "New accounts have daily limits: 111 pages a day" in landing
    for old in OLD:
        assert old not in landing, old


def test_the_parts_page_and_its_errors_do_not_show_the_old_limit_as_an_example(web):
    response = web.alice.post(
        "/jobs",
        data={"csrf_token": token(web.alice)},
        files={"pdf": ("big.pdf", make_pdf(80), "application/pdf")},
    )
    assert response.status_code == 303, response.text[:300]
    page = web.alice.get(response.headers["location"])
    text = flat(page.text)
    assert "80 pages" in text and "up to 77 pages" in text
    assert "of up to 77 pages" in text
    for old in OLD:
        assert old not in text, old
    with pytest.raises(ranges.PartsError) as error:
        ranges.parse_parts("", total=80, limit=77)
    assert "1-30" not in error.value.message
