"""Navigation in the output file: the skip link, the contents list, page markers, footnote
links (pipeline/navigation.py, docs/output.md "Navigation")."""

from __future__ import annotations

import re

import pytest

from readablestem.core.types import Element, Fix, HeadingFix, Kind, Link, Status, inline_marker
from readablestem.pipeline import layout, navigation
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


def test_the_skip_link_comes_first_and_jumps_to_the_start_of_main():
    html = render([block(0, Kind.PARAGRAPH, "Hello.")])
    body = html.split("<body>", 1)[1]
    assert body.lstrip().startswith(
        '<a class="skip-link readablestem" href="#main-content">Skip to main content</a>'
    )
    # the target is the empty anchor just before <main>, which keeps its plain tag
    assert body.index('id="main-content"') < body.index("<main>")
    assert html.count('id="main-content"') == 1
    # focusable by the link (Firefox, VoiceOver), but not a tab stop; the only tabindex
    assert re.findall(r"tabindex=\"[^\"]*\"", html) == ['tabindex="-1"']
    assert '<span id="main-content" tabindex="-1"></span>' in html


def test_the_contents_label_is_english_and_its_entries_the_documents_language():
    pairs = three_headings()
    elements = [p[0] if isinstance(p, tuple) else p for p in pairs]
    fixes = [p[1] for p in pairs if isinstance(p, tuple)]
    html = render_html(title="Doc", lang="de", elements=elements, fixes=fixes, crops={})
    assert '<nav class="toc" lang="en" aria-label="Contents">' in html
    assert '<div lang="de"><ul>' in contents_nav(html)
    english = render(three_headings())
    assert 'lang="' not in contents_nav(english)


def test_the_skip_link_is_hidden_until_it_is_focused():
    html = render([block(0, Kind.PARAGRAPH, "Hello.")])
    assert ".skip-link:not(:focus)" in html
    assert "clip-path: inset(50%)" in html.split(".skip-link:not(:focus)", 1)[1][:300]


def test_the_skip_link_and_contents_label_say_they_are_english_in_other_languages():
    pairs = three_headings()
    elements = [p[0] if isinstance(p, tuple) else p for p in pairs]
    fixes = [p[1] for p in pairs if isinstance(p, tuple)]
    html = render_html(title="Doc", lang="de", elements=elements, fixes=fixes, crops={})
    assert 'class="skip-link readablestem" lang="en"' in html
    assert 'class="toc-title readablestem" lang="en"' in html
    assert "<li><a href" in html and 'lang="en"><a href="#p1-e0"' not in html  # entries stay German


# ----------------------------------------------------------------------------- contents list


def test_three_headings_make_a_nested_contents_list_of_links_to_their_ids():
    html = render(three_headings())
    nav = contents_nav(html)
    assert nav.startswith('<nav class="toc" aria-label="Contents">')
    assert (
        '<ul><li><a href="#p1-e0">Motion</a><ul><li><a href="#p1-e2">Speed</a></li>'
        '<li><a href="#p1-e4">Velocity</a></li></ul></li></ul>'
    ) in nav
    assert html.count("<nav") == 1
    # outside <main>, after the skip link, so the skip link jumps over it
    assert html.index("skip-link") < html.index("<nav") < html.index("<main>")
    for target in re.findall(r'href="#([^"]+)"', nav):
        assert f'id="{target}"' in html
    assert all(c.passed for c in static_checks(html) if c.severity == "error")


def test_fewer_than_three_headings_make_no_contents_list():
    html = render(numbered([heading(0, "Motion", 1), heading(1, "Speed", 2)]))
    assert "<nav" not in html and "Contents" not in html


def test_the_list_follows_the_repaired_outline_not_the_levels_in_the_fixes():
    # h1, then an h3 straight under it: the outline repair writes it as an h2, and the
    # list nests it one level, under the first
    pairs = numbered([heading(0, "A", 1), heading(1, "B", 3), heading(2, "C", 3)])
    html = render(pairs)
    assert '<h2 id="p1-e1">B</h2>' in html
    assert (
        '<ul><li><a href="#p1-e0">A</a><ul><li><a href="#p1-e1">B</a></li>'
        '<li><a href="#p1-e2">C</a></li></ul></li></ul>'
    ) in contents_nav(html)


def test_a_deck_with_one_heading_per_slide_lists_its_slides_under_the_title():
    pairs = numbered([heading(i, f"Slide {i}", 1, page=i) for i in range(3)])
    html = render(pairs)
    assert "<h1>Doc</h1>" in html and '<h2 id="p1-e0">Slide 0</h2>' in html
    nav = contents_nav(html)
    assert nav.count("<li>") == 3 and "Doc" not in nav  # the title is not an entry


