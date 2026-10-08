"""eval.render: MathML cleaning, the printable page, image cache, demo wrapper (offline),
plus Chromium rendering (browser marker)."""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import pymupdf
import pytest

from a11y_stem.eval import render
from a11y_stem.eval.render import (
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
BALL_URL = f"{ARCHIVE}/resources/ball123"
FRACTION = '<math display="block"><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math>'


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


def test_clean_mathml_refuses_non_math_and_broken_xml():
    with pytest.raises(RenderError, match="not a <math>"):
        clean_mathml("<p>hi</p>")
    with pytest.raises(RenderError, match="well-formed"):
        clean_mathml("<math><mi>x</math>")


def test_clean_mathml_refuses_entity_expansion():
    bomb = '<!DOCTYPE m [<!ENTITY a "aaaa">]><math><mi>&a;</mi></math>'
    with pytest.raises(RenderError):
        clean_mathml(bomb)


# --------------------------------------------------------------- printable page


@pytest.fixture
def printed(tmp_path) -> tuple[str, ImageCache]:
    images = fake_images(tmp_path)
    return printable_html(page_source(), images.data_uri), images


def test_printable_page_keeps_headings_at_their_levels(printed):
    page_html, _ = printed
    for tag, text in [
        ("h2", "Rolling Balls"),
        ("h3", "Section Learning Objectives"),
        ("h3", "Measuring Speed"),
        ("h4", "Inside a Box"),
    ]:
        assert f"<{tag}>" in page_html and text in page_html


def test_printable_page_leaves_the_answer_key_out(printed):
    page_html, _ = printed
    assert "A red ball on a ramp." not in page_html  # alt text is the figure answer
    assert 'alt=""' in page_html
    assert "SECRET-ANNOTATION" not in page_html  # Content MathML annotation
    assert "<semantics" not in page_html


def test_printable_page_drops_scripts_styles_iframes_and_links(printed):
    page_html, _ = printed
    assert "window.leak" not in page_html
    assert "color: red" not in page_html
    assert "<iframe" not in page_html and "example.org/embed" not in page_html
    assert "short video" in page_html and "openstax.org/l/video" not in page_html


def test_printable_page_has_maths_figures_captions_and_boxes(printed):
    page_html, images = printed
    assert page_html.count("<math") == 2
    assert re.search(r'<div class="equation">\s*<math display="block">', page_html)
    assert re.search(r"<figure>\s*<img src=\"data:image/png;base64,", page_html)
    assert "<figcaption>" in page_html and "A ball rolls down a ramp." in page_html
    assert 'class="box"' in page_html
    assert '<p class="label">Solution</p>' in page_html
    assert images.served == [BALL_URL]  # type: ignore[attr-defined]


def test_printable_page_footer_attributes_openstax(printed):
    page_html, _ = printed
    footer = page_html.split("<footer>", 1)[1]
    assert "from Physics by Ada Author, Bo Writer, OpenStax" in footer
    assert "CC BY 4.0" in footer
    assert 'href="http://creativecommons.org/licenses/by/4.0/"' in footer
    assert "https://openstax.org/books/physics/pages/9-1-rolling-balls" in footer
    assert "Access for free at openstax.org." in footer
    assert "not affiliated with or endorsed by OpenStax" in footer


def test_printable_page_escapes_text(tmp_path):
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><div data-type="page">'
        "<h2>A &lt;b&gt; &amp; c</h2></div></body></html>"
    )
    page_html = printable_html(page_source(xhtml=xhtml, page_title="T <i>"), lambda url: None)
    assert "<h2>A &lt;b&gt; &amp; c</h2>" in page_html
    assert "<title>T &lt;i&gt;</title>" in page_html


def test_footer_template_names_book_and_license():
    footer = render.footer_template(page_source(book_title="Physics & Co"))
    assert "Physics &amp; Co (OpenStax), CC BY 4.0" in footer
    assert 'class="pageNumber"' in footer


# ------------------------------------------------------------------- image cache


def test_image_cache_fetches_once_and_converts_to_png(tmp_path):
    images = fake_images(tmp_path)
    first = images.png(BALL_URL)
    second = images.png(BALL_URL)
    assert first.startswith(b"\x89PNG") and first == second
    assert images.served == [BALL_URL]  # type: ignore[attr-defined]
    assert images.downloads == 1


