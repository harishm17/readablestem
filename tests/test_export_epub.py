"""W2: the EPUB 3 export (pipeline/export_epub.py, `readablestem export epub`).

The package is checked three ways: structure rules written here (the package is well formed,
every link and file resolves, MathML and alt text are kept), EPUBCheck, and DAISY Ace. The last
two need Java and Node; `test_export_epub_validators.py` runs them when they are installed and
says why it skipped when they are not (CI installs both: .github/workflows/epub.yml).
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime

import pytest
from defusedxml import ElementTree

import readablestem.cli.main as cli
from readablestem.pipeline import export_epub as ee
from readablestem.pipeline.export_canvas import _parse
from test_export_canvas import DATA_URI, source_html, source_ids

MODIFIED = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
XHTML = "{http://www.w3.org/1999/xhtml}"
OPF = "{http://www.idpf.org/2007/opf}"
MATHML = "{http://www.w3.org/1998/Math/MathML}"
EPUB_NS = "{http://www.idpf.org/2007/ops}"


def make(html: str, **kwargs) -> tuple[ee.EpubExport, dict[str, bytes]]:
    export = ee.export_epub(html, modified=MODIFIED, **kwargs)
    data = ee.build_epub(export)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return export, {n: archive.read(n) for n in archive.namelist()}


def xml(parts: dict[str, bytes], name: str):
    return ElementTree.fromstring(parts[name])


def metadata(parts: dict[str, bytes]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for meta in xml(parts, ee.PACKAGE).iter(f"{OPF}meta"):
        out.setdefault(meta.get("property", ""), []).append((meta.text or "").strip())
    return out


PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Speed</title>'
    "<style>p > b { color: #111 }</style></head><body><main>"
    '<h1 id="p1-e0">Speed</h1><p>See <a href="#eq1">the equation</a> and '
    '<a href="/">home</a> and <a href="https://example.org/a?b=1&c=2">the web</a>.</p>'
    '<h3 id="p1-e1">Deep first</h3><h2>Then shallower</h2>'
    '<hr role="doc-pagebreak" aria-label="Page 2" id="pb2"><span>Page 2</span>'
    '<hr role="doc-pagebreak" aria-label="Page iii">'
    '<p><math id="eq1" display="block" alttext="v equals d over t"><mi>v</mi><mo>=</mo>'
    '<mfrac><mi>d</mi><mi>t</mi></mfrac><mspace width="1em"></mspace></math></p>'
    f'<figure><img src="{DATA_URI}" alt="A dot" width="1" height="1">'
    "<figcaption>Figure 1</figcaption></figure>"
    '<details class="figure-description"><summary>Description</summary><p>A dot.</p></details>'
    "<p>Air &amp; water<br>second line</p>"
    "<script>alert(1)</script></main></body></html>"
)


# --------------------------------------------------------------------- every document


@pytest.fixture(params=source_ids())
def made(request):
    html = source_html(request.param)
    export, parts = make(html)
    return html, export, parts


def test_package_structure(made):
    _, export, parts = made
    names = list(zipfile.ZipFile(io.BytesIO(ee.build_epub(export))).infolist())
    assert names[0].filename == "mimetype"
    assert names[0].compress_type == zipfile.ZIP_STORED
    assert parts["mimetype"] == b"application/epub+zip"
    root = xml(parts, ee.PACKAGE)
    assert root.get("version") == "3.0"
    hrefs = [item.get("href") for item in root.iter(f"{OPF}item")]
    assert "nav.xhtml" in hrefs and "content.xhtml" in hrefs
    for href in hrefs:
        assert f"EPUB/{href}" in parts, href
    listed = {f"EPUB/{h}" for h in hrefs}
    assert set(parts) - listed == {"mimetype", "META-INF/container.xml", ee.PACKAGE}
    for name in (ee.NAV, ee.CONTENT, "META-INF/container.xml"):
        xml(parts, name)  # well formed, no DTD


def test_links_ids_and_images_resolve(made):
    _, _, parts = made
    content = xml(parts, ee.CONTENT)
    ids = [e.get("id") for e in content.iter() if e.get("id")]
    assert len(ids) == len(set(ids))
    for a in content.iter(f"{XHTML}a"):
        href = a.get("href")
        if href is not None:
            assert href.startswith(("#", "http://", "https://", "mailto:")), href
            if href.startswith("#"):
                assert href[1:] in ids, href
    for img in content.iter(f"{XHTML}img"):
        assert f"EPUB/{img.get('src')}" in parts
    nav = xml(parts, ee.NAV)
    for a in nav.iter(f"{XHTML}a"):
        target = a.get("href").removeprefix("content.xhtml#")
        assert target in ids, target
    assert b"data:" not in parts[ee.CONTENT]


def test_maths_images_and_headings_are_kept(made):
    html, export, parts = made
    source = _parse(html)
    content = xml(parts, ee.CONTENT)
    assert len(list(content.iter(f"{MATHML}math"))) == sum(
        1 for n in source.walk() if n.tag == "math"
    )
    # nothing was turned into a picture: the same number of <img> as the source had
    assert len(list(content.iter(f"{XHTML}img"))) == sum(1 for n in source.walk() if n.tag == "img")
    assert [i.get("alt") for i in content.iter(f"{XHTML}img")] == [
        n.attrs.get("alt") for n in source.walk() if n.tag == "img"
    ]
    properties = [
        i.get("properties")
        for i in xml(parts, ee.PACKAGE).iter(f"{OPF}item")
        if i.get("id") == "content"
    ]
    has_math = any(n.tag == "math" for n in source.walk())
    assert (properties == ["mathml"]) if has_math else (properties == [None])
    wanted = [
        " ".join("".join(_flat(h)).split()) for h in source.walk() if re.fullmatch(r"h[1-6]", h.tag)
    ]
    toc = [
        " ".join("".join(a.itertext()).split())
        for nav in xml(parts, ee.NAV).iter(f"{XHTML}nav")
        if nav.get(f"{EPUB_NS}type") == "toc"
        for a in nav.iter(f"{XHTML}a")
    ]
    assert toc == [w for w in wanted if w] or (not wanted and len(toc) == 1)
    assert export.headings == len([w for w in wanted if w])


def _flat(node):
    for child in node.children:
        if isinstance(child, str):
            yield child
        else:
            yield from _flat(child)


def test_metadata_makes_no_conformance_claim(made):
    _, _, parts = made
    meta = metadata(parts)
    assert meta["schema:accessMode"][0] == "textual"
    assert meta["schema:accessibilityHazard"] == ["none"]
    assert meta["schema:accessibilitySummary"] == [ee.ACCESSIBILITY_SUMMARY]
    assert "tableOfContents" in meta["schema:accessibilityFeature"]
    assert "dcterms:conformsTo" not in meta
    for name in (ee.PACKAGE, ee.NAV, ee.CONTENT):
        assert b"conformsTo" not in parts[name] and b"EPUB Accessibility" not in parts[name]
    assert meta["dcterms:modified"] == ["2026-11-02T12:00:00Z"]


def test_same_document_same_bytes(made):
    html, _, _ = made
    first = ee.build_epub(ee.export_epub(html, modified=MODIFIED))
    assert first == ee.build_epub(ee.export_epub(html, modified=MODIFIED))


# ---------------------------------------------------------------------- one synthetic page


def test_math_stays_native_with_namespace_and_property():
    export, parts = make(PAGE)
    content = xml(parts, ee.CONTENT)
    (math,) = content.iter(f"{MATHML}math")
    assert math.get("alttext") == "v equals d over t"
    assert [c.tag for c in math] == [f"{MATHML}{t}" for t in ("mi", "mo", "mfrac", "mspace")]
    assert b"<mspace" in parts[ee.CONTENT] and b"</mspace>" not in parts[ee.CONTENT]
    assert 'properties="mathml"' in export.package


def test_images_are_extracted_with_alt_and_deduplicated():
    page = PAGE.replace("</main>", f'<img src="{DATA_URI}" alt="Same dot again"></main>')
    export, parts = make(page)
    assert list(export.images) == ["images/image-01.png"]
    assert parts["EPUB/images/image-01.png"] == export.images["images/image-01.png"]
    alts = [i.get("alt") for i in xml(parts, ee.CONTENT).iter(f"{XHTML}img")]
    assert alts == ["A dot", "Same dot again"]
    assert 'media-type="image/png"' in export.package


def test_nav_has_contents_nested_by_level_and_a_page_list():
    export, parts = make(PAGE)
    nav = xml(parts, ee.NAV)
    toc, pages = [n for n in nav.iter(f"{XHTML}nav")]
    assert toc.get(f"{EPUB_NS}type") == "toc" and toc.get("role") == "doc-toc"
    assert pages.get(f"{EPUB_NS}type") == "page-list" and pages.get("hidden") is not None
    entries = [(a.get("href"), "".join(a.itertext())) for a in toc.iter(f"{XHTML}a")]
    assert entries == [
        ("content.xhtml#p1-e0", "Speed"),
        ("content.xhtml#p1-e1", "Deep first"),
        ("content.xhtml#heading-3", "Then shallower"),
    ]
    # h1 holds h3; the h2 after it is not deeper than the h3, so it sits beside it
    top = toc.find(f"{XHTML}ol")
    (speed,) = list(top)
    inner = speed.find(f"{XHTML}ol")
    assert [li.find(f"{XHTML}a").text for li in inner] == ["Deep first", "Then shallower"]
    labels = [(a.get("href"), a.text) for a in pages.iter(f"{XHTML}a")]
    assert labels == [("content.xhtml#pb2", "Page 2"), ("content.xhtml#page-2", "Page iii")]
    assert export.page_labels == 2
    assert 'property="source-of">pagination<' in export.package  # what the pages belong to
    assert "source-of" not in make(PAGE.replace('role="doc-pagebreak"', 'role="x"'))[0].package
    content = xml(parts, ee.CONTENT)
    marker = next(e for e in content.iter(f"{XHTML}hr") if e.get("id") == "pb2")
    assert marker.get("role") == "doc-pagebreak"
    assert marker.get(f"{EPUB_NS}type") == "pagebreak"


def test_no_page_list_without_markers_and_contents_never_empty():
    export, parts = make('<html lang="en"><body><main><p>Just text.</p></main></body></html>')
    nav = xml(parts, ee.NAV)
    assert len(list(nav.iter(f"{XHTML}nav"))) == 1
    (a,) = nav.iter(f"{XHTML}a")
    assert a.get("href") == "content.xhtml#start"
    assert export.title == "Converted document" and export.headings == 0
    assert "structuralNavigation" not in export.features
    assert "pageNavigation" not in export.features
    assert any(e.get("id") == "start" for e in xml(parts, ee.CONTENT).iter())


def test_features_come_from_the_document():
    _, parts = make(PAGE)
    meta = metadata(parts)
    assert meta["schema:accessibilityFeature"] == sorted(
        [
            "MathML",
            "alternativeText",
            "describedMath",
            "longDescription",
            "pageNavigation",
            "structuralNavigation",
            "tableOfContents",
        ]
    )
    assert meta["schema:accessMode"] == ["textual", "visual"]
    assert meta["schema:accessModeSufficient"] == ["textual"]
    plain = '<html lang="en"><body><main><h1>A</h1><p>Text</p></main></body></html>'
    assert metadata(make(plain)[1])["schema:accessibilityFeature"] == [
        "structuralNavigation",
        "tableOfContents",
    ]


def test_sufficiency_is_not_declared_while_items_are_flagged():
    flagged = PAGE.replace("<p>See", '<div class="flagged" data-status="abstained"></div><p>See')
    assert "schema:accessModeSufficient" not in metadata(make(flagged)[1])


def test_links_scripts_and_text_are_cleaned():
    _, parts = make(PAGE)
    text = parts[ee.CONTENT].decode()
    assert "<script" not in text and "alert" not in text
    content = xml(parts, ee.CONTENT)
    anchors = {a.text: a.get("href") for a in content.iter(f"{XHTML}a")}
    assert anchors["home"] is None  # a site-relative link points at nothing in a book
    assert anchors["the web"] == "https://example.org/a?b=1&c=2"
    assert "<br/>" in text and "Air &amp; water" in text
    assert "<style>p &gt; b { color: #111 }</style>" in text  # stylesheet kept, escaped


def test_language_title_and_identifier():
    export, parts = make(PAGE)
    assert export.lang == "en"
    root = xml(parts, ee.CONTENT)
    assert root.get("lang") == "en"
    assert root.get("{http://www.w3.org/XML/1998/namespace}lang") == "en"
    package = export.package
    assert "<dc:language>en</dc:language>" in package
    assert "<dc:title>Speed</dc:title>" in package
    assert re.search(r"<dc:identifier id=\"pub-id\">urn:uuid:[0-9a-f-]{36}<", package)
    other, _ = make(PAGE, title="  Other\ntitle ", identifier="urn:isbn:1")
    assert other.title == "Other title" and "urn:isbn:1" in other.package
    german, _ = make(PAGE.replace('lang="en"', 'lang="de"'))
    assert german.lang == "de" and "<dc:language>de</dc:language>" in german.package
    odd, _ = make(PAGE.replace('lang="en"', 'lang="x&quot; onload=&quot;1"'))
    assert odd.lang == "en"


def test_inline_svg_gets_its_namespace_and_property():
    page = '<html lang="en"><body><main><svg viewbox="0 0 4 4"><circle r="1"></circle></svg></main>'
    export, parts = make(page)
    assert 'properties="svg"' in export.package
    (svg,) = xml(parts, ee.CONTENT).iter("{http://www.w3.org/2000/svg}svg")
    assert svg.get("viewBox") == "0 0 4 4"


def test_a_document_without_a_body_is_refused():
    with pytest.raises(ee.EpubExportError):
        ee.export_epub("just text")


def test_no_control_characters_reach_the_xml():
    _, parts = make(PAGE.replace("Air &amp;", "Air \x01 &amp;"))
    xml(parts, ee.CONTENT)


# ------------------------------------------------------------------------------- the CLI


def test_cli_writes_an_epub(tmp_path, capsys):
    src = tmp_path / "in.html"
    src.write_text(PAGE, encoding="utf-8")
    out = tmp_path / "out.epub"
    assert cli.main(["export", "epub", str(src), str(out), "--title", "Chosen"]) == 0
    assert "wrote" in capsys.readouterr().out
    with zipfile.ZipFile(out) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert b"<dc:title>Chosen</dc:title>" in archive.read(ee.PACKAGE)


def test_cli_reports_a_missing_file_without_a_traceback(tmp_path, capsys):
    assert cli.main(["export", "epub", str(tmp_path / "none.html"), str(tmp_path / "o.epub")]) == 2
    assert "cannot read" in capsys.readouterr().err
    bad = tmp_path / "bad.html"
    bad.write_text("plain", encoding="utf-8")
    assert cli.main(["export", "epub", str(bad), str(tmp_path / "o.epub")]) == 2
    assert not (tmp_path / "o.epub").exists()
