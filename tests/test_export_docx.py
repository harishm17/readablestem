"""W1: the Word export (pipeline/export_docx.py, `readablestem export docx`).

The file is opened with python-docx and its XML read directly: as many Office Math equations as
`<math>` in the HTML, headings in Word's heading styles, alt text on every picture (and never
the picture's address), header rows on tables, lists, links, the language and the title. The
documents are the committed demo result, the golden documents and the LaTeX conversions, as in
test_export_canvas.py. They need pandoc: the tests skip, saying so, when it is not installed
(CI installs it).
"""

from __future__ import annotations

import base64
import io
import re
import shutil
import socket
import stat
import subprocess
import threading
import zipfile
from html import unescape
from pathlib import Path
from xml.etree import ElementTree as ET

import docx
import pytest

import readablestem.cli.main as cli
from readablestem.pipeline import export_docx as ed
from readablestem.pipeline import export_epub as ee
from readablestem.pipeline.assemble import ABOUT_TITLE, CONTACT
from readablestem.pipeline.export_canvas import _parse, export_canvas
from readablestem.web import listen
from test_export_canvas import DATA_URI, source_html, source_ids

pytestmark = pytest.mark.skipif(not ed.available(), reason="pandoc is not installed")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"


def open_docx(data: bytes) -> docx.document.Document:
    return docx.Document(io.BytesIO(data))


def count(html: str, tag: str) -> int:
    return sum(1 for n in _parse(html).walk() if n.tag == tag)


def drawings(document) -> list:
    return list(document.element.body.iter(f"{WP}inline"))


PAGE = (
    '<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>La vitesse</title>'
    "<style>p { color: red }</style></head><body><main>"
    '<h1 id="t">La vitesse</h1><h2>Deux</h2><h3>Trois</h3><h6>Six</h6>'
    '<p>See <a href="#t">the top</a> and <a href="https://example.org/a?b=1&amp;c=2">the web</a>'
    ' and <math display="inline"><mi>x</mi><mo>=</mo><msup><mi>y</mi><mn>2</mn></msup></math>.</p>'
    '<div class="pagebreak readablestem"><hr role="doc-pagebreak" aria-label="Page 2"'
    ' id="pagebreak-2"><span aria-hidden="true">Page 2</span></div>'
    '<div class="equation"><math display="block"><mfrac><mi>d</mi><mi>t</mi></mfrac></math></div>'
    '<table><caption>Speeds</caption><thead><tr><th scope="col">Car</th>'
    '<th scope="col">km/h</th></tr></thead><tbody><tr><th scope="row">A</th><td>50</td></tr>'
    "</tbody></table>"
    f'<figure><img src="{DATA_URI}" alt="A dot at the origin" width="1" height="1">'
    "<figcaption>Figure 1</figcaption></figure>"
    '<details class="figure-description"><summary>Description of Figure 1</summary>'
    "<p>A single dot where the axes cross.</p></details>"
    "<figure><p>A picture left out, in words.</p><figcaption>Figure 2</figcaption></figure>"
    '<img src="https://example.org/remote.png" alt="a remote picture">'
    "<ol><li>one</li><li>two<ul><li>nested</li></ul></li></ol>"
    '<script>alert(1)</script><form action="/x"><input name="a"><button>Go</button></form>'
    "</main></body></html>"
)


@pytest.fixture(scope="module")
def page():
    made = ed.export_docx(PAGE)
    return made, open_docx(made.data)


# --------------------------------------------------------------------- every document


@pytest.fixture(params=source_ids())
def made(request):
    html = source_html(request.param)
    export = ed.export_docx(html)
    return html, export, open_docx(export.data)


def test_every_equation_is_a_native_equation(made):
    html, export, document = made
    equations = list(document.element.body.iter(f"{M}oMath"))
    assert len(equations) == count(html, "math") == export.equations


