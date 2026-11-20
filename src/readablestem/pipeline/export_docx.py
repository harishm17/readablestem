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
  distribution build, which reads its default styles from disk). A long description
  (`<details>`) becomes its heading line in bold and its paragraphs, in place after the
  figure. `<main>` is unwrapped: when a page has one, pandoc reads only what is inside it,
  which would leave out the Contents and a draft's banner (the student note "About this
  version" is inside `<main>`, so every output made from the page has it). A WebP picture
  becomes a PNG (Word cannot show WebP). The language and the title are set explicitly.
- **In pandoc.** One Lua filter (`data/export_docx.lua`, passed with `--lua-filter`) takes out
  the page-break markers (a Word document has its own pages) and the skip link, and writes a
  figure with no picture as paragraphs.
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

Equations are counted: the file must hold as many `m:oMath` as the HTML had `<math>`, less the
ones the file says it could not write. One equation pandoc cannot write as an Office equation
(a MathML shape its TeX step refuses leaves raw TeX in the text, or nothing) does not fail the
file: the filter tries each equation once and writes, where it cannot, the equation's reading
and the words "This equation could not be written as a Word equation; it is complete on the
web page" in the "Not Converted" style (`DocxExport.not_word` lists their element ids; the
failure is said in the file, never hidden). If the count still does not add up (an equation
lost with nothing said), or such an equation's reading is too long to write (`_READING_MAX`:
the file would hold the sentence and nothing of the equation), the export fails
(`DocxExportError("equations")`) rather than hand over a file that lacks an equation; the HTML
and EPUB stay available.

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
import secrets
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

from readablestem.core.childenv import child_env
from readablestem.core.images import open_within_budget
from readablestem.pipeline import assemble, output_chars
from readablestem.pipeline.export_canvas import _DATA_URI, _find, _Node, _parse, _write
from readablestem.pipeline.export_epub import _HEADING, _LANGUAGE, _sanitise, _text

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FILTER = Path(__file__).parent / "data" / "export_docx.lua"  # pandoc's text changes (below)
TIMEOUT_S = 60.0
CPU_SECONDS = 90
MAX_DOCX_BYTES = 300 * 1024 * 1024
HEAP_LIMIT = "2g"
OPEN_FILES = 256

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
ADEC = "http://schemas.microsoft.com/office/drawing/2017/decorative"
_DECORATIVE_URI = "{C183D7F6-B498-43B3-948B-1728B52AA6E4}"  # Word's own id for the extension
_NAMESPACES = {
    "w": W,
    "m": M,
    "wp": WP,
    "pic": PIC,
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": A,
    "adec": ADEC,
    "o": "urn:schemas-microsoft-com:office:office",
    "v": "urn:schemas-microsoft-com:vml",
    "w10": "urn:schemas-microsoft-com:office:word",
}
for _prefix, _uri in _NAMESPACES.items():
    ET.register_namespace(_prefix, _uri)


# Parts of the Word file that hold no document text, left out of the character rule: the
# list styles draw their bullets with Symbol and Wingdings glyphs at private-use code points
# (U+F0B7, U+F0A7), as Word itself does; Word shows them as list markers, not as text.
_STYLE_PARTS = frozenset({"word/numbering.xml"})
# The parts that hold text, and so links.
_LINK_PARTS = ("word/document.xml", "word/footnotes.xml")


class DocxExportError(ValueError):
    """The document cannot be written as a Word file. `code` is a short category (no
    document text): `unavailable`, `failed`, `timeout`, `equations`, `invalid`,
    `unreadable_character` (the built file holds a character that is not text,
    `output_chars`: the page never holds one, so it is the export's own)."""

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
    not_word: list[str]  # element ids of equations written as words, not as a Word equation
    tables: int
    header_rows: int  # tables whose first row is marked as a header
    links_unlinked: int = 0  # in-page links with no target in the file, written as their words


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


