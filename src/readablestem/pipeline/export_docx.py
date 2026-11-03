"""The finished document as a Word (.docx) file, with native equations.

Like the Canvas export and the EPUB, this reads the finished HTML (the file the pipeline
wrote, docs/output.md) and nothing from the pipeline's steps. Pandoc does the conversion
(`pandoc -f html -t docx`): it reads MathML and writes Office Math (OMML), so an equation is
an editable Word equation that a screen reader with Word can navigate, not a picture. Pandoc
is an external program (GPL-2.0-or-later; run as a separate process, not linked), installed
in the image from the distribution (Dockerfile, THIRD_PARTY_NOTICES.md). Without it the
export is unavailable (`available()` is false) and the web app does not offer the button.

What this module does around pandoc:

- **Before.** The same sanitiser as the EPUB (`export_epub._sanitise`): scripts, frames,
  forms, remote pictures and `javascript:` links go, so pandoc is only given a document that
  names nothing outside itself and cannot fetch anything (it has no sandbox mode on the
  distribution build, which reads its default styles from disk). The page-break markers are
  taken out (a Word document has its own pages). A long description (`<details>`) becomes
  its heading line in bold and its paragraphs, in place after the figure. A WebP picture
  becomes a PNG (Word cannot show WebP). The language and the title are set explicitly.
- **The child.** A separate process in its own session with a minimal environment
  (`core.childenv`: no keys, no database URL), a private temporary directory, CPU, file-size
  and heap limits, and a time limit (60 s); killed with its group when it overruns. A
  failure raises `DocxExportError` with a category code and nothing from the document.
- **After.** The package is checked and fixed in `word/document.xml`, whatever the pandoc
  version: every picture's alternative text is written where Word reads it (pandoc puts the
  picture's address in the inner `descr`, which would be read aloud), a table's header row
  is marked to repeat and to be read as a header (`w:tblHeader`), and the title paragraph is
  dropped when the first heading says the same thing. Headings are Word's Heading 1 to 6
  styles, lists are Word lists, links are hyperlinks, and the document language is set.

Equations are counted: the file must hold as many `m:oMath` as the HTML had `<math>`. If
pandoc could not convert one, the export fails (`DocxExportError("equations")`) rather than
hand over a file that silently lacks an equation; the HTML and EPUB stay available.

What is not carried: scripts and styles (the Word styles are pandoc's), inline SVG,
remote pictures, the page markers, and the colours of the flagged boxes (the words of a flag
and an image's alt text are kept).
"""

from __future__ import annotations

import base64
import contextlib
import io
import os
import re
import shutil
import signal
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET  # only serialises what defusedxml parsed

from defusedxml import ElementTree as SafeET
from PIL import Image

from readablestem.core.childenv import child_env
from readablestem.pipeline.export_canvas import _DATA_URI, _find, _Node, _parse, _write
from readablestem.pipeline.export_epub import _HEADING, _LANGUAGE, _sanitise, _text

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TIMEOUT_S = 60.0
CPU_SECONDS = 90
MAX_DOCX_BYTES = 300 * 1024 * 1024
HEAP_LIMIT = "2g"
OPEN_FILES = 256

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_NAMESPACES = {
    "w": W,
    "m": M,
    "wp": WP,
    "pic": PIC,
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "o": "urn:schemas-microsoft-com:office:office",
    "v": "urn:schemas-microsoft-com:vml",
    "w10": "urn:schemas-microsoft-com:office:word",
}
for _prefix, _uri in _NAMESPACES.items():
    ET.register_namespace(_prefix, _uri)


