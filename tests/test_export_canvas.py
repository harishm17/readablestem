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
    folder_name,
    readme,
    valid_course_number,
)
from test_content_e2e import FIXTURES, converted

HERE = Path(__file__).parent
DEMO = Path(readablestem.__file__).parent / "web" / "static" / "demo" / "index.html"
GOLDEN = sorted((HERE / "fixtures" / "assemble" / "golden").glob("*.html"))
IMAGE_NAME = r"images/image-\d\d\.(png|jpg)"
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


def test_folder_name_is_ascii_and_ends_with_the_start_of_the_job_id():
    assert folder_name("Problem Set 2: Kinematics", "3f9a2b7c") == "problem-set-2-kinematics-3f9a2b"
    assert folder_name("Ünïcode Δ-Titel", "3f9a2b7c") == "unicode-titel-3f9a2b"
    assert folder_name("???", "3f9a2b7c") == "document-3f9a2b"
    assert len(folder_name("word " * 40, "3f9a2b7c")) <= 40 + len("-3f9a2b")


@pytest.mark.parametrize("text", ["1", "12345", "9" * 20])
def test_course_numbers(text):
    assert valid_course_number(text)


@pytest.mark.parametrize("text", ["", " 1", "1 ", "12a", "-1", "1/2", "٣", "9" * 21, "1\n"])
def test_not_course_numbers(text):
    assert not valid_course_number(text)


def test_a_bad_course_number_is_refused():
    with pytest.raises(CanvasExportError):
        export_canvas("<p>x</p>", title="t", folder=FOLDER, course_number="12/../3")


# ----------------------------------------------------------------------------- the presentation


def test_every_property_the_export_writes_is_one_canvas_keeps():
    assert set(cs.allowlist()["css_properties"]) >= ec.CANVAS_PROPERTIES


def test_the_only_styles_left_behind_are_known(made):
    """A new rule in the pipeline's stylesheet that Canvas would drop shows here, so it is
    either carried another way or named in the README on purpose."""
    notes = made[1].notes
    assert notes.dropped_css <= {"font-weight", "overflow-wrap", "caption-side"}
    assert notes.uncarried_selectors <= {
        ":root",
        ":focus-visible",
        "@media (min-width: 44rem)",
        "@media print",
        "@media screen and (prefers-color-scheme: dark)",  # Canvas has its own colours
    }


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


def test_no_style_block_no_svg_no_data_images_no_scripts(made):
    _, export = made
    for text in (export.index_html, export.page_html):
        assert text is not None
        tree = cs.parse(text)
        assert tree.find_all("style", "svg", "script", "link") == []
        assert "data:" not in text
        assert "<!--" not in text


def test_the_model_does_catch_what_the_unconverted_file_loses():
    """The control: the file as the pipeline writes it, pasted into a page."""
    raw = DEMO.read_text(encoding="utf-8")
    got = cs.content(cs.sanitize(cs.parse(raw)))
    source = cs.content(cs.parse(raw))
    assert len(source.images) == 7
    assert [src for src, _ in got.images] == [""] * 7  # every picture lost its source
    assert [alt for _, alt in got.images] == [alt for _, alt in source.images]
    assert "color-scheme" not in cs.serialize(cs.sanitize(cs.parse(raw)))  # the CSS text is gone


def test_the_page_has_no_wrapper_elements_but_a_language(made):
    page = made[1].page_html
    assert page is not None and page.startswith(f'<div lang="{made[1].lang}">')
    assert cs.parse(page).find_all("html", "head", "body", "main", "title", "meta") == []


# ----------------------------------------------------------------------------- the zip


def test_the_zip_holds_index_page_readme_and_images(made):
    _, export = made
    names = zipfile.ZipFile(io.BytesIO(build_zip(export))).namelist()
    assert names[:3] == ["index.html", "canvas-page.html", "README.txt"]
    assert names[3:] == sorted(export.images)
    assert all(n.startswith("images/image-") for n in names[3:])


def test_the_zip_is_the_same_bytes_every_time(made):
    export = made[1]
    assert build_zip(export) == build_zip(export)
    again = export_canvas(made[0], title="Doc", folder=FOLDER, course_number=COURSE)
    assert build_zip(again) == build_zip(export)


def test_every_image_is_a_file_and_every_file_is_used(made):
    html, export = made
    source_images = cs.parse(html).find_all("img")
    index_images = cs.parse(export.index_html).find_all("img")
    page_images = cs.parse(export.page_html or "").find_all("img")
    assert len(source_images) == len(index_images) == len(page_images)
    data_images = [i for i in source_images if i.attrs["src"].startswith("data:")]
    assert len(data_images) == len(source_images)  # the pipeline writes no other kind
    assert {i.attrs["src"] for i in index_images} == set(export.images)
    assert {i.attrs["src"] for i in page_images} == {BASE + n for n in export.images}
    assert all(
        data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")) for data in export.images.values()
    )
    assert all(re.fullmatch(IMAGE_NAME, name) for name in export.images)
    # the bytes are the picture that was in the page
    decoded = {base64.b64decode(i.attrs["src"].split(",", 1)[1]) for i in data_images}
    assert decoded == set(export.images.values())
    assert export.notes.images == len(export.images)