def _run_pandoc(html: str, lang: str, pandoc: str, timeout_s: float) -> tuple[bytes, str]:
    """The Word file pandoc makes, and what the filter said on stderr (`_failed_keys` reads it:
    pandoc's own warnings quote the document, so nothing else of it is kept)."""
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
            "--lua-filter",
            str(FILTER),
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
                stderr=subprocess.PIPE,
                start_new_session=True,
                preexec_fn=_limits,
            )
        except OSError:
            raise DocxExportError("unavailable") from None
        try:
            try:
                _, said = process.communicate(html.encode("utf-8"), timeout=timeout_s)
            except subprocess.TimeoutExpired:
                raise DocxExportError("timeout") from None
        finally:
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
        if process.returncode != 0 or not out.is_file():
            raise DocxExportError("failed")
        return out.read_bytes(), said.decode("utf-8", "replace")
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
            with open_within_budget(raw) as picture:
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


def _lines_of(pre: _Node) -> _Node:
    """A code block that holds an equation (an algorithm's step), as a paragraph: pandoc reads
    a code block as plain text and would write the equation as its characters. Its lines are
    separated by line breaks and its indentation is no-break spaces."""
    paragraph = _Node("p", {k: v for k, v in pre.attrs.items() if k == "id"})
    start = True  # nothing but indentation on this line so far

    def add(children: list[_Node | str]) -> None:
        nonlocal start
        for child in children:
            if isinstance(child, _Node) and child.tag == "code":
                add(child.children)
            elif isinstance(child, _Node):
                child.parent = paragraph
                paragraph.children.append(child)
                start = False
            else:
                for number, line in enumerate(child.split("\n")):
                    if number:
                        br = _Node("br", parent=paragraph)
                        paragraph.children.append(br)
                        start = True
                    body = line.lstrip(" ") if start else line
                    if body:
                        start = False
                    if line:
                        # a line number set apart from its step leaves a run of spaces
                        body = re.sub(" {2,}", lambda run: "\u00a0" * len(run.group()), body)
                        paragraph.children.append("\u00a0" * (len(line) - len(body)) + body)

    add(pre.children)
    return paragraph


def _prepare(root: _Node) -> None:
    """The changes only the page's own markup can carry (pandoc drops the elements): a long
    description's `<details>` as a bold line and paragraphs, a sidebar's `<aside>` as a div in
    Word's "Sidebar" style, `<main>` unwrapped, and a code
    block that holds an equation as lines of a paragraph (`_lines_of`). Pandoc's
    HTML reader reads only what is inside `<main>` when there is one (3.7.0.2); the Contents
    and a draft's banner stand before it and are in the file. (The note "About this version"
    is inside `<main>`.) The page markers, the skip link and a figure with no picture are
    changed on pandoc's side, in `data/export_docx.lua`."""
    for node in list(root.walk()):
        if node.parent is None:
            continue
        if node.tag == "a" and "doc-noteref" in node.attrs.get("role", "").split():
            # The reader would make the mark an empty note (the note stands after it): the
            # filter moves the note's words to the mark (`data/export_docx.lua`).
            del node.attrs["role"]
            node.attrs["data-rsnote"] = "1"
        if node.tag == "aside":
            # Pandoc drops `<aside>` and its label: the paragraphs would read as body text.
            # A div with `custom-style` gives them Word's own style, named "Sidebar".
            node.tag = "div"
            node.attrs = {k: v for k, v in node.attrs.items() if k == "id"}
            node.attrs["custom-style"] = assemble.ASIDE_LABEL
        elif node.tag == "main":
            _replace(node, list(node.children))
        elif node.tag == "pre" and any(n.tag == "math" for n in node.walk()):
            _replace(node, [_lines_of(node)])
        elif node.tag == "summary":
            strong = _Node("strong", {}, list(node.children))
            for child in strong.children:
                if isinstance(child, _Node):
                    child.parent = strong
            paragraph = _Node("p", {}, [strong])
            strong.parent = paragraph
            _replace(node, [paragraph])
        elif node.tag == "details":
            inner: list[_Node | str] = list(node.children)
            if node.attrs.get("id"):  # a link to the description still lands at its start
                inner.insert(0, _Node("span", {"id": node.attrs["id"]}))
            _replace(node, inner)