class DocxExportError(ValueError):
    """The document cannot be written as a Word file. `code` is a short category (no
    document text): `unavailable`, `failed`, `timeout`, `equations`, `invalid`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class DocxExport:
    title: str
    lang: str
    data: bytes
    equations: int  # `<math>` in the HTML, and `m:oMath` in the file
    headings: int
    images: int  # pictures with alt text written
    tables: int
    header_rows: int  # tables whose first row is marked as a header


# --------------------------------------------------------------------------- pandoc


def pandoc_command(configured: str | None = None) -> str | None:
    """The pandoc program: the configured path, else `pandoc` on PATH."""
    return shutil.which(configured or "pandoc")


def available(configured: str | None = None) -> bool:
    return pandoc_command(configured) is not None


def _limits() -> None:  # pragma: no cover - runs in the forked child, before exec
    import resource

    # No address-space limit: the Haskell runtime reserves a large range it never touches;
    # the heap is capped with `+RTS -M` instead.
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_DOCX_BYTES, MAX_DOCX_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (OPEN_FILES, OPEN_FILES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:  # if the web process dies, so does pandoc (Linux)
        import ctypes

        ctypes.CDLL(None).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def _run_pandoc(html: str, lang: str, pandoc: str, timeout_s: float) -> bytes:
    work = Path(tempfile.mkdtemp(prefix="readablestem-docx-"))
    try:
        work.chmod(0o700)
        out = work / "out.docx"
        command = [
            pandoc,
            "+RTS",
            f"-M{HEAP_LIMIT}",
            "-RTS",
            "-f",
            "html",
            "-t",
            "docx",
            "-M",
            f"lang={lang}",
            "-o",
            str(out),
        ]
        env = child_env() | {"HOME": str(work), "TMPDIR": str(work)}
        try:
            process = subprocess.Popen(
                command,
                env=env,
                cwd=work,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                preexec_fn=_limits,
            )
        except OSError:
            raise DocxExportError("unavailable") from None
        try:
            try:
                process.communicate(html.encode("utf-8"), timeout=timeout_s)
            except subprocess.TimeoutExpired:
                raise DocxExportError("timeout") from None
        finally:
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
        if process.returncode != 0 or not out.is_file():
            raise DocxExportError("failed")
        return out.read_bytes()
    except OSError:
        raise DocxExportError("failed") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- before


def _webp_to_png(root: _Node) -> None:
    """Word cannot show WebP: such a picture is written as a PNG."""
    for img in (n for n in root.walk() if n.tag == "img"):
        match = _DATA_URI.match(img.attrs.get("src", ""))
        if not match or match.group("type").lower() != "image/webp":
            continue
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", match.group("data")), validate=True)
            with Image.open(io.BytesIO(raw)) as picture:
                buffer = io.BytesIO()
                picture.convert("RGBA").save(buffer, "PNG")
        except (ValueError, OSError):
            raise DocxExportError("invalid") from None
        img.attrs["src"] = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _replace(node: _Node, replacement: list[_Node | str]) -> None:
    assert node.parent is not None
    siblings = node.parent.children
    at = siblings.index(node)
    siblings[at : at + 1] = replacement
    for child in replacement:
        if isinstance(child, _Node):
            child.parent = node.parent
    node.parent = None


def _prepare(root: _Node) -> None:
    """Page markers out; a long description's `<details>` as a bold line and paragraphs."""
    for node in list(root.walk()):
        if node.parent is None:
            continue
        marker = node.tag == "hr" and "doc-pagebreak" in node.attrs.get("role", "").split()
        if marker or (node.tag == "div" and "pagebreak" in node.classes()):
            _replace(node, [])
        elif node.tag == "summary":
            strong = _Node("strong", {}, list(node.children))
            for child in strong.children:
                if isinstance(child, _Node):
                    child.parent = strong
            paragraph = _Node("p", {}, [strong])
            strong.parent = paragraph
            _replace(node, [paragraph])
        elif node.tag == "details":
            _replace(node, list(node.children))
        elif node.tag == "figure" and not _is_picture_figure(node):
            # Without a picture pandoc writes a figure as a one-cell table, a layout table
            # for a screen reader: the words stay, as paragraphs.
            _replace(node, [_caption_paragraph(c) for c in node.children])


def _is_picture_figure(figure: _Node) -> bool:
    return any(isinstance(c, _Node) and c.tag == "img" for c in figure.children)


def _caption_paragraph(child: _Node | str) -> _Node | str:
    if isinstance(child, _Node) and child.tag == "figcaption":
        child.tag = "p"
    return child


def _normal(text: str) -> str:
    return " ".join(text.split())


# --------------------------------------------------------------------------- after