def test_image_cache_refuses_other_hosts(tmp_path):
    images = fake_images(tmp_path)
    with pytest.raises(RenderError, match=r"openstax\.org"):
        images.raw("https://example.org/x.png")
    assert images.served == []  # type: ignore[attr-defined]


def test_png_bytes_caps_the_long_side():
    pix = pymupdf.Pixmap(render.png_bytes(tiny_png(300, 100), max_side_px=150))
    assert (pix.width, pix.height) == (150, 50)


def test_png_bytes_rejects_garbage():
    with pytest.raises(RenderError, match="decoded"):
        render.png_bytes(b"not an image")


# ------------------------------------------------------------------ page sources


class CacheFetcher:
    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads

    def get_json(self, url: str):
        return self.payloads[url]


def test_page_source_from_records_uses_the_cached_archive_page():
    record = {
        "book_slug": "physics",
        "book_title": "Physics",
        "page_slug": "9-1-rolling-balls",
        "page_title": "9.1 Rolling Balls",
        "content_url": CONTENT_URL,
        "source_url": "https://openstax.org/books/physics/pages/9-1-rolling-balls#Figure_R",
        "license_name": "CC BY",
        "license_url": "http://creativecommons.org/licenses/by/4.0/",
    }
    fetcher = CacheFetcher({CONTENT_URL: {"content": PAGE_XHTML}})
    page = render.page_source_from_records([record], fetcher, authors=["Ada Author"])
    assert page.xhtml == PAGE_XHTML
    assert page.page_url.endswith("9-1-rolling-balls")  # fragment dropped
    assert page.authors == ["Ada Author"]


def test_page_source_from_records_explains_a_missing_page():
    record = {"content_url": CONTENT_URL, "page_slug": "x"}
    with pytest.raises(RenderError, match="not cached"):
        render.page_source_from_records([record], CacheFetcher({}))


def test_render_page_pdf_accepts_gold_records(tmp_path):
    record = {
        "book_slug": "physics",
        "book_title": "Physics",
        "page_slug": "9-1-rolling-balls",
        "page_title": "9.1 Rolling Balls",
        "content_url": CONTENT_URL,
        "source_url": "https://openstax.org/books/physics/pages/9-1-rolling-balls",
        "license_name": "CC BY",
        "license_url": "http://creativecommons.org/licenses/by/4.0/",
    }
    printed = []

    class StubPrinter(render.ChromiumRenderer):
        def pdf(self, page_html, *, paper="Letter", footer_html=""):
            printed.append((page_html, paper, footer_html))
            return b"%PDF-stub"

    out = tmp_path / "out.pdf"
    pdf = render.render_page_pdf(
        [record],
        out,
        images=fake_images(tmp_path),
        renderer=StubPrinter(),
        fetcher=CacheFetcher({CONTENT_URL: {"content": PAGE_XHTML}}),
        authors=["Ada Author"],
        paper="A4",
    )
    assert pdf == out.read_bytes() == b"%PDF-stub"
    (page_html, paper, footer) = printed[0]
    assert paper == "A4" and "Rolling Balls" in page_html and "by Ada Author" in page_html
    assert "Access for free at openstax.org" in footer


def test_book_authors_read_from_gold(tmp_path):
    book = tmp_path / "openstax" / "physics"
    book.mkdir(parents=True)
    (book / "_book.json").write_text('{"authors": ["Ada Author"]}')
    assert render.book_authors(tmp_path, "physics") == ["Ada Author"]
    assert render.book_authors(tmp_path, "nope") == []


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


def test_demo_links_back_to_the_app_and_to_the_source_and_marks_placeholders():
    page = page_source()
    wrapped = wrap_demo_html(
        "<main></main>", page, date="2026-10-07", source_url="https://example.org/src"
    )
    assert '<a href="/">Back to a11y-stem</a>' in wrapped
    assert '<a href="https://example.org/src">' in wrapped and "Affero" in wrapped
    assert "data-a11y-stem-demo" not in wrapped
    fake = wrap_demo_html("<main></main>", page, date="2026-10-07", placeholder=True)
    assert 'data-a11y-stem-demo="placeholder"' in fake and "Placeholder output." in fake


