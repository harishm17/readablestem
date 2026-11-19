"""One allowance phrase: used, then total, then left, never "left of total"."""

from __future__ import annotations

import pytest

from fixtures.web.support import ALICE, WebEnv, login
from readablestem.web.quota import PAGES_RULE, allowance_phrase


@pytest.mark.parametrize(
    ("used", "total", "unit", "expected"),
    [
        (0, 150, "page", "0 of 150 pages used today, 150 left."),
        (40, 150, "page", "40 of 150 pages used today, 110 left."),
        (150, 150, "page", "150 of 150 pages used today, 0 left."),
        (0, 1, "read", "0 of 1 read used today, 1 left."),
        (12, None, "page", "12 pages used today, no limit set."),
    ],
)
def test_the_phrase_says_used_then_total_then_left(used, total, unit, expected):
    assert allowance_phrase(used, total, unit) == expected


def test_a_reset_time_is_added_as_given():
    assert allowance_phrase(0, 150, "page", "at midnight UTC").endswith(
        "150 left. Resets at midnight UTC."
    )


def test_the_account_page_prints_the_phrase_and_the_rule(tmp_path):
    env = WebEnv(tmp_path, per_user_daily_page_quota=150)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        page = client.get("/account").text
    assert "0 of 150 pages used today, 150 left." in page
    assert PAGES_RULE in page