def test_a_heading_whose_id_is_taken_gets_one_of_its_own():
    first, fix1 = heading(0, "One", 1)
    clash = first.model_copy(update={"order": 1})  # a layout bug: the same id twice
    third, fix3 = heading(2, "Three", 2)
    html = render(numbered([(first, fix1), (clash, fix1), (third, fix3)]))
    assert '<h2 id="heading-1">One</h2>' in html  # (two top headings: all go one level down)
    assert '<a href="#heading-1">One</a>' in contents_nav(html)
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))


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


def test_the_pdfs_own_contents_page_is_the_only_contents_list():
    entry = block(
        1, Kind.LIST_ITEM, "Motion", list_marker="•", list_ordered=False, list_level=1,
        reasons=[navigation.PRINTED_CONTENTS_REASON],
    )  # fmt: skip
    pairs = numbered([heading(0, "Contents", 1), entry, *three_headings()[1:]])
    html = render(pairs)
    assert "<nav" not in html
    assert "<li" in html  # the printed list is still there
    assert "skip-link" in html  # the skip link is not a contents list


def test_the_reason_layout_gives_a_contents_entry_is_the_one_assembly_looks_for():
    assert navigation.PRINTED_CONTENTS_REASON == layout.CONTENTS_ENTRY


def test_the_contents_list_text_is_escaped():
    pairs = numbered(
        [heading(0, "<b>One</b>", 1), heading(1, "Two & three", 2), heading(2, "Four", 2)]
    )
    nav = contents_nav(render(pairs))
    assert "<b>" not in nav and "&lt;b&gt;One&lt;/b&gt;" in nav and "Two &amp; three" in nav


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


def test_the_page_is_also_written_as_visible_text_for_readers_that_drop_the_role():
    html = render(two_pages())
    assert (
        '<div class="pagebreak readablestem"><hr role="doc-pagebreak" aria-label="Page 2" '
        'id="pagebreak-2"><span aria-hidden="true">Page 2</span></div>'
    ) in html


def test_the_printed_label_is_used_where_there_is_one():
    html = render(two_pages(), page_labels={0: "iv", 1: "12"})
    assert re.findall(r'aria-label="(P[^"]+)"', html) == ["Page iv", "Page 12", "PDF page 3"]
    assert 'id="pagebreak-2"' in html  # the id is by position, so it never repeats


def test_a_page_without_a_printed_label_never_reads_like_one():
    # page 3 is not "Page 3" next to a page printed "3" (the cover came first)
    html = render(two_pages(), page_labels={1: "3"})
    assert re.findall(r'aria-label="(P[^"]+)"', html) == ["PDF page 1", "Page 3", "PDF page 3"]
    # a part of a longer file: its pages are the source's, by position
    part = render(two_pages(), pdf_pages={0: 30, 1: 31, 2: 32})
    assert re.findall(r'aria-label="(P[^"]+)"', part) == ["Page 31", "Page 32", "Page 33"]
    labelled = render(two_pages(), pdf_pages={0: 30, 1: 31, 2: 32}, page_labels={0: "12"})
    assert re.findall(r'aria-label="(P[^"]+)"', labelled) == [
        "Page 12", "PDF page 32", "PDF page 33",
    ]  # fmt: skip


def test_a_one_page_document_has_no_markers():
    html = render([block(0, Kind.PARAGRAPH, "Only page.")])
    assert "doc-pagebreak" not in html and ".pagebreak" not in html


def test_a_page_with_nothing_on_it_gets_no_marker():
    pairs = numbered(
        [
            block(0, Kind.PARAGRAPH, "One."),
            block(1, Kind.PARAGRAPH, "   ", page=1),  # nothing to write
            block(2, Kind.PARAGRAPH, "Three.", page=2),
        ]
    )
    assert re.findall(r'aria-label="(Page [^"]+)"', render(pairs)) == ["Page 1", "Page 3"]


def test_a_marker_never_stands_inside_a_paragraph():
    html = render(two_pages())
    for match in re.finditer(r'<div class="pagebreak', html):
        before = html[: match.start()]
        assert before.rstrip().endswith(("</p>", "</main>", "<main>")) or before.endswith("\n")
        assert before.count("<p") == before.count("</p>")


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
    assert html.count("<ul>") == 1  # one list, not cut in two
    assert '<li id="p2-e1"><div class="pagebreak' in html
    assert "<ul><div" not in html  # a div is not a child of a ul


