"""One row with no characters, or one page that raises, does not stop the document.

A list marker ("1 .") whose equation number is cut off the end of the row left a row of
the marker alone; reading it raised on the font size of nothing and the whole job failed
(arXiv 2403.06509). Now that row is a line of text, and a page whose reading raises for any
reason is kept as its picture while the other pages are read.
"""

from __future__ import annotations

import copy
import logging

import pymupdf
import pytest

from readablestem.config import Settings
from readablestem.models import OFFLINE_DEFAULTS
from readablestem.models.fake import FakeVisionClient
from readablestem.models.pricing import UnknownModelError
from readablestem.pipeline import layout
from readablestem.pipeline.run import convert_pdf

SENTENCE = "Ordinary running text of the paragraph that sits above the equation."


def _document(pages: int = 3, *, marker_row_on: int | None = None) -> bytes:
    """A generated document of text pages. On page `marker_row_on` a row reads "1 ." with an
    equation number at the right ("." in the Symbol font is maths, "1" is a small digit),
    which layout takes for a list marker and then cuts the number off."""
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page(width=595, height=842)
        for i in range(12):
            page.insert_text((72, 100 + i * 14), f"{SENTENCE} Page {number + 1}.", fontsize=10.9)
        if number == marker_row_on:
            page.insert_text((437, 330), "1", fontsize=6)
            page.insert_text((484, 330), ".", fontsize=10.9, fontname="symb")
            page.insert_text((517, 330), "(2.2)", fontsize=10.9)
    data = doc.tobytes()
    doc.close()
    return data


def test_a_row_that_is_only_a_marker_is_read_not_raised():
    result = layout.analyze(_document(1, marker_row_on=0))
    assert [e.page for e in result.elements] == [0] * len(result.elements) and result.elements
    assert not result.kept_pages


def _raise_on_second_page(monkeypatch):
    real = layout._page_blocks

    def page_blocks(page, number, *args, **kwargs):
        if number == 1:
            raise KeyError(SENTENCE)  # a bug that quotes the page in its message
        return real(page, number, *args, **kwargs)

    monkeypatch.setattr(layout, "_page_blocks", page_blocks)


def test_a_page_that_raises_is_kept_as_its_picture_and_the_others_are_read(monkeypatch, caplog):
    _raise_on_second_page(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="readablestem.pipeline.layout"):
        result = layout.analyze(_document())
    assert result.kept_pages == {1: "page_read_failed"}
    assert sorted({e.page for e in result.elements}) == [0, 2]
    # What is logged says the page and the class of the error, never the page's words.
    assert "page 2" in caplog.text and "KeyError" in caplog.text and SENTENCE not in caplog.text


def test_the_job_finishes_with_the_page_as_a_picture_and_a_notice_a_person_sees(monkeypatch):
    _raise_on_second_page(monkeypatch)
    client = FakeVisionClient(defaults=copy.deepcopy(OFFLINE_DEFAULTS))
    output = convert_pdf(_document(), settings=Settings(_env_file=None), client=client)  # type: ignore[call-arg]
    result = output.result
    (fix,) = [f for f in result.fixes if f.reason == "page_read_failed"]
    assert fix.review == "required"
    assert [(n.code, n.page) for n in result.notices if n.code == "page_read_failed"] == [
        ("page_read_failed", 1)
    ]
    assert "This page could not be read" in result.html
    assert "Page 1." in result.html and "Page 3." in result.html


def test_a_document_in_which_every_page_raises_still_fails(monkeypatch):
    def page_blocks(*args, **kwargs):
        raise KeyError("x")

    monkeypatch.setattr(layout, "_page_blocks", page_blocks)
    with pytest.raises(KeyError):
        layout.analyze(_document(2))


def test_an_error_that_stops_a_job_on_purpose_is_not_taken_for_a_page_that_raised(monkeypatch):
    real = layout._page_blocks

    def page_blocks(page, number, *args, **kwargs):
        if number == 1:
            raise UnknownModelError("not-a-model")  # a configuration error ends the run
        return real(page, number, *args, **kwargs)

    monkeypatch.setattr(layout, "_page_blocks", page_blocks)
    with pytest.raises(UnknownModelError):
        layout.analyze(_document(2))