def _normal(text: str) -> str:
    return " ".join(text.split())


# The longest reading written in place of an equation the file could not hold: a long one
# is left out, since a reading cut short would mislead more than the words alone.
_READING_MAX = 400


def _mark_equations(root: _Node, token: str) -> tuple[list[str], set[int]]:
    """Wrap every `<math>` in `<span data-eq="TOKEN-N" data-reading="...">`, so the filter
    can try each equation and write words where Word cannot hold it (`data/export_docx.lua`).
    The reading is the stored speech (`alttext`) or else the equation's own characters.
    Returns the element id of each equation (the nearest id on or above it, "" when none),
    by N, and the N of each equation whose reading is too long to write: the file would hold
    nothing of such an equation if Word cannot hold it."""
    ids: list[str] = []
    unsaid: set[int] = set()
    for node in [n for n in root.walk() if n.tag == "math"]:
        holder: _Node | None = node
        while holder is not None and not holder.attrs.get("id"):
            holder = holder.parent
        ids.append(holder.attrs["id"] if holder is not None else "")
        reading = _normal(node.attrs.get("alttext", "")) or _normal(_text(node))
        attrs = {"data-eq": f"{token}-{len(ids) - 1}"}
        if 0 < len(reading) <= _READING_MAX:
            attrs["data-reading"] = reading
        elif reading:
            unsaid.add(len(ids) - 1)
        span = _Node("span", attrs, [node])
        _replace(node, [span])
        node.parent = span
    return ids, unsaid


def _failed(said: str, token: str) -> set[int]:
    """The equations the filter could not write as Word equations, by number (its lines on
    stderr; anything else pandoc said quotes the document and is not read)."""
    found = re.findall(rf"^RSFAIL {re.escape(token)}-(\d+)$", said, re.MULTILINE)
    return {int(n) for n in found}


# Where pandoc writes an element's id as a Word bookmark in every version we run (3.1.3 in CI,
# 3.1.11.1 in production, 3.7 here; checked by running each with each tag): a heading, `div`,
# `span`, `section`, `table`, `pre`, list item and picture. On any other element it drops the
# id, and a link to it leads nowhere. (A figure's id is a bookmark only from 3.2 on: the filter
# puts the figure in a div that has the id.)
_BOOKMARKED = frozenset(["div", "span", "section", "table", "pre", "li", "img"]) | {
    f"h{n}" for n in range(1, 7)
}
# A link target's id goes on an empty span (which pandoc writes as a bookmark) at the start of
# the element; for these it goes before the element, since a span cannot start inside them.
_INLINE = frozenset(
    ["math", "a", "em", "strong", "i", "b", "code", "sup", "sub", "mark", "small", "u", "s"]
)
# ... and for a list, a row or a table's body, at the start of its first item or cell.
_GROUPS = frozenset(["ul", "ol", "dl", "tr", "tbody", "thead", "tfoot"])


def _land_links(root: _Node) -> None:
    """Every id an in-page link names is on something pandoc keeps (F-514): where it is not
    (a paragraph, a figure, a list, a cell, a row), the id moves to an empty span at the start
    of the element. A link whose target is still missing after pandoc is plain text
    (`_unlink`)."""
    targets = {
        n.attrs["href"][1:]
        for n in root.walk()
        if n.tag == "a" and n.attrs.get("href", "").startswith("#") and len(n.attrs["href"]) > 1
    }
    for node in list(root.walk()):
        ident = node.attrs.get("id")
        if not ident or ident not in targets or node.parent is None:
            continue
        if node.tag in _BOOKMARKED:
            continue
        # A figure with a picture keeps its id for the filter, which wraps the figure in a
        # div with it (a span inside the figure makes pandoc write a one-cell layout table);
        # one without is written as paragraphs by the filter.
        if node.tag == "figure" and any(n.tag == "img" for n in node.walk()):
            continue
        if node.tag == "hr":  # nothing can stand in it (a page marker, which is left out)
            continue
        del node.attrs["id"]
        mark = _Node("span", {"id": ident})
        inside = node
        while inside.tag in _GROUPS:
            first = next((c for c in inside.children if isinstance(c, _Node)), None)
            if first is None:
                break
            inside = first
        if inside.tag in _INLINE:
            before = inside.parent if inside.parent and "data-eq" in inside.parent.attrs else inside
            assert before.parent is not None
            siblings = before.parent.children
            siblings.insert(siblings.index(before), mark)
            mark.parent = before.parent
        else:
            inside.children.insert(0, mark)
            mark.parent = inside


