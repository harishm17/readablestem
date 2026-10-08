"""assemble.render_html and sanitize_mathml: structure, escaping, flags, MathML allowlist."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import pymupdf
import pytest
from latex2mathml.converter import convert

from a11y_stem.core.types import (
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
    inline_marker,
)
from a11y_stem.pipeline.assemble import (
    ABOUT_WORDING,
    EQUATION_NOT_CONVERTED_ALT,
    FIGURE_NOT_DESCRIBED_ALT,
    render_html,
    sanitize_mathml,
)
from a11y_stem.pipeline.html_checks import static_checks

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
    assert not re.search(r"(src|href)=\"(?!data:)", sample_html)  # nothing external
    assert "compli" + "ant" not in sample_html.lower()  # rule 7, anywhere in the output


def test_tags_balance_and_no_event_handlers(sample_html):
    parsed = parse(sample_html)
    assert parsed.errors == []
    assert parsed.stack == []
    assert not [a for a in parsed.attrs if a[1].startswith("on")]


def test_passes_static_checks(sample_html):
    failed = [c for c in static_checks(sample_html) if not c.passed]
    assert failed == []


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


def test_inline_equation_not_converted_is_flagged_in_place(sample_html):
    paragraph = block(sample_html, "p1-e1")
    flagged = re.search(
        r'<span class="flagged-inline" id="p1-e3"[^>]*>.*?</span></span>', paragraph
    )
    assert flagged, paragraph
    assert 'data-status="abstained"' in flagged.group(0)
    assert f'alt="{EQUATION_NOT_CONVERTED_ALT}"' in flagged.group(0)
    assert 'src="data:image/png;base64,' in flagged.group(0)
    assert "(equation not converted)" in flagged.group(0)
    assert paragraph.endswith("is the distance travelled.</p>")


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


def test_budget_failure_reason_is_exposed_as_a_code(sample_html):
    assert 'data-reason="budget_job_usd"' in block(sample_html, "p1-e5")


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


def test_flag_reason_is_omitted_when_the_fix_has_none(sample_html):
    assert 'data-status="rejected">' in block(sample_html, "p2-e2")  # no data-reason


def test_headings_follow_fixes_and_rejected_ones_become_paragraphs(sample_html):
    assert '<h1 id="p1-e0">2.2 Speed and Velocity</h1>' in sample_html
    assert '<h2 id="p2-e0">Instantaneous velocity</h2>' in sample_html  # needs_review: kept
    assert '<p id="p2-e3">NOTE TO STUDENTS</p>' in sample_html


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


def test_outline_keeps_siblings_after_a_rejection():
    # Chapter > [Section 1, rejected] > Sub 1.1, Sub 1.2; then Section 2.
    pairs = [
        heading("a", 0, 1),
        heading("b", 1, 2, Status.REJECTED),
        heading("c", 2, 3),
        heading("d", 3, 3),
        heading("e", 4, 2),
    ]
    html = render([e for e, _ in pairs], [f for _, f in pairs])
    # Sub 1.2 stays a sibling of Sub 1.1 (not its child); Section 2 is back at h2.
    assert outline(html) == ["h1", "p", "h2", "h2", "h2"]
    assert all(c.passed for c in static_checks(html))


def test_outline_moves_a_rejected_headings_subtree_up_together():
    levels = [1, 2, 3, 4, 3, 2, 3]
    pairs = [
        heading(eid, order, level, Status.REJECTED if eid == "b" else Status.ACCEPTED)
        for order, (eid, level) in enumerate(zip("abcdefg", levels, strict=True))
    ]
    html = render([e for e, _ in pairs], [f for _, f in pairs])
    assert outline(html) == ["h1", "p", "h2", "h3", "h2", "h2", "h3"]
    assert all(c.passed for c in static_checks(html))


def test_outline_without_rejections_is_unchanged():
    levels = [1, 2, 3, 3, 2, 3, 1, 2]
    pairs = [
        heading(eid, o, lv) for o, (eid, lv) in enumerate(zip("abcdefgh", levels, strict=True))
    ]
    html = render([e for e, _ in pairs], [f for _, f in pairs])
    assert outline(html) == [f"h{level}" for level in levels]


def test_notice_is_the_first_thing_in_main_and_is_escaped():
    element, fix = heading("a", 0, 1)
    html = render([element], [fix], notice="Offline <b>test</b> run:\n placeholders.")
    after_main = html.split("<main>\n", 1)[1]
    assert after_main.startswith(
        '<p class="notice a11y-stem" role="note"><span class="flag-label">Note:</span> '
        "Offline &lt;b&gt;test&lt;/b&gt; run: placeholders.</p>\n<h1"
    )
    assert all(c.passed for c in static_checks(html))
    assert 'class="notice' not in render([element], [fix], notice="  ")


def test_heading_without_a_fix_is_a_paragraph():
    element, _ = heading("a", 0, 1)
    assert '<p id="a">H a</p>' in render([element], [])


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
        "html", "meta", "img", "h1", "p", "figure", "section", "h2", "span", "div",
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


def test_sanitizer_sets_display_and_id():
    cleaned = sanitize_mathml(convert("x"), display="block", element_id="p1-e3")
    assert cleaned == '<math display="block" id="p1-e3"><mrow><mi>x</mi></mrow></math>'


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


def test_rejected_inline_mathml_hands_its_id_to_the_flag():
    para = Element(id="p", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 9, 9), order=0, text="a ⟦e1⟧ b")
    html = render(
        [para, equation("e1", 1, parent="p")], [accepted("e1", "<math><bad/></math>")], {}
    )
    assert '<span class="flagged-inline" id="e1"' in html
    assert static_checks(html)[6].passed  # html.unique_ids


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


def test_unknown_marker_is_left_as_text():
    text = f"see {inline_marker('p9-e9')} here"
    para = Element(id="p", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 9, 9), order=0, text=text)
    assert f'<p id="p">{text}</p>' in render([para], [])


def test_duplicate_element_ids_are_used_once():
    paras = [
        Element(id="p", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 9, 9), order=i, text=f"t{i}")
        for i in range(2)
    ]
    html = render(paras, [])
    assert html.count('id="p"') == 1
    assert "<p>t1</p>" in html


def test_first_fix_for_an_element_wins_like_fix_for():
    failed = Fix(element_id="e1", kind=Kind.EQUATION, status=Status.FAILED, reason="x")
    html = render([equation("e1", 0)], [failed, accepted("e1", convert("y"))], {"e1": png()})
    assert 'data-status="failed"' in html and "<math" not in html
    assert "<li>Equations (1): 1 not converted (error or spending limit)</li>" in html


def test_a_repeated_marker_is_flagged_and_counted_once():
    text = "a ⟦e1⟧ b ⟦e1⟧ c"
    para = Element(id="p", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 9, 9), order=0, text=text)
    abstained = Fix(element_id="e1", kind=Kind.EQUATION, status=Status.ABSTAINED)
    html = render([para, equation("e1", 1, parent="p")], [abstained], {"e1": png()})
    assert html.count('class="flagged-inline"') == 2 and html.count('id="e1"') == 1
    assert "<p>1 item could not be converted. They keep the original" in html


# --------------------------------------------------------------------------- about


def test_about_section_counts_by_kind_and_status(sample_html):
    about = sample_html[sample_html.index('<section id="about-conversion"') :]
    assert '<h2 id="about-conversion-title">About this conversion</h2>' in about
    assert "<li>Headings (3): 1 accepted, 1 needs review, 1 rejected by a reviewer</li>" in about
    assert (
        "<li>Equations (5): 2 accepted, 1 not converted (model unsure), "
        "1 not converted (error or spending limit), 1 rejected by a reviewer</li>"
    ) in about
    assert "<li>Figures (3): 2 accepted, 1 not converted (model unsure)</li>" in about
    assert "<p>4 items could not be converted." in about
    assert "<p>Test note.</p>" in about


def test_about_section_without_fixes_and_defaults():
    para = Element(id="p", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 9, 9), order=0, text="x")
    html = render([para], [], title="  ", lang="")
    assert "<p>No headings, equations or figures were found.</p>" in html
    assert "<title>Converted document</title>" in html
    assert '<html lang="en">' in html
    assert "could not be converted" not in html
    assert [c for c in static_checks(html) if not c.passed] == []


def test_source_note_is_plain_escaped_text():
    html = render([], [], source_note="Adapted from <i>OpenStax</i> & friends, CC BY 4.0.")
    assert "<p>Adapted from &lt;i&gt;OpenStax&lt;/i&gt; &amp; friends, CC BY 4.0.</p>" in html
