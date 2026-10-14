"""T3-3: the words and the arithmetic of choosing pages (web/ranges.py)."""

from __future__ import annotations

import pytest

from readablestem.web import ranges
from readablestem.web.ranges import Part, PartsError, label, parse_parts, suggest_parts


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


def test_parts_are_read_one_per_line_or_after_a_semicolon():
    parts = parse_parts("1-30\n31-59", total=59, limit=30)
    assert [p.label for p in parts] == ["1-30", "31-59"]
    assert parse_parts("1-3; 4-6", total=10, limit=30) == [
        Part((0, 1, 2)),
        Part((3, 4, 5)),
    ]


def test_ranges_in_one_part_are_joined_by_commas_and_spaces_and_dashes_are_forgiven():
    [part] = parse_parts("1 - 3,  7, 9\u201310", total=20, limit=30)
    assert part.pages == (0, 1, 2, 6, 8, 9) and part.label == "1-3, 7, 9-10"


def test_one_page_is_a_part():
    [part] = parse_parts("5", total=9, limit=30)
    assert part.pages == (4,)


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


def test_a_single_part_is_not_called_part_one():
    with pytest.raises(PartsError) as exc:
        parse_parts("1-70", total=59, limit=30)
    assert exc.value.message.startswith("The pages:")
    with pytest.raises(PartsError) as exc:
        parse_parts("1-70\n2", total=59, limit=30)
    assert exc.value.message.startswith("Part 1:")


def test_pages_already_converted_are_refused_again():
    with pytest.raises(PartsError) as exc:
        parse_parts("1-5", total=59, limit=30, taken={2})
    assert "page 3 was already converted" in exc.value.message
    assert parse_parts("6-10", total=59, limit=30, taken={2})


def test_too_many_parts_at_once_is_refused():
    text = "\n".join(str(n) for n in range(1, ranges.MAX_PARTS + 2))
    with pytest.raises(PartsError) as exc:
        parse_parts(text, total=100, limit=30)
    assert f"at most {ranges.MAX_PARTS}" in exc.value.message


@pytest.mark.parametrize(
    ("total", "limit", "sizes"),
    [
        (59, 30, [30, 29]),
        (30, 30, [30]),
        (31, 30, [16, 15]),
        (61, 30, [21, 20, 20]),
        (7, 30, [7]),
        (1, 30, [1]),
    ],
)
def test_suggested_parts_are_as_few_as_fit_and_nearly_equal(total, limit, sizes):
    parts = suggest_parts(total, limit)
    assert [len(p.pages) for p in parts] == sizes
    covered = [page for p in parts for page in p.pages]
    assert covered == list(range(total))  # every page once, in order


def test_suggested_parts_leave_out_the_pages_already_sent():
    parts = suggest_parts(59, 30, taken=set(range(30)))
    assert [p.label for p in parts] == ["31-59"]
    assert [p.label for p in suggest_parts(10, 30, taken={4})] == ["1-4", "6-10"]
    assert suggest_parts(5, 30, taken=set(range(5))) == []


def test_the_suggestion_is_the_text_the_box_takes_and_reads_back_the_same():
    parts = suggest_parts(100, 30)
    text = ranges.suggestion_text(parts)
    assert parse_parts(text, total=100, limit=30) == parts
