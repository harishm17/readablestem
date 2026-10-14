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
import random
import time

import pytest

from a11y_stem.core.types import PageType
from a11y_stem.pipeline import layout, page_type, tables
from layout_synth import dots_page, ink_document, ink_page, slide_with_ink

BOUND_S = 60.0  # the slowest of these takes well under a tenth of it on one core


def _timed(data: bytes) -> tuple[layout.Layout, float]:
    started = time.monotonic()
    result = layout.analyze(data)
    return result, time.monotonic() - started


# ------------------------------------------------------------------------- the clustering


def _random_boxes(rnd: random.Random, count: int, *, tall: bool = False):
    boxes = []
    for _ in range(count):
        x, y = rnd.uniform(0, 300), rnd.uniform(0, 400)
        w = rnd.choice([0, 1, 3, 10, 60, 300])
        h = rnd.choice([0, 1, 3, 10, 60, 400 if tall else 60])
        boxes.append((x, y, x + rnd.uniform(0, w), y + rnd.uniform(0, h)))
    return boxes


@pytest.mark.parametrize("gap", [0.0, 2.0, 6.0, 24.0])
def test_the_banded_cluster_gives_the_groups_of_the_plain_scan(gap):
    rnd = random.Random(7)
    for count in (0, 1, 2, 5, 40, 300, 900):
        boxes = _random_boxes(rnd, count, tall=True)
        assert layout._cluster(boxes, gap) == layout._cluster_scan(boxes, gap)


def test_the_banded_cluster_handles_boxes_drawn_backwards_and_off_the_page():
    rnd = random.Random(9)
    boxes = [(x, y, x + rnd.uniform(-5, 60), y + rnd.uniform(-5, 60)) for x, y in _points(rnd)]
    boxes += [(-500.0, -500.0, -490.0, -480.0), (2000.0, 3000.0, 2100.0, 3050.0)]
    assert layout._cluster(boxes, 6.0) == layout._cluster_scan(boxes, 6.0)


def test_a_box_with_no_place_falls_back_to_the_plain_scan():
    boxes = [(0.0, 0.0, 10.0, 10.0), (5.0, 5.0, math.inf, 20.0), (50.0, 50.0, 60.0, 60.0)]
    assert layout._cluster(boxes, 6.0) == layout._cluster_scan(boxes, 6.0)


def _points(rnd: random.Random, count: int = 200):
    return [(rnd.uniform(0, 300), rnd.uniform(0, 400)) for _ in range(count)]


def test_sixty_thousand_strokes_cluster_in_seconds():
    rnd = random.Random(3)
    boxes = []
    for _ in range(60_000):  # a page of handwriting: small boxes spread over the whole page
        x, y = rnd.uniform(0, 595), rnd.uniform(0, 842)
        boxes.append((x, y, x + rnd.uniform(1, 8), y + rnd.uniform(1, 8)))
    started = time.monotonic()
    groups = layout._cluster(boxes, layout.DRAWING_CLUSTER_GAP)
    assert time.monotonic() - started < 20  # the plain scan took about a minute
    assert sorted(i for group in groups for i in group) == list(range(60_000))


# ------------------------------------------------------------------------- the page cap


def test_a_page_over_the_drawing_cap_is_kept_as_its_image_without_a_scan_of_its_paths():
    data = ink_page(page_type.PAGE_MAX_PATHS + 1000, typed=False)
    result, seconds = _timed(data)
    assert seconds < BOUND_S
    assert result.kept_pages == {0: page_type.UNREAD_PAGE}
    assert not result.elements
    reasons = result.page_types[0].reasons
    assert page_type.OVER_CAP_REASON in reasons and "ink_not_analysed" in reasons


def test_a_typed_page_over_the_drawing_cap_keeps_its_text_and_its_image():
    data = ink_page(page_type.PAGE_MAX_PATHS + 1000, typed=True)
    result, seconds = _timed(data)
    assert seconds < BOUND_S
    assert result.kept_pages == {0: page_type.UNREAD_PAGE}
    assert result.elements and all(e.kind.value == "paragraph" for e in result.elements)
    assert "quick brown fox" in (result.elements[0].text or "")
    assert result.page_types[0].kind == "born_digital"


def test_a_page_just_under_the_cap_is_still_read():
    data = ink_page(3000, typed=True)
    result, seconds = _timed(data)
    assert seconds < BOUND_S
    assert page_type.OVER_CAP_REASON not in result.page_types[0].reasons
    assert result.page_types[0].ink.analysed
    assert result.elements and not result.kept_pages


def test_the_cap_counts_path_items_as_well_as_paths(monkeypatch):
    monkeypatch.setattr(page_type, "PAGE_MAX_ITEMS", 500)
    result, _ = _timed(ink_page(200, typed=True))  # 200 paths, 1,400 items
    assert page_type.OVER_CAP_REASON in result.page_types[0].reasons
    assert result.kept_pages == {0: page_type.UNREAD_PAGE}


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


def test_the_stored_record_of_an_over_cap_page_is_kept_and_never_refuses_the_upload():
    record = PageType(
        page=0, kind="born_digital", visible_chars=900, reasons=[page_type.OVER_CAP_REASON]
    )
    assert page_type.kept_reason(record) == page_type.UNREAD_PAGE
    assert page_type.refusal_code([record]) is None
    bare = PageType(page=0, kind="vector_no_text", reasons=[page_type.OVER_CAP_REASON])
    assert page_type.refusal_code([bare]) is None


def test_the_ink_stats_of_an_over_cap_page_say_so_without_counting_its_strokes(monkeypatch):
    monkeypatch.setattr(page_type, "PAGE_MAX_PATHS", 100)
    import pymupdf

    with pymupdf.open(stream=ink_page(300, typed=False), filetype="pdf") as doc:
        page = doc[0]
        stats = page_type.ink_stats(page, page.get_cdrawings())
    assert stats.over_cap and not stats.analysed and stats.paths == 300 and stats.marks == 300


# ------------------------------------------------------------------------- the detector


def test_the_table_detector_leaves_a_page_of_pen_strokes_alone():
    import pymupdf

    with pymupdf.open(stream=slide_with_ink(400, 80), filetype="pdf") as doc:
        page = doc[0]
        assert sum(len(d["items"]) for d in page.get_cdrawings()) > tables.MAX_DETECTOR_ITEMS
        started = time.monotonic()
        assert tables.propose(page) == []
        assert time.monotonic() - started < BOUND_S


def test_the_whole_slide_with_pen_strokes_is_read_in_bounded_time():
    result, seconds = _timed(slide_with_ink(400, 80))
    assert seconds < BOUND_S
    assert result.page_count == 1


def test_a_detector_budget_is_drawn_on_only_by_the_time_beyond_the_free_part():
    budget = tables.DetectorBudget(excess_s=10.0, free_s=3.0)
    budget.spend(2.5)  # an ordinary page: nothing
    budget.spend(3.0)
    assert budget.left == 10.0 and budget.open()
    budget.spend(8.0)  # 5 s beyond the free part
    assert budget.left == 5.0 and budget.open()
    budget.spend(20.0)
    assert not budget.open()


def test_a_page_after_the_detector_budget_is_used_up_gets_no_proposals():
    import pymupdf

    spent = tables.DetectorBudget(excess_s=1.0, free_s=0.0)
    spent.spend(5.0)
    with pymupdf.open(stream=slide_with_ink(2, 5), filetype="pdf") as doc:
        started = time.monotonic()
        assert tables.propose(doc[0], spent) == []
        assert time.monotonic() - started < 2  # it did not even start the model
