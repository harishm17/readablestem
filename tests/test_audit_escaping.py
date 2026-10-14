"""Launch audit: every field a model or a PDF can write reaches the HTML escaped.

The converted file is handed to students, opened from an LMS and, in the app, shown from
our own origin. So the test fills every free-text field with markup that would run script,
load a resource or break out of an attribute, renders the document, and checks the result
by parsing it, not by looking for strings: no script-capable element, no event handler, no
URL that is not an ordinary link, and the payload present only as text.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

import pytest

from a11y_stem.core.types import (
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Link,
    Status,
    TableCell,
    TableFix,
    TableGrid,
)
from a11y_stem.pipeline.assemble import render_html, sanitize_mathml

BREAKOUT = '"><script>alert(1)</script><img src=x onerror=alert(2)><svg onload=alert(3)>'
PAYLOADS = [
    BREAKOUT,
    "'><iframe srcdoc='<script>alert(4)</script>'></iframe>",
    "</title><script>alert(5)</script>",
    "</style><script>alert(6)</script>",
    '<a href="javascript:alert(7)">x</a>',
    "]]><![CDATA[<script>alert(8)</script>",
    "&lt;script&gt; &#60;script&#62; ‮\x00\x1b[31m",
    "<math><mglyph><style><img src=x onerror=alert(9)></style></mglyph></math>",
]

HOSTILE_MATHML = [
    "<math><mi>x</mi><script>alert(1)</script></math>",
    '<math><mi onclick="alert(1)">x</mi></math>',
    '<math><mglyph src="javascript:alert(1)"/></math>',
    '<math><mi href="javascript:alert(1)">x</mi></math>',
    '<math xmlns:x="http://www.w3.org/1999/xlink"><mi x:href="javascript:alert(1)">x</mi></math>',
    '<math><annotation-xml encoding="text/html"><img src=x onerror=alert(1)></annotation-xml>'
    "</math>",
    '<math><maction actiontype="statusline#http://evil"><mi>x</mi></maction></math>',
    "<math><mtext><![CDATA[<script>alert(1)</script>]]></mtext></math>",
    '<!DOCTYPE math [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    "<math><mi>&b;</mi></math>",
    '<!DOCTYPE math [<!ENTITY x SYSTEM "file:///etc/passwd">]><math><mi>&x;</mi></math>',
    '<math><mi style="background:url(http://evil/x)">x</mi></math>',
    '<math><mstyle href="http://evil"><mi>x</mi></mstyle></math>',
]

# Elements that can run script, load something, or submit something.
FORBIDDEN_TAGS = {
    "script", "iframe", "frame", "frameset", "object", "embed", "applet", "link", "base",
    "form", "input", "button", "textarea", "select", "svg", "audio", "video", "source",
    "track", "canvas", "portal", "template", "noscript", "mglyph", "annotation-xml",
    "maction", "foreignobject", "use",
}  # fmt: skip


class Inspect(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str, str]] = []
        self.text: list[str] = []
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.in_style = tag == "style"
        self.attrs.extend((tag, k, v or "") for k, v in attrs)

    def handle_endtag(self, tag):
        self.in_style = False

    def handle_data(self, data):
        if not self.in_style:
            self.text.append(data)


def inspect(html: str) -> Inspect:
    parser = Inspect()
    parser.feed(html)
    parser.close()
    return parser


def assert_inert(html: str, where: str = "") -> Inspect:
    parsed = inspect(html)
    assert not FORBIDDEN_TAGS & set(parsed.tags), (where, FORBIDDEN_TAGS & set(parsed.tags))
    for tag, name, value in parsed.attrs:
        assert not name.startswith("on"), (where, tag, name)
        assert name not in {"srcdoc", "formaction", "action", "xlink:href", "background"}, name
        if name == "style":
            assert "url(" not in value.lower() and "expression" not in value.lower(), value
        if name == "href":
            assert tag == "a" and value.startswith(("http://", "https://", "mailto:", "#")), (
                where,
                tag,
                value[:60],
            )
        if name == "src":
            assert tag == "img" and value.startswith("data:image/png;base64,"), (where, tag, value)
        assert name != "data", (where, tag)
    assert "<script" not in html.lower().replace("&lt;script", ""), where
    assert re.search(r"<(?:img|svg|iframe)[^>]*onerror", html, re.I) is None, where
    return parsed


def el(index: int, kind: Kind, **kw) -> Element:
    return Element(
        id=f"p1-e{index}", kind=kind, page=0, bbox=(0, index, 1, index + 1), order=index, **kw
    )


def build(payload: str):
    """A document where every text field a model or a PDF controls carries `payload`."""
    cell = TableCell(row=0, col=0, text=payload, header="col")
    math_cell = TableCell(row=1, col=0, text=payload, latex=payload, mathml=HOSTILE_MATHML[0])
    grid = TableGrid(rows=2, cols=1, cells=[cell, math_cell], header_rows=1, caption=payload)
    elements = [
        el(0, Kind.HEADING, text=payload, font_size=18, bold=True),
        el(1, Kind.PARAGRAPH, text=f"{payload} ⟦p1-e2⟧ and {payload}", lang="fr"),
        el(2, Kind.EQUATION, display="inline", parent_id="p1-e1", equation_label=payload),
        el(3, Kind.EQUATION, display="block", equation_label=payload),
        el(4, Kind.FIGURE, caption=payload),
        el(5, Kind.TABLE, table=grid, caption=payload),
        el(6, Kind.LIST_ITEM, text=payload, list_marker=payload, list_ordered=False, list_level=0)
        if hasattr(Kind, "LIST_ITEM")
        else el(6, Kind.PARAGRAPH, text=payload),
    ]
    fixes = [
        Fix(element_id="p1-e0", kind=Kind.HEADING, status=Status.ACCEPTED, confidence=1.0,
            heading=HeadingFix(level=1, text=payload)),
        Fix(element_id="p1-e2", kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=0.99,
            equation=EquationFix(latex=payload, mathml=HOSTILE_MATHML[0], speech=payload,
                                 display="inline")),
        Fix(element_id="p1-e3", kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=0.99,
            equation=EquationFix(latex=payload, mathml="<math><mi>x</mi></math>", speech=payload,
                                 display="block")),
        Fix(element_id="p1-e4", kind=Kind.FIGURE, status=Status.ACCEPTED, confidence=0.99,
            figure=FigureFix(figure_type="chart", alt=payload, long_description=payload,
                             data_table=grid)),
        Fix(element_id="p1-e5", kind=Kind.TABLE, status=Status.ACCEPTED, confidence=0.99,
            table=TableFix(grid=grid)),
    ]  # fmt: skip
    return elements, fixes


@pytest.mark.parametrize("payload", PAYLOADS)
def test_every_text_field_is_escaped(payload):
    elements, fixes = build(payload)
    crops = {}
    html = render_html(
        title=payload,
        lang="en",
        elements=elements,
        fixes=fixes,
        crops=crops,
        source_note=payload,
        notice=payload,
    )
    assert_inert(html, payload[:20])
    assert html.count("<main>") == 1
    # The payload is there, as text: the heading and the title carry it escaped.
    visible = re.sub(r"[\x00-\x08\x0b-\x1f]", "", payload)  # control characters are dropped
    assert escape(visible, quote=True) in html or escape(visible, quote=False) in html


def test_the_inspector_catches_what_it_is_meant_to_catch():
    for bad in (
        "<p>ok</p><script>x</script>",
        '<p onclick="x">ok</p>',
        '<a href="javascript:alert(1)">x</a>',
        '<img src="http://evil/x.png">',
        '<p style="background:url(http://evil)">x</p>',
    ):
        with pytest.raises(AssertionError):
            assert_inert(bad)


@pytest.mark.parametrize("language", ['en"><script>alert(1)</script>', "en onload=alert(1)", ""])
def test_a_language_tag_cannot_break_out_of_the_attribute(language):
    elements, fixes = build("plain")
    html = render_html(title="t", lang=language, elements=elements, fixes=fixes, crops={})
    assert_inert(html, language)
    assert re.search(r'<html lang="[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*"', html)


def test_a_pdf_link_cannot_carry_a_script_url():
    for href in (
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>",
        "file:///x",
    ):
        with pytest.raises(ValueError):
            Link(start=0, end=1, kind="uri", href=href)
    ok = Link(start=0, end=4, kind="uri", href='https://example.org/a"b<c>')
    paragraph = el(1, Kind.PARAGRAPH, text="link text", links=[ok])
    html = render_html(title="t", lang="en", elements=[paragraph], fixes=[], crops={})
    assert_inert(html)
    assert 'href="https://example.org/a"b' not in html


@pytest.mark.parametrize("mathml", HOSTILE_MATHML)
def test_hostile_mathml_is_rebuilt_from_an_allowlist_or_refused(mathml):
    try:
        out = str(sanitize_mathml(mathml))
    except Exception:  # refusing is a fine answer
        return
    assert_inert(out, mathml[:40])
    assert "evil" not in out and "passwd" not in out
    assert "<script" not in out.lower() and "onclick" not in out and "javascript" not in out


@pytest.mark.parametrize("mathml", HOSTILE_MATHML)
def test_hostile_mathml_in_a_fix_never_reaches_the_document(mathml):
    equation = el(1, Kind.EQUATION, display="block")
    shown = EquationFix(latex="x", mathml=mathml, speech="x", display="block")
    fix = Fix(
        element_id="p1-e1", kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=1.0,
        equation=shown,
    )  # fmt: skip
    html = render_html(title="t", lang="en", elements=[equation], fixes=[fix], crops={})
    assert_inert(html, mathml[:40])
    assert "evil" not in html and "passwd" not in html


def test_the_review_screen_shows_hostile_alt_text_and_latex_as_text(tmp_path):
    from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, token, upload

    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job = job_id_from(upload(client, make_pdf(1)))
        env.run_worker()
        csrf = token(client, f"/jobs/{job}/review")
        evil = '"><script>alert(1)</script><img src=x onerror=alert(2)>'
        client.post(
            f"/jobs/{job}/review/p1-e4",
            data={"csrf_token": csrf, "action": "edit", "alt": evil},
        )
        client.post(
            f"/jobs/{job}/review/p1-e3",
            data={"csrf_token": csrf, "action": "edit", "latex": r"\href{javascript:alert(1)}{x}"},
        )
        for path in (f"/jobs/{job}/review", f"/jobs/{job}/review/list", f"/jobs/{job}", "/app"):
            page = client.get(path)
            assert "<script>alert" not in page.text, path
            assert (
                "onerror=alert" not in re.sub(r"&[#\w]+;", "", page.text) or "&lt;img" in page.text
            )
            for tag, name, _value in inspect(page.text).attrs:
                assert not name.startswith("on"), (path, tag, name)
        document = client.get(f"/jobs/{job}/view").text
        assert_inert(document)
        assert "javascript:" not in document
