"""WF2. A dense set of worked solutions, and the review it leaves.

The file is the solutions to a problem set of MIT 18.02 (14 pages, about 600 equations).
It converts with 143 decisions to make, 26 of them equations the reader gave no reading
for. No decision is made here.
"""

from __future__ import annotations

import itertools
import re

import harness
import lxml.html
import pytest

from readablestem.core.types import Status

pytestmark = pytest.mark.workflow


@pytest.fixture(scope="module")
def solutions(site: harness.ReplaySite) -> str:
    return site.convert("w_solutions_1802")


def test_the_document_page_does_not_say_every_check_passed_with_decisions_open(
    site: harness.ReplaySite, solutions: str
) -> None:
    said = harness.words(site.page(f"/jobs/{solutions}").xpath("//main")[0])
    assert site.job(solutions).open_decisions > 0
    assert not re.search(r"All \d+ checks passed", said)


@harness.finding("F-061")
def test_an_equation_with_no_reading_offers_more_than_typing_it(
    site: harness.ReplaySite, solutions: str
) -> None:
    unread = next(
        fix.element_id
        for fix in site.result(solutions).fixes
        if fix.kind == "equation" and fix.status is Status.ABSTAINED
    )
    card = site.page(f"/jobs/{solutions}/review?item={unread}")
    (form,) = card.xpath(f'//main//form[contains(@action, "/review/{unread}")]')
    ways = {button.get("value") for button in form.xpath('.//button[@name="action"]')}
    # Today: type the LaTeX ("edit") or keep the picture ("reject").
    assert ways - {"edit", "reject"}


def test_the_list_of_every_item_is_small_enough_to_open(
    site: harness.ReplaySite, solutions: str
) -> None:
    listing = site.page(f"/jobs/{solutions}/review/list")
    assert sum(1 for _ in listing.iter()) <= 5000


def test_no_display_equation_is_written_twice_in_a_row(
    site: harness.ReplaySite, solutions: str
) -> None:
    shown = [
        lxml.html.tostring(equation, with_tail=False)
        for equation in site.student_file(solutions).xpath('//math[@display="block"]')
    ]
    assert shown
    assert [a for a, b in itertools.pairwise(shown) if a == b] == []
