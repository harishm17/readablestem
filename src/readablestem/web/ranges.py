"""Page ranges for converting a large file in parts (T3-3).

A file over the limits (`MAX_PAGES_PER_JOB` pages, `MAX_UPLOAD_MB`) is not refused: the
instructor chooses which pages to convert, in parts that each fit. This module is the words
and the arithmetic, with no file in it:

- `parse_parts` reads what the person typed: one part per line (or separated by semicolons),
  each part one or more ranges separated by commas: `1-30`, `31-59`, `1-10, 15-20; 25`.
  Pages are numbered from 1, as in a PDF viewer. Anything it cannot use is explained in
  plain words, naming the part and the page, never a code.
- `suggest_parts` offers a starting point: the pages not yet converted, cut into as few
  parts as the limit allows, of nearly equal size (59 pages with a limit of 30 are two
  parts, 30 and 29 pages; 61 pages are three, 21, 20 and 20). Given the PDF's chapter
  starts it cuts there when a chapter fits, and given the pages left today it makes the
  first part what can start now (60 pages left: the first part has at most 60 pages).
- `schedule` counts which of the typed parts start now and which wait for the day's reset,
  the one count behind the preview's sentences; `spoken` writes a label in words ("1 to 80").
- `label` writes a set of pages the way people write them ("1-30", "1-5, 9") for a part's
  name and for the lists on the pages; `pages_of_label` reads one back.

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


def pages_of_label(text: str | None) -> tuple[int, ...] | None:
    """The 0-based pages a part's `label` names ("31-59" or "1-5, 9"), ascending; None when
    it is not a label `label` wrote. The inverse of `label`, for putting a part back among
    the pages of its source."""
    pages: set[int] = set()
    for piece in (text or "").split(","):
        match = _RANGE.match(piece.strip())
        if match is None:
            return None
        first, last = int(match.group(1)), int(match.group(2) or match.group(1))
        if first < 1 or last < first:
            return None
        pages.update(range(first - 1, last))
    return tuple(sorted(pages)) or None


def parse_parts(text: str, *, total: int, limit: int, taken: set[int] | None = None) -> list[Part]:
    """The parts typed in `text`, for a file of `total` pages whose parts may have at most
    `limit` pages each. `taken`: 0-based pages already converted, refused again.

    Raises `PartsError` for an empty entry, something that is not a page or a range, a page
    that is not in the file, a range written backwards, a part over `limit`, a page in two
    parts or already converted, or more than `MAX_PARTS` parts.
    """
    entries = entries_of(text)
    if not entries:
        raise PartsError("Type the pages to convert, for example 1-10.")
    if len(entries) > MAX_PARTS:
        raise PartsError(f"That is {len(entries)} parts; convert at most {MAX_PARTS} at a time.")
    parts: list[Part] = []
    seen: dict[int, int] = {}
    for number, entry in enumerate(entries, start=1):
        named = f"Part {number}" if len(entries) > 1 else ""
        parts.append(_read_entry(entry, number, named, total, limit, taken, seen))
    return parts


def entries_of(text: str | None) -> list[str]:
    """The parts typed in `text`: one per line, or separated by semicolons, blanks left out."""
    return [e.strip() for e in re.split(r"[\n;]+", text or "") if e.strip()]


def _open(named: str, text: str) -> str:
    """An error about one typed part: "Part 2: page 5 ..." when there are several, and with a
    single one just the sentence ("Page 5 ..."), never "The pages:"."""
    return f"{named}: {text}" if named else text[:1].upper() + text[1:]


def _read_entry(
    entry: str,
    number: int,
    named: str,
    total: int,
    limit: int,
    taken: set[int] | None,
    seen: dict[int, int],
) -> Part:
    """One typed part as a `Part`, or a `PartsError` naming `named`. `seen` maps each page
    already placed in an earlier part to that part's number, and gets this part's pages."""
    pages: set[int] = set()
    for piece in (p.strip() for p in entry.split(",")):
        match = _RANGE.match(piece)
        if not piece or match is None:
            raise PartsError(
                _open(
                    named,
                    f"“{_short(piece or entry)}” is not a page number or a range such as "
                    "3-10. Use numbers, hyphens and commas.",
                )
            )
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if first < 1 or last < 1:
            raise PartsError(_open(named, "pages are numbered from 1."))
        if last < first:
            raise PartsError(_open(named, f"“{piece}” runs backwards. Write it as {last}-{first}."))
        beyond = max(first, last)
        if beyond > total:
            raise PartsError(
                _open(named, f"page {beyond} is not in this file, which has {total} pages.")
            )
        pages.update(range(first - 1, last))
    if len(pages) > limit:
        raise PartsError(
            f"{named or 'This'} has {len(pages)} pages. A part can have at most {limit}: "
            "split it into two."
        )
    placed: dict[int, int] = {}
    for page in sorted(pages):
        if taken and page in taken:
            raise PartsError(_open(named, f"page {page + 1} was already converted."))
        if page in seen:
            raise PartsError(
                f"Page {page + 1} is in part {seen[page]} and part {number}. "
                "Each page can go in one part."
            )
        placed[page] = number
    seen.update(placed)
    return Part(tuple(sorted(pages)))


@dataclass(frozen=True)
class PreviewRow:
    """One typed part as the preview shows it: its pages written plainly and how many, or
    the reason it cannot be used (`error`, in the same words as `parse_parts`)."""

    number: int
    label: str
    count: int
    error: str = ""

    @property
    def words(self) -> str:
        """The pages in words ("1 to 80"), for the sentences and the list."""
        return spoken(self.label)


def preview(
    text: str, *, total: int, limit: int, taken: set[int] | None = None
) -> list[PreviewRow]:
    """What `parse_parts` would make of `text`, one row per typed part, without stopping at
    the first problem: a part that cannot be used is a row with its error and the others
    still count. Empty for nothing typed. Parts past `MAX_PARTS` are one error row."""
    entries = entries_of(text)
    rows: list[PreviewRow] = []
    seen: dict[int, int] = {}
    for number, entry in enumerate(entries[:MAX_PARTS], start=1):
        named = f"Part {number}" if len(entries) > 1 else ""
        try:
            part = _read_entry(entry, number, named, total, limit, taken, seen)
        except PartsError as exc:
            rows.append(PreviewRow(number, _short(entry), 0, exc.message))
        else:
            rows.append(PreviewRow(number, part.label, len(part.pages)))
    if len(entries) > MAX_PARTS:
        message = f"That is {len(entries)} parts; convert at most {MAX_PARTS} at a time."
        rows.append(PreviewRow(MAX_PARTS + 1, "", 0, message))
    return rows


def _short(text: str, limit: int = 30) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def suggest_parts(
    total: int,
    limit: int,
    taken: set[int] | None = None,
    *,
    left: int | None = None,
    chapters: list[int] | tuple[int, ...] = (),
) -> list[Part]:
    """The pages not yet converted, in as few parts as `limit` allows and of nearly equal
    size, each a run of consecutive pages (a gap left by converted pages starts a new part).

    `left`: the pages the person may still convert today. When fewer than the pages to
    convert, the first parts are cut to exactly what can start now (the rest follow, cut
    the same way), so a part is never suggested that today's pages cannot start. `chapters`:
    the 0-based pages where the PDF's chapters start (`pdf_probe outline`); a part ends before
    one when that leaves it at least half the limit, and the pages are cut evenly otherwise."""
    starts = set(chapters)
    todo = [p for p in range(total) if not taken or p not in taken]
    runs: list[list[int]] = []
    for page in todo:
        if runs and page == runs[-1][-1] + 1:
            runs[-1].append(page)
        else:
            runs.append([page])
    parts: list[Part] = []
    budget = left
    for run in runs:
        if budget is not None and 0 < budget < len(run):
            pieces = [run[:budget], run[budget:]]
            budget = 0
        else:
            pieces = [run]
            budget = None if budget is None else budget - len(run)
        for piece in pieces:
            parts += _cut(piece, limit, starts)
    return parts[:MAX_PARTS]


def _cut(run: list[int], limit: int, chapters: set[int]) -> list[Part]:
    """One run of consecutive pages in parts of at most `limit`: ending before the latest
    chapter start that leaves the part at least half the limit, else evenly (the size the
    rest would have if it were cut into as few equal parts as fit)."""
    parts: list[Part] = []
    rest = run
    while rest:
        if len(rest) <= limit:
            parts.append(Part(tuple(rest)))
            break
        size = -(-len(rest) // -(-len(rest) // limit))
        at = [i for i in range(max(1, limit // 2), limit + 1) if rest[i] in chapters]
        if at:
            size = at[-1]
        parts.append(Part(tuple(rest[:size])))
        rest = rest[size:]
    return parts


def spoken(text: str) -> str:
    """A `label` in words: "1-80" is "1 to 80", "1-5, 9, 12" is "1 to 5, 9 and 12"."""
    pieces = [piece.replace("-", " to ") for piece in text.split(", ")]
    if len(pieces) == 1:
        return pieces[0]
    return ", ".join(pieces[:-1]) + " and " + pieces[-1]


@dataclass(frozen=True)
class Schedule:
    """Which of the usable typed parts start now and which wait for the day's reset, in the
    order they run (`routes.parts._start_parts` stops at the first part today's pages cannot
    start): the one count the preview's sentences are written from. `bad` is the number of
    parts that cannot be used."""

    now: tuple[PreviewRow, ...]
    later: tuple[PreviewRow, ...]
    bad: int


def schedule(rows: list[PreviewRow], left: int | None) -> Schedule:
    """Split the rows into the parts `left` pages today start now and the rest. `left=None`:
    no daily limit, everything starts now."""
    good = [r for r in rows if not r.error]
    used = 0
    fits = 0
    for row in good:
        if left is not None and used + row.count > left:
            break
        used += row.count
        fits += 1
    return Schedule(tuple(good[:fits]), tuple(good[fits:]), len(rows) - len(good))


def left_out(total: int, taken: set[int] | None, parts: list[Part]) -> list[int]:
    """The pages (0-based) that no suggested part covers and that are not converted yet: what
    `suggest_parts` cut off at `MAX_PARTS`. Empty when the suggestion covers everything."""
    covered = {page for part in parts for page in part.pages}
    return [p for p in range(total) if p not in covered and not (taken and p in taken)]


def examples(limit: int, total: int) -> list[str]:
    """Range examples for the instructions, valid for this file and the active page limit
    (never "1-10" under a limit of 5, never a page the file does not have)."""
    out = [f"1-{max(1, min(limit, 10, total))}"]
    count = min(8, limit - 1)  # pages 5 to 5+count-1, plus one more page
    last = 4 + count
    single = min(20, total)
    if count >= 2 and single > last:
        out.append(f"5-{last}, {single}")
    return out


def suggestion_text(parts: list[Part]) -> str:
    """The suggested parts as the text box takes them: one per line."""
    return "\n".join(p.label for p in parts)
