"""WF1. Typed lecture notes, from upload to the file a student opens.

The file is a chapter of MIT's "Mathematics for Computer Science" (14 pages, 65 equations,
two footnotes), uploaded with no box ticked. It converts with 47 decisions left to make,
and none is made here: these tests read what the instructor and the student get at that
point.
"""

from __future__ import annotations

import re
from collections import Counter

import harness
import lxml.html
import pytest

pytestmark = pytest.mark.workflow


@pytest.fixture(scope="module")
def notes(site: harness.ReplaySite) -> str:
    return site.convert("w_notes_6042_ch2")


@pytest.fixture(scope="module")
def document_page(site: harness.ReplaySite, notes: str) -> lxml.html.HtmlElement:
    return site.page(f"/jobs/{notes}").xpath("//main")[0]


@pytest.fixture(scope="module")
def student(site: harness.ReplaySite, notes: str) -> lxml.html.HtmlElement:
    return site.student_file(notes)


@harness.finding("F-033")
def test_the_documents_list_and_the_document_page_name_the_same_state(
    site: harness.ReplaySite, notes: str, document_page: lxml.html.HtmlElement
) -> None:
    row = site.page("/app").xpath(f'//main//li[.//a[contains(@href, "/jobs/{notes}")]]')[0]
    on_the_list = harness.words(row.xpath('.//*[contains(@class, "status-badge")]')[0])
    on_the_page = harness.words(document_page.xpath('.//*[contains(@class, "status-badge")]')[0])
    assert on_the_page == on_the_list


@harness.finding("F-058")
def test_the_document_page_says_what_the_conversion_cost(
    site: harness.ReplaySite, notes: str, document_page: lxml.html.HtmlElement
) -> None:
    cost = site.job(notes).cost_usd
    assert f"${cost:.2f}" in harness.words(document_page)


@harness.finding("F-043")
def test_not_handled_lists_nothing_the_student_file_holds(
    document_page: lxml.html.HtmlElement,
) -> None:
    # Nothing in this file is left out: its two footnotes are in the student file, each
    # with its mark linked. So there is nothing for a "not handled" list to hold.
    headings = [harness.words(h) for h in document_page.xpath(".//h2")]
    assert "Not handled in this file" not in headings


def test_the_student_file_has_one_top_heading(student: lxml.html.HtmlElement) -> None:
    assert len(student.xpath("//h1")) == 1


@harness.finding("F-253")
def test_no_page_header_is_repeated_as_a_heading(student: lxml.html.HtmlElement) -> None:
    headings = Counter(harness.words(h) for h in student.xpath("//h1|//h2|//h3|//h4|//h5|//h6"))
    assert [text for text, count in headings.items() if count > 1] == []


@harness.finding("F-253")
def test_no_print_stamp_is_in_the_text(student: lxml.html.HtmlElement) -> None:
    # Each page of the PDF carries the typesetter's line: “mcs” — 2015/5/18 — 1:43 — page 27.
    assert "“mcs” —" not in harness.words(student.xpath("//main")[0])


@harness.finding("F-226")
def test_no_character_is_left_unreadable(student: lxml.html.HtmlElement) -> None:
    text = harness.words(student.xpath("//main")[0])
    lost = re.findall(r"\[unreadable character\]|[\ufffd\ue000-\uf8ff]", text)
    assert lost == []


@harness.finding("F-226")
def test_an_equation_in_a_sentence_is_not_read_as_stray_letters(
    student: lxml.html.HtmlElement,
) -> None:
    # Euler's equation a^4 + b^4 + c^4 = d^4: the PDF's text gives "C" for plus and "D" for
    # equals.
    assert "a4 C b4 C c4 D d 4" not in harness.words(student.xpath("//main")[0])


@harness.finding("F-302")
def test_every_equation_has_words_for_a_screen_reader(student: lxml.html.HtmlElement) -> None:
    equations = student.xpath("//math")
    assert equations
    assert [m.get("id") for m in equations if not (m.get("alttext") or "").strip()] == []


def test_the_student_file_says_it_has_not_been_checked(student: lxml.html.HtmlElement) -> None:
    # No decision has been made, so the file may not tell a student that anyone checked it.
    (about,) = student.xpath('//main//section[@id="about-conversion"]')
    assert "has not been reviewed by a person" in harness.words(about)


def test_listen_speaks_what_is_raised_and_lowered(site: harness.ReplaySite, notes: str) -> None:
    spoken = harness.words(site.page(f"/jobs/{notes}/listen").xpath("//main")[0])
    assert "m sub 0" in spoken
    assert "3 to the power n" in spoken
