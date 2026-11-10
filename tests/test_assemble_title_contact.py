"""The student file's title (T1-7) and contact line (T1-17), as the assembler writes them."""

from __future__ import annotations

import re

import pytest

from readablestem.core.types import Element, Fix, HeadingFix, Kind, Status
from readablestem.pipeline import assemble, html_checks


def _heading(eid: str, order: int, text: str, level: int) -> tuple[Element, Fix]:
    element = Element(
        id=eid, kind=Kind.HEADING, page=0, bbox=(0, 0, 10, 10), order=order, text=text
    )
    fix = Fix(
        element_id=eid,
        kind=Kind.HEADING,
        status=Status.ACCEPTED,
        confidence=0.9,
        heading=HeadingFix(level=level, text=text),
    )
    return element, fix


def render(title, headings=(("Contents", 2),), **kw) -> str:
    pairs = [_heading(f"p1-e{i}", i, text, level) for i, (text, level) in enumerate(headings)]
    return assemble.render_html(
        title=title,
        lang="en",
        elements=[e for e, _ in pairs],
        fixes=[f for _, f in pairs],
        crops={},
        **kw,
    )


def title_of(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html)
    assert match
    return match.group(1)


def test_a_title_the_instructor_set_is_used_as_typed_and_becomes_the_top_heading():
    html = render("1 Derivatives", title_set=True)
    assert title_of(html) == "1 Derivatives"
    assert re.search(r"<main>\n<h1>1 Derivatives</h1>\n<h2", html)
    assert html_checks.static_checks(html)


def headings_of(html: str) -> list[tuple[int, str]]:
    return [(int(m.group(1)), m.group(2)) for m in re.finditer(r"<h([1-6])[^>]*>(.*?)</h", html)]


SLIDES = (("Motion", 1), ("Position", 2), ("Constant acceleration", 1), ("Velocity graph", 1))


def test_slide_titles_are_h2_under_the_one_h1_the_document_title():
    """T3-13: a file with a top-level heading per slide has one h1, the title."""
    html = render("Kinematics slides", headings=SLIDES)
    assert headings_of(html) == [
        (1, "Kinematics slides"),
        (2, "Motion"),
        (3, "Position"),
        (2, "Constant acceleration"),
        (2, "Velocity graph"),
    ]
    assert html.count("<h1") == 1 and title_of(html) == "Kinematics slides"
    assert html_checks.static_checks(html)


def test_levels_stay_inside_h6_when_a_slide_deck_is_moved_down():
    deep = (("A", 1), ("B", 2), ("C", 3), ("D", 4), ("E", 5), ("F", 6), ("G", 1))
    levels = [level for level, _ in headings_of(render("T", headings=deep))]
    assert max(levels) == 6 and levels[0] == 1


def test_no_outline_of_up_to_four_headings_ends_with_two_h1s():
    """T3-13, exhaustively: a file has at most one top-level heading, and the move down
    (`title_shift`) is made exactly when the repaired outline would have had two."""
    import itertools

    from readablestem.core.types import Status

    for n in range(1, 5):
        for spec in itertools.product((None, 1, 2, 3), repeat=n):
            pairs = [_heading(f"p1-e{i}", i, f"H{i}", level or 2) for i, level in enumerate(spec)]
            fixes = [
                f.model_copy(update={"status": Status.REJECTED}) if level is None else f
                for (_, f), level in zip(pairs, spec, strict=True)
            ]
            elements = [e for e, _ in pairs]
            html = assemble.render_html(
                title="T", lang="en", elements=elements, fixes=fixes, crops={}
            )
            assert html.count("<h1") <= 1, spec
            moved = assemble.title_shift(elements, fixes)
            assert (html.count("<h1") == 1 and moved == 1) or moved == 0, spec


def test_a_chosen_title_is_escaped():
    html = render('Forces <script>x</script> & "more"', title_set=True)
    assert "<script>x" not in html
    assert title_of(html) == "Forces &lt;script&gt;x&lt;/script&gt; &amp; &quot;more&quot;"


def footer(html: str) -> str:
    return html[html.index("<footer") :]


def test_an_email_contact_line_is_a_mailto_link_in_the_footer():
    html = render("T", contact="prof.lee@example.edu")
    assert (
        "tell your instructor: "
        '<a href="mailto:prof.lee@example.edu">prof.lee@example.edu</a>.</p>' in footer(html)
    )
    assert all(c.passed for c in html_checks.static_checks(html))


def test_a_web_address_contact_line_is_a_link_in_the_footer():
    html = render("T", contact="https://math.example.edu/help?a=1&b=2")
    assert '<a href="https://math.example.edu/help?a=1&amp;b=2">' in footer(html)
    assert all(c.passed for c in html_checks.static_checks(html))


@pytest.mark.parametrize(
    "contact",
    [
        "javascript:alert(1)",
        "ftp://example.edu/x",
        '"><script>x</script>',
        "two words@example.edu",
        'https://example.edu/"quoted',
        "not an address",
        "https://" + "a" * 400 + ".edu",
    ],
)
def test_anything_that_is_not_an_email_or_web_address_is_left_out(contact):
    html = render("T", contact=contact)
    assert f"<p>{assemble.CONTACT}</p>" in footer(html)
    assert "<script>x" not in html and "javascript:" not in html
