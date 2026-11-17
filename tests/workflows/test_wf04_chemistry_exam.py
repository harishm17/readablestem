"""WF4. A chemistry exam uploaded with the exam box ticked.

The file is the third exam of MIT 5.111 (12 pages): questions with blanks, tables of
constants, structural formulas drawn on the page, and a periodic table. It converts with 67
decisions to make, and none is made here.
"""

from __future__ import annotations

import harness
import lxml.html
import pytest

pytestmark = pytest.mark.workflow


@pytest.fixture(scope="module")
def exam(site: harness.ReplaySite) -> str:
    return site.convert("w_exam_5111", exam=True)


@pytest.fixture(scope="module")
def student(site: harness.ReplaySite, exam: str) -> lxml.html.HtmlElement:
    return site.student_file(exam)


@harness.finding("F-047")
def test_the_document_page_says_the_exam_box_was_ticked(
    site: harness.ReplaySite, exam: str
) -> None:
    # The same file uploaded without the box: its page may not read the same about exams.
    plain = site.convert("w_exam_5111")
    with_box = harness.words(site.page(f"/jobs/{exam}").xpath("//main")[0]).casefold()
    without = harness.words(site.page(f"/jobs/{plain}").xpath("//main")[0]).casefold()
    assert with_box.count("exam") > without.count("exam")


@harness.finding("F-303")
def test_no_table_header_is_hidden_from_a_screen_reader(student: lxml.html.HtmlElement) -> None:
    headers = student.xpath("//th")
    assert headers
    assert [harness.words(th) for th in headers if th.xpath('.//*[@aria-hidden="true"]')] == []


@harness.finding("F-347")
def test_no_drawn_structure_is_left_as_loose_labels(student: lxml.html.HtmlElement) -> None:
    # A structural formula's atom labels (CH2, H2N) each became a picture of their own.
    labels = [
        picture.get("alt")
        for picture in student.xpath("//img")
        if (picture.get("alt") or "").split(",")[0] in {"CH2", "H2N", "OH", "CH3", "NH2"}
    ]
    assert labels == []


def test_no_chemical_formula_shows_its_markup(student: lxml.html.HtmlElement) -> None:
    assert "\\ce" not in harness.words(student.xpath("//main")[0])
