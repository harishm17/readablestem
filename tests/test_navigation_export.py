"""The navigation (skip link, contents list, page markers, footnote links) in the Canvas and
Common Cartridge exports, and when an LMS removes more than Canvas does.

Canvas keeps `nav`, `hr`, `role`, `aria-label`, `id` and relative `#` links (the allowlist
in tests/fixtures/canvas), so the contents list, the markers and the note links survive a
pasted page. It removes `<main>` and `tabindex`: the skip link would jump to a landmark that
is gone, so the paste version leaves it out. Moodle's purifier and others may remove roles
and labels; the page numbers and links are then still visible text.
"""

from __future__ import annotations

import io
import re
import zipfile

import pytest

import canvas_sanitizer as cs
from readablestem.core.types import Element, Kind, Link
from readablestem.pipeline import export_cartridge as cc
from readablestem.pipeline.export_canvas import build_zip, export_canvas, readme
from test_assemble_navigation import block, heading, numbered, render

FOLDER = "doc-3f9a2b"
COURSE = "12345"


def document() -> str:
    pairs = numbered(
        [
            heading(0, "Motion", 1),
            heading(1, "Speed", 2),
            block(2, Kind.PARAGRAPH, "Light travels fast.1 It is a constant.", page=1,
                  links=[Link(start=19, end=20, kind="internal", target_id="p2-e3")]),
            block(3, Kind.PARAGRAPH, "1 In a vacuum.", page=1),
            heading(4, "Velocity", 2, page=2),
            block(5, Kind.PARAGRAPH, "Last page text.", page=2),
        ]
    )  # fmt: skip
    return render(pairs, footnote_ids=["p2-e3"], page_labels={1: "12", 2: "13"})


@pytest.fixture(scope="module")
def html() -> str:
    return document()


@pytest.fixture(scope="module")
def export(html):
    return export_canvas(html, title="Doc", folder=FOLDER, course_number=COURSE)


def link_targets_exist(markup: str) -> None:
    ids = set(re.findall(r'\bid="([^"]+)"', markup))
    for target in re.findall(r'href="#([^"]+)"', markup):
        assert target in ids, target


def test_the_source_has_every_piece(html):
    assert 'aria-label="Contents"' in html and html.count("doc-pagebreak") == 3
    assert "doc-noteref" in html and "doc-footnote" in html and "doc-backlink" in html
    assert 'href="#main-content"' in html and 'id="main-content"' in html


def test_the_file_in_the_zip_keeps_all_of_it_and_adds_no_tab_stop(export):
    page = export.index_html
    assert 'href="#main-content"' in page and 'id="main-content"' in page
    assert "tabindex" not in page and export.notes.scroll_boxes == 0
    link_targets_exist(page)
    assert re.findall(r'aria-label="(Page [^"]+)"', page) == ["Page 1", "Page 12", "Page 13"]


def test_the_skip_link_is_a_plain_visible_link_there_because_canvas_keeps_no_pseudo_rules(export):
    link = re.search(r'<a [^>]*class="skip-link[^>]*>', export.index_html)
    assert link is not None
    assert "position" not in link.group(0)  # the hiding rule is `.skip-link:not(:focus)`
    assert ".skip-link:not(:focus)" in export.notes.uncarried_selectors


def test_the_page_to_paste_leaves_out_the_skip_link_and_what_it_points_to(export):
    page = export.page_html
    assert page is not None
    assert "skip-link" not in page and "main-content" not in page and "<main" not in page
    assert export.notes.skip_link_left_out
    link_targets_exist(page)  # nothing points at what was left out


def test_the_page_to_paste_keeps_the_contents_markers_and_note_links(export):
    page = export.page_html or ""
    assert '<nav class="toc" aria-label="Contents"' in page
    for text in ("Motion", "Speed", "Velocity"):
        assert f">{text}</a>" in page
    assert re.findall(r'aria-label="(Page [^"]+)"', page) == ["Page 1", "Page 12", "Page 13"]
    assert 'role="doc-noteref"' in page and 'role="doc-backlink"' in page
    assert "tabindex" not in page


def test_canvas_loses_nothing_of_the_navigation_in_the_page_to_paste(export):
    page = export.page_html or ""
    before = cs.serialize(cs.parse(page), canonical=True)
    after = cs.serialize(cs.sanitize(cs.parse(page)), canonical=True)
    assert after == before
    kept = cs.sanitize(cs.parse(page))
    assert [h.attrs["role"] for h in kept.find_all("hr")] == ["doc-pagebreak"] * 3
    assert len(kept.find_all("nav")) == 1


def test_the_headings_go_down_a_level_in_the_page_but_the_contents_list_does_not(export):
    page = export.page_html or ""
    assert '<h2 id="p1-e0">Motion</h2>' in page
    nav = re.search(r"<nav.*?</nav>", page, re.S)
    assert nav and nav.group(0).count("<ul") == 2  # nested as in the source


def test_the_readme_says_the_page_has_no_skip_link(export):
    text = " ".join(readme(export).split())
    assert "has no “Skip to main content” link" in text
    plain = export_canvas(document(), title="Doc", folder=FOLDER)
    assert "Skip to main content" not in readme(plain)  # no page to paste: nothing to say


def strip_like_a_strict_purifier(markup: str) -> str:
    """What is left if an LMS removes roles, aria attributes, classes, styles and `hr`."""
    markup = re.sub(r"<hr[^>]*>", "", markup)
    return re.sub(r'\s(?:role|class|style|aria-[a-z]+)="[^"]*"', "", markup)


def test_if_an_lms_strips_roles_and_labels_the_pages_and_links_are_still_text(export):
    for text in (export.index_html, export.page_html or ""):
        plain = cs.content(cs.parse(strip_like_a_strict_purifier(text)))
        squashed = plain.text
        for label in ("Page 1", "Page 12", "Page 13", "Back to the text", "Contents"):
            assert label in squashed, label
        links = {href: shown for href, shown in plain.links}
        assert links["#p1-e1"] == "Speed"  # the contents entry is still a link
        assert any(shown == "1" for shown in links.values())  # the note's mark too
        assert any(shown == "Back to the text" for shown in links.values())


def test_the_zip_and_the_cartridge_carry_it(export):
    names = zipfile.ZipFile(io.BytesIO(build_zip(export))).namelist()
    assert names[:3] == ["index.html", "canvas-page.html", "README.txt"]
    data = cc.build_cartridge(export)
    assert cc.check_cartridge(data) == []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        page = archive.read(f"web_resources/{FOLDER}/index.html").decode()
    assert page == export.index_html
    link_targets_exist(page)


def test_a_document_without_navigation_exports_as_before():
    html = render([Element(id="p1-e0", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 1, 1), order=0,
                           text="Just text.")])  # fmt: skip
    export = export_canvas(html, title="Doc", folder=FOLDER, course_number=COURSE)
    assert "<nav" not in (export.page_html or "") and "doc-pagebreak" not in (
        export.page_html or ""
    )
    assert export.notes.skip_link_left_out
