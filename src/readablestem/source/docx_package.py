"""A Word file as the reader needs it: the gate it passes and the working copy pandoc is given
(WS18.14, WS18.15).

Two things, both in the process that works on the bytes (never the file's name):

`check(data)`: the gate. It is today's Office preflight (`web.office_convert.preflight`: zip
limits, no macros, no DTD or entity declaration, no relationship outside the file except a
hyperlink, exactly one Word document) and, for what a program that reads the *text* of the file
must also be sure of, four more refusals: a member name that leaves the package (`..`, an
absolute path, a backslash, a drive letter, a repeated name), a member that inflates far beyond
its stored size (a zip bomb inside the zip), a part that is larger than the reader will parse,
and XML nested deeper than any document is. Each is `OfficeRejected("office_unsafe")`.

`prepare(data)`: the working copy and the facts. pandoc is never given the upload. It is given
a new zip, written here from the parts the reader needs and from nothing else: the main part,
its styles, numbering, footnotes and endnotes, and the relationships those parts need. Left
out, so that pandoc cannot open, follow or print them: document properties (`docProps`: the
reader never reads properties), embedded objects and packages (`word/embeddings`), headers and
footers, comments, the settings and theme, custom XML, fonts, every picture (our own code takes
a picture from the package, not pandoc), and every relationship that points outside the file
(a hyperlink's address is kept: it is text and is never fetched). The original is not changed.

The same pass reads what pandoc drops or prints wrongly (K209g, K209h) and writes it into the
working copy or into `Facts`:

- hidden text (`w:vanish`, in the run, its character style or its paragraph style) is taken out
  of the working copy and counted, so that pandoc cannot print it;
- the header rows a table is marked with (`w:tblHeader`) ride through pandoc on the table's
  style id (`HEADER_STYLE`): pandoc reads a table's `firstRow` look as a header too, and the
  reader must tell a mark from a default;
- the words the file holds (`Facts.words`), counted the way the reader counts what it read, so
  that a word pandoc did not carry is a counted gap (`docx_reader`);
- counts of what pandoc leaves no trace of: headers and footers, text boxes, SmartArt, charts,
  embedded objects, right-to-left paragraphs, and the language of each run's text.
"""

from __future__ import annotations

import base64
import io
import posixpath
import re
import zipfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET
from xml.parsers import expat

import defusedxml.ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from readablestem.web import office_convert as office
from readablestem.web.office_convert import Census, OfficeRejected

MAX_PART_BYTES = 80 * 1024 * 1024  # the main part: no document is larger
MAX_SMALL_PART_BYTES = 20 * 1024 * 1024  # styles, numbering, footnotes, endnotes
MAX_DEPTH = 300  # nesting of elements: Word writes about 30 in a table of tables
MAX_RATIO = 400  # inflation of one member; above it only when the member is large (below)
RATIO_FLOOR = 4 * 1024 * 1024
MAX_MEDIA_BYTES = 30 * 1024 * 1024  # one picture (the figure checks cap it again)
HEADER_STYLE = "rs-header-"  # + the number of leading rows marked as header rows
_DRIVE = re.compile(r"^[A-Za-z]:")

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_STRICT_NS = "http://purl.oclc.org/ooxml/wordprocessingml"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_NAMESPACES = {
    "w": W,
    "r": R,
    "m": M,
    "mc": MC,
    "": PKG_RELS,
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "v": "urn:schemas-microsoft-com:vml",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    "a16": "http://schemas.microsoft.com/office/drawing/2014/main",
}
for _prefix, _uri in _NAMESPACES.items():
    ET.register_namespace(_prefix, _uri)

# Relationship types the working copy keeps (after the last "/" of the type).
_KEPT_REL_TYPES = {"styles", "numbering", "footnotes", "endnotes", "hyperlink", "image"}
_ROOT_KEPT = {"officeDocument"}
# pandoc drops a picture whose file is not in the package, and it decodes the pictures it finds.
# So each picture of the working copy is this one-pixel file under the original name: pandoc keeps
# the picture, its alt text and its place, and never sees a byte of the real image.
PLACEHOLDER = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

