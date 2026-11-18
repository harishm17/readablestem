"""The one place a time becomes words or a machine stamp (SW9).

The server knows no time zone for the person reading: only a `<time>` element is rewritten in
their own zone, in the browser (`app.js`, the `utc` template filter). So every date the server
writes into a sentence, a heading, a title or a file says the zone it is in: "UTC". There is
no stored time-zone preference; a person's zone is not asked for or kept.

- `format_when` is for text: `long` ("October 9, 2026, 14:03 UTC", the review record) and
  `short` ("Oct 9, 2026, 14:03 UTC", the fallback inside a `<time>` element and title text).
- `machine_date` is for machines (`<time datetime>`, the record's CSV): ISO 8601, UTC.

A quota day stays a UTC day (`web.quota`), and the pages that show it say "UTC" with it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from readablestem.web.database import as_utc

Style = Literal["long", "short"]


def format_when(value: datetime | None, style: Style = "long") -> str:
    """A time as words, in UTC and saying so; "" for none."""
    when = as_utc(value)
    if when is None:
        return ""
    month = f"{when:%B}" if style == "long" else f"{when:%b}"
    return f"{month} {when.day}, {when.year}, {when:%H:%M} UTC"


def machine_date(value: datetime | None) -> str:
    """A time for a machine: ISO 8601 in UTC, "" for none."""
    when = as_utc(value)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ") if when is not None else ""
