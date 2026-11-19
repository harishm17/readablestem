"""Joining the parts of a long source into one file (pipeline/combine.py): page order, ids
that cannot clash, equation markers and text offsets moved together, page markers that
continue, one contents list, gaps named, and each part's maths and figures intact."""

from __future__ import annotations

import base64
import re

import pytest

from readablestem.core.types import (
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Link,
    Notice,
    Run,
    ScriptRun,
    Status,
    inline_marker,
)
from readablestem.pipeline.combine import CombineError, Part, common_language, join
from readablestem.pipeline.combine import combine as combine_html
from readablestem.pipeline.html_checks import static_checks
from readablestem.pipeline.text_pass import set_document_language

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
MATHML = "<math><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math>"


def part_result(
    tag: str, *, pages: int = 2, headings: int = 2, **extra
) -> tuple[DocumentResult, dict[str, bytes]]:
    """A part of `pages` pages (ids from p1, as every part has): a heading on each page, a
    paragraph with an inline equation and a link, a display equation, and a figure."""
    elements: list[Element] = []
    fixes: list[Fix] = []
    crops: dict[str, bytes] = {}
    order = 0

    def add(element: Element, fix: Fix | None = None, crop: bool = False) -> None:
        nonlocal order
        elements.append(element.model_copy(update={"order": order}))
        order += 1
        if fix:
            fixes.append(fix)
        if crop:
            crops[element.id] = PNG

    for page in range(pages):
        n = page + 1
        if page < headings:
            add(
                Element(id=f"p{n}-e0", kind=Kind.HEADING, page=page, bbox=(0, 0, 1, 1), order=0,
                        text=f"{tag} heading {n}"),
                Fix(element_id=f"p{n}-e0", kind=Kind.HEADING, status=Status.ACCEPTED,
                    heading=HeadingFix(level=1 if page == 0 else 2, text=f"{tag} heading {n}")),
            )  # fmt: skip
        marker = inline_marker(f"p{n}-e2")
        text = f"{tag} speed {marker} then a link here and H2O end, not 3 but 4"
        start = text.index("a link")
        add(
            Element(id=f"p{n}-e1", kind=Kind.PARAGRAPH, page=page, bbox=(0, 1, 1, 2), order=0,
                    text=text,
                    links=[Link(start=start, end=start + 6, kind="internal",
                                target_id=f"p{n}-e3")],
                    script_runs=[ScriptRun(start=text.index("H2O") + 1,
                                           end=text.index("H2O") + 2, kind="sub")],
                    runs=[Run(start=text.index("3"), end=text.index("3") + 1,
                              kind="deleted")]),
        )  # fmt: skip
        add(
            Element(id=f"p{n}-e2", kind=Kind.EQUATION, page=page, bbox=(0, 1, 1, 2), order=0,
                    display="inline", parent_id=f"p{n}-e1"),
            Fix(element_id=f"p{n}-e2", kind=Kind.EQUATION, status=Status.ACCEPTED,
                equation=EquationFix(latex="v=d/t", mathml=MATHML, speech="v equals d over t",
                                     display="inline")),
        )  # fmt: skip
        add(
            Element(id=f"p{n}-e3", kind=Kind.EQUATION, page=page, bbox=(0, 2, 1, 3), order=0,
                    display="block", equation_label="(1)"),
            Fix(element_id=f"p{n}-e3", kind=Kind.EQUATION, status=Status.ACCEPTED,
                equation=EquationFix(latex="x+1", mathml="<math display='block'><mi>x</mi>"
                                     "<mo>+</mo><mn>1</mn></math>", speech="x plus 1",
                                     display="block")),
        )  # fmt: skip
        add(
            Element(id=f"p{n}-e4", kind=Kind.FIGURE, page=page, bbox=(0, 3, 10, 13), order=0,
                    caption=f"Figure {tag}{n}"),
            Fix(element_id=f"p{n}-e4", kind=Kind.FIGURE, status=Status.ACCEPTED,
                figure=FigureFix(figure_type="photo", alt=f"{tag} a dot {n}")),
            crop=True,
        )  # fmt: skip
    result = DocumentResult(
        pipeline_version="test",
        title=f"{tag} title",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(pages=pages, cost_usd=0, latency_ms=0, model_calls=0),
        **extra,
    )
    return result, crops


