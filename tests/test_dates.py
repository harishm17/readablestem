"""One date function: server-built dates say UTC, machine dates are ISO UTC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from readablestem.web.dates import format_day, format_time, format_when, machine_date


def test_a_date_is_written_in_utc_and_says_so_whatever_zone_it_came_in():
    # 11:46 PM Pacific on November 9 is already November 10 in UTC: the text says UTC, so
    # nobody reads it as the Pacific date.
    pacific = datetime(2026, 11, 9, 23, 46, tzinfo=timezone(timedelta(hours=-8)))
    assert format_when(pacific) == "November 10, 2026, 07:46 UTC"
    assert format_when(pacific, "short") == "Nov 10, 2026, 07:46 UTC"
    assert machine_date(pacific) == "2026-11-10T07:46:00Z"
    assert format_when(datetime(2026, 11, 10, 7, 46)) == format_when(pacific)  # naive is UTC
    assert format_when(None) == machine_date(None) == ""
    assert machine_date(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == "2026-01-02T03:04:05Z"
    # A day alone is the same text without the time, in UTC, from a date, a time or ISO text.
    assert format_day(pacific) == "November 10, 2026"
    assert format_day(pacific, "short") == "Nov 10, 2026"
    assert format_day("2026-11-04") == "November 4, 2026" and format_day(None) == ""
    assert format_time(pacific) == "07:46 UTC"
