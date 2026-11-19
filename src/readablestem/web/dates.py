"""The one place a time becomes words or a machine stamp (SW9).

The server knows no time zone for the person reading: only a `<time>` element is rewritten in
their own zone, in the browser (`app.js`, the `utc` template filter). So every date the server
writes into a sentence, a heading, a title or a file says the zone it is in: "UTC". There is
no stored time-zone preference; a person's zone is not asked for or kept.

- `format_when` is for text: `long` ("October 9, 2026, 14:03 UTC", the review record) and
  `short` ("Oct 9, 2026, 14:03 UTC", the fallback inside a `<time>` element and title text).
- `format_day` is for a calendar day with no time ("October 9, 2026", or "Oct 9, 2026"): the
  day a version was made, a policy's date, the last day of a link, a usage row. `format_time`
  is the time of day alone ("00:00 UTC", when a quota day resets).
- `machine_date` is for machines (`<time datetime>`, the record's CSV): ISO 8601, UTC.

A quota day stays a UTC day (`web.quota`), and the pages that show it say "UTC" with it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from markupsafe import Markup

from readablestem.pipeline.checked_words import long_date
from readablestem.web.database import as_utc

Style = Literal["long", "short"]


def format_when(value: datetime | None, style: Style = "long") -> str:
    """A time as words, in UTC and saying so; "" for none."""
    when = as_utc(value)
    if when is None:
        return ""
    return f"{format_day(when, style)}, {format_time(when)}"


def format_day(value: date | datetime | str | None, style: Style = "long") -> str:
    """A calendar day with no time: "October 9, 2026" (`short`: "Oct 9, 2026"); "" for none.
    A time is put in UTC first, and a string is an ISO date ("2026-10-09")."""
    if isinstance(value, str):
        value = date.fromisoformat(value) if value else None
    if isinstance(value, datetime):
        value = as_utc(value)
    if value is None:
        return ""
    return long_date(value) if style == "long" else f"{value:%b} {value.day}, {value.year}"


def format_time(value: datetime | None) -> str:
    """The time of day in UTC, saying so ("14:03 UTC"); "" for none."""
    when = as_utc(value)
    return f"{when:%H:%M} UTC" if when is not None else ""


def machine_date(value: datetime | None) -> str:
    """A time for a machine: ISO 8601 in UTC, "" for none."""
    when = as_utc(value)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ") if when is not None else ""


def time_tag(value: datetime | None) -> Markup:
    """A time for the page body: `<time datetime>` saying it in UTC, which app.js rewrites in
    the reader's own time zone; without JavaScript the UTC text stays (the `utc` template
    filter). Not for <title> or attributes: use `format_when` there."""
    if value is None:
        return Markup("")
    return Markup('<time class="local-time" datetime="{}">{}</time>').format(
        machine_date(value), format_when(value, "short")
    )
