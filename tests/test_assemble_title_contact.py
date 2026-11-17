"""The student file's title (T1-7) and contact line (T1-17), as the assembler writes them."""

from __future__ import annotations

import io
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
    content = re.sub(r'<section id="checked-notice".*?</section>\n', "", html, flags=re.S)
    assert re.search(r"<main>\n<h1>1 Derivatives</h1>\n<h2", content)  # first, after the notice
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


def test_every_outline_of_up_to_four_headings_has_exactly_one_h1():
    """T3-13 and SW6c, exhaustively: whatever the repaired outline, the file has one h1."""
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
            assert html.count("<h1") == 1, spec


@pytest.mark.parametrize(
    ("title", "headings", "h1"),
    [
        # The title is the first heading: that heading is the h1, said once.
        ("Kinematics", (("1 Kinematics", 1), ("Position", 2), ("Forces", 1)), "1 Kinematics"),
        ("KINEMATICS.", (("Kinematics", 1), ("Position", 2), ("Forces", 1)), "Kinematics"),
        ("kinematics", (("1 Kinematics", 1), ("Position", 2)), "1 Kinematics"),
        # One top-level heading that is not the title, none, or several.
        ("Physics notes", (("Kinematics", 1), ("Position", 2)), "Kinematics"),
        ("Kinematics", (("Position", 2), ("Velocity", 2)), "Kinematics"),
        ("Physics notes", (("Motion", 1), ("Forces", 1)), "Physics notes"),
    ],
    ids=["equal", "equal-case", "equal-one-top", "one-top", "none", "several"],
)
def test_the_title_is_one_h1_said_once_in_every_output(title, headings, h1):
    """SW6c (F-255, F-568): exactly one H1, the page's title, and the title is the text of
    exactly one heading in the page, the Word file, the EPUB and the Listen transcript."""
    from readablestem.pipeline import export_docx, export_epub
    from readablestem.web import documents, listen

    html = render(title, headings=headings)

    def words(text: str) -> list[str]:  # "1 Kinematics" and "KINEMATICS." are one title
        return re.findall(r"[a-z]+", text.casefold())

    def said(texts: list[str]) -> int:
        return sum(words(text) == words(h1) for text in texts)

    page = headings_of(html)
    assert [level for level, _ in page].count(1) == 1 and said([t for _, t in page]) == 1
    content = export_epub.export_epub(html).content
    book = headings_of(content)
    assert [level for level, _ in book].count(1) == 1 and said([t for _, t in book]) == 1
    heard = [i for i in listen.build_transcript(html) if i.role.startswith("Heading level ")]
    assert [i.role for i in heard].count("Heading level 1") == 1
    assert said([i.text for i in heard]) == 1
    if export_docx.available():  # the Word file's title as the download names it
        import docx

        made = export_docx.export_docx(html, title=documents.display_title(title, "notes.pdf"))
        styled = [(p.style.name, p.text) for p in docx.Document(io.BytesIO(made.data)).paragraphs]
        named = [text for style, text in styled if style == "Title" or "Heading" in style]
        assert [style for style, _ in styled].count("Heading 1") == 1 and said(named) == 1
    assert title_of(html) == h1  # the page title is the H1's text


def test_a_chosen_title_is_escaped():
    html = render('Forces <script>x</script> & "more"', title_set=True)
    assert "<script>x" not in html
    assert title_of(html) == "Forces &lt;script&gt;x&lt;/script&gt; &amp; &quot;more&quot;"


def footer(html: str) -> str:
    """The student note, which holds the contact line (inside `<main>`, after the content)."""
    return html[html.index('<section id="about-conversion"') :]


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
