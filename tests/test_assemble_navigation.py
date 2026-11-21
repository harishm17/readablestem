"""Navigation in the output file: the skip link, the contents list, page markers, footnote
links (pipeline/navigation.py, docs/output.md "Navigation")."""

from __future__ import annotations

import re

import pytest

from readablestem.core.types import Element, Fix, HeadingFix, Kind, Link, Status, inline_marker
from readablestem.pipeline.assemble import render_html
from readablestem.pipeline.html_checks import axe_checks, static_checks


def block(
    index: int, kind: Kind, text: str, *, page: int = 0, order: int | None = None, **extra
) -> Element:
    return Element(
        id=f"p{page + 1}-e{index}",
        kind=kind,
        page=page,
        bbox=(72, 100 + 15 * index, 300, 110 + 15 * index),
        order=index if order is None else order,
        text=text,
        **extra,
    )


def heading(
    index: int, text: str, level: int = 1, *, page: int = 0, **extra
) -> tuple[Element, Fix]:
    element = block(index, Kind.HEADING, text, page=page, **extra)
    fix = Fix(
        element_id=element.id,
        kind=Kind.HEADING,
        status=Status.ACCEPTED,
        heading=HeadingFix(level=level, text=text),
    )
    return element, fix


def render(pairs: list[Element | tuple[Element, Fix]], **kwargs) -> str:
    elements = [p[0] if isinstance(p, tuple) else p for p in pairs]
    fixes = [p[1] for p in pairs if isinstance(p, tuple)]
    return render_html(title="Doc", lang="en", elements=elements, fixes=fixes, crops={}, **kwargs)


def numbered(pairs: list[Element | tuple[Element, Fix]]) -> list[Element | tuple[Element, Fix]]:
    """The same blocks with reading order one after the other."""
    out: list[Element | tuple[Element, Fix]] = []
    for order, pair in enumerate(pairs):
        element = pair[0] if isinstance(pair, tuple) else pair
        element = element.model_copy(update={"order": order})
        out.append((element, pair[1]) if isinstance(pair, tuple) else element)
    return out


def three_headings() -> list[Element | tuple[Element, Fix]]:
    return numbered(
        [
            heading(0, "Motion", 1),
            block(1, Kind.PARAGRAPH, "Intro."),
            heading(2, "Speed", 2),
            block(3, Kind.PARAGRAPH, "Speed text."),
            heading(4, "Velocity", 2),
        ]
    )


def contents_nav(html: str) -> str:
    match = re.search(r'<nav[^>]*aria-label="Contents".*?</nav>', html, re.S)
    assert match, "no contents list"
    return match.group(0)


# ----------------------------------------------------------------------------- skip link


def test_the_skip_link_comes_first_and_jumps_to_the_notice_or_the_h1_which_takes_focus():
    html = render([block(0, Kind.PARAGRAPH, "Hello.")])
    body = html.split("<body>", 1)[1]
    # nobody reviewed this page, so it opens with the notice: a link past it would hide it
    assert body.lstrip().startswith(
        '<a class="skip-link readablestem" href="#checked-notice">Skip to main content</a>'
    )
    # the notice and the page's one h1 take focus from a link (Firefox, VoiceOver) and are
    # not tab stops; nothing else carries a tabindex
    assert html.count('id="main-content"') == 1
    assert '<h1 id="main-content" tabindex="-1">Doc</h1>' in html
    assert re.findall(r"tabindex=\"[^\"]*\"", html) == ['tabindex="-1"'] * 2
    # with no notice (a person reviewed the page and nothing is a picture) the target is the
    # h1, and a heading that is the title is the target under its own id
    reviewed = three_headings()
    reviewed[0][1].reviewed = True
    titled = render(reviewed)
    assert "checked-notice" not in titled.split("<body>", 1)[1]
    assert 'href="#p1-e0">Skip' in titled
    assert '<h1 id="p1-e0" tabindex="-1">Motion</h1>' in titled