def test_markers_come_between_glossary_entries_inside_the_term():
    entries = numbered(
        [
            block(0, Kind.PARAGRAPH, "term one definition", term_end=8),
            block(1, Kind.PARAGRAPH, "term two definition", page=1, term_end=8),
        ]
    )
    html = render(entries)
    assert html.count("<dl>") == 1
    assert '<dt id="p2-e1"><div class="pagebreak' in html


def test_documents_pass_the_static_checks_with_markers_and_a_contents_list():
    pairs = numbered(
        [
            heading(0, "Motion", 1),
            heading(1, "Speed", 2, page=1),
            block(2, Kind.PARAGRAPH, "Text.", page=1),
            heading(3, "Velocity", 2, page=2),
        ]
    )
    html = render(pairs)
    failed = [c for c in static_checks(html) if not c.passed and c.severity == "error"]
    assert failed == []
    assert html.count("doc-pagebreak") == 3 and html.count('aria-label="Contents"') == 1


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


def test_a_note_whose_mark_was_not_found_has_no_backlink_but_is_still_a_footnote():
    pairs, ids = noted(with_link=False)
    html = render(pairs, footnote_ids=ids)
    assert 'role="doc-footnote"' in html and "doc-backlink" not in html
    assert "\x00" not in html


def test_without_footnotes_a_link_between_blocks_is_an_ordinary_link():
    pairs, _ = noted()
    html = render(pairs)
    assert "doc-noteref" not in html and "doc-footnote" not in html
    assert '<a href="#p1-e1">1</a>' in html


def test_the_mark_of_a_footnote_keeps_its_role_when_text_also_links_elsewhere():
    pairs, ids = noted()
    html = render(pairs, footnote_ids=ids)
    assert html.count('role="doc-noteref"') == 1


# ----------------------------------------------------------------------------- unit


def test_contents_nav_nests_by_level_and_never_skips_a_closing_tag():
    entries = [
        navigation.TocEntry(2, "a", "A"),
        navigation.TocEntry(3, "b", "B"),
        navigation.TocEntry(4, "c", "C"),
        navigation.TocEntry(2, "d", "D"),
        navigation.TocEntry(3, "e", "E"),
    ]
    nav = navigation.contents_nav(entries, "readablestem")
    assert (
        '<ul><li><a href="#a">A</a><ul><li><a href="#b">B</a><ul><li><a href="#c">C</a></li>'
        '</ul></li></ul></li><li><a href="#d">D</a><ul><li><a href="#e">E</a></li></ul></li></ul>'
    ) in nav
    assert nav.count("<ul>") == nav.count("</ul>") and nav.count("<li>") == nav.count("</li>")


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
            block(0, Kind.PARAGRAPH, "Third page.", page=2),
        ]
    )
    html = render(pairs)
    assert re.findall(r'aria-label="(Page \d+)"', html) == ["Page 1", "Page 2", "Page 3"]
    # the marker comes after the joined paragraph, never inside it
    assert html.index("Runs over the break and ends.") < html.index("pagebreak-2")
    assert html.index("pagebreak-2") < html.index("Third page.")
    inside = re.search(r"<p[^>]*>Runs over the break and ends\.</p>", html)
    assert inside, "the paragraph is written whole"


def test_a_paragraph_over_three_pages_marks_each_page_in_order():
    pairs = numbered(
        [
            block(0, Kind.PARAGRAPH, "Runs over two breaks.", last_page=2),
            block(0, Kind.PARAGRAPH, "Then the fourth page.", page=3),
        ]
    )
    html = render(pairs)
    assert re.findall(r'aria-label="(Page \d+)"', html) == ["Page 1", "Page 2", "Page 3", "Page 4"]
    assert html.index("pagebreak-3") < html.index("Then the fourth page.")


def test_the_next_block_on_the_last_page_of_a_joined_paragraph_gets_no_second_marker():
    pairs = numbered(
        [
            block(0, Kind.PARAGRAPH, "Runs over.", last_page=1),
            block(0, Kind.PARAGRAPH, "Rest of page two.", page=1),
        ]
    )
    html = render(pairs)
    assert re.findall(r'aria-label="(Page \d+)"', html) == ["Page 1", "Page 2"]


def test_a_two_page_document_whose_second_page_is_only_a_continuation_is_marked():
    html = render(numbered([block(0, Kind.PARAGRAPH, "Runs over.", last_page=1)]))
    assert re.findall(r'aria-label="(Page \d+)"', html) == ["Page 1", "Page 2"]