def parts_of(*spec: tuple[str, list[int]], **extra) -> list[Part]:
    out = []
    for tag, pages in spec:
        result, crops = part_result(tag, pages=len(pages), **extra)
        out.append(Part(result, crops, pages))
    return out


def three() -> list[Part]:
    return parts_of(("A", [0, 1]), ("B", [2, 3]), ("C", [4, 5]))


def ids_of(html: str) -> list[str]:
    return re.findall(r'\bid="([^"]+)"', html)


def test_parts_given_out_of_order_are_joined_in_page_order():
    first, second, third = three()
    html = combine_html([third, first, second], title="Chapter")
    order = [html.index(f"{tag} speed") for tag in "ABC"]
    assert order == sorted(order)
    assert "<title>Chapter</title>" in html


def test_ids_are_the_source_page_so_no_part_can_clash_and_all_are_unique():
    html = combine_html(three(), title="Chapter")
    ids = ids_of(html)
    assert len(ids) == len(set(ids))
    assert {"p1-e0", "p3-e0", "p5-e0", "p6-e1"} <= set(ids)  # page 5 of part C is page 6
    joined = join(three())
    assert len({e.id for e in joined.elements}) == len(joined.elements)
    assert [e.page for e in joined.elements] == sorted(e.page for e in joined.elements)


def test_equation_markers_and_offsets_are_moved_together():
    joined = join(three())
    paragraph = next(e for e in joined.elements if e.id == "p3-e1")
    assert inline_marker("p3-e2") in (paragraph.text or "")  # was p1-e2 in part B
    text = paragraph.text or ""
    [link] = paragraph.links
    assert text[link.start : link.end] == "a link" and link.target_id == "p3-e3"
    [script] = paragraph.script_runs
    assert text[script.start : script.end] == "2"  # the 2 of H2O
    [struck] = paragraph.runs
    assert (text[struck.start : struck.end], struck.kind) == ("3", "deleted")
    assert paragraph.parent_id is None
    inline = next(e for e in joined.elements if e.id == "p3-e2")
    assert inline.parent_id == "p3-e1" and inline.page == 2


def test_the_maths_of_every_part_is_written_with_its_own_figure_and_alt_text():
    html = combine_html(three(), title="Chapter")
    assert html.count("<math") == 12  # inline and display equation on each of six pages
    assert html.count('<div class="equation equation-labelled"') == 6
    for tag, n in (("A", 1), ("B", 1), ("C", 2)):
        assert f'alt="{tag} a dot {n}"' in html
    assert html.count("<figure") == 6 and html.count("<img") == 6
    assert html.count(base64.b64encode(PNG).decode()) == 6
    # a link inside a part points at that part's equation, by its new id
    assert '<a href="#p3-e3">a link</a>' in html and 'id="p3-e3"' in html


def test_one_contents_list_covers_the_headings_of_every_part_and_page_markers_continue():
    html = combine_html(three(), title="Chapter")
    assert html.count("<nav") == 1
    nav = re.search(r"<nav.*?</nav>", html, re.S)
    assert nav
    for tag, n in (("A", 1), ("A", 2), ("B", 1), ("B", 2), ("C", 1), ("C", 2)):
        assert f"{tag} heading {n}</a>" in nav.group(0)
    assert re.findall(r'aria-label="(Page \d+)"', html) == [f"Page {n}" for n in range(1, 7)]
    assert all(c.passed for c in static_checks(html) if c.severity == "error")