def test_the_contents_label_is_english_and_its_entries_the_documents_language():
    pairs = three_headings()
    elements = [p[0] if isinstance(p, tuple) else p for p in pairs]
    fixes = [p[1] for p in pairs if isinstance(p, tuple)]
    html = render_html(title="Doc", lang="de", elements=elements, fixes=fixes, crops={})
    assert '<nav class="toc" lang="en" aria-label="Contents">' in html
    assert '<div lang="de"><ul>' in contents_nav(html)
    english = render(three_headings())
    assert 'lang="' not in contents_nav(english)


# ----------------------------------------------------------------------------- contents list


def test_three_headings_make_a_nested_contents_list_of_links_to_their_ids():
    html = render(three_headings())
    nav = contents_nav(html)
    assert nav.startswith('<nav class="toc" aria-label="Contents">')
    # the title stands right above the list and is not an entry of it
    assert (
        '<ul><li><a href="#p1-e2">Speed</a></li><li><a href="#p1-e4">Velocity</a></li></ul>'
    ) in nav
    assert html.count("<nav") == 1
    # after the title, so the title is the first thing on a phone, and closed under 40rem
    assert html.index("skip-link") < html.index("<h1") < html.index("<nav")
    assert '<details class="toc-details"><summary' in nav and "<details open" not in nav
    for target in re.findall(r'href="#([^"]+)"', nav):
        assert f'id="{target}"' in html
    assert all(c.passed for c in static_checks(html) if c.severity == "error")


def test_a_deck_with_one_heading_per_slide_lists_its_slides_under_the_title():
    pairs = numbered([heading(i, f"Slide {i}", 1, page=i) for i in range(3)])
    html = render(pairs)
    assert ">Doc</h1>" in html and '<h2 id="p1-e0">Slide 0</h2>' in html
    nav = contents_nav(html)
    assert nav.count("<li>") == 3 and "Doc" not in nav  # the title is not an entry


def test_a_heading_whose_id_is_taken_gets_one_of_its_own():
    first, fix1 = heading(0, "One", 1)
    clash = first.model_copy(update={"order": 1})  # a layout bug: the same id twice
    third, fix3 = heading(2, "Three", 2)
    html = render(numbered([(first, fix1), (clash, fix1), (third, fix3)]))
    assert '<h2 id="heading-1">One</h2>' in html  # (two top headings: all go one level down)
    assert 'href="#heading-1"' in contents_nav(html)
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))


def test_a_heading_that_repeats_under_one_parent_is_one_entry_that_still_reaches_each():
    pairs = [heading(0, "Motion", 1)]
    for n in range(3):
        pairs += [heading(2 * n + 1, "Teacher Support", 2), heading(2 * n + 2, f"Part {n}", 2)]
    nav = contents_nav(render(numbered(pairs)))
    assert nav.count("Teacher Support") == 1 + 2  # the entry, and two named links to the others
    for place in ("p1-e1", "p1-e3", "p1-e5"):
        assert f'href="#{place}"' in nav
    assert nav.count("Part ") == 3


def test_an_equation_in_a_heading_is_called_equation_in_the_list():
    marker = inline_marker("p1-e9")
    pairs = numbered(
        [
            heading(0, "Motion", 1),
            heading(1, f"The case {marker}", 2),
            heading(2, "End", 2),
        ]
    )
    nav = contents_nav(render(pairs))
    assert "The case [equation]" in nav


def test_the_contents_list_text_is_escaped():
    pairs = numbered(
        [heading(0, "Title", 1), heading(1, "<b>One</b> & two", 2), heading(2, "Four", 2)]
    )
    nav = contents_nav(render(pairs))
    assert "<b>" not in nav and "&lt;b&gt;One&lt;/b&gt; &amp; two" in nav


# ----------------------------------------------------------------------------- page markers


def two_pages() -> list[Element | tuple[Element, Fix]]:
    return numbered(
        [
            block(0, Kind.PARAGRAPH, "First page."),
            block(1, Kind.PARAGRAPH, "Second page, first block.", page=1),
            block(2, Kind.PARAGRAPH, "Second page, second block.", page=1),
            block(0, Kind.PARAGRAPH, "Third page.", page=2),
        ]
    )


