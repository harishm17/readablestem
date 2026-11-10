"""T3-3: the words and the arithmetic of choosing pages (web/ranges.py)."""

from __future__ import annotations

import pytest

from readablestem.web import ranges
from readablestem.web.ranges import PartsError, label, parse_parts


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        ((0,), "1"),
        ((0, 1, 2), "1-3"),
        ((0, 1, 2, 4), "1-3, 5"),
        ((4, 0, 1), "1-2, 5"),
        ((9, 10, 11, 12), "10-13"),
    ],
)
def test_a_set_of_pages_is_written_as_people_write_it(pages, expected):
    assert label(pages) == expected


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("", "Type the pages to convert"),
        ("   \n  ", "Type the pages to convert"),
        ("abc", "“abc” is not a page number or a range"),
        ("1-3-5", "is not a page number or a range"),
        ("0-3", "pages are numbered from 1"),
        ("8-3", "runs backwards. Write it as 3-8"),
        ("1-70", "page 70 is not in this file, which has 59 pages"),
        ("1-31", "has 31 pages. A part can have at most 30"),
        ("1-10\n5-12", "Page 5 is in part 1 and part 2"),
        ("1-3,", "is not a page number or a range"),
    ],
)
def test_what_cannot_be_used_is_explained_in_plain_words(text, words):
    with pytest.raises(PartsError) as exc:
        parse_parts(text, total=59, limit=30)
    assert words in exc.value.message
    assert "Traceback" not in exc.value.message and "Error(" not in exc.value.message


def test_too_many_parts_at_once_is_refused():
    text = "\n".join(str(n) for n in range(1, ranges.MAX_PARTS + 2))
    with pytest.raises(PartsError) as exc:
        parse_parts(text, total=100, limit=30)
    assert f"at most {ranges.MAX_PARTS}" in exc.value.message