def test_the_same_picture_twice_is_one_file():
    html = f'<main><img src="{DATA_URI}" alt="A"><img src="{DATA_URI}" alt="B"></main>'
    export = export_canvas(html, title="t", folder=FOLDER)
    assert list(export.images) == ["images/image-01.png"]
    assert [i.attrs["alt"] for i in cs.parse(export.index_html).find_all("img")] == ["A", "B"]


def test_a_picture_that_is_already_a_web_address_is_left_alone():
    html = '<main><img src="https://example.org/a.png" alt="A"></main>'
    export = export_canvas(html, title="t", folder=FOLDER, course_number=COURSE)
    assert export.images == {}
    assert 'src="https://example.org/a.png"' in export.index_html
    assert 'src="https://example.org/a.png"' in (export.page_html or "")


def test_an_image_type_the_export_does_not_write_is_refused():
    html = '<img src="data:image/svg+xml;base64,PHN2Zy8+" alt="x">'
    with pytest.raises(CanvasExportError, match="svg"):
        export_canvas(html, title="t", folder=FOLDER)


def test_without_a_course_number_there_is_no_page_to_paste(made):
    html = made[0]
    export = export_canvas(html, title="Doc", folder=FOLDER)
    assert export.page_html is None
    names = zipfile.ZipFile(io.BytesIO(build_zip(export))).namelist()
    assert "canvas-page.html" not in names and names[0] == "index.html"
    assert "course number" in readme(export)


# ----------------------------------------------------------------------------- what Canvas keeps


def test_the_page_loses_nothing_in_the_sanitizer(made):
    """The paste version has already left out what Canvas removes, and kept only what it
    keeps: sanitizing it changes nothing."""
    page = made[1].page_html
    assert page is not None
    before = cs.serialize(cs.parse(page), canonical=True)
    after = cs.serialize(cs.sanitize(cs.parse(page)), canonical=True)
    assert after == before


def test_the_page_keeps_what_a_reader_needs(made):
    html, export = made
    source = cs.content(cs.parse(html))
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


def test_the_standalone_page_keeps_what_a_reader_needs(made):
    html, export = made
    source = cs.content(cs.parse(html))
    kept = cs.sanitize(cs.parse(export.index_html))
    got = cs.content(kept)
    assert [alt for _, alt in got.images] == [alt for _, alt in source.images]
    assert all(re.fullmatch(IMAGE_NAME, src) for src, _ in got.images)
    assert [strip_style(m) for m in got.math] == [strip_style(m) for m in source.math]
    assert got.headings == source.headings
    assert got.tables == source.tables
    assert got.links == source.links
    assert got.ids == source.ids
    # a file is not sanitized, but pasted whole, Canvas would leave the title's text in: that
    # is the only text added, and none is lost.
    title = " ".join(cs.parse(export.index_html).find_all("title")[0].text().split())
    assert got.text == f"{title} {source.text}".strip()


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


def test_rules_are_applied_by_specificity_and_the_element_style_wins():
    html = (
        "<html><head><style>p { color: #111 } .a { color: #222 } p.a { margin: 0 }"
        " div > p { padding: 1px } div p.b { padding: 2px }</style></head><body>"
        '<div><p class="a b" style="color:#333">x</p></div></body></html>'
    )
    export = export_canvas(html, title="t", folder=FOLDER)
    p = cs.parse(export.index_html).find_all("p")[0]
    declared = dict(cs._split_declarations(p.attrs["style"]))
    assert declared == {"color": "#333", "margin": "0", "padding": "2px"}


def test_a_page_leaves_typography_to_canvas_but_keeps_the_rest():
    html = (
        "<html><head><style>p { margin: 0 0 1em } h2 { margin: 1em } ul, ol { padding-left: 2em }"
        " .box { border: 1px solid #000 } .box > p { margin: 0 }"
        " img { max-width: 100%; height: auto }"
        "</style></head><body><main><h2>H</h2><p>t</p><ul><li>i</li></ul>"
        '<div class="box"><p>b</p><img src="u.png" alt="a"></div></main></body></html>'
    )
    export = export_canvas(html, title="t", folder=FOLDER, course_number="1")
    page = cs.parse(export.page_html or "")
    standalone = cs.parse(export.index_html)
    assert "style" not in page.find_all("h3")[0].attrs  # the h2, one level down
    assert "style" not in page.find_all("ul")[0].attrs
    assert "margin" in standalone.find_all("h2")[0].attrs["style"]
    assert "padding-left" in standalone.find_all("ul")[0].attrs["style"]
    box = page.find_all("div")[1]  # the first is the language wrapper
    assert "border" in box.attrs["style"]
    assert "margin:0" in box.find_all("p")[0].attrs["style"].replace(" ", "")
    assert "max-width:100%" in box.find_all("img")[0].attrs["style"].replace(" ", "")


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