def test_a_marker_precedes_the_first_block_of_each_page_by_its_pdf_position():
    html = render(two_pages())
    markers = re.findall(r'<hr role="doc-pagebreak" aria-label="(Page \d+)" id="([^"]+)">', html)
    assert markers == [
        ("Page 1", "pagebreak-1"),
        ("Page 2", "pagebreak-2"),
        ("Page 3", "pagebreak-3"),
    ]
    assert html.index("pagebreak-2") < html.index("Second page, first block.")
    assert html.index("Second page, second block.") < html.index("pagebreak-3")


def test_the_printed_label_is_used_where_there_is_one():
    html = render(two_pages(), page_labels={0: "iv", 1: "12"})
    assert re.findall(r'aria-label="(P[^"]+)"', html) == ["Page iv", "Page 12", "PDF page 3"]
    assert 'id="pagebreak-2"' in html  # the id is by position, so it never repeats


def test_a_list_that_runs_over_a_page_gets_the_marker_inside_the_item_that_starts_the_page():
    items = numbered(
        [
            block(0, Kind.LIST_ITEM, "one", list_marker="•", list_ordered=False, list_level=1),
            block(
                1, Kind.LIST_ITEM, "two", page=1, list_marker="•", list_ordered=False, list_level=1
            ),
        ]
    )
    html = render(items)
    content = re.sub(r'<section id="checked-notice".*?</section>', "", html, flags=re.S)
    assert content.count("<ul>") == 1  # one list, not cut in two (the notice's links aside)
    assert '<li id="p2-e1"><div class="pagebreak' in html
    assert "<ul><div" not in html  # a div is not a child of a ul


# ----------------------------------------------------------------------------- footnotes


def noted(*, with_link: bool = True) -> tuple[list[Element | tuple[Element, Fix]], list[str]]:
    text = "Light travels fast.1 It is a constant."
    links = [Link(start=19, end=20, kind="internal", target_id="p1-e1")] if with_link else []
    pairs = numbered(
        [
            block(0, Kind.PARAGRAPH, text, links=links),
            block(1, Kind.PARAGRAPH, "1 In a vacuum."),
        ]
    )
    return pairs, ["p1-e1"]


def test_the_mark_is_a_noteref_the_note_a_footnote_and_the_note_links_back():
    pairs, ids = noted()
    html = render(pairs, footnote_ids=ids)
    assert '<a href="#p1-e1" id="noteref-p1-e1" role="doc-noteref">1</a>' in html
    assert '<div role="doc-footnote" id="p1-e1"><p>1 In a vacuum. ' in html
    assert (
        '<a class="backlink readablestem" href="#noteref-p1-e1" role="doc-backlink">'
        "Back to the text</a></p></div>"
    ) in html
    ids_used = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids_used) == len(set(ids_used))
    assert all(c.passed for c in static_checks(html) if c.severity == "error")


def test_without_footnotes_a_link_between_blocks_is_an_ordinary_link():
    pairs, _ = noted()
    html = render(pairs)
    assert "doc-noteref" not in html and "doc-footnote" not in html
    assert '<a href="#p1-e1">1</a>' in html


def test_a_typed_address_and_a_section_reference_reach_the_html_with_working_ids():
    from readablestem.pipeline.text_pass import PdfText, annotate

    text = "See Section 3.2 or write to a.b@example.edu."
    head, fix = heading(0, "3.2 Rates of change", 2)
    elements = annotate([head, block(1, Kind.PARAGRAPH, text)], PdfText()).elements
    html = render([(elements[0], fix), elements[1]])
    assert '<a href="#p1-e0">Section 3.2</a>' in html and 'id="p1-e0"' in html
    assert '<a href="mailto:a.b@example.edu">a.b@example.edu</a>.' in html
    assert all(c.passed for c in static_checks(html) if c.severity == "error")


# ----------------------------------------------------------------------------- unit


# ----------------------------------------------------------------------------- axe


