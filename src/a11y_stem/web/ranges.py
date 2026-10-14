"""Page ranges for converting a large file in parts (T3-3).

A file over the limits (30 pages, 20 MB) is not refused: the instructor chooses which pages
to convert, in parts that each fit. This module is the words and the arithmetic, with no
file in it:

- `parse_parts` reads what the person typed: one part per line (or separated by semicolons),
  each part one or more ranges separated by commas: `1-30`, `31-59`, `1-10, 15-20; 25`.
  Pages are numbered from 1, as in a PDF viewer. Anything it cannot use is explained in
  plain words, naming the part and the page, never a code.
- `suggest_parts` offers a starting point: the pages not yet converted, cut into as few
  parts as the limit allows, of nearly equal size (59 pages with a limit of 30 are two
  parts, 30 and 29 pages; 61 pages are three, 21, 20 and 20).
- `label` writes a set of pages the way people write them ("1-30", "1-5, 9") for a part's
  name and for the lists on the pages.

Pages inside this module are 0-based sets, like everywhere in the pipeline; text is 1-based.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# More parts than this in one go is a typing mistake, and each part is a document.
MAX_PARTS = 20
_RANGE = re.compile(r"^(\d{1,5})(?:\s*[-–—]\s*(\d{1,5}))?$")  # noqa: RUF001  (en and em dash)


@dataclass(frozen=True)
class Part:
    """One part to convert: its pages (0-based, ascending)."""

    pages: tuple[int, ...]

    @property
    def label(self) -> str:
        return label(self.pages)


class PartsError(Exception):
    """What was typed cannot be used. `message` says why in plain words, naming the part."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def label(pages: tuple[int, ...] | list[int]) -> str:
    """ "1-30", "5", "1-5, 9" for 0-based `pages`."""
    ordered = sorted(set(pages))
    spans: list[tuple[int, int]] = []
    for page in ordered:
        if spans and page == spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], page)
        else:
            spans.append((page, page))
    return ", ".join(f"{a + 1}" if a == b else f"{a + 1}-{b + 1}" for a, b in spans)


def parse_parts(text: str, *, total: int, limit: int, taken: set[int] | None = None) -> list[Part]:
    """The parts typed in `text`, for a file of `total` pages whose parts may have at most
    `limit` pages each. `taken`: 0-based pages already converted, refused again.

    Raises `PartsError` for an empty entry, something that is not a page or a range, a page
    that is not in the file, a range written backwards, a part over `limit`, a page in two
    parts or already converted, or more than `MAX_PARTS` parts.
    """
    entries = [e.strip() for e in re.split(r"[\n;]+", text or "") if e.strip()]
    if not entries:
        raise PartsError("Type the pages to convert, for example 1-30.")
    if len(entries) > MAX_PARTS:
        raise PartsError(f"That is {len(entries)} parts; convert at most {MAX_PARTS} at a time.")
    parts: list[Part] = []
    seen: dict[int, int] = {}
    for number, entry in enumerate(entries, start=1):
        named = f"Part {number}" if len(entries) > 1 else "The pages"
        pages: set[int] = set()
        for piece in (p.strip() for p in entry.split(",")):
            match = _RANGE.match(piece)
            if not piece or match is None:
                raise PartsError(
                    f"{named}: “{_short(piece or entry)}” is not a page number or a range such "
                    "as 3-10. Use numbers, hyphens and commas."
                )
            first = int(match.group(1))
            last = int(match.group(2) or first)
            if first < 1 or last < 1:
                raise PartsError(f"{named}: pages are numbered from 1.")
            if last < first:
                raise PartsError(f"{named}: “{piece}” runs backwards. Write it as {last}-{first}.")
            beyond = max(first, last)
            if beyond > total:
                raise PartsError(
                    f"{named}: page {beyond} is not in this file, which has {total} pages."
                )
            pages.update(range(first - 1, last))
        if len(pages) > limit:
            raise PartsError(
                f"{named} has {len(pages)} pages. A part can have at most {limit}: "
                "split it into two."
            )
        for page in sorted(pages):
            if taken and page in taken:
                raise PartsError(f"{named}: page {page + 1} was already converted.")
            if page in seen:
                raise PartsError(
                    f"Page {page + 1} is in part {seen[page]} and part {number}. "
                    "Each page can go in one part."
                )
            seen[page] = number
        parts.append(Part(tuple(sorted(pages))))
    return parts


def _short(text: str, limit: int = 30) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def suggest_parts(total: int, limit: int, taken: set[int] | None = None) -> list[Part]:
    """The pages not yet converted, in as few parts as `limit` allows and of nearly equal
    size, each a run of consecutive pages (a gap left by converted pages starts a new part)."""
    left = [p for p in range(total) if not taken or p not in taken]
    runs: list[list[int]] = []
    for page in left:
        if runs and page == runs[-1][-1] + 1:
            runs[-1].append(page)
        else:
            runs.append([page])
    parts: list[Part] = []
    for run in runs:
        count = -(-len(run) // limit)  # as few parts as fit
        size, extra = divmod(len(run), count)
        start = 0
        for index in range(count):
            end = start + size + (1 if index < extra else 0)
            parts.append(Part(tuple(run[start:end])))
            start = end
    return parts[:MAX_PARTS]


def suggestion_text(parts: list[Part]) -> str:
    """The suggested parts as the text box takes them: one per line."""
    return "\n".join(p.label for p in parts)
