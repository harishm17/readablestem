"""The encoded Canvas allowlist and the model of the sanitizer built on it.

The allowlist (`fixtures/canvas/canvas_sanitize_allowlist.json`) is read from Canvas's own
`canvas_sanitize.rb` by `fixtures/canvas/extract_allowlist.py`; these tests pin the facts the
Canvas export depends on, so a re-extraction that changes one shows here, and show that the
model removes what the sanitizer removes.
"""

from __future__ import annotations

import json

import pytest

from canvas_sanitizer import ALLOWLIST_FILE, allowlist, clean_style, parse, sanitize, serialize


def kept(html: str) -> str:
    return serialize(sanitize(parse(html)), canonical=True)


def test_the_allowlist_names_its_source():
    source = allowlist()["source"]
    assert source["repo"] == "instructure/canvas-lms"
    assert source["path"] == "gems/canvas_sanitize/lib/canvas_sanitize/canvas_sanitize.rb"
    assert len(source["commit"]) == 40 and len(source["git_blob_sha"]) == 40
    assert allowlist()["sanitize_gem"]["version"] == "7.0.0"
    assert json.loads(ALLOWLIST_FILE.read_text()) == allowlist()


def test_the_allowlist_has_the_facts_the_export_is_built_on():
    rules = allowlist()
    elements = set(rules["elements"])
    # What the export relies on surviving ...
    assert {"h1", "h6", "p", "ul", "ol", "li", "table", "caption", "th", "td", "thead"} <= elements
    assert {"figure", "figcaption", "img", "details", "summary", "footer", "a"} <= elements
    assert {"math", "mrow", "mi", "mo", "mn", "mfrac", "msub", "mtable", "mtr", "mtd"} <= elements
    # ... and what it cannot: no style element, no svg, no main, no page wrapper.
    assert (
        not {"style", "svg", "main", "html", "head", "body", "title", "meta", "script"} & elements
    )
    everywhere = set(rules["attributes"]["*"])
    assert {"style", "class", "id", "lang", "role", "aria-label", "aria-labelledby", ":data"} <= (
        everywhere
    )
    assert "tabindex" not in everywhere
    assert "scope" in rules["attributes"]["th"] and "scope" not in rules["attributes"]["td"]
    assert "headers" not in rules["attributes"]["td"] + rules["attributes"]["th"]
    assert {"alt", "src", "width", "height"} <= set(rules["attributes"]["img"])
    assert {"display", "xmlns", "alttext"} <= set(rules["attributes"]["math"])
    assert rules["protocols"]["img"] == {"src": ["http", "https", ":relative"]}
    assert "data" not in rules["protocols"]["img"]["src"]
    assert "clip" in rules["css_properties"] and "clip-path" not in rules["css_properties"]
    assert "font-weight" not in rules["css_properties"] and "font-size" in rules["css_properties"]
    assert {"style", "svg", "script"} <= set(rules["remove_contents_when_removed"])


def test_a_style_element_goes_with_its_css_and_a_wrapper_leaves_its_text():
    out = kept(
        "<html><head><title>T</title><style>p { color: red }</style></head>"
        "<body><main><p>Hello</p></main></body></html>"
    )
    assert out == "T<p>Hello</p>"  # the title's text is left behind; the CSS is not


def test_svg_goes_with_its_contents():
    assert (
        kept('<p>a</p><svg viewBox="0 0 1 1"><text>label</text></svg><p>b</p>')
        == "<p>a</p><p>b</p>"
    )


def test_an_image_survives_only_with_an_http_or_relative_source():
    assert kept('<img src="images/a.png" alt="A">') == '<img src="images/a.png" alt="A">'
    assert kept('<img src="/courses/1/file_contents/x.png" alt="A">') == (
        '<img src="/courses/1/file_contents/x.png" alt="A">'
    )
    assert kept('<img src="https://x.example/a.png" alt="A">') == (
        '<img src="https://x.example/a.png" alt="A">'
    )
    assert kept('<img src="data:image/png;base64,AAAA" alt="A">') == '<img alt="A">'
    assert kept('<img src="javascript:alert(1)" alt="A">') == '<img alt="A">'
    # a colon after a slash is a path, not a scheme
    assert kept('<img src="images/a:b.png" alt="A">') == '<img src="images/a:b.png" alt="A">'


def test_attributes_canvas_removes():
    assert kept(
        '<div tabindex="0" role="group" aria-label="x" lang="de" onclick="x()">t</div>'
    ) == ('<div role="group" aria-label="x" lang="de">t</div>')
    assert kept(
        '<table><tr><th scope="col" id="a">H</th><td headers="a" colspan="2">v</td></tr></table>'
    ) == ('<table><tr><th scope="col" id="a">H</th><td colspan="2">v</td></tr></table>')
    assert kept('<p data-reason="x" data-method="y">t</p>') == '<p data-reason="x">t</p>'


def test_mathml_survives():
    math = (
        '<math display="block" id="e1"><mrow><mi>x</mi><mo>=</mo>'
        "<mfrac><mn>1</mn><mn>2</mn></mfrac></mrow></math>"
    )
    assert kept(math) == math


def test_style_keeps_only_the_properties_canvas_lists():
    assert clean_style("color:#111;clip-path:inset(50%);font-weight:700;clip:rect(0,0,0,0)") == (
        "color: #111; clip: rect(0,0,0,0);"
    )
    assert clean_style("overflow-wrap:anywhere") == ""
    assert clean_style("background-image:url(data:image/png;base64,AA)") == ""
    assert (
        clean_style("background-image:url(images/a.png)") == "background-image: url(images/a.png);"
    )
    assert kept('<p style="overflow-wrap:anywhere">t</p>') == "<p>t</p>"


@pytest.mark.parametrize("tag", ["figure", "figcaption", "details", "summary", "footer", "section"])
def test_structural_elements_survive(tag):
    assert kept(f"<{tag}>t</{tag}>") == f"<{tag}>t</{tag}>"
