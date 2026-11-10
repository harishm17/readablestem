"""assemble.render_html and sanitize_mathml: structure, escaping, flags, MathML allowlist."""

from __future__ import annotations

import json
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import pymupdf
import pytest
from latex2mathml.converter import convert

from readablestem.core.types import (
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
)
from readablestem.pipeline.assemble import (
    ABOUT_TITLE,
    ABOUT_WORDING,
    CHECKED,
    CONTACT,
    EQUATION_NOT_CONVERTED_ALT,
    FIGURE_NOT_DESCRIBED_ALT,
    FLAGGED_NOTE,
    MATHS_NOTE,
    NOT_CHECKED_YET,
    render_html,
    sanitize_mathml,
)
from readablestem.pipeline.html_checks import static_checks

FIXTURE = Path(__file__).parent / "fixtures" / "assemble" / "sample_document.json"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}


def png(width: int = 24, height: int = 12) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.set_rect(pixmap.irect, (40, 40, 40))
    return pixmap.tobytes("png")


def load_sample() -> tuple[str, str, list[Element], list[Fix], dict[str, bytes]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    elements = [Element.model_validate(e) for e in data["elements"]]
    fixes = [Fix.model_validate(f) for f in data["fixes"]]
    crops = {e.id: png() for e in elements if e.kind in (Kind.EQUATION, Kind.FIGURE)}
    return data["title"], data["lang"], elements, fixes, crops


def render(
    elements, fixes, crops=None, *, title="T", lang="en", source_note=None, notice=None
) -> str:
    return render_html(
        title=title,
        lang=lang,
        elements=elements,
        fixes=fixes,
        crops=crops or {},
        source_note=source_note,
        notice=notice,
    )


@pytest.fixture
def sample_html() -> str:
    title, lang, elements, fixes, crops = load_sample()
    return render(elements, fixes, crops, title=title, lang=lang, source_note="Test note.")


def block(html: str, element_id: str) -> str:
    """The outer element carrying `id`, up to the end of its line (one item per line)."""
    match = re.search(rf'<[^>]+ id="{re.escape(element_id)}"[^\n]*', html)
    assert match, f"no element with id {element_id}"
    return match.group(0)


class TagBalance(HTMLParser):
    """Every non-void start tag is closed, in order (our output never relies on implied ends)."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.attrs: list[tuple[str, str, str | None]] = []

    def handle_starttag(self, tag, attrs):
        self.attrs.extend((tag, k, v) for k, v in attrs)
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> (open: {self.stack[-3:]})")
        else:
            self.stack.pop()


def parse(html: str) -> TagBalance:
    parser = TagBalance()
    parser.feed(html)
    parser.close()
    return parser


# --------------------------------------------------------------------------- document


def test_document_skeleton(sample_html):
    assert sample_html.startswith("<!doctype html>\n")
    for needle in (
        '<html lang="en">',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Speed and Velocity</title>",
        "<style>",
        ABOUT_WORDING,
    ):
        assert needle in sample_html
    assert sample_html.count("<main>") == 1
    assert "<script" not in sample_html.lower()
    assert not re.search(r"(src|href)=\"(?!data:|#)", sample_html)  # nothing external
    assert "compli" + "ant" not in sample_html.lower()  # rule 7, anywhere in the output


def test_content_follows_reading_order_not_list_order():
    _, _, elements, fixes, crops = load_sample()
    html = render(list(reversed(elements)), fixes, crops)
    ids = [e.id for e in sorted(elements, key=lambda e: e.order)]
    positions = [html.find(f'id="{i}"') for i in ids if f'id="{i}"' in html]
    assert positions == sorted(positions)
    assert len(positions) >= 10


def test_inline_markers_become_inline_maths(sample_html):
    assert "⟦" not in sample_html and "⟧" not in sample_html
    paragraph = block(sample_html, "p1-e1")
    assert paragraph.startswith('<p id="p1-e1">Average speed is distance over time, <math')
    assert '<math display="inline" id="p1-e2"><mrow><mi>v</mi><mo>=</mo><mfrac>' in paragraph
    assert "</math>, where " in paragraph
    assert sample_html.count('id="p1-e2"') == 1  # not rendered again at its own position


def test_block_equation_is_display_block_mathml(sample_html):
    equation = block(sample_html, "p1-e4")
    assert equation.startswith('<div class="equation" id="p1-e4"><math display="block">')
    assert '<mover><mrow><mi>v</mi></mrow><mo stretchy="true">' in equation  # \bar{v}
    assert "<img" not in equation


@pytest.mark.parametrize(
    ("element_id", "status", "alt", "label"),
    [
        ("p1-e5", "failed", EQUATION_NOT_CONVERTED_ALT, "Not converted."),
        ("p2-e2", "rejected", EQUATION_NOT_CONVERTED_ALT, "Not converted."),
        ("p1-e7", "abstained", FIGURE_NOT_DESCRIBED_ALT, "Needs a description."),
    ],
)
def test_not_converted_items_keep_the_original_with_a_visible_note(
    sample_html, element_id, status, alt, label
):
    item = block(sample_html, element_id)
    assert item.startswith(f'<div class="flagged" id="{element_id}" data-status="{status}"')
    assert 'src="data:image/png;base64,' in item
    assert f'alt="{alt}"' in item
    assert 'role="note"' in item
    assert f'<span class="flag-label">{label}</span>' in item  # text, not colour alone
    assert "<math" not in item  # a rejected proposal is never shown


def test_figure_with_alt_text_and_caption(sample_html):
    figure = block(sample_html, "p1-e6")
    assert figure.startswith('<figure id="p1-e6"><img src="data:image/png;base64,')
    assert 'alt="Line graph of position against time: a straight line rising' in figure
    assert "<figcaption>Figure 2.5 Position against time" in figure
    assert 'width="437" height="267"' in figure  # printed size: 328 x 200 pt in CSS px


def test_flagged_figure_keeps_its_caption(sample_html):
    assert "<figcaption>Figure 2.6 A cart on a ramp" in block(sample_html, "p1-e7")


def test_decorative_image_without_caption_is_not_an_empty_figure(sample_html):
    item = block(sample_html, "p2-e4")
    assert item.startswith('<div class="decorative" id="p2-e4"><img src="data:image/png')
    assert 'alt=""' in item and "<figure" not in item


def test_headings_follow_fixes_and_rejected_ones_become_paragraphs(sample_html):
    assert '<h1 id="p1-e0">2.2 Speed and Velocity</h1>' in sample_html
    assert '<h2 id="p2-e0">Instantaneous velocity</h2>' in sample_html  # needs_review: kept
    # A rejected bold heading is bold text, as in the PDF ("Make them bold text").
    assert '<p id="p2-e3"><strong>NOTE TO STUDENTS</strong></p>' in sample_html


def heading(eid: str, order: int, level: int, status=Status.ACCEPTED) -> tuple[Element, Fix]:
    element = Element(
        id=eid, kind=Kind.HEADING, page=0, bbox=(0, 0, 10, 10), order=order, text=f"H {eid}"
    )
    fix = Fix(
        element_id=eid,
        kind=Kind.HEADING,
        status=status,
        heading=HeadingFix(level=level, text=f"H {eid}"),
    )
    return element, fix


def test_outline_never_skips_a_level_after_a_rejection():
    pairs = [
        heading("a", 0, 1),
        heading("b", 1, 2, Status.REJECTED),
        heading("c", 2, 3),
        heading("d", 3, 5),
    ]
    html = render([e for e, _ in pairs], [f for _, f in pairs])
    assert re.findall(r'<h(\d) id="[a-d]"', html) == ["1", "2", "3"]
    assert static_checks(html)[3].passed  # html.heading_order


def outline(html: str) -> list[str]:
    return [tag for tag, _ in re.findall(r'<(h\d|p) id="([a-z])"', html)]


# --------------------------------------------------------------------------- escaping


def test_hostile_text_is_escaped_everywhere():
    elements = [
        Element(
            id="p1-e0",
            kind=Kind.HEADING,
            page=0,
            bbox=(0, 0, 9, 9),
            order=0,
            text="</h1><script>alert(1)</script>",
        ),
        Element(
            id="p1-e1",
            kind=Kind.PARAGRAPH,
            page=0,
            bbox=(0, 0, 9, 9),
            order=1,
            text="<script>alert('p')</script><img src=x onerror=alert(2)> & \x00\x07",
        ),
        Element(
            id="p1-e2",
            kind=Kind.FIGURE,
            page=0,
            bbox=(0, 0, 9, 9),
            order=2,
            caption="<b onclick=alert(3)>Figure</b>",
        ),
    ]
    fixes = [
        Fix(
            element_id="p1-e0",
            kind=Kind.HEADING,
            status=Status.ACCEPTED,
            heading=HeadingFix(level=1, text="<script>alert('h')</script>"),
        ),
        Fix(
            element_id="p1-e2",
            kind=Kind.FIGURE,
            status=Status.ACCEPTED,
            figure=FigureFix(figure_type="photo", alt='"><img src=x onerror=alert(4)>'),
        ),
    ]
    html = render(
        elements,
        fixes,
        {"p1-e2": png()},
        title="</title><script>alert(5)</script>",
        lang='en" onload="alert(6)',
        source_note="<a href='javascript:alert(7)'>source</a>",
    )
    parsed = parse(html)
    assert parsed.errors == []
    assert "<script" not in html.lower()
    assert not [a for a in parsed.attrs if a[1].startswith("on")]
    assert {tag for tag, _, _ in parsed.attrs} <= {
        "html", "meta", "img", "h1", "p", "figure", "footer", "h2", "span", "div", "a",
    }  # fmt: skip
    assert "&lt;script&gt;alert(&#x27;p&#x27;)&lt;/script&gt;" in html
    assert 'alt="&quot;&gt;&lt;img src=x onerror=alert(4)&gt;"' in html
    assert "\x00" not in html and "\x07" not in html
    assert '<html lang="en">' in html
    assert [c for c in static_checks(html) if not c.passed] == []


# --------------------------------------------------------------------------- MathML


def shape(mathml: str) -> list[tuple[str, str]]:
    """(local tag, stripped text) for every element: what the maths says."""
    root = ElementTree.fromstring(mathml)
    return [(el.tag.split("}")[-1], (el.text or "").strip()) for el in root.iter()]


@pytest.mark.parametrize(
    "latex",
    [
        r"v = \frac{d}{t}",
        r"\bar{v} = \frac{\Delta x}{\Delta t}",
        r"\sqrt[3]{x^2 + y_1^2}",
        r"\left( \frac{a}{b} \right)",
        r"\begin{array}{rcl} a & = & b \\ c & = & d \end{array}",
        r"\sum_{i=1}^{n} i^2 = \int_0^\infty e^{-x}\,dx",
        r"\mathbf{F} = m\mathbf{a}",
        r"\boxed{E = mc^2}",
        r"{}^{14}_{6}\mathrm{C}",
    ],
)
def test_sanitizer_keeps_converter_output_intact(latex):
    original = convert(latex, display="block")
    cleaned = sanitize_mathml(original, display="block")
    assert cleaned is not None
    assert shape(cleaned) == shape(original)


@pytest.mark.parametrize(
    ("mathml", "expected"),
    [
        ('<math><mi href="javascript:alert(1)">x</mi></math>', "<mi>x</mi>"),
        ('<math onload="alert(1)" display="block"><mi>x</mi></math>', "<mi>x</mi>"),
        (
            '<math><maction actiontype="statusline"><mi>x</mi><mtext>tip</mtext></maction></math>',
            "<mi>x</mi>",
        ),
        (
            "<math><semantics><mi>x</mi><annotation-xml encoding='text/html'>"
            "<script>alert(1)</script></annotation-xml></semantics></math>",
            "<mi>x</mi>",
        ),
        (
            '<math><mi style="color:white" class="c" id="p1-e0" mathcolor="#fff" '
            'mathbackground="#fff" onclick="x">x</mi></math>',
            "<mi>x</mi>",
        ),
        (
            '<math><mspace width="javascript:alert(1)"/><mi>x</mi></math>',
            "<mspace></mspace><mi>x</mi>",
        ),
        (
            "<math><mtext>&lt;script&gt;alert(1)&lt;/script&gt;</mtext></math>",
            "<mtext>&lt;script&gt;alert(1)&lt;/script&gt;</mtext>",
        ),
        ("<math><mi>x</mi><!-- a comment --><?pi data?></math>", "<mi>x</mi>"),
    ],
)
def test_sanitizer_strips_what_is_not_allowed(mathml, expected):
    cleaned = sanitize_mathml(mathml, display="inline")
    assert cleaned == f'<math display="inline">{expected}</math>'


@pytest.mark.parametrize(
    "mathml",
    [
        "<math><mi>x</mi><script>alert(1)</script></math>",
        '<math><mtext><img xmlns="http://www.w3.org/1999/xhtml" src="x" onerror="a()"/></mtext>'
        "</math>",
        '<math><mi>x</mi><svg xmlns="http://www.w3.org/2000/svg"><a href="x">y</a></svg></math>',
        '<!DOCTYPE math [<!ENTITY a "aaaa">]><math><mi>&a;</mi></math>',
        '<!DOCTYPE math SYSTEM "http://example.org/x.dtd"><math><mi>x</mi></math>',
        "<math><mi>&nbsp;</mi></math>",
        "<math><mi>x</mi>",
        "<svg><mi>x</mi></svg>",
        '<math xmlns="http://example.org/not-mathml"><mi>x</mi></math>',
        "<math><mrow></mrow><mspace/></math>",
        "<math><mi>x</mi><math><mi>y</mi></math></math>",
        "<math><mfenced><mi>x</mi></mfenced></math>",
        "<math>" + "<mrow>" * 200 + "<mi>x</mi>" + "</mrow>" * 200 + "</math>",
        "not xml at all",
        "",
    ],
)
def test_sanitizer_rejects_unusable_mathml(mathml):
    assert sanitize_mathml(mathml, display="block") is None


def equation(eid: str, order: int, *, parent: str | None = None, text=None) -> Element:
    return Element(
        id=eid,
        kind=Kind.EQUATION,
        page=0,
        bbox=(0, 0, 30, 12),
        order=order,
        display="inline" if parent else "block",
        parent_id=parent,
        text=text,
    )


def accepted(eid: str, mathml: str) -> Fix:
    return Fix(
        element_id=eid,
        kind=Kind.EQUATION,
        status=Status.ACCEPTED,
        confidence=0.99,
        equation=EquationFix(latex="x", mathml=mathml, speech="x"),
    )


def test_unsafe_mathml_in_an_accepted_fix_falls_back_to_the_original():
    html = render(
        [equation("e1", 0)],
        [accepted("e1", "<math><mi>x</mi><script>alert(1)</script></math>")],
        {"e1": png()},
    )
    item = block(html, "e1")
    assert 'data-status="accepted" data-reason="mathml_rejected"' in item
    assert f'alt="{EQUATION_NOT_CONVERTED_ALT}"' in item
    assert "<script" not in html and "<math" not in html


def test_missing_crop_shows_extracted_text_and_says_so():
    html = render(
        [equation("e1", 0, text="v = d / t")],
        [Fix(element_id="e1", kind=Kind.EQUATION, status=Status.FAILED, reason="crop_error")],
    )
    item = block(html, "e1")
    assert '<p class="original-text">v = d / t</p>' in item
    assert "The original image is not available." in item
    assert "<img" not in item


def test_inline_equation_without_its_marker_is_rendered_at_its_own_position():
    para = Element(id="p", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 9, 9), order=0, text="ab")
    html = render([para, equation("e1", 1, parent="p")], [accepted("e1", convert("y"))])
    assert '<div class="equation" id="e1"><math display="block">' in html


# --------------------------------------------------------------------------- about


def about(html: str) -> str:
    start = html.index('<footer id="about-conversion"')
    return html[start : html.index("</footer>", start)]


def test_the_student_note_comes_after_main_and_speaks_to_students(sample_html):
    """The note is for the people who read the file (MF7 of the UX review): what it is,
    whether the instructor has checked it, who to tell. No counts and no review words."""
    note = about(sample_html)
    assert sample_html.index("</main>") < sample_html.index('<footer id="about-conversion"')
    # Its title labels the footer region; it is not a heading in the outline (T3-13).
    assert f'<p id="about-conversion-title" class="about-title">{ABOUT_TITLE}</p>' in note
    assert 'aria-labelledby="about-conversion-title"' in note and "<h" not in note
    assert ABOUT_WORDING in note and MATHS_NOTE in note and CONTACT in note
    assert NOT_CHECKED_YET in note  # the sample has decisions still open
    assert FLAGGED_NOTE in note
    assert "<p>Test note.</p>" in note
    for word in ("needs review", "rejected", "accepted", "reviewer", "("):
        assert word not in note.lower(), word
    text = re.sub(r"<[^>]+>", "", note).replace("readablestem", "")
    assert not re.search(r"\d", text)  # no counts


def test_the_note_says_checked_with_the_date_once_every_decision_is_made():
    pairs = [heading("a", 0, 1), heading("b", 1, 2, Status.NEEDS_REVIEW)]
    elements, fixes = [e for e, _ in pairs], [f for _, f in pairs]
    draft = render_html(title="T", lang="en", elements=elements, fixes=fixes, crops={},
                        checked_on=date(2026, 10, 9))  # fmt: skip
    assert NOT_CHECKED_YET in about(draft) and "2026" not in about(draft)
    fixes[1].status, fixes[1].reviewed = Status.ACCEPTED, True
    done = render_html(title="T", lang="en", elements=elements, fixes=fixes, crops={},
                       checked_on=date(2026, 10, 9))  # fmt: skip
    assert f"<p>{CHECKED} on October 9, 2026.</p>" in about(done)
    undated = render(elements, fixes)
    assert f"<p>{CHECKED}.</p>" in about(undated)


def test_about_section_without_fixes_and_defaults():
    para = Element(id="p", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 9, 9), order=0, text="x")
    html = render([para], [], title="  ", lang="")
    assert ABOUT_WORDING in html and MATHS_NOTE not in html  # no equations, no maths line
    assert "<title>Converted document</title>" in html
    assert '<html lang="en">' in html
    assert "could not be converted" not in html
    assert [c for c in static_checks(html) if not c.passed] == []
