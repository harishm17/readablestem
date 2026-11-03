"""The page labels of the output's page markers (pipeline/page_labels.py): the PDF's own
page label, else the number printed in the margin when pages agree, else none; and a part
cut out of a larger file keeps its pages' labels (web/pdf_probe.py)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import DocumentResult, DocumentStats
from readablestem.pipeline import page_labels
from readablestem.pipeline.page_labels import read
from readablestem.web import pdf_probe

LATEX = Path(__file__).parent / "fixtures" / "layout" / "latex"


def make_pdf(footers: list[str | None], *, labels: list[dict] | None = None) -> bytes:
    """A PDF with one body line per page and, when given, text in the bottom margin."""
    doc = pymupdf.open()
    for number, footer in enumerate(footers):
        page = doc.new_page()
        page.insert_text((72, 100), f"Body text of page {number}.")
        if footer is not None:
            page.insert_text((290, page.rect.height - 30), footer)
    if labels:
        doc.set_page_labels(labels)
    return doc.tobytes()


def test_the_pdfs_own_labels_come_first():
    pdf = make_pdf(
        ["9", "9", "9", "9"],  # printed numbers that disagree with the labels
        labels=[
            {"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
            {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1},
        ],
    )
    assert read(pdf, range(4)) == {0: "i", 1: "ii", 2: "1", 3: "2"}


def test_a_label_that_is_not_short_plain_text_is_not_used():
    assert page_labels.clean_label("A-3") == "A-3"
    assert page_labels.clean_label(" 12 ") == "12"
    for bad in ("", None, "x" * 17, "<b>", "page 3", "a b"):
        assert page_labels.clean_label(bad) is None


def test_printed_numbers_that_agree_on_an_offset_are_the_labels():
    # a cover and a contents page, then pages printed 1, 2, 3
    pdf = make_pdf([None, None, "1", "2", "3"])
    assert read(pdf, range(5)) == {2: "1", 3: "2", 4: "3"}


@pytest.mark.parametrize("footer", ["{n}", "Page {n}", "- {n} -", "{n} of 9"])
def test_the_usual_ways_of_printing_a_page_number(footer):
    pdf = make_pdf([footer.format(n=n + 3) for n in range(3)])
    assert read(pdf, range(3)) == {0: "3", 1: "4", 2: "5"}


def test_a_page_between_two_numbered_pages_that_prints_none_gets_the_number_in_between():
    pdf = make_pdf(["4", None, "6"])
    assert read(pdf, range(3)) == {0: "4", 1: "5", 2: "6"}


def test_roman_numbers_are_read_apart_from_arabic_ones():
    pdf = make_pdf(["i", "ii", "iii", "1", "2"])
    assert read(pdf, range(5)) == {0: "i", 1: "ii", 2: "iii", 3: "1", 4: "2"}


def test_one_number_on_one_page_is_not_a_page_number():
    assert read(make_pdf([None, "7", None]), range(3)) == {}


def test_numbers_that_do_not_agree_are_not_page_numbers():
    # figure numbers in the margin: 7, 2, 40 are not one page apart from each other
    assert read(make_pdf(["7", "2", "40"]), range(3)) == {}


def test_a_number_in_the_body_is_not_a_page_number():
    doc = pymupdf.open()
    for n in range(3):
        doc.new_page().insert_text((72, 400), str(n + 10))
    assert read(doc.tobytes(), range(3)) == {}


def test_two_numbers_in_the_margin_are_not_a_page_number():
    pdf = make_pdf(["3   7", "4   7", "5   7"])
    assert read(pdf, range(3)) == {}


def test_only_the_pages_asked_for_are_labelled_and_a_bad_pdf_gives_none():
    pdf = make_pdf(["1", "2", "3"])
    assert read(pdf, [1]) == {}  # one page alone cannot agree with another
    assert read(pdf, [7]) == {}
    assert read(b"not a pdf", range(3)) == {}


def test_the_page_numbers_of_latex_documents_are_found_and_slides_have_their_frame_numbers():
    def found(name: str, pages: int) -> dict[int, str]:
        return read((LATEX / name).read_bytes(), range(pages))

    assert found("tables.pdf", 4) == {0: "1", 1: "2", 2: "3", 3: "4"}
    assert found("calculus_notes.pdf", 2) == {0: "1", 1: "2"}
    # a Beamer deck's own labels (hyperref: one per frame, written as UTF-16) are its slide numbers
    assert found("kinematics_slides.pdf", 3) == {0: "1", 1: "2", 2: "3"}


def test_a_part_cut_from_a_file_keeps_its_pages_labels():
    source = make_pdf(
        ["a", "b", "c", "d", "e"],
        labels=[
            {"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
            {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1},
        ],
    )
    part = pdf_probe.extract(source, [1, 3, 4])
    assert read(part, range(3)) == {0: "ii", 1: "2", 2: "3"}


def test_a_part_of_a_file_with_no_labels_has_none():
    part = pdf_probe.extract(make_pdf(["a", "b", "c"]), [0, 2])
    opened = pymupdf.open(stream=part)
    assert [p.get_label() for p in opened] == ["", ""]


def test_stored_results_without_labels_still_load_and_leave_the_field_out():
    result = DocumentResult(
        pipeline_version="0",
        stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0, model_calls=0),
    )
    stored = result.model_dump(mode="json")
    assert "page_labels" not in stored
    assert DocumentResult.model_validate(stored).page_labels == {}
    result.page_labels = {0: "iv"}
    again = DocumentResult.model_validate(result.model_dump(mode="json"))
    assert again.page_labels == {0: "iv"}