def test_printed_page_labels_travel_with_their_pages():
    first, second = parts_of(("A", [0, 1]), ("B", [2, 3]))
    first.result.page_labels = {0: "iv", 1: "v"}
    second.result.page_labels = {1: "12"}
    html = combine_html([first, second], title="Chapter")
    assert re.findall(r'aria-label="(P[^"]+)"', html) == [
        "Page iv", "Page v", "PDF page 3", "Page 12",
    ]  # fmt: skip


def test_pages_no_part_holds_are_named_as_left_out():
    first, third = parts_of(("A", [0, 1]), ("C", [4, 5]))  # the part with pages 3-4 is gone
    html = combine_html([first, third], title="Chapter")
    assert "Pages 3 and 4 of the original PDF were left out of this file." in html
    full = combine_html(three(), title="Chapter")
    assert "left out of this file" not in full


def test_pages_a_part_left_out_stay_left_out():
    first, second = parts_of(("A", [0, 1]), ("B", [2, 3]))
    from readablestem.core.types import OmittedPage

    second.result.omitted = [OmittedPage(page=1)]
    html = combine_html([first, second], title="Chapter")
    assert "Page 4 of the original PDF was left out of this file." in html


def test_footnote_notices_follow_their_new_ids():
    first, second = parts_of(("A", [0, 1]), ("B", [2, 3]))
    second.result.notices = [Notice(code="footnote", page=0, element_id="p1-e1", review="none")]
    assert join([first, second]).footnote_ids == ["p3-e1"]


def test_a_part_with_a_pdf_contents_page_keeps_it_and_no_second_list_is_made():
    from readablestem.pipeline import navigation

    first, second = parts_of(("A", [0, 1]), ("B", [2, 3]))
    entry = first.result.elements[1].model_copy(
        update={"reasons": [navigation.PRINTED_CONTENTS_REASON]}
    )
    first.result.elements[1] = entry
    assert "<nav" not in combine_html([first, second], title="Chapter")


@pytest.mark.parametrize(
    ("entries", "headings", "linked"),
    [
        # a part holds one namesake: its number is not the entry's
        (["3.1 The model"], ["2.1 The model"], [None]),
        # two namesakes: the number says which one each entry means
        (
            ["2.1 The model", "3.1 The model"],
            ["2.1 The model", "3.1 The model"],
            ["2.1 The model", "3.1 The model"],
        ),
        # a number on one side only, or none: the title decides as before
        (["The model"], ["2.1 The model"], ["2.1 The model"]),
        (["Appendix A Tables"], ["Appendix A  Tables"], ["Appendix A  Tables"]),
        # a section number against an outline's roman numeral: not compared, the title decides
        (["4.1 Scope", "4.2 Method"], ["I. Scope", "II. Method"], ["I. Scope", "II. Method"]),
    ],
)
def test_a_contents_entry_links_to_the_heading_with_its_number_not_a_namesake(
    entries, headings, linked
):
    result, _ = part_result("A", pages=1, headings=0)
    box = {"bbox": (0, 0, 1, 1), "page": 0, "order": 0}
    result.fixes = []
    result.elements = [
        Element(id=f"p1-e{i}", kind=Kind.LIST_ITEM, text=text, reasons=["contents entry"], **box)
        for i, text in enumerate(entries)
    ] + [
        Element(id=f"p1-e{len(entries) + i}", kind=Kind.HEADING, text=text, **box)
        for i, text in enumerate(headings)
    ]
    for order, element in enumerate(result.elements):
        element.order = order
    joined = join([Part(result, {}, [0])]).elements
    by_id = {e.id: e for e in joined}
    got = [
        by_id[e.links[0].target_id].text if e.links else None
        for e in joined
        if "contents entry" in e.reasons
    ]
    assert got == linked


def lang_of(html: str, text: str) -> str | None:
    """The `lang` on the block that holds `text`, None when it has none of its own."""
    block = re.search(rf"<(?:p|h\d|li)\b([^>]*)>[^<]*{text}", html)
    assert block, text
    found = re.search(r'\blang="([^"]+)"', block.group(1))
    return found.group(1) if found else None


