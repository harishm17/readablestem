"""The Canvas export (pipeline/export_canvas.py), run through a model of Canvas's sanitizer.

The document is the committed public demo result, the golden documents and the LaTeX
fixtures converted with every content feature on (as tests/test_content_e2e.py does). For
each, the export is written and then put through `canvas_sanitizer`, the allowlist read
from canvas-lms (tests/fixtures/canvas): nothing a reader needs may be lost, i.e. every
image keeps its alt text and a source Canvas keeps, every equation its MathML, headings,
tables (caption, th, scope), links, ids and language are as in the source, and the text is
the same text.
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
from pathlib import Path

import pytest

import canvas_sanitizer as cs
import readablestem
from readablestem.pipeline import export_canvas as ec
from readablestem.pipeline.export_canvas import (
    CanvasExportError,
    build_zip,
    export_canvas,
    readme,
)
from test_content_e2e import FIXTURES, converted

HERE = Path(__file__).parent
DEMO = Path(readablestem.__file__).parent / "web" / "static" / "demo" / "index.html"
GOLDEN = sorted((HERE / "fixtures" / "assemble" / "golden").glob("*.html"))
COURSE = "12345"
FOLDER = "doc-3f9a2b"
BASE = f"/courses/{COURSE}/file_contents/course%20files/{FOLDER}/"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
DATA_URI = "data:image/png;base64," + base64.b64encode(PNG).decode()


def sources() -> dict[str, str]:
    out = {"demo": DEMO.read_text(encoding="utf-8")}
    out |= {f"golden/{p.stem}": p.read_text(encoding="utf-8") for p in GOLDEN}
    return out


def source_ids() -> list[str]:
    return [*sources(), *(f"latex/{n}" for n in FIXTURES)]


def source_html(source: str) -> str:
    if source.startswith("latex/"):
        return converted(source.removeprefix("latex/")).result.html
    return sources()[source]


@pytest.fixture(params=source_ids())
def made(request):
    html = source_html(request.param)
    return html, export_canvas(html, title="Doc", folder=FOLDER, course_number=COURSE)


def strip_style(text: str) -> str:
    return re.sub(r' style="[^"]*"', "", text)


# ----------------------------------------------------------------------------- names


def test_a_bad_course_number_is_refused():
    with pytest.raises(CanvasExportError):
        export_canvas("<p>x</p>", title="t", folder=FOLDER, course_number="12/../3")


# ----------------------------------------------------------------------------- the presentation


def test_hidden_labels_stay_hidden_with_properties_canvas_keeps():
    html = (
        "<html><head><style>.visually-hidden { position: absolute; width: 1px; height: 1px;"
        " overflow: hidden; clip-path: inset(50%); white-space: nowrap; }</style></head>"
        '<body><main><p>Eq <span class="visually-hidden readablestem">Equation </span>2.4</p>'
        "</main></body></html>"
    )
    made = export_canvas(html, title="t", folder=FOLDER, course_number=COURSE)
    for text in (made.index_html, made.page_html or ""):
        span = cs.parse(text).find_all("span")[0]
        assert "screenreader-only" in span.attrs["class"]
        style = cs.clean_style(span.attrs["style"])
        assert "clip: rect(0,0,0,0)" in style and "position: absolute" in style
        assert style == cs.canonical_style(span.attrs["style"])  # nothing of it is dropped
    assert made.notes.dropped_css == set()


# ----------------------------------------------------------------------------- the zip


def test_the_zip_holds_index_page_readme_and_images(made):
    _, export = made
    names = zipfile.ZipFile(io.BytesIO(build_zip(export))).namelist()
    assert names[:3] == ["index.html", "canvas-page.html", "README.txt"]
    assert names[3:] == sorted(export.images)
    assert all(n.startswith("images/image-") for n in names[3:])


def test_an_image_type_the_export_does_not_write_is_refused():
    html = '<img src="data:image/svg+xml;base64,PHN2Zy8+" alt="x">'
    with pytest.raises(CanvasExportError, match="svg"):
        export_canvas(html, title="t", folder=FOLDER)


# ----------------------------------------------------------------------------- what Canvas keeps


def without_skip_link(html: str) -> str:
    """The source without its skip link and the anchor it jumps to: the paste version leaves
    both out on purpose (`<main>`, where the link goes, is removed by Canvas)."""
    html = re.sub(r'<a class="skip-link[^>]*>[^<]*</a>\n?', "", html)
    return re.sub(r'<span id="main-content"[^>]*></span>\n?', "", html)


def test_the_page_keeps_what_a_reader_needs(made):
    html, export = made
    page = export.page_html or ""
    assert ("skip-link" in html) == export.notes.skip_link_left_out  # deliberately left out
    assert "skip-link" not in page and "main-content" not in page  # left out on purpose
    source = cs.content(cs.parse(without_skip_link(html)))
    got = cs.content(cs.sanitize(cs.parse(export.page_html or "")))
    # every image, with its alt text (an empty alt stays empty), at an address Canvas keeps
    assert [alt for _, alt in got.images] == [alt for _, alt in source.images]
    assert all(src.startswith(BASE + "images/image-") for src, _ in got.images)
    # every equation, as the same MathML
    assert len(source.math) == len(got.math)
    assert [strip_style(m) for m in got.math] == [strip_style(m) for m in source.math]
    # the same headings, one level down (the Page's title is the top heading in Canvas)
    assert got.headings == [(level + 1, text) for level, text in source.headings]
    # tables: caption, header cells with their scope, spans, cells
    assert got.tables == source.tables
    assert got.links == source.links
    assert got.ids == source.ids
    assert set(source.langs) <= set(got.langs)
    assert got.text == source.text


def test_what_only_the_standalone_page_keeps():
    """tabindex and headers: Canvas removes both from a Page, and a file keeps them."""
    html = (
        "<html lang='en'><head><title>T</title></head><body><main>"
        '<div class="table-scroll" role="group" tabindex="0" aria-label="t">'
        '<table><caption>C</caption><tr><th id="a" scope="col">A</th></tr>'
        '<tr><td headers="a">1</td></tr></table></div></main></body></html>'
    )
    export = export_canvas(html, title="T", folder=FOLDER, course_number=COURSE)
    assert 'tabindex="0"' in export.index_html and 'headers="a"' in export.index_html
    assert export.page_html is not None
    assert "tabindex" not in export.page_html and "headers" not in export.page_html
    assert 'scope="col"' in export.page_html and "<caption>C</caption>" in export.page_html
    assert export.notes.scroll_boxes == 1 and export.notes.tables_with_headers == 1
    text = readme(export)
    assert "1 table link cells to their headers" in text and "scrolls sideways" in text


# ----------------------------------------------------------------------------- headings


def test_headings_move_down_one_level_unless_that_would_merge_two():
    shifted = export_canvas(
        "<main><h1>A</h1><h2>B</h2><h5>C</h5></main>", title="t", folder=FOLDER, course_number="1"
    )
    assert re.findall(r"<h(\d)[ >]", shifted.page_html or "") == ["2", "3", "6"]
    assert shifted.notes.headings_shifted
    kept = export_canvas(
        "<main><h1>A</h1><h6>F</h6></main>", title="t", folder=FOLDER, course_number="1"
    )
    assert re.findall(r"<h(\d)[ >]", kept.page_html or "") == ["1", "6"]
    assert not kept.notes.headings_shifted
    assert "<h1>A</h1>" in kept.index_html  # the standalone page is never shifted


# ----------------------------------------------------------------------------- the stylesheet


# ----------------------------------------------------------------------------- the README


def test_the_readme_gives_the_folder_the_course_number_and_no_claims(made):
    _, export = made
    text = readme(export)
    assert FOLDER in text and COURSE in text
    assert "canvas-page.html" in text and "index.html" in text and "images/" in text
    assert "have not been tried in your" in text  # honest about what was checked
    assert "compliant" not in text.lower() and "certif" not in text.lower()
    assert all(len(line) <= 90 for line in text.splitlines())
    assert text.isascii() or "“" in text  # plain text; only the curly quotes


def test_a_page_too_long_for_canvas_is_not_offered(monkeypatch):
    monkeypatch.setattr(ec, "CANVAS_PAGE_LIMIT", 100)
    export = export_canvas("<main><p>" + "x" * 200 + "</p></main>", title="t", folder=FOLDER,
                           course_number="1")  # fmt: skip
    assert export.page_html is None and export.notes.page_too_large
    assert "holds about 500 KB" in readme(export)
