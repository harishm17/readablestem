"""What the PDF route did with a Word or PowerPoint upload, said on the job page (WS18.13).

The upload's preflight counted what is in the file (`web.office_convert.Census`: counts only,
stored on the job as `Job.office_json`). Here the counts become two things:

- notices (`core.types.Notice`, whole document), added to the result when it is stored: open
  tracked changes in a Word file are *required* (listed under "Not handled in this file", and
  the job is "Ready", not "Ready to share"); speaker notes, hidden slides and comments are
  *optional* ("Worth checking"). Each says how many; none quotes the file.
- `read_lines`: the sentences of the job page's "How this file was read" section.

Counts the census holds but nothing here reads (`units`, `units_exact`) are for a later charge
by size; the counts it does not hold (hidden text, fields, text boxes, headers, footers, tables)
were left out because no sentence needs them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from readablestem.core.types import Notice
from readablestem.web.office_convert import Census

TRACKED_CHANGES = "office_tracked_changes"
SPEAKER_NOTES = "office_speaker_notes"
HIDDEN_SLIDES = "office_hidden_slides"
COMMENTS = "office_comments"
CODES = (TRACKED_CHANGES, SPEAKER_NOTES, HIDDEN_SLIDES, COMMENTS)


def parse(data: Mapping[str, Any] | None) -> Census | None:
    """The census stored on a job, or None for a PDF (or a row that holds nothing usable)."""
    if not data:
        return None
    try:
        return Census.from_json(dict(data))
    except ValueError:
        return None


def notices_of(census: Census) -> list[Notice]:
    """The notices the census calls for, for the whole document (`web.worker` adds them to
    the result): a count each, never a word of the file."""
    changes = census.tracked_insertions + census.tracked_deletions
    out: list[Notice] = []
    if changes:
        out.append(Notice(code=TRACKED_CHANGES, count=changes, review="required"))
    for code, count in (
        (SPEAKER_NOTES, census.speaker_notes),
        (HIDDEN_SLIDES, census.hidden_slides),
        (COMMENTS, census.comments),
    ):
        if count:
            out.append(Notice(code=code, count=count))
    return out


def with_notices(notices: list[Notice], census: Census | None) -> list[Notice]:
    """`notices` with the census's, once each (a result written again, as by "Try again",
    does not list them twice)."""
    if census is None:
        return notices
    return [n for n in notices if n.code not in CODES] + notices_of(census)


def read_lines(census: Census | None) -> list[str]:
    """The job page's sentences about how the file was read: empty for a PDF."""
    if census is None:
        return []
    word = census.kind == "docx"
    lines = [
        ("This Word file" if word else "This PowerPoint file")
        + " was turned into a tagged PDF first, and that PDF was read the way any PDF is. "
        "Nothing was read from the Word or PowerPoint file itself."
    ]
    if census.typed_equations:
        many = census.typed_equations
        lines.append(
            f"{many} equation{'s' if many != 1 else ''} typed in the equation editor "
            f"{'was' if many == 1 else 'were'} read as {'a picture' if many == 1 else 'pictures'} "
            "of the page, like any equation in a PDF, so check them against the original."
        )
    return lines