_P, _R, _T = f"{{{W}}}p", f"{{{W}}}r", f"{{{W}}}t"
_RPR, _PPR = f"{{{W}}}rPr", f"{{{W}}}pPr"
_TBL, _TR, _TRPR = f"{{{W}}}tbl", f"{{{W}}}tr", f"{{{W}}}trPr"
_TBLPR, _TBLSTYLE, _TBLHEADER = f"{{{W}}}tblPr", f"{{{W}}}tblStyle", f"{{{W}}}tblHeader"
_VANISH, _VAL = f"{{{W}}}vanish", f"{{{W}}}val"
_LANG, _BIDI = f"{{{W}}}lang", f"{{{W}}}bidi"
_PSTYLE, _RSTYLE = f"{{{W}}}pStyle", f"{{{W}}}rStyle"
_TXBX = f"{{{W}}}txbxContent"
_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
_FALLBACK = f"{{{MC}}}Fallback"
_FLDSIMPLE, _INSTR = f"{{{W}}}fldSimple", f"{{{W}}}instr"
_OMATH = f"{{{M}}}oMath"
_DELETED = (f"{{{W}}}del", f"{{{W}}}moveFrom")
_NOBREAK_HYPHEN = f"{{{W}}}noBreakHyphen"
_BR, _CR = f"{{{W}}}br", f"{{{W}}}cr"
# a tab, a break and the mark of a note each stand between two words in the reading
_BREAKS = (f"{{{W}}}tab", _BR, _CR, f"{{{W}}}footnoteReference", f"{{{W}}}endnoteReference")
_FALSE = {"0", "false", "off"}


# --------------------------------------------------------------------------- the gate


def check(data: bytes) -> Census:
    """The census of a Word file that may be read; `OfficeRejected` otherwise (module
    docstring). It is `office_convert.preflight` first, so a file refused there is refused here
    with the same code; a PowerPoint file is not a Word file ("office_unsupported")."""
    census = office.preflight(data)
    if census.kind != "docx":
        raise OfficeRejected("office_unsupported")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            _check_members(archive)
            for name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
                if name in archive.namelist():
                    _check_depth(archive, name)
    except OfficeRejected:
        raise
    except Exception:  # fails closed, like the preflight
        raise OfficeRejected("unreadable") from None
    return census


def _check_members(archive: zipfile.ZipFile) -> None:
    seen: set[str] = set()
    for info in archive.infolist():
        name = info.filename
        folded = name.lower()
        parts = name.split("/")
        if (
            name.startswith("/") or "\\" in name or _DRIVE.match(name) or ".." in parts
            or "." in parts[:-1] or (name.endswith("/") and info.file_size)
            or folded in seen
        ):  # fmt: skip
            raise OfficeRejected("office_unsafe")
        seen.add(folded)
        if info.file_size > RATIO_FLOOR and info.file_size > MAX_RATIO * max(info.compress_size, 1):
            raise OfficeRejected("office_unsafe")
        limit = MAX_PART_BYTES if name == "word/document.xml" else None
        if limit is None and name.endswith(".xml") and name.startswith("word/"):
            limit = MAX_SMALL_PART_BYTES if not name.startswith("word/media/") else None
        if limit is not None and info.file_size > limit:
            raise OfficeRejected("office_unsafe")


def _check_depth(archive: zipfile.ZipFile, name: str) -> None:
    """Refuse a part nested deeper than `MAX_DEPTH`, counted by a parser that builds nothing."""
    depth = 0

    def start(_tag: str, attrs: dict[str, str]) -> None:
        nonlocal depth
        depth += 1
        if depth == 1 and any(v.startswith(_STRICT_NS) for v in attrs.values()):
            raise OfficeRejected("office_unsupported")  # pandoc cannot read Strict Open XML
        if depth > MAX_DEPTH:
            raise OfficeRejected("office_unsafe")

    def end(_tag: str) -> None:
        nonlocal depth
        depth -= 1

    parser = expat.ParserCreate()
    parser.StartElementHandler, parser.EndElementHandler = start, end
    try:
        with archive.open(name) as part:
            while chunk := part.read(1 << 20):
                parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except expat.ExpatError:
        raise OfficeRejected("office_unsafe") from None


# --------------------------------------------------------------------------- the facts