def test_demo_note_links_say_where_they_go_not_raw_urls():
    """A raw URL as link text is poor link purpose (WCAG 2.4.4) and, unbroken, it widened
    the real demo to 401 px at a 320 px viewport (WCAG 1.4.10)."""
    page = page_source()
    wrapped = wrap_demo_html(
        "<main></main>", page, date="2026-10-08", source_url="https://example.org/src"
    )
    note = wrapped[wrapped.index("<aside") : wrapped.index("</aside>")]
    link_texts = re.findall(r"<a [^>]*>(.*?)</a>", note)
    assert link_texts and not any("://" in text for text in link_texts)
    assert "the original page on openstax.org" in link_texts
    assert "overflow-wrap:anywhere" in note


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


def test_demo_wraps_a_fragment_in_a_document():
    wrapped = wrap_demo_html("<main><p>x</p></main>", page_source(), date="2026-10-07")
    assert wrapped.startswith("<!doctype html>")
    assert '<html lang="en">' in wrapped and "<title>Demo: 9.1 Rolling Balls</title>" in wrapped


def test_render_equations_uses_the_cache_without_a_browser(tmp_path):
    record = {"id": "eq-1", "kind": "equation", "mathml": "<math><mi>x</mi></math>"}
    cache = tmp_path / "eq"
    cache.mkdir()
    key = render._sha256(record["mathml"])
    (cache / f"{key}-2x-{render.RENDER_VERSION}.png").write_bytes(b"cached")

    class NoBrowser(render.ChromiumRenderer):
        def _start(self) -> None:
            raise AssertionError("browser started on a cache hit")

    out = render.render_equations([record], cache, renderer=NoBrowser())
    assert out == {"eq-1": b"cached"}


def test_equation_contexts_mirror_what_convert_pdf_sends():
    contexts = render.equation_contexts(PAGE_XHTML)
    assert [tokens for tokens, _ in contexts] == ["vSECRET-ANNOTATION", "v=dt"]
    # Inline, inside a paragraph: the paragraph around "[this equation]".
    assert contexts[0][1] == "Speed is [this equation] and we watch a short video."
    # Display: the previous and next text blocks (the figure caption is not one; the
    # paragraph's own inline equation shows as "[equation]").
    assert contexts[1][1] == (
        "Speed is [equation] and we watch a short video.\n[this equation]\nWorked Example"
    )


def test_equation_contexts_window_long_text_and_handle_lone_equations():
    long_before = "word " * 100
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><div data-type="page">'
        f"<h2>Title</h2><p>{long_before}<math><mi>x</mi></math> after.</p>"
        "<p><math><mi>y</mi></math></p><ul><li><p>Next item.</p></li></ul>"
        "</div></body></html>"
    )
    (_, first), (_, alone) = render.equation_contexts(xhtml)
    assert (
        first is not None
        and first.startswith("... word")
        and first.endswith("[this equation] after.")
    )
    assert len(first) < 300 + len("... [this equation] after.") + 1
    # An inline equation alone in its paragraph gets its neighbours instead: the end of
    # the previous block (its equation shown as "[equation]") and the start of the next.
    previous = " ".join(long_before.split()) + " [equation] after."
    assert alone == f"... {previous[-300:].lstrip()}\n[this equation]\nNext item."


def test_equation_contexts_refuse_unparseable_pages():
    with pytest.raises(RenderError):
        render.equation_contexts("<html><p>unclosed")


def test_math_tokens_identify_an_equation():
    assert render.math_tokens(FRACTION) == "v=dt"
    with pytest.raises(RenderError):
        render.math_tokens("<math><mi>x</mi>")


def test_pdf_text_collapses_the_text_layer():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "v =\n d / t")
    assert render.pdf_text(doc.tobytes()) == "v = d / t"


def test_render_equation_text_uses_the_cache_without_a_browser(tmp_path):
    key = render._sha256(FRACTION)
    (tmp_path / f"{key}-text-{render.RENDER_VERSION}.txt").write_text("", encoding="utf-8")

    class NoBrowser(render.ChromiumRenderer):
        def _start(self) -> None:
            raise AssertionError("browser started on a cache hit")

    # A cached empty text layer means "the PDF has none": None, as layout would give.
    assert render.render_equation_text(FRACTION, tmp_path, renderer=NoBrowser()) is None


