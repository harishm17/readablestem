"""WF3. Two slide decks, from upload to the file a student opens.

The decks are lectures 1 and 10 of MIT 6.0001 (Python): slides with a title, bullets drawn
with a symbol font, code, a table of growth rates and a footer on every slide. Each is
uploaded with no box ticked, and no decision is made.
"""

from __future__ import annotations

import re

import harness
import lxml.html
import pytest

pytestmark = pytest.mark.workflow


@pytest.fixture(scope="module")
def lecture_1(site: harness.ReplaySite) -> str:
    return site.convert("w_slides_60001_lec1")


@pytest.fixture(scope="module")
def lecture_10(site: harness.ReplaySite) -> str:
    return site.convert("w_slides_60001_lec10")


@pytest.fixture(scope="module", params=["lecture_1", "lecture_10"])
def deck(request: pytest.FixtureRequest) -> str:
    return request.getfixturevalue(request.param)


@pytest.fixture(scope="module")
def student(site: harness.ReplaySite, deck: str) -> lxml.html.HtmlElement:
    return site.student_file(deck)


def top_heading(student: lxml.html.HtmlElement) -> str:
    (heading,) = student.xpath("//h1")
    return harness.words(heading)


@harness.finding("F-125")
def test_the_document_is_named_by_its_first_slide(
    site: harness.ReplaySite, deck: str, student: lxml.html.HtmlElement
) -> None:
    # The PDF's own title field is a file slug ("MIT6_0001F16_Welcome").
    (name,) = site.page(f"/jobs/{deck}").xpath("//main//h1")
    assert harness.words(name).casefold() == top_heading(student).casefold()


@harness.finding("F-125")
def test_the_title_a_screen_reader_announces_is_the_top_heading(
    student: lxml.html.HtmlElement,
) -> None:
    (title,) = student.xpath("//title")
    assert harness.words(title).casefold() == top_heading(student).casefold()


@harness.finding("F-253")
def test_no_slide_footer_is_in_the_text(student: lxml.html.HtmlElement) -> None:
    # Every slide ends with the course and lecture number: "6.0001 LECTURE 1".
    assert not re.search(r"6\.0001 LECTURE \d", harness.words(student.xpath("//main")[0]))


@harness.finding("F-227")
def test_no_bullet_is_an_unreadable_character(student: lxml.html.HtmlElement) -> None:
    text = harness.words(student.xpath("//main")[0])
    lost = re.findall(r"\[unreadable character\]|[\ufffd\ue000-\uf8ff]", text)
    assert lost == []


def test_every_table_has_header_cells(student: lxml.html.HtmlElement) -> None:
    assert [table.get("id") for table in student.xpath("//table") if not table.xpath(".//th")] == []