@pytest.mark.parametrize("fixture", ["golden/sample", "golden/stored_demo_0_2_0"])
def test_the_note_about_this_version_is_in_every_output_once(fixture):
    """One page, one note: the Word file, the EPUB, the Canvas page and Listen's transcript
    each carry the note's facts, said once (pandoc reads only `<main>`, F-127)."""
    html = source_html(fixture)
    about = re.search(r'<section id="about-conversion".*?</section>', html, re.S)
    assert about is not None
    lines = [
        " ".join(unescape(re.sub(r"<[^>]+>", "", p)).split())
        for p in re.findall(r"<p\b[^>]*>(.*?)</p>", about.group(0), re.S)
    ]
    assert lines[0] == ABOUT_TITLE and len(lines) >= 3
    word = open_docx(ed.export_docx(html).data)
    canvas = export_canvas(html, title="Doc", folder="doc-3f9a2b", course_number="12345")
    texts = {
        "word": [" ".join(p.text.split()) for p in word.paragraphs],
        "epub": plain(ee.export_epub(html).content),
        "canvas": plain(canvas.page_html or ""),
        "listen": [item.text for item in listen.build_transcript(html, {})],
    }
    for name, text in texts.items():
        joined = " ".join(text) if isinstance(text, list) else text
        assert all(line in joined for line in lines[1:]), name
        assert joined.count(CONTACT) == 1, name