def test_playwright_failures_become_render_errors():
    """Regression: a Playwright timeout escaped as a traceback instead of RenderError."""
    sync_api = pytest.importorskip("playwright.sync_api")

    class TimingOut:
        def new_context(self, **_):
            raise sync_api.TimeoutError("Timeout 30000ms exceeded")

    renderer = render.ChromiumRenderer()
    renderer._browser = TimingOut()
    try:
        with pytest.raises(RenderError, match=r"Chromium could not open a page \(TimeoutError\)"):
            renderer.equation_png(FRACTION)
    finally:
        renderer._browser = None
    other = render.ChromiumRenderer()
    other._browser = type("Broken", (), {"new_context": lambda self, **_: 1 / 0})()
    try:
        with pytest.raises(ZeroDivisionError):  # not a Playwright error: left alone
            other.pdf("<p>x</p>")
    finally:
        other._browser = None


# ======================================================================= browser


@pytest.fixture(scope="module")
def chromium():
    with render.ChromiumRenderer() as renderer:
        yield renderer


@pytest.mark.browser
def test_render_equation_png_is_a_tight_deterministic_crop(chromium):
    png = render.render_equation_png(FRACTION, renderer=chromium)
    again = render.render_equation_png(FRACTION, renderer=chromium)
    assert png.startswith(b"\x89PNG")
    pix = pymupdf.Pixmap(png)
    assert (pix.width, pix.height) == (pymupdf.Pixmap(again).width, pymupdf.Pixmap(again).height)
    assert png == again
    # Tight: a short equation is far smaller than the 1280 px default viewport.
    assert 20 < pix.width < 300 and 20 < pix.height < 200
    # White background: the corner pixel is white (padding), not transparent.
    assert pix.pixel(0, 0)[:3] == (255, 255, 255)


@pytest.mark.browser
def test_render_equation_png_scale_doubles_pixels(chromium):
    one = pymupdf.Pixmap(render.render_equation_png(FRACTION, scale=1, renderer=chromium))
    two = pymupdf.Pixmap(render.render_equation_png(FRACTION, scale=2, renderer=chromium))
    # Font hinting differs per scale, so "about double" rather than exactly double.
    assert 1.8 < two.width / one.width < 2.2
    assert 1.8 < two.height / one.height < 2.2


@pytest.mark.browser
def test_render_equations_caches_by_mathml_hash(tmp_path, chromium):
    record = {"id": "eq-1", "kind": "equation", "mathml": FRACTION}
    first = render.render_equations([record], tmp_path, renderer=chromium)
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].name.startswith(render._sha256(FRACTION))
    assert render.render_equations([record], tmp_path, renderer=chromium) == first


@pytest.mark.browser
def test_render_equation_text_is_the_printed_text_layer(tmp_path, chromium):
    text = render.render_equation_text(FRACTION, tmp_path, renderer=chromium)
    # Math italic glyphs, fraction flattened: what layout reads from a printed page.
    assert text is not None
    assert sorted(ch for ch in unicodedata.normalize("NFKC", text) if ch.isalnum()) == [
        "d",
        "t",
        "v",
    ]
    (cached,) = tmp_path.iterdir()
    assert cached.read_text(encoding="utf-8") == text


@pytest.mark.browser
def test_render_page_pdf_has_headings_in_its_text_layer(tmp_path, chromium):
    out = tmp_path / "page.pdf"
    pdf = render.render_page_pdf(
        page_source(),
        out,
        images=fake_images(tmp_path),
        renderer=chromium,
        cache_dir=tmp_path / "pages",
    )
    assert out.read_bytes() == pdf and pdf.startswith(b"%PDF")
    with pymupdf.open(stream=pdf) as doc:
        text = " ".join(page.get_text() for page in doc)
        catalog = doc.pdf_catalog()
        assert doc.xref_get_key(catalog, "StructTreeRoot")[0] == "null"  # untagged
        assert doc.metadata["title"] == "9.1 Rolling Balls"
        assert any(page.get_images() for page in doc)
    for heading in ("Rolling Balls", "Section Learning Objectives", "Measuring Speed"):
        assert heading in text
    assert "Access for free at openstax.org" in text
    assert "A red ball on a ramp." not in text
    # Second call is served from the PDF cache.
    assert len(list((tmp_path / "pages").iterdir())) == 1
    assert (
        render.render_page_pdf(
            page_source(), images=fake_images(tmp_path), cache_dir=tmp_path / "pages"
        )
        == pdf
    )