@pytest.mark.browser
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_finds_no_problems_with_the_navigation_in_either_scheme(scheme):
    pytest.importorskip("playwright.sync_api")
    deeper = numbered(
        [
            heading(0, "Motion", 1),
            heading(1, "Speed", 2, page=1),
            block(2, Kind.PARAGRAPH, "Light travels fast.1 It is a constant.", page=1,
                  links=[Link(start=19, end=20, kind="internal", target_id="p2-e3")]),
            block(3, Kind.PARAGRAPH, "1 In a vacuum.", page=1),
            heading(4, "Velocity", 2, page=2),
        ]
    )  # fmt: skip
    html = render(deeper, footnote_ids=["p2-e3"], page_labels={0: "iv", 1: "1", 2: "2"})
    checks = axe_checks(html, color_scheme=scheme)  # type: ignore[arg-type]
    if [c.code for c in checks] == ["skipped"]:
        pytest.skip("Chromium for Playwright is not installed")
    assert [c for c in checks if not c.passed] == []
    assert "doc-footnote" in html and "doc-noteref" in html


def test_a_paragraph_over_two_pages_does_not_swallow_the_marker_of_its_last_page():
    pairs = numbered(
        [
            block(0, Kind.PARAGRAPH, "Before it."),
            block(1, Kind.PARAGRAPH, "Runs over the break and ends.", last_page=1),
            block(2, Kind.PARAGRAPH, "Still on the first page."),
            block(0, Kind.PARAGRAPH, "Third page.", page=2),
        ]
    )
    html = render(pairs)
    assert re.findall(r'aria-label="(Page \d+)"', html) == ["Page 1", "Page 2", "Page 3"]
    # what follows it on the page it starts stays under that page's marker
    assert html.index("Still on the first page.") < html.index("pagebreak-2")
    # the marker comes after the joined paragraph, never inside it
    assert html.index("Runs over the break and ends.") < html.index("pagebreak-2")
    assert html.index("pagebreak-2") < html.index("Third page.")
    inside = re.search(r"<p[^>]*>Runs over the break and ends\.</p>", html)
    assert inside, "the paragraph is written whole"


@pytest.mark.browser
def test_on_a_phone_the_title_is_on_the_first_screen_and_the_skip_link_lands_on_its_target():
    """F-116: a long Contents list no longer hides the title on a 390 px screen. It is a closed
    line there, a control that works from the keyboard, and an open list from 40rem up. The
    skip link lands on the notice a reader must not miss (this page is not reviewed), which
    stands right above the title."""
    sync_api = pytest.importorskip("playwright.sync_api")
    pairs = [heading(0, "Motion", 1)]
    for n in range(20):
        pairs += [heading(2 * n + 1, f"Section {n}", 2), block(2 * n + 2, Kind.PARAGRAPH, "Text.")]
    html = render(numbered(pairs))
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error:
            pytest.skip("Playwright's Chromium is not installed")
        try:
            page = browser.new_page(viewport={"width": 390, "height": 700})
            page.set_content(html)
            title_bottom = page.evaluate(
                "document.querySelector('h1').getBoundingClientRect().bottom"
            )
            was_open = page.evaluate("document.querySelector('.toc-details').open")
            page.keyboard.press("Tab")
            page.keyboard.press("Enter")  # the skip link
            landed = page.evaluate(
                "'#' + document.activeElement.id === "
                "document.querySelector('.skip-link').getAttribute('href')"
            )
            page.focus(".toc-details > summary")
            page.keyboard.press("Enter")  # the contents line, from the keyboard
            opened = page.evaluate("document.querySelector('.toc-details').open")
            page.set_viewport_size({"width": 1000, "height": 700})
            controls = page.evaluate(
                "getComputedStyle(document.querySelector('.toc-details > summary')).display"
            )
            first_link = page.evaluate(
                "document.querySelector('.toc a').getBoundingClientRect().height"
            )
        finally:
            browser.close()
    assert title_bottom < 700 and not was_open and landed and opened
    assert controls == "none" and first_link > 0  # from 40rem up the list is shown, with no control
