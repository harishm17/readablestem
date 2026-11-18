"""WF6. Handwriting and scans, uploaded with "Convert scanned or handwritten pages" ticked.

Three files: four pages of handwritten lecture notes with drawings (MIT 6.006), a slide
deck scanned to pictures of its pages, and the same deck with every second slide scanned.
No decision is made here.
"""

from __future__ import annotations

from collections import Counter

import harness
import lxml.html
import pytest

pytestmark = pytest.mark.workflow


@pytest.fixture(scope="module")
def handwritten(site: harness.ReplaySite) -> str:
    return site.convert("w_handwritten_6006", image_pages=True)


@pytest.fixture(scope="module")
def scanned(site: harness.ReplaySite) -> str:
    return site.convert("w_scan_slides", image_pages=True)


@pytest.fixture(scope="module")
def mixed(site: harness.ReplaySite) -> str:
    return site.convert("w_mixed_slides", image_pages=True)


@pytest.fixture(scope="module", params=["scanned", "mixed"])
def deck(request: pytest.FixtureRequest) -> str:
    return request.getfixturevalue(request.param)


@pytest.fixture(scope="module")
def student(site: harness.ReplaySite, deck: str) -> lxml.html.HtmlElement:
    return site.student_file(deck)


def test_the_scan_box_says_what_ticking_it_costs(site: harness.ReplaySite) -> None:
    upload = site.page("/app").xpath("//main")[0]
    (box,) = upload.xpath('.//input[@name="image_pages"]')
    (hint,) = upload.xpath(f'.//*[@id="{box.get("aria-describedby")}"]')
    assert "$" in harness.words(hint)


@harness.finding("F-268")
def test_every_handwritten_page_is_read(site: harness.ReplaySite, handwritten: str) -> None:
    pictures = [
        picture.get("alt") or "" for picture in site.student_file(handwritten).xpath("//img")
    ]
    assert [alt for alt in pictures if "not converted" in alt] == []


@pytest.mark.parametrize("name", ["scanned", "mixed"])
def test_the_title_slide_is_read_once(
    request: pytest.FixtureRequest, site: harness.ReplaySite, name: str
) -> None:
    student = site.student_file(request.getfixturevalue(name))
    headings = Counter(harness.words(h) for h in student.xpath("//h1|//h2|//h3"))
    (top,) = student.xpath("//h1")
    assert headings[harness.words(top)] == 1


def test_a_deck_of_scanned_slides_is_named_by_its_first_slide(
    site: harness.ReplaySite, scanned: str
) -> None:
    # No page of it has text, so the name can only come from what was read on its first slide.
    (name,) = site.page(f"/jobs/{scanned}").xpath("//main//h1")
    (top,) = site.student_file(scanned).xpath("//h1")
    assert harness.words(name).casefold() == harness.words(top).casefold()


@harness.finding("F-254")
def test_a_scanned_slide_and_a_typed_slide_give_their_titles_one_level(
    site: harness.ReplaySite, mixed: str
) -> None:
    # Every second slide is scanned, and today its title comes out one level lower.
    assert site.student_file(mixed).xpath("//h3|//h4|//h5|//h6") == []


@harness.finding("F-245")
def test_a_slide_footer_is_not_a_decision(site: harness.ReplaySite, deck: str) -> None:
    assert site.job(deck).open_decisions == 0