def plain(markup: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def test_headings_are_word_heading_styles(made):
    html, export, document = made
    levels = [
        int(p.style.name.removeprefix("Heading "))
        for p in document.paragraphs
        if p.style.name.startswith("Heading ")
    ]
    expected = [int(n.tag[1]) for n in _parse(html).walk() if re.fullmatch(r"h[1-6]", n.tag)]
    assert levels == expected
    assert export.headings == len(expected)


def test_every_picture_has_alt_text_and_never_its_address(made):
    html, export, document = made
    pictures = drawings(document)
    assert export.images == len(pictures)
    for inline in pictures:
        outer = inline.find(f"{WP}docPr").get("descr")
        assert outer
        for inner in inline.iter(f"{PIC}cNvPr"):
            assert inner.get("descr") == outer
        assert not outer.startswith(("data:", "http"))
    alts = [n.attrs.get("alt", "") for n in _parse(html).walk() if n.tag == "img"]
    assert sorted(i.find(f"{WP}docPr").get("descr") for i in pictures) == sorted(
        a for a in alts if a
    )


# --------------------------------------------------------------------- one page with everything


def test_a_table_has_a_header_row_and_a_caption(page):
    export, document = page
    assert len(document.tables) == 1 == export.tables
    assert export.header_rows == 1
    table = document.tables[0]
    row = table.rows[0]._tr.find(f"{W}trPr")
    assert row is not None and row.find(f"{W}tblHeader") is not None
    assert [c.text for c in table.rows[0].cells] == ["Car", "km/h"]
    assert table.rows[1].cells[1].text == "50"
    assert any(p.text == "Speeds" for p in document.paragraphs)


def test_nothing_that_runs_or_loads_is_carried(page):
    export, document = page
    names = zipfile.ZipFile(io.BytesIO(export.data)).namelist()
    assert not [n for n in names if "vba" in n.lower() or n.endswith(".js")]
    text = " ".join(p.text for p in document.paragraphs)
    assert "alert(1)" not in text and "Go" not in text.split()
    assert "a remote picture" not in [i.find(f"{WP}docPr").get("descr") for i in drawings(document)]
    assert "a remote picture" in text  # the remote picture became its alt text


def test_a_remote_picture_is_never_fetched():
    """The address of a picture in the page is not opened, by pandoc or by this module."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    server.settimeout(0.5)
    hits: list[bytes] = []
    done = threading.Event()

    def accept() -> None:
        while not done.is_set():
            try:
                conn, _ = server.accept()
            except (TimeoutError, OSError):
                continue
            hits.append(conn.recv(100))
            conn.close()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    port = server.getsockname()[1]
    try:
        html = (
            f'<html lang="en"><body><h1>T</h1><img src="http://127.0.0.1:{port}/a.png" alt="x">'
            '<img src="file:///etc/hostname" alt="y"><img src="/etc/hostname" alt="z">'
            "</body></html>"
        )
        made = ed.export_docx(html)
    finally:
        done.set()
        thread.join(2)
        server.close()
    assert hits == []
    assert made.images == 0


def test_a_webp_picture_is_written_as_a_png():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 3), "red").save(buffer, "WEBP")
    uri = "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode()
    html = f'<html lang="en"><body><h1>T</h1><img src="{uri}" alt="red box"></body></html>'
    made = ed.export_docx(html)
    names = zipfile.ZipFile(io.BytesIO(made.data)).namelist()
    assert any(n.startswith("word/media/") and n.endswith(".png") for n in names)
    assert made.images == 1


# --------------------------------------------------------------------- the child process


def fake_pandoc(tmp_path: Path, body: str) -> str:
    script = tmp_path / "pandoc"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


COPY_TO_OUT = 'out=""\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
SIMPLE = '<html lang="en"><body><h1>T</h1><p>Hi</p></body></html>'


def test_a_failing_child_is_a_failure_without_text(tmp_path):
    with pytest.raises(ed.DocxExportError) as error:
        ed.export_docx(SIMPLE, pandoc=fake_pandoc(tmp_path, "echo SECRET-TEXT >&2; exit 3\n"))
    assert error.value.code == "failed" and "SECRET" not in str(error.value)


def test_a_slow_child_is_stopped(tmp_path):
    with pytest.raises(ed.DocxExportError) as error:
        ed.export_docx(SIMPLE, pandoc=fake_pandoc(tmp_path, "sleep 30\n"), timeout_s=0.5)
    assert error.value.code == "timeout"


def test_the_child_has_no_secrets_in_its_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "hunter2")
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    seen = tmp_path / "env.txt"
    real = shutil.which("pandoc")
    program = fake_pandoc(tmp_path, f'env > "{seen}"\nexec "{real}" "$@"\n')
    ed.export_docx(SIMPLE, pandoc=program)
    env = seen.read_text()
    assert "hunter2" not in env and "DATABASE_URL" not in env and "SESSION_SECRET" not in env


def test_a_file_that_lacks_an_equation_is_refused(tmp_path):
    """If pandoc cannot convert a `<math>`, the export fails rather than drop it."""
    plain = ed.export_docx(SIMPLE).data
    stub = tmp_path / "plain.docx"
    stub.write_bytes(plain)
    program = fake_pandoc(
        tmp_path,
        COPY_TO_OUT + f'cp "{stub}" "$out"\n',
    )
    html = '<html lang="en"><body><h1>T</h1><p><math><mi>x</mi></math></p></body></html>'
    with pytest.raises(ed.DocxExportError) as error:
        ed.export_docx(html, pandoc=program)
    assert error.value.code == "equations"


def test_one_equation_pandoc_cannot_write_does_not_fail_the_file():
    """F-502: a bold `"` (an instructor's edit) makes pandoc leave raw TeX in the text, and an
    empty `<math>` leaves nothing. The file is made; each such equation is words in its place
    (its reading and the sentence, in the Not Converted style), the export names its element,
    the other equations are Office equations, and no TeX is left."""
    bad = '<mi mathvariant="bold">&quot;</mi>'
    html = (
        '<html lang="en"><head><title>T</title></head><body><main><h1>T</h1>'
        f'<p>a <math id="m1" alttext="quote">{bad}</math> b <math id="m2"><mi>x</mi></math>'
        ' c <math id="m3"></math></p>'
        f'<div class="equation" id="m4"><math display="block">{bad}</math></div>'
        '<div class="equation" id="m5"><math display="block"><mi>y</mi></math></div>'
        "</main></body></html>"
    )
    export = ed.export_docx(html)
    assert export.not_word == ["m1", "m3", "m4"]
    assert export.equations == 2
    with zipfile.ZipFile(io.BytesIO(export.data)) as archive:
        xml = archive.read("word/document.xml").decode()
    assert xml.count("<m:oMath>") == 2
    assert xml.count("it is complete on the web page") == 3
    assert xml.count('<w:rStyle w:val="NotConverted" />') == 3
    assert "quote. This equation could not be written" in xml
    assert "$" not in xml and "mathbf" not in xml


LINKS = (
    '<html lang="en"><head><title>T</title></head><body><nav><a href="#h1">One</a></nav><main>'
    '<h1 id="h1">One</h1><p id="p1">Text<a id="noteref-n1" role="doc-noteref" href="#n1">1</a>'
    ' and <a role="doc-noteref" href="#n1">1</a>: <a href="#p3">a paragraph</a>,'
    ' <a href="#fig">a figure</a>, <a href="#l2">a list</a>, <a href="#c">a cell</a>,'
    ' <a href="#d">a description</a>, <a href="#pic">a picture</a>,'
    ' <a href="#gone">nothing</a>.</p>'
    '<p id="p3">Three</p><figure id="fig"><p>words</p><figcaption>F</figcaption></figure>'
    '<ul id="l2"><li>a</li></ul><table><tr><th>h</th></tr><tr><td id="c">x</td></tr></table>'
    '<details id="d"><summary>S</summary><p>long</p></details>'
    f'<figure id="pic"><img src="{DATA_URI}" alt="A dot" width="1" height="1">'
    "<figcaption>G</figcaption></figure>"
    '<div role="doc-footnote" id="n1"><p>The note says <math><mi>x</mi></math>.'
    ' <a role="doc-backlink" href="#noteref-n1">Back to the text</a></p></div>'
    "</main></body></html>"
)


def test_every_link_in_a_word_file_lands_and_a_note_is_a_word_footnote():
    """F-514, F-566: pandoc keeps an id on a heading, a div or a span and drops it on a
    paragraph, figure, list or cell, so most links led nowhere; a footnote's mark was an empty
    Word footnote beside a body paragraph. Now a link lands on a bookmark, the note's words are
    in the Word footnote (its equation too), and the one link with no target is its words."""
    export = ed.export_docx(LINKS)
    with zipfile.ZipFile(io.BytesIO(export.data)) as archive:
        body = ET.fromstring(archive.read("word/document.xml"))
        notes = ET.fromstring(archive.read("word/footnotes.xml"))
    bookmarks = {b.get(f"{W}name") for b in body.iter(f"{W}bookmarkStart")}
    anchors = [h.get(f"{W}anchor") for h in body.iter(f"{W}hyperlink")]
    assert anchors == ["h1", "p3", "fig", "l2", "c", "d", "pic"]
    assert set(anchors) <= bookmarks
    assert export.links_unlinked == 1  # "nothing" is text
    real = [n for n in notes if n.get(f"{W}type") is None]
    assert len(real) == 1
    note = "".join(t.text or "" for t in real[0].iter(f"{W}t"))
    assert "The note says" in note and "Back to the text" not in note
    assert len(list(real[0].iter(f"{M}oMath"))) == 1 == export.equations
    assert len(list(body.iter(f"{W}footnoteReference"))) == 1
    assert "The note says" not in "".join(t.text or "" for t in body.iter(f"{W}t"))


def test_output_that_is_not_a_word_file_is_refused(tmp_path):
    program = fake_pandoc(
        tmp_path,
        COPY_TO_OUT + 'echo hi > "$out"\n',
    )
    with pytest.raises(ed.DocxExportError) as error:
        ed.export_docx(SIMPLE, pandoc=program)
    assert error.value.code == "invalid"


# --------------------------------------------------------------------- the office programs


@pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice is not installed")
def test_libreoffice_opens_the_file(tmp_path):
    made = ed.export_docx(source_html("demo"))
    source = tmp_path / "in" / "demo.docx"
    source.parent.mkdir()
    source.write_bytes(made.data)
    out = tmp_path / "out"
    out.mkdir()
    subprocess.run(
        [
            "soffice",
            f"-env:UserInstallation={(tmp_path / 'profile').as_uri()}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out),
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    assert (out / "demo.pdf").read_bytes().startswith(b"%PDF-")


# --------------------------------------------------------------------- the command


def test_the_command_writes_the_file(tmp_path, capsys):
    src = tmp_path / "doc.html"
    src.write_text(PAGE, encoding="utf-8")
    out = tmp_path / "doc.docx"
    assert cli.main(["export", "docx", str(src), str(out), "--title", "Mon titre"]) == 0
    assert open_docx(out.read_bytes()).core_properties.title == "Mon titre"
    assert "2 equations" in capsys.readouterr().out


def test_the_command_says_when_it_cannot(tmp_path, capsys):
    assert cli.main(["export", "docx", str(tmp_path / "missing.html"), str(tmp_path / "o")]) == 2
    bad = tmp_path / "bad.html"
    bad.write_text("words", encoding="utf-8")
    assert cli.main(["export", "docx", str(bad), str(tmp_path / "o.docx")]) == 2
    assert "cannot write a Word file: invalid" in capsys.readouterr().err
