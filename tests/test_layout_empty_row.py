"""One row with no characters, or one page that raises, does not stop the document.

A list marker ("1 .") whose equation number is cut off the end of the row left a row of
the marker alone; reading it raised on the font size of nothing and the whole job failed
(arXiv 2403.06509). Now that row is a line of text, and a page whose reading raises for any
reason is kept as its picture while the other pages are read. That holds for every step that
works on one page at a time, not only the one that builds the page's blocks.
"""

from __future__ import annotations

import copy
import logging
from collections import Counter
from pathlib import Path

import pymupdf
import pytest

from readablestem.config import Settings
from readablestem.core import uid
from readablestem.models import OFFLINE_DEFAULTS
from readablestem.models.fake import FakeVisionClient
from readablestem.models.pricing import UnknownModelError
from readablestem.pipeline import layout, page_type
from readablestem.pipeline.layout import document, elements, furniture, lists
from readablestem.pipeline.layout import page as page_steps
from readablestem.pipeline.run import convert_pdf

SENTENCE = "Ordinary running text of the paragraph that sits above the equation."


def _document(pages: int = 3, *, marker_row_on: int | None = None) -> bytes:
    """A generated document of text pages. On page `marker_row_on` a row reads "1 ." with an
    equation number at the right ("." in the Symbol font is maths, "1" is a small digit),
    which layout takes for a list marker and then cuts the number off."""
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 40), "Unit review", fontsize=9)  # a running header on every page
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


def _on_page(args) -> int | None:
    """The page a step was called for: the `pymupdf.Page` among its arguments."""
    return next((a.number for a in args if isinstance(a, pymupdf.Page)), None)


def _on_row(args) -> int | None:
    """The page of a text row, by the page number its text ends with (see `_document`)."""
    return (
        int(args[0].clean.split("Page ")[-1].rstrip(".")) - 1 if "Page " in args[0].clean else None
    )


def _on_block(args) -> int | None:
    return _on_row([args[0].rows[0]])


def _on_third_look():
    """The page type of the second page, the third time a step looks at it: the first two
    are `_kept_whole` (after the page is read, and for the slide-deck check), the third is
    the step that settles which pages are kept."""
    seen: list[int] = []
    looks: Counter[int] = Counter()

    def locate(args) -> int | None:
        key = id(args[0])
        if key not in seen:
            seen.append(key)
        looks[key] += 1
        return seen.index(key) if looks[key] == 3 else None

    return locate


# The steps of `_analyze_document` that work on one page at a time: the function (in its
# module) that is made to raise, how it tells which page a call is for, and the page (0-based)
# it is made to raise on. The label keys step skips a page whose header the first page already
# ruled out, so it is the first page it fails on.
STAGES = {
    "read": (document, "_read_page", _on_page, 1),
    "margins": (furniture, "_band_figures", _on_page, 1),
    "page_kind": (page_type, "kept_reason", "third_look", 1),
    "tables": (document, "_claim_tables", _on_page, 1),
    "rows": (document, "_prepare_rows", _on_page, 1),
    "list_markers": (lists, "_marker_candidate", _on_row, 1),
    "label_keys": (page_steps, "_rough_figures", _on_page, 0),
    "blocks": (document, "_page_blocks", _on_page, 1),
    "elements": (elements, "_whole_pieces", _on_block, 1),
    "safety_net": (document, "_page_picture", _on_page, 1),
}


def _make_raise(monkeypatch, stage: str, error: Exception, *, page: int | None = None) -> int:
    """Make the step `stage` raise `error` for one page only (`page`, else the stage's own);
    returns that page's number, 0-based."""
    module, name, locate, own = STAGES[stage]
    page = own if page is None else page
    if locate == "third_look":
        locate = _on_third_look()
    real = getattr(module, name)

    def step(*args, **kwargs):
        if locate(args) == page:
            raise error
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, step)
    return page


def _convert(pdf: bytes):
    client = FakeVisionClient(defaults=copy.deepcopy(OFFLINE_DEFAULTS))
    return convert_pdf(pdf, settings=Settings(_env_file=None), client=client).result  # type: ignore[call-arg]


def _ids(result, *, without: int) -> list[tuple[str, str]]:
    """Each element's positional id and stable id (`core.uid`), off the page `without`."""
    uids = uid.assign(result).by_id
    return [(e.id, uids[e.id]) for e in result.elements if e.page != without]


@pytest.fixture(scope="module")
def unbroken():
    """The same document converted with no page raising."""
    return _convert(_document())


@pytest.mark.parametrize("stage", STAGES)
def test_a_page_that_raises_in_any_step_is_its_picture_and_a_notice_and_the_others_are_read(
    stage, monkeypatch, caplog, unbroken
):
    bad = _make_raise(monkeypatch, stage, KeyError(SENTENCE))  # a bug that quotes the page
    with caplog.at_level(logging.WARNING, logger="readablestem.pipeline.layout"):
        result = _convert(_document())
    # Two pages read, one kept as its picture with a required decision, one notice.
    (fix,) = [f for f in result.fixes if f.reason == "page_read_failed"]
    assert fix.review == "required"
    assert [(n.code, n.page) for n in result.notices if n.code == "page_read_failed"] == [
        ("page_read_failed", bad)
    ]
    assert "This page could not be read" in result.html
    for page in range(3):
        read = f"Page {page + 1}." in result.html
        # (the safety net only adds the picture beside a page: what was read from it stays)
        assert read == (page != bad or stage == "safety_net")
    # The pages that were read keep the ids, positional and stable, they have when no page
    # raises: taking the failed page's elements back renames nothing around it.
    assert _ids(result, without=bad) == _ids(unbroken, without=bad)
    # What is logged says the page and the class of the error, never the page's words.
    assert (
        f"page {bad + 1}" in caplog.text
        and "KeyError" in caplog.text
        and SENTENCE not in caplog.text
    )


@pytest.mark.parametrize("stage", STAGES)
def test_an_error_that_stops_a_job_on_purpose_is_not_taken_for_a_page_that_raised(
    stage, monkeypatch
):
    _make_raise(monkeypatch, stage, UnknownModelError("not-a-model"))  # a configuration error
    with pytest.raises(UnknownModelError):
        layout.analyze(_document(2))


def test_a_document_in_which_every_page_raises_still_fails_whichever_steps_they_raise_in(
    monkeypatch,
):
    """Counted over the document: no step has lost every page, but no page could be read."""
    for stage, page in (("rows", 0), ("tables", 1), ("blocks", 2)):
        _make_raise(monkeypatch, stage, KeyError("x"), page=page)
    with pytest.raises(KeyError):
        layout.analyze(_document(3))


def test_a_slide_whose_pictures_cannot_be_read_is_its_picture_and_the_deck_goes_on(monkeypatch):
    """A step of a frame is compared with its frame's last page by its pictures and drawings,
    read then and not before (`slides.LazyMarks`): a page they cannot be read for is kept as its
    picture, as for any step, and the pages beside it are read as a deck."""
    fixtures = Path(__file__).parent / "fixtures" / "layout" / "latex"
    pdf = (fixtures / "beamer_overlay.pdf").read_bytes()
    real = document._slide_marks

    def marks(page, *args, **kwargs):
        if page.number == 1:
            raise KeyError("x")
        return real(page, *args, **kwargs)

    monkeypatch.setattr(document, "_slide_marks", marks)
    result = layout.analyze(pdf)
    assert result.kept_pages == {1: page_type.PAGE_READ_FAILED}
    assert result.elements and 1 not in {e.page for e in result.elements}