# --------------------------------------------------------------------------- after


def _unlink(root: ET.Element, bookmarks: set[str]) -> int:
    """A link inside the document to a bookmark the file does not have is its words, not a
    link that leads nowhere; returns how many. (A link is dead when the page's id was on
    something pandoc does not write, or nothing at all, `_land_links`.)"""
    parent_of = {child: parent for parent in root.iter() for child in parent}
    count = 0
    for link in list(root.iter(f"{{{W}}}hyperlink")):
        anchor = link.get(f"{{{W}}}anchor")
        if anchor is None or anchor in bookmarks:
            continue
        parent = parent_of[link]
        at = list(parent).index(link)
        parent.remove(link)
        for offset, run in enumerate(list(link)):
            properties = run.find(f"{{{W}}}rPr")
            style = properties.find(f"{{{W}}}rStyle") if properties is not None else None
            if properties is not None and style is not None:
                if style.get(f"{{{W}}}val") == "Hyperlink":
                    properties.remove(style)
                if len(properties) == 0:
                    run.remove(properties)
            parent.insert(at + offset, run)
        count += 1
    return count


def _mark_decorative(doc_pr: ET.Element) -> None:
    """Mark a picture as decorative the way Word does (`adec:decorative` in the extension list
    of its `docPr`), so Word's checker does not ask for alt text and a screen reader leaves
    it out."""
    doc_pr.set("descr", "")
    extensions = doc_pr.find(f"{{{A}}}extLst")
    if extensions is None:
        extensions = ET.SubElement(doc_pr, f"{{{A}}}extLst")
    extension = ET.SubElement(extensions, f"{{{A}}}ext", {"uri": _DECORATIVE_URI})
    ET.SubElement(extension, f"{{{ADEC}}}decorative", {"val": "1"})


def _fix_document(
    xml: bytes, title: str, bookmarks: set[str]
) -> tuple[bytes, tuple[int, int, int, int, int]]:
    """Alt text on the picture itself, header rows, the duplicate title paragraph, links with
    no target. Returns the new part and the counts (equations, pictures with alt text, tables,
    header rows, links turned into words)."""
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
        if not descr and doc_pr is not None:  # the page wrote it as a decoration (empty alt)
            _mark_decorative(doc_pr)
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
    unlinked = _unlink(root, bookmarks)
    out = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    return out, (equations, pictures, tables, headers, unlinked)


def _bookmarks(source: zipfile.ZipFile) -> set[str]:
    """The names of the bookmarks in the body and the notes: a link in either may name one in
    the other."""
    names: set[str] = set()
    for part in _LINK_PARTS:
        if part in source.namelist():
            root = SafeET.fromstring(source.read(part))
            names.update(b.get(f"{{{W}}}name", "") for b in root.iter(f"{{{W}}}bookmarkStart"))
    return names


def _paragraph_text(paragraph: ET.Element) -> str:
    return _normal("".join(t.text or "" for t in paragraph.iter(f"{{{W}}}t")))


def _style(paragraph: ET.Element) -> str:
    style = paragraph.find(f"{{{W}}}pPr/{{{W}}}pStyle")
    return style.get(f"{{{W}}}val", "") if style is not None else ""


