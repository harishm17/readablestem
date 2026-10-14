"""The student file's title (T1-7) and contact line (T1-17), as the assembler writes them."""

from __future__ import annotations

import re

import pytest

from a11y_stem.core.types import Element, Fix, HeadingFix, Kind, Status
from a11y_stem.pipeline import assemble, html_checks


@pytest.mark.parametrize(
    ("raw", "stripped"),
    [
        ("1 Derivatives", "Derivatives"),
        ("2.1 Speed and velocity", "Speed and velocity"),
        ("3. Forces", "Forces"),
        ("4) Energy", "Energy"),
        ("Chapter 3: Forces", "Chapter 3: Forces"),  # words are part of the title
        ("Week 1: Vectors", "Week 1: Vectors"),
        ("12.3.4 Deep", "Deep"),
        ("2024 Annual report", "2024 Annual report"),  # a year is not a section number
        ("3", "3"),  # nothing would be left
        ("Derivatives", "Derivatives"),
        ("1st Law", "1st Law"),
    ],
)
def test_a_section_number_is_stripped_from_the_front_of_a_title(raw, stripped):
    assert assemble.strip_section_number(raw) == stripped


def test_the_suggested_title_skips_a_part_name_and_falls_back_to_a_heading():
    assert assemble.suggested_title("1 Derivatives") == "Derivatives"
    assert assemble.suggested_title("Contents", ["Contents", "2 Limits"]) == "Limits"
    assert assemble.suggested_title("Table of Contents.", ["Calculus notes"]) == "Calculus notes"
    assert assemble.suggested_title(None, ["1 Vectors"]) == "Vectors"
    assert assemble.suggested_title("", []) == assemble.DEFAULT_TITLE
    assert assemble.suggested_title("Contents", ["Contents"]) == assemble.DEFAULT_TITLE


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


def test_the_default_title_has_no_section_number():
    assert title_of(render("1 Derivatives")) == "Derivatives"


def test_a_title_the_instructor_set_is_used_as_typed_and_becomes_the_top_heading():
    html = render("1 Derivatives", title_set=True)
    assert title_of(html) == "1 Derivatives"
    assert re.search(r"<main>\n<h1>1 Derivatives</h1>\n<h2", html)
    assert html_checks.static_checks(html)


def test_the_title_is_not_a_second_top_heading_when_the_file_has_one():
    html = render("Calculus", headings=(("Limits", 1), ("Rules", 2)), title_set=True)
    assert title_of(html) == "Calculus"
    assert html.count("<h1") == 1
    assert ">Limits</h1>" in html


def test_a_chosen_title_is_escaped():
    html = render('Forces <script>x</script> & "more"', title_set=True)
    assert "<script>x" not in html
    assert title_of(html) == "Forces &lt;script&gt;x&lt;/script&gt; &amp; &quot;more&quot;"


def footer(html: str) -> str:
    return html[html.index("<footer") :]


def test_no_contact_line_leaves_the_sentence_as_it_was():
    assert f"<p>{assemble.CONTACT}</p>" in footer(render("T"))


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
