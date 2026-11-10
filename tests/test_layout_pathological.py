"""Layout analysis of pages made of tens of thousands of tiny vector paths.

A scanned-looking or handwritten PDF can be 86,000 separate strokes a page. Layout used to
spend minutes and gigabytes on 30 such pages (a quadratic clustering of the strokes, and
every stroke held as an object for every page). These tests build small synthetic files
(`layout_synth`) and check the guards: the clustering gives the groups the plain scan
gave, a page over the drawing cap is kept as its page image (with its typed text) at once,
the table detector leaves a page of pen strokes alone, and every one finishes under a bound
set far above what it takes, so only a return of the old cost fails them.
"""

from __future__ import annotations

import math
import time

from layout_synth import dots_page, ink_document, ink_page, slide_with_ink
from readablestem.pipeline import layout, page_type, tables

# A ceiling for "bounded time", not a benchmark: the slowest of these takes about 50 s alone and
# over 60 s with 16 xdist workers on a busy box. The unbounded scan took far longer.
BOUND_S = 180.0


def _timed(data: bytes) -> tuple[layout.Layout, float]:
    started = time.monotonic()
    result = layout.analyze(data)
    return result, time.monotonic() - started


# ------------------------------------------------------------------------- the clustering


def test_a_box_with_no_place_falls_back_to_the_plain_scan():
    boxes = [(0.0, 0.0, 10.0, 10.0), (5.0, 5.0, math.inf, 20.0), (50.0, 50.0, 60.0, 60.0)]
    assert layout._cluster(boxes, 6.0) == layout._cluster_scan(boxes, 6.0)


# ------------------------------------------------------------------------- the page cap


def test_a_page_just_under_the_cap_is_still_read(monkeypatch):
    monkeypatch.setattr(page_type, "PAGE_MAX_PATHS", 700)  # the same code, a smaller page
    data = ink_page(600, typed=True)
    result, seconds = _timed(data)
    assert seconds < BOUND_S
    assert page_type.OVER_CAP_REASON not in result.page_types[0].reasons
    assert result.page_types[0].ink.analysed
    assert result.elements and not result.kept_pages


def test_a_document_of_over_cap_pages_is_read_in_bounded_time(monkeypatch):
    monkeypatch.setattr(page_type, "PAGE_MAX_PATHS", 2000)  # the same code, smaller pages
    data = ink_document(5, 2500, typed_first=True)
    result, seconds = _timed(data)
    assert seconds < BOUND_S
    assert sorted(result.kept_pages) == [0, 1, 2, 3, 4]
    assert all(r == page_type.UNREAD_PAGE for r in result.kept_pages.values())
    assert {e.page for e in result.elements} <= {0}  # only the typed paragraph of page 1


def test_a_page_of_many_separate_dots_is_read_in_bounded_time():
    result, seconds = _timed(dots_page(40_000))
    assert seconds < BOUND_S
    assert result.page_types[0].kind in {"born_digital", "mixed_ink"}


# ------------------------------------------------------------------------- the detector


def test_the_whole_slide_with_pen_strokes_is_read_in_bounded_time():
    result, seconds = _timed(slide_with_ink(400, 80))
    assert seconds < BOUND_S
    assert result.page_count == 1


def test_a_page_after_the_detector_budget_is_used_up_gets_no_proposals():
    import pymupdf

    spent = tables.DetectorBudget(excess_s=1.0, free_s=0.0)
    spent.spend(5.0)
    with pymupdf.open(stream=slide_with_ink(2, 5), filetype="pdf") as doc:
        started = time.monotonic()
        assert tables.propose(doc[0], spent) == []
        assert time.monotonic() - started < 2  # it did not even start the model
