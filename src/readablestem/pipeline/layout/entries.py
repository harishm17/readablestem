"""Where a list of entries starts a new one (`REGION_TREE`, rules 9 and 10 of the second round).

An index, a glossary, a list of key terms and a chapter outline set one entry per line or per few
lines with no blank between them, so the geometry that joins the lines of a paragraph (the same
size, the next baseline, the same left edge) joins the entries. `starts_entry` is asked by
`figures._continues` and only with `_Context.region_tree` on; it says that `row` starts a new
entry, never that it continues the run.

- Rule 9, index pages. On a page of 20 or more lines where half end in a page number and the
  median line has seven words or fewer (`regions._is_index`), a line that starts at the entry
  margin after a line that ends in a page number starts a new entry. The page is known to the
  rows by their column's name (`INDEX_PAGE`), which every copy of a row keeps.
- Rule 10, entry lists. A line at the first line's edge starts a new paragraph when the run
  hangs (a glossary: the lines after the first are indented), or when both lines open with a
  section number (a chapter outline) or with a bold term (key terms).
"""

from __future__ import annotations

import re

from readablestem.pipeline.layout.shared import _Row, _Run

# The rules of the second round that live here, by number (see `regions.RULES` for the others).
RULES: frozenset[int] = frozenset({9, 10})

INDEX_PAGE = "~index"  # added to the column name of every row of an index page
PAGE_NUMBER_END = re.compile(r"\d{1,4}\s*$")
_SECTION_NUMBER = re.compile(r"^\s*\d{1,2}\.\d{1,2}\s+\S")


def _bold_lead(row: _Row) -> bool:
    """The row starts with a bold term and goes on in regular type."""
    letters = [c for c in row.ink if c.c.isalnum()]
    if len(letters) < 6 or not (letters[0].bold and letters[1].bold) or letters[-1].bold:
        return False
    n = next((k for k, c in enumerate(letters) if not c.bold), len(letters))
    return 2 <= n <= 0.7 * len(letters)


def starts_entry(prev: _Row, row: _Row, run: _Run) -> bool:
    """Does `row` start a new entry rather than continue `run`, whose last row is `prev`?"""
    size = prev.size
    if (
        9 in RULES
        and row.column.endswith(INDEX_PAGE)
        and PAGE_NUMBER_END.search(prev.clean)
        and row.x0 <= run.margin + 0.5 * size
    ):
        return True
    if 10 not in RULES or row.vertical or row.marker is not None:
        return False
    first = run.rows[0]
    if row.x0 > first.x0 + 0.3 * size:
        return False  # not at the entry edge
    hanging = len(run.rows) >= 2 and run.margin - first.x0 >= 0.8 * size
    if hanging:  # a glossary: a line back at the entry edge is the next entry
        return True
    if _SECTION_NUMBER.match(row.clean) and _SECTION_NUMBER.match(first.clean):
        return True  # a chapter outline: "3.1 Title" lines under one another
    # a run of "term definition" entries
    return (
        _bold_lead(first)
        and _bold_lead(row)
        and (len(run.rows) == 1 or prev.x1 < run.box[2] - 1.5 * size)
    )
