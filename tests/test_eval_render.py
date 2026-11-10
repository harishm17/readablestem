"""eval.render: MathML cleaning, the printable page, image cache, demo wrapper (offline),
plus Chromium rendering (browser marker)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pymupdf
import pytest

from readablestem.eval import render
from readablestem.eval.render import (
    ImageCache,
    PageSource,
    RenderError,
    clean_mathml,
    printable_html,
    wrap_demo_html,
)

FIXTURES = Path(__file__).parent / "fixtures" / "eval"
PAGE_XHTML = (FIXTURES / "page.xhtml").read_text(encoding="utf-8")
ARCHIVE = "https://openstax.org/apps/archive/20990101.000000"
CONTENT_URL = f"{ARCHIVE}/contents/book@v1:page.json"


def tiny_png(width: int = 8, height: int = 4) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), 0)
    pix.clear_with(200)
    return pix.tobytes("png")


def page_source(**overrides) -> PageSource:
    fields = dict(
        book_slug="physics",
        book_title="Physics",
        page_slug="9-1-rolling-balls",
        page_title="9.1 Rolling Balls",
        page_url="https://openstax.org/books/physics/pages/9-1-rolling-balls",
        content_url=CONTENT_URL,
        license_name="Creative Commons Attribution License",
        license_url="http://creativecommons.org/licenses/by/4.0/",
        xhtml=PAGE_XHTML,
        authors=["Ada Author", "Bo Writer"],
    )
    fields.update(overrides)
    return PageSource(**fields)


def fake_images(tmp_path: Path) -> ImageCache:
    served: list[str] = []

    def get(url: str) -> bytes:
        served.append(url)
        return tiny_png()

    cache = ImageCache(tmp_path / "images", get=get)
    cache.served = served  # type: ignore[attr-defined]
    return cache


# ----------------------------------------------------------------------- MathML


def test_clean_mathml_keeps_presentation_and_drops_annotations_and_scripts():
    dirty = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block" onclick="x()">'
        "<semantics><mrow><mi href='javascript:x'>v</mi><script>bad()</script><mo>=</mo>"
        "<mn>1</mn></mrow><annotation-xml><ci>v</ci></annotation-xml></semantics></math>"
    )
    cleaned = clean_mathml(dirty)
    root = ET.fromstring(cleaned)
    tags = [el.tag.rsplit("}", 1)[-1] for el in root.iter()]
    assert tags == ["math", "mrow", "mi", "mo", "mn"]
    assert root.get("display") == "block"
    assert "onclick" not in cleaned and "href" not in cleaned and "script" not in cleaned


def test_clean_mathml_refuses_entity_expansion():
    bomb = '<!DOCTYPE m [<!ENTITY a "aaaa">]><math><mi>&a;</mi></math>'
    with pytest.raises(RenderError):
        clean_mathml(bomb)


# --------------------------------------------------------------- printable page


@pytest.fixture
def printed(tmp_path) -> tuple[str, ImageCache]:
    images = fake_images(tmp_path)
    return printable_html(page_source(), images.data_uri), images


def test_printable_page_leaves_the_answer_key_out(printed):
    page_html, _ = printed
    assert "A red ball on a ramp." not in page_html  # alt text is the figure answer
    assert 'alt=""' in page_html
    assert "SECRET-ANNOTATION" not in page_html  # Content MathML annotation
    assert "<semantics" not in page_html


def test_footer_template_names_book_and_license():
    footer = render.footer_template(page_source(book_title="Physics & Co"))
    assert "Physics &amp; Co (OpenStax), CC BY 4.0" in footer
    assert 'class="pageNumber"' in footer


# ------------------------------------------------------------------- image cache


def test_image_cache_refuses_other_hosts(tmp_path):
    images = fake_images(tmp_path)
    with pytest.raises(RenderError, match=r"openstax\.org"):
        images.raw("https://example.org/x.png")
    assert images.served == []  # type: ignore[attr-defined]


# ------------------------------------------------------------------ page sources


# -------------------------------------------------------------------------- demo


def test_demo_note_goes_right_after_body_and_is_escaped():
    converted = '<!doctype html><html lang="en"><body class="x"><main><h1>T</h1></main></body>'
    page = page_source(page_title="Speed <&> Velocity")
    wrapped = wrap_demo_html(converted, page, date="2026-10-07")
    body_at = wrapped.index('<body class="x">')
    assert wrapped.index('<aside aria-label="About this demo"') > body_at
    assert wrapped.index("<aside") < wrapped.index("<main>")
    assert "Speed &lt;&amp;&gt; Velocity" in wrapped
    assert "Precomputed demo." in wrapped and "2026-10-07" in wrapped
    assert 'href="https://openstax.org/books/physics/pages/9-1-rolling-balls"' in wrapped
    assert "CC BY 4.0" in wrapped
    assert "compliant" not in wrapped.lower()


@pytest.mark.browser
def test_demo_note_does_not_widen_the_page_at_320_px(tmp_path):
    from playwright.sync_api import sync_playwright

    page = page_source()
    wrapped = wrap_demo_html(
        "<main><p>x</p></main>",
        page,
        date="2026-10-08",
        source_url="https://example.org/" + "s" * 80,
    )
    path = tmp_path / "demo.html"
    path.write_text(wrapped, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        tab = browser.new_page(viewport={"width": 320, "height": 640})
        tab.goto(path.as_uri())
        widths = tab.evaluate(
            "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
        )
        browser.close()
    assert widths[0] <= widths[1]


# ======================================================================= browser


@pytest.fixture(scope="module")
def chromium():
    with render.ChromiumRenderer() as renderer:
        yield renderer


def test_demo_note_has_its_own_link_colours_in_the_head():
    """The note is white in every scheme, so its links carry light-scheme and forced-colour
    rules (the document's `color-scheme: light dark` gave them pale blue on white)."""
    page = page_source()
    document = (
        '<!doctype html><html lang="en"><head><style>p{margin:0}</style></head>'
        "<body><main><p>x</p></main></body></html>"
    )
    wrapped = wrap_demo_html(document, page, date="2026-10-07")
    head = wrapped[: wrapped.index("</head>")]
    assert ".demo-note a:link { color: #0b3d91; }" in head
    assert ".demo-note a:visited" in head and "@media (forced-colors: active)" in head
    assert wrapped.count("<style>") == 2  # the document's own, then the note's
    assert 'class="demo-note"' in wrapped[wrapped.index("<aside") :]
    bare = wrap_demo_html("<main></main>", page, date="2026-10-07")
    assert bare.index(".demo-note a:link") < bare.index("</head>")