def _fix_document(xml: bytes, title: str) -> tuple[bytes, tuple[int, int, int, int]]:
    """Alt text on the picture itself, header rows, the duplicate title paragraph. Returns the
    new part and the counts (equations, pictures with alt text, tables, header rows)."""
    root = SafeET.fromstring(xml)
    pictures = 0
    for inline in root.iter(f"{{{WP}}}inline"):
        doc_pr = inline.find(f"{{{WP}}}docPr")
        descr = (doc_pr.get("descr") if doc_pr is not None else "") or ""
        for name in inline.iter(f"{{{PIC}}}cNvPr"):
            if descr:
                name.set("descr", descr)
            else:  # pandoc wrote the address; never leave that to be read aloud
                name.attrib.pop("descr", None)
        pictures += 1 if descr else 0
    tables = headers = 0
    for table in root.iter(f"{{{W}}}tbl"):
        tables += 1
        look = table.find(f"{{{W}}}tblPr/{{{W}}}tblLook")
        row = table.find(f"{{{W}}}tr")
        if row is None:
            continue
        first_row = look is not None and look.get(f"{{{W}}}firstRow") == "1"
        properties = row.find(f"{{{W}}}trPr")
        marked = properties is not None and properties.find(f"{{{W}}}tblHeader") is not None
        if first_row and not marked:
            if properties is None:
                properties = ET.Element(f"{{{W}}}trPr")
                row.insert(0, properties)
            ET.SubElement(properties, f"{{{W}}}tblHeader").set(f"{{{W}}}val", "on")
            marked = True
        headers += 1 if marked else 0
    body = root.find(f"{{{W}}}body")
    if body is not None:
        _drop_title_paragraph(body, title)
    equations = sum(1 for _ in root.iter(f"{{{M}}}oMath"))
    out = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    return out, (equations, pictures, tables, headers)


def _paragraph_text(paragraph: ET.Element) -> str:
    return _normal("".join(t.text or "" for t in paragraph.iter(f"{{{W}}}t")))


def _style(paragraph: ET.Element) -> str:
    style = paragraph.find(f"{{{W}}}pPr/{{{W}}}pStyle")
    return style.get(f"{{{W}}}val", "") if style is not None else ""


def _drop_title_paragraph(body: ET.Element, title: str) -> None:
    """Pandoc writes the page title as a Title paragraph before the content. When the first
    heading says the same thing, the line would read twice; the document's title property
    (`docProps/core.xml`) keeps the title either way."""
    paragraphs = [p for p in body if p.tag == f"{{{W}}}p"]
    if not paragraphs or _style(paragraphs[0]) != "Title":
        return
    first = next((p for p in paragraphs[1:] if _style(p).startswith("Heading")), None)
    if first is not None and _paragraph_text(first) == _normal(title):
        body.remove(paragraphs[0])


def _rebuild(data: bytes, title: str) -> tuple[bytes, tuple[int, int, int, int]]:
    try:
        source = zipfile.ZipFile(io.BytesIO(data))
        names = source.namelist()
        if "word/document.xml" not in names:
            raise DocxExportError("invalid")
        out = io.BytesIO()
        counts = (0, 0, 0, 0)  # equations, pictures, tables, header rows
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                content = source.read(info.filename)
                if info.filename == "word/document.xml":
                    content, counts = _fix_document(content, title)
                target.writestr(info, content)
    except (zipfile.BadZipFile, ET.ParseError, SafeET.ParseError):
        raise DocxExportError("invalid") from None
    return out.getvalue(), counts


# --------------------------------------------------------------------------- export


def export_docx(
    html: str,
    *,
    title: str | None = None,
    pandoc: str | None = None,
    timeout_s: float = TIMEOUT_S,
) -> DocxExport:
    """The Word file of a finished document. `title` overrides the page's `<title>`;
    `pandoc` is the program's path (default: `pandoc` on PATH)."""
    program = pandoc_command(pandoc)
    if program is None:
        raise DocxExportError("unavailable")
    root = _parse(html)
    html_tag, body = _find(root, "html"), _find(root, "body")
    if html_tag is None or body is None:
        raise DocxExportError("invalid")
    lang = html_tag.attrs.get("lang", "").strip()
    lang = lang if _LANGUAGE.match(lang) else "en"
    page_title = _find(root, "title")
    title = (
        _normal(title or (_text(page_title) if page_title else "") or "") or "Converted document"
    )

    images = {n.attrs["src"] for n in root.walk() if n.tag == "img" and n.attrs.get("src")}
    _sanitise(root, {s for s in images if _DATA_URI.match(s)})
    _prepare(root)
    _webp_to_png(root)
    n_math = sum(1 for n in body.walk() if n.tag == "math")
    n_headings = sum(1 for n in body.walk() if _HEADING.match(n.tag))
    document = (
        f'<!doctype html><html lang="{escape(lang, quote=True)}"><head><meta charset="utf-8">'
        f"<title>{escape(title, quote=False)}</title></head>"
        f"<body>{''.join(_write(c) for c in body.children)}</body></html>"
    )
    data = _run_pandoc(document, lang, program, timeout_s)
    data, (equations, pictures, tables, headers) = _rebuild(data, title)
    if equations != n_math:
        raise DocxExportError("equations")
    return DocxExport(title, lang, data, equations, n_headings, pictures, tables, headers)