def _drop_title_paragraph(body: ET.Element, title: str) -> None:
    """Pandoc writes the page title as a Title paragraph before the content. When the first
    heading is the same title (`assemble.same_title`: case, spacing, punctuation and a
    section number aside, F-568), the line would read twice, so the Title paragraph goes and
    the heading, the page's one H1 (SW6c), stays; the document's title property
    (`docProps/core.xml`) keeps the title either way."""
    paragraphs = [p for p in body if p.tag == f"{{{W}}}p"]
    if not paragraphs or _style(paragraphs[0]) != "Title":
        return
    first = next((p for p in paragraphs[1:] if _style(p).startswith("Heading")), None)
    if first is not None and assemble.same_title(_paragraph_text(first), title):
        body.remove(paragraphs[0])


def _fix_notes(xml: bytes, bookmarks: set[str]) -> tuple[bytes, int, int]:
    """The footnotes part: links with no target, and the equations in it (counted with the
    body's). Returns the new part, those equations and the links turned into words."""
    root = SafeET.fromstring(xml)
    unlinked = _unlink(root, bookmarks)
    equations = sum(1 for _ in root.iter(f"{{{M}}}oMath"))
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True), equations, unlinked


def _rebuild(data: bytes, title: str) -> tuple[bytes, tuple[int, int, int, int, int]]:
    try:
        source = zipfile.ZipFile(io.BytesIO(data))
        names = source.namelist()
        if "word/document.xml" not in names:
            raise DocxExportError("invalid")
        bookmarks = _bookmarks(source)
        out = io.BytesIO()
        counts = (0, 0, 0, 0, 0)  # equations, pictures, tables, header rows, unlinked
        in_notes = (0, 0)  # equations, unlinked
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                content = source.read(info.filename)
                if info.filename == "word/document.xml":
                    content, counts = _fix_document(content, title, bookmarks)
                elif info.filename == "word/footnotes.xml":
                    content, *in_notes = _fix_notes(content, bookmarks)
                target.writestr(info, content)
    except (zipfile.BadZipFile, ET.ParseError, SafeET.ParseError):
        raise DocxExportError("invalid") from None
    equations, pictures, tables, headers, unlinked = counts
    return out.getvalue(), (
        equations + in_notes[0],
        pictures,
        tables,
        headers,
        unlinked + in_notes[1],
    )


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
    root = _parse(assemble.with_medium(html, "Word"))
    html_tag, body = _find(root, "html"), _find(root, "body")
    if html_tag is None or body is None:
        raise DocxExportError("invalid")
    lang = html_tag.attrs.get("lang", "").strip()
    lang = lang if _LANGUAGE.match(lang) else "en"
    page_title = _find(root, "title")
    title = (
        _normal(title or (_text(page_title) if page_title else "") or "") or assemble.DEFAULT_TITLE
    )

    images = {n.attrs["src"] for n in root.walk() if n.tag == "img" and n.attrs.get("src")}
    _sanitise(root, {s for s in images if _DATA_URI.match(s)})
    _prepare(root)
    _webp_to_png(root)
    token = secrets.token_hex(6)
    equation_ids, unsaid = _mark_equations(body, token)
    _land_links(body)
    n_math = len(equation_ids)
    n_headings = sum(1 for n in body.walk() if _HEADING.match(n.tag))
    document = (
        f'<!doctype html><html lang="{escape(lang, quote=True)}"><head><meta charset="utf-8">'
        f"<title>{escape(title, quote=False)}</title></head>"
        f"<body>{''.join(_write(c) for c in body.children)}</body></html>"
    )
    data, said = _run_pandoc(document, lang, program, timeout_s)
    data, (equations, pictures, tables, headers, unlinked) = _rebuild(data, title)
    not_word = sorted(_failed(said, token) & set(range(n_math)))
    if equations + len(not_word) != n_math or unsaid & set(not_word):
        raise DocxExportError("equations")
    if output_chars.count_in_zip(data, skip=_STYLE_PARTS):
        raise DocxExportError("unreadable_character")
    return DocxExport(
        title,
        lang,
        data,
        equations,
        n_headings,
        pictures,
        [equation_ids[n] for n in not_word],
        tables,
        headers,
        unlinked,
    )