@dataclass
class Facts:
    """What the reader's own pass found, counts only (never a word of the file), and the words
    for the coverage check (`words`, a count by word, which lives only as long as the run)."""

    hidden_runs: int = 0
    hidden_words: int = 0
    text_boxes: int = 0
    headers_footers: int = 0  # header and footer parts the working copy leaves out
    smartart: int = 0
    charts: int = 0
    embedded: int = 0  # embedded objects and packages
    rtl_paragraphs: int = 0
    simple_fields: int = 0  # `w:fldSimple`, written as their runs (pandoc drops their results)
    equations: int = 0  # `m:oMath` in the body, notes included (a Fallback copy once)
    tables: int = 0
    marked_tables: int = 0  # tables with at least one row marked "repeat as header row"
    # for each table of the body, in document order, how many leading rows are so marked: the
    # reading of a pandoc that does not hand the style id back (3.1) goes by this list
    header_marks: list[int] = field(default_factory=list)
    language_chars: Counter[str] = field(default_factory=Counter)  # BCP 47 tag -> letters
    default_language: str = ""
    words: Counter[str] = field(default_factory=Counter)
    chars: int = 0  # letters, digits and signs of the text, spaces between words included
    pictures: int = 0  # picture relationships of the main part (an upper bound)

    def pages(self) -> int:
        """The pages the file counts as before it is read: one per 3,000 characters of text, or
        one per picture, whichever is larger, and at least one (a source's rule,
        `source.intake`)."""
        return max(1, -(-self.chars // 3000), self.pictures)

    def counts(self) -> dict[str, int]:
        """Everything but the words and the languages, for logs and measurements."""
        return {
            k: v for k, v in vars(self).items() if isinstance(v, int) and not isinstance(v, bool)
        }


@dataclass
class Prepared:
    """A Word file made ready: the working copy for pandoc, the facts, and the pictures
    (`picture(src)`: a member of the original, by pandoc's path, never by a name pandoc made up)."""

    working: bytes
    facts: Facts
    census: Census
    _original: bytes = b""

    def picture(self, src: str) -> bytes | None:
        """The bytes of the picture pandoc calls `src` ("media/image1.png"), or None when the
        package has no such member or it is too large."""
        if not src or src.startswith(("/", "\\")) or ".." in src.split("/") or "\x00" in src:
            return None
        member = posixpath.normpath(posixpath.join("word", src))
        if not member.startswith("word/media/"):
            return None
        try:
            with zipfile.ZipFile(io.BytesIO(self._original)) as archive:
                info = archive.getinfo(member)
                if info.file_size > MAX_MEDIA_BYTES:
                    return None
                with archive.open(info) as part:
                    data = part.read(MAX_MEDIA_BYTES + 1)
        except (KeyError, OSError, zipfile.BadZipFile, ValueError):
            return None
        return data if len(data) <= MAX_MEDIA_BYTES else None


# --------------------------------------------------------------------------- the working copy


def prepare(data: bytes, census: Census | None = None) -> Prepared:
    """The working copy and the facts of a Word file that `check` passed (`census` is its
    result, or None to run the gate here)."""
    census = census or check(data)
    facts = Facts()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        facts.headers_footers = sum(
            1 for n in names if re.fullmatch(r"word/(?:header|footer)\d*\.xml", n)
        )
        facts.embedded = sum(1 for n in names if n.startswith("word/embeddings/"))
        facts.charts = sum(1 for n in names if re.fullmatch(r"word/charts/chart[^/]*\.xml", n))
        facts.smartart = sum(1 for n in names if re.fullmatch(r"word/diagrams/data[^/]*\.xml", n))
        hidden_styles = _hidden_styles(archive, names)
        out: dict[str, bytes] = {}
        out["[Content_Types].xml"] = _read_bytes(archive, "[Content_Types].xml", 1 << 20)
        out["_rels/.rels"] = _filter_rels(
            _read_bytes(archive, "_rels/.rels", 1 << 20), "", _ROOT_KEPT, names
        )
        facts.default_language = _default_language(archive, names)
        for name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
            if name not in names:
                continue
            root = _parse(archive, name, MAX_PART_BYTES)
            _hide(root, hidden_styles, facts)
            _count(root, facts, main=name == "word/document.xml")
            if name == "word/document.xml":
                _mark_tables(root, facts)
            out[name] = _tostring(root)
        for name in ("word/styles.xml", "word/numbering.xml"):
            if name in names:
                out[name] = _read_bytes(archive, name, MAX_SMALL_PART_BYTES)
        rels_name = "word/_rels/document.xml.rels"  # a file made by a chat export has none
        rels = (
            _read_bytes(archive, rels_name, office.MAX_RELS_BYTES)
            if rels_name in names
            else b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
        )
        for target in _image_targets(rels, names):
            out[target] = PLACEHOLDER
        out["word/_rels/document.xml.rels"] = _filter_rels(
            rels, "word", _KEPT_REL_TYPES, names, keep_present=out.keys()
        )
    facts.equations = _equations_of(out)
    return Prepared(working=_zip(out), facts=facts, census=census, _original=data)


def _read_bytes(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    with archive.open(name) as part:
        data = part.read(limit + 1)
    if len(data) > limit:
        raise OfficeRejected("office_unsafe")
    return data


def _parse(archive: zipfile.ZipFile, name: str, limit: int) -> ET.Element:
    try:
        return SafeET.fromstring(_read_bytes(archive, name, limit), forbid_dtd=True)
    except (ET.ParseError, DefusedXmlException, ValueError, RecursionError):
        raise OfficeRejected("office_unsafe") from None


def _tostring(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _zip(parts: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return out.getvalue()


def _filter_rels(
    data: bytes,
    folder: str,
    kept: set[str],
    names: set[str],
    keep_present: Iterable[str] | None = None,
) -> bytes:
    """The relationships part with only the kinds in `kept`; an internal one only when its
    target is in the working copy (`keep_present`) or, for the package's root, in the file."""
    root = SafeET.fromstring(data, forbid_dtd=True)
    out = ET.Element(f"{{{PKG_RELS}}}Relationships")
    present = set(keep_present) if keep_present is not None else names
    for rel in root:
        kind = rel.get("Type", "").rsplit("/", 1)[-1]
        if kind not in kept:
            continue
        target = rel.get("Target", "")
        if kind == "hyperlink":
            if rel.get("TargetMode", "").lower() != "external" and not target.startswith("#"):
                continue
        else:
            path = target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target)
            if posixpath.normpath(path) not in present:
                continue
        out.append(rel)
    return _tostring(out)


def _image_targets(rels: bytes, names: set[str]) -> list[str]:
    """Members of the package that the main part's picture relationships point at (under
    `word/media/` only)."""
    found: list[str] = []
    for rel in SafeET.fromstring(rels, forbid_dtd=True):
        if not rel.get("Type", "").endswith("/image") or rel.get("TargetMode", "") == "External":
            continue
        target = rel.get("Target", "")
        path = posixpath.normpath(
            target.lstrip("/") if target.startswith("/") else f"word/{target}"
        )
        if path.startswith("word/media/") and path in names and path not in found:
            found.append(path)
    return found


def _default_language(archive: zipfile.ZipFile, names: set[str]) -> str:
    """The language Word's own defaults state (`w:docDefaults`), or ""."""
    if "word/styles.xml" not in names:
        return ""
    root = _parse(archive, "word/styles.xml", MAX_SMALL_PART_BYTES)
    for lang in root.iter(_LANG):
        return lang.get(_VAL, "") or ""
    return ""


def _hidden_styles(archive: zipfile.ZipFile, names: set[str]) -> set[str]:
    """Ids of the styles that hide what they style."""
    if "word/styles.xml" not in names:
        return set()
    root = _parse(archive, "word/styles.xml", MAX_SMALL_PART_BYTES)
    hidden: set[str] = set()
    for style in root.iter(f"{{{W}}}style"):
        for props in (style.find(_RPR),):
            if props is not None and _is_on(props.find(_VANISH)):
                hidden.add(style.get(f"{{{W}}}styleId", ""))
    return hidden


def _is_on(node: ET.Element | None) -> bool:
    """A toggle property that is present and not switched off."""
    return node is not None and node.get(_VAL, "1").lower() not in _FALSE


def _text_of(node: ET.Element) -> str:
    return "".join(t.text or "" for t in node.iter(_T))


def _hide(root: ET.Element, hidden_styles: set[str], facts: Facts) -> None:
    """Take hidden runs (and paragraphs of a hidden style) out of `root`, counting them."""
    stack = [root]
    while stack:
        parent = stack.pop()
        for child in list(parent):
            if (child.tag == _R and _run_hidden(child, hidden_styles)) or (
                child.tag == _P and _paragraph_hidden(child, hidden_styles)
            ):
                facts.hidden_runs += 1
                facts.hidden_words += len(_text_of(child).split())
                parent.remove(child)
            elif child.tag == _FLDSIMPLE and not child.get(_INSTR, "").lstrip().upper().startswith(
                "HYPERLINK"
            ):
                # pandoc drops the result of a simple field (a page number, a reference, a merge
                # field) and keeps that of a complex one: the same field is written the second way
                facts.simple_fields += 1
                at = list(parent).index(child)
                parent.remove(child)
                for offset, inner in enumerate(list(child)):
                    parent.insert(at + offset, inner)
                    stack.append(inner)
            else:
                if child.tag == _CR:  # pandoc reads a carriage return as nothing; a break is read
                    child.tag = _BR
                stack.append(child)


def _run_hidden(run: ET.Element, hidden_styles: set[str]) -> bool:
    props = run.find(_RPR)
    if props is None:
        return False
    if _is_on(props.find(_VANISH)):
        return True
    style = props.find(_RSTYLE)
    return style is not None and style.get(_VAL, "") in hidden_styles


def _paragraph_hidden(paragraph: ET.Element, hidden_styles: set[str]) -> bool:
    props = paragraph.find(_PPR)
    if props is None:
        return False
    style = props.find(_PSTYLE)
    return (
        style is not None
        and style.get(_VAL, "") in hidden_styles
        and not _paragraph_math(paragraph)
    )


def _paragraph_math(paragraph: ET.Element) -> bool:
    return next(paragraph.iter(_OMATH), None) is not None


def _count(root: ET.Element, facts: Facts, *, main: bool) -> None:
    """The counts and the words of one part (a `mc:Fallback` copy is the same content again:
    it is not read)."""
    stack: list[tuple[ET.Element, bool]] = [(root, False)]
    while stack:
        node, deleted = stack.pop()
        if node.tag == _FALLBACK:
            continue
        if node.tag == _TXBX and main:
            facts.text_boxes += 1
        if node.tag == _BLIP and main:
            facts.pictures += 1
        if node.tag == _OMATH:
            continue
        if node.tag in _DELETED:
            deleted = True
        if node.tag == _P:
            if not deleted:
                _paragraph(node, facts)
            props = node.find(_PPR)
            if props is not None and _is_on(props.find(_BIDI)):
                facts.rtl_paragraphs += 1
        stack.extend((child, deleted) for child in node)


def _paragraph(paragraph: ET.Element, facts: Facts) -> None:
    """Words and languages of one paragraph: its text as the reader will count it (runs joined,
    a tab or a break a space), and the letters of each run by the run's own language."""
    parts: list[str] = []
    stack = [paragraph]
    while stack:
        node = stack.pop()
        for child in reversed(list(node)):
            if child.tag in (_FALLBACK, _OMATH) or child.tag in _DELETED or child.tag == _P:
                continue
            stack.append(child)
        if node.tag == _T:
            parts.append(node.text or "")
        elif node.tag in _BREAKS:
            parts.append(" ")
        elif node.tag == _NOBREAK_HYPHEN:
            parts.append("\u2011")  # pandoc writes it as the non-breaking hyphen sign
        elif node.tag == _R:
            props = node.find(_RPR)
            lang = props.find(_LANG) if props is not None else None
            tag = (lang.get(_VAL, "") if lang is not None else "") or facts.default_language
            letters = sum(c.isalpha() for c in _text_of(node))
            if letters:  # a run with no mark of its own is in the document's default language
                facts.language_chars[tag or "en"] += letters
    # nested paragraphs (a text box inside a run) are counted as paragraphs of their own
    words = "".join(parts).split()
    facts.words.update(words)
    facts.chars += sum(len(w) for w in words) + max(len(words) - 1, 0)


def _mark_tables(root: ET.Element, facts: Facts) -> None:
    """Give each table the style id `HEADER_STYLE` + the number of leading rows marked as
    header rows (`w:tblHeader`), so pandoc hands that back with the table (module docstring)."""
    for table in root.iter(_TBL):
        facts.tables += 1
        marked = 0
        for row in table.findall(_TR):
            props = row.find(_TRPR)
            if props is not None and _is_on(props.find(_TBLHEADER)):
                marked += 1
            else:
                break
        facts.marked_tables += bool(marked)
        facts.header_marks.append(marked)
        props = table.find(_TBLPR)
        if props is None:
            props = ET.Element(_TBLPR)
            table.insert(0, props)
        style = props.find(_TBLSTYLE)
        if style is None:
            style = ET.Element(_TBLSTYLE)
            props.insert(0, style)
        style.set(_VAL, f"{HEADER_STYLE}{marked}")


def _equations_of(parts: dict[str, bytes]) -> int:
    """`m:oMath` in the body and the notes of the working copy: counted from the text, so no
    second tree is built for it (a `Fallback` copy was never an equation of its own here:
    Office writes none for maths)."""
    return sum(
        len(re.findall(rb"<(?:\w+:)?oMath[\s>/]", content))
        for name, content in parts.items()
        if name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")
    )