def test_the_language_is_the_one_most_parts_have_and_a_part_in_another_keeps_its_own():
    parts = three()
    parts[0].result.lang, parts[1].result.lang, parts[2].result.lang = "de", "fr", "fr-CA"
    assert join(parts).lang == "fr"
    html = combine_html(parts, title="Chapter")
    assert '<html lang="fr">' in html
    # the part in German is read with a German voice; the French ones are the file's language
    assert [lang_of(html, f"{t} speed") for t in "ABC"] == ["de", None, None]
    assert [lang_of(html, f"{t} heading 1") for t in "ABC"] == ["de", None, None]
    # a passage the part found in another language keeps it
    parts[0].result.elements[1].lang = "es"
    assert lang_of(combine_html(parts, title="Chapter"), "A speed") == "es"


def test_a_language_nobody_confirmed_votes_for_nothing_and_marks_nothing():
    parts = three()
    parts[0].result.lang, parts[1].result.lang, parts[2].result.lang = "en", "fr", "fr"
    parts[0].result.notices = [Notice(code="lang_assumed", review="required")]
    joined = join(parts)
    assert joined.lang == "fr"
    html = combine_html(parts, title="Chapter")
    assert lang_of(html, "A speed") is None  # a guess is not written as the part's language
    # the owner answers on the part's own page: the file follows, and the part now marks itself
    done = set_document_language(parts[0].result, "German")
    assert join([Part(done, parts[0].crops, parts[0].pages), *parts[1:]]).lang == "fr"
    changed = combine_html([Part(done, parts[0].crops, parts[0].pages), *parts[1:]], title="C")
    assert lang_of(changed, "A speed") == "de"


@pytest.mark.parametrize(
    ("langs", "waiting", "tag", "check"),
    [
        (["en", "en", "fr"], [False] * 3, "en", ""),
        (["en", "en-US", "fr"], [False] * 3, "en", ""),
        (["en", "fr"], [False] * 2, "en", "split"),
        (["fr", "en", "de"], [False] * 3, "fr", "split"),
        (["en", "fr", "fr"], [True, False, False], "fr", "unconfirmed"),
        (["en", "en"], [True, True], "en", "unconfirmed"),
    ],
)
def test_one_check_when_a_language_is_waiting_or_the_parts_have_no_majority(
    langs, waiting, tag, check
):
    assert common_language(list(zip(langs, waiting, strict=True))) == (tag, check)


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        ([("A", [0, 1]), ("B", [1, 2])], "pages_overlap"),
        ([], "no_parts"),
    ],
)
def test_parts_that_cannot_be_joined_are_refused_with_a_category(spec, code):
    with pytest.raises(CombineError) as raised:
        join(parts_of(*spec))
    assert raised.value.code == code


def test_an_element_on_a_page_the_part_does_not_have_is_refused():
    result, crops = part_result("A", pages=3)
    with pytest.raises(CombineError) as raised:
        join([Part(result, crops, [0, 1])])  # the part says it has two pages
    assert raised.value.code == "page_outside_part"


def test_joining_does_not_change_the_parts():
    parts = three()
    before = [p.result.model_dump_json() for p in parts]
    join(parts)
    assert [p.result.model_dump_json() for p in parts] == before


def test_the_last_page_of_a_joined_paragraph_moves_with_its_part():
    first, second = parts_of(("A", [0, 1]), ("B", [2, 3]))
    # the last element of the part's first page is the one that runs over the page break
    last = max(i for i, e in enumerate(first.result.elements) if e.page == 0)
    first.result.elements[last] = first.result.elements[last].model_copy(update={"last_page": 1})
    html = combine_html([first, second], title="Chapter")
    assert re.findall(r'aria-label="(Page \d+)"', html) == [f"Page {n}" for n in range(1, 5)]
    moved = next(e for e in join([first, second]).elements if e.last_page is not None)
    assert (moved.page, moved.last_page) == (0, 1)
    first.result.elements[last] = first.result.elements[last].model_copy(update={"last_page": 5})
    with pytest.raises(CombineError):
        join([first, second])
