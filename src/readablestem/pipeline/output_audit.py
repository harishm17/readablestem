"""The built files, read and counted.

Every other check stops at the element list or at the markup of the student page. This
module opens what a student is given (the page, the Word file, the EPUB, the two Canvas
files, the Cartridge and the Listen transcript) and counts what is wrong in it, so that an
error made after the element list (by the renderer, by pandoc, by an export, by the
transcript) is in a number.

Two things are here.

**Readers.** One per built output, on the real bytes: `read_html` (the page, and the
Canvas files, which are HTML), `read_docx`, `read_epub`, `read_cartridge` and
`read_transcript`. Each returns the same `Inventory`: the blocks of text in reading order
(headings with their level, paragraphs, list items, cells, notices), the equations, the
pictures with their alt text, the tables, the links and whether each leads somewhere in
that file, the notes, the languages the file states, and the places that hold a character
that is not text (`output_chars`). An inventory holds the document's words, so it is never
stored or logged.

**Counts.** `audit` compares an output's inventory with the page's, and the page's with the
stored result, and returns `Count`s. A count is content-free: a code, the output, a page
number and an element id. Each wrong place is counted once.

| Code | A place where |
|---|---|
| `out_char` | a character is not text |
| `out_text_lost` | text of the page is in no place of the output, outside the written list |
| `out_text_twice` | text that is once on the page is twice in the output |
| `out_order` | a block is in another order than on the page |
| `out_math_fallback` | an equation is not an equation in the output, or has no spoken form |
| `out_link_dead` | a link inside the file leads nowhere in it |
| `out_alt_missing` | a picture that is not decoration has no alt text |
| `out_table_header` | a table has no header cell (an export: where the page's has one) |
| `out_heading` | a heading is empty or skips a level, or the title is said twice |
| `out_lang` | the file states no language, or another than the document's |
| `out_note_lost` | a note of the page is not in the output |
| `out_hole` | a sentence has a gap (the rule of `html_checks`, on every output) |
| `out_notice_missing` | an open decision or a kept picture has no note at its place |

What "the page" is. For an export, the page is the student page it was made from: the
export is right when it says what the page says. For the student page, it is the stored
result: every element has a place, in the stored order, and every open decision and kept
picture is marked. Whether the words inside a place are the element's words is a count of
its own and not made here.

The written list (`ABSENT_BY_DESIGN`) names, per output, the parts of the page an output
leaves out on purpose. Anything else that is missing is counted.

What a format cannot hold is not asked of it, and each such limit is one rule here: a Word
table can mark a header row and nothing else, so a header column of the page is not asked
of the Word file; a note's mark is numbered by each format in its own way, so the mark's
digits are not compared; a voice adds the word "link" and reads a table row by row, so
spoken words are compared by their letters; the transcript closes the space before a full
stop, so a sentence's gap is judged where the page has it and counted for the transcript
when the transcript says that sentence. A thing written once for a voice and once for
the eyes (a formula in a table cell) is one thing: a Word file that shows both is counted
as text twice.

Nothing here changes an output or gates one. It is a measuring tool: the eval runs it
(`readablestem eval outputs`) and the counts are a lane of the quality ledger.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from xml.etree import ElementTree as ET

from readablestem.core.types import DocumentResult, Status
from readablestem.pipeline import output_chars
from readablestem.pipeline.export_canvas import _Node, _parse
from readablestem.pipeline.html_checks import has_hole

HTML, DOCX, EPUB = "html", "docx", "epub"
CANVAS_INDEX, CANVAS_PAGE, CARTRIDGE, TRANSCRIPT = (
    "canvas_index",
    "canvas_page",
    "cartridge",
    "transcript",
)
OUTPUTS: tuple[str, ...] = (HTML, DOCX, EPUB, CANVAS_INDEX, CANVAS_PAGE, CARTRIDGE, TRANSCRIPT)

CODES = (
    "out_char",
    "out_text_lost",
    "out_text_twice",
    "out_order",
    "out_math_fallback",
    "out_link_dead",
    "out_alt_missing",
    "out_table_header",
    "out_heading",
    "out_lang",
    "out_note_lost",
    "out_hole",
    "out_notice_missing",
)

# Stands for maths or a picture inside a block's text: the words on either side of it are
# compared, the thing itself is counted as an equation or a picture.
THING = "\ufffc"
# Text shorter than this is not looked for twice and not used to find the order: "1", "(a)"
# and "Solution" are on a page many times by right.
MIN_WORDS = 4

# Parts of the page (`Block.part`) an output leaves out on purpose. The skip link has
# nowhere to skip to outside a browser; the page's Contents list is the format's own
# contents in an EPUB (its nav) and is not read as text there; the visible page label
# stands beside a page break that each format writes in its own way.
ABSENT_BY_DESIGN: dict[str, frozenset[str]] = {
    DOCX: frozenset({"skip_link", "page_label"}),
    EPUB: frozenset({"skip_link", "toc", "page_label"}),
    CANVAS_INDEX: frozenset(),
    CANVAS_PAGE: frozenset({"skip_link"}),
    CARTRIDGE: frozenset(),
    TRANSCRIPT: frozenset({"page_label", "toc_title"}),
}


# --------------------------------------------------------------------------- the inventory


@dataclass(frozen=True)
class Block:
    """One run of words a reader meets, in reading order."""

    role: str  # heading, text, item, cell, notice, note
    text: str  # its words, spaces collapsed; maths and pictures inside it are `THING`
    level: int = 0  # a heading's
    id: str = ""  # the element id, where the output keeps one
    page: int | None = None  # 0-based
    part: str = ""  # a part of the tool's own, by name (skip_link, toc, page_label, about)
    hidden: bool = False  # on the screen and not read aloud (`aria-hidden`)
    follows: bool = False  # straight after a formula or picture that stands alone
    unseen: bool = False  # read aloud and not on the screen (the page's `visually-hidden`)


@dataclass(frozen=True)
class Equation:
    id: str
    page: int | None
    math: bool  # written as mathematics (MathML, Office Math), not as a picture or text
    spoken: bool  # has words to say it with


@dataclass(frozen=True)
class Picture:
    id: str
    page: int | None
    alt: str | None  # None: no alt text at all
    decorative: bool


@dataclass(frozen=True)
class Table:
    id: str
    page: int | None
    rows: int
    columns: int
    header_cells: int
    header_rows: int = 0  # rows that are headers as a whole: all a Word file can mark


@dataclass(frozen=True)
class Link:
    target: str
    found: bool  # leads to a place in this file (a link out of the file is not judged)
    page: int | None


@dataclass
class Inventory:
    """What one built output holds. It holds the document's words: never stored or logged."""

    output: str
    title: str = ""
    # Every place the file states a language, by name ("html", "package", "core").
    langs: dict[str, str] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)
    equations: list[Equation] = field(default_factory=list)
    pictures: list[Picture] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)  # every id, in order, repeats kept
    marked: set[str] = field(default_factory=set)  # ids with a note or flag at their place
    # One thing written twice on purpose, for a voice and for the eyes (a formula in a table
    # cell): its spoken words, what is seen, the page and the element id.
    twins: list[tuple[str, str, int | None, str]] = field(default_factory=list)
    not_text: list[tuple[int | None, str]] = field(default_factory=list)  # (page, id)

    @property
    def text(self) -> str:
        """Every word the output holds, block after block."""
        return "\n".join(block.text for block in self.blocks)


@dataclass(frozen=True)
class Count:
    """One wrong place. Content-free."""

    code: str
    output: str
    page: int | None = None  # 0-based
    element: str = ""


_SPACE = re.compile(r"[\s\u00a0\u200b\u2060\ufeff]+")
_ELEMENT_ID = re.compile(r"p(\d+)-e\d+")
_PAGE_BREAK = re.compile(r"pagebreak-(\d+)")


def clean(text: str) -> str:
    """Words as they are compared: soft hyphens out, every run of spaces one space."""
    return _SPACE.sub(" ", text.replace("\u00ad", "")).strip()


def _page_of(element_id: str) -> int | None:
    match = _ELEMENT_ID.fullmatch(element_id)
    return int(match.group(1)) - 1 if match else None


def _note_chars(inventory: Inventory, text: str | None, page: int | None, where: str) -> None:
    if text and output_chars.count(text):
        inventory.not_text.append((page, where))


# --------------------------------------------------------------------------- HTML

_HEADING = {f"h{n}": n for n in range(1, 7)}
_ROLE = {"li": "item", "td": "cell", "th": "cell"} | dict.fromkeys(_HEADING, "heading")
_UNREAD = frozenset({"style", "script", "template", "head", "noscript"})
# Elements that sit inside a line of text. Everything else starts a block of its own.
_INLINE = frozenset(
    {
        "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "del", "dfn", "em", "i",
        "img", "ins", "kbd", "label", "mark", "math", "q", "s", "samp", "small", "span",
        "strong", "sub", "sup", "svg", "time", "u", "var", "wbr",
    }
)  # fmt: skip
_NOTICE = frozenset({"flag-note", "notice", "flag-label", "draft-mark"})
# An element is marked where the node that carries its id is one of these. A flag inside
# an element (the mark of one unreadable character) says nothing of the element's decision.
_MARK = frozenset({"flagged", "flagged-inline"})


def _all_text(node: _Node) -> str:
    return clean(
        "".join(c if isinstance(c, str) else " " + _all_text(c) + " " for c in node.children)
    )


def _part(node: _Node, inherited: str) -> str:
    classes = set(node.classes())
    if "skip-link" in classes:
        return "skip_link"
    if "toc-title" in classes:
        return "toc_title"
    if "toc" in classes or node.attrs.get("epub:type") == "toc":
        return "toc"
    if "pagebreak" in classes:
        return "page_label"
    if "about" in classes:
        return "about"
    return inherited


@dataclass(frozen=True)
class _State:
    """What a node inherits from the nodes above it."""

    id: str = ""  # the element id it stands at
    part: str = ""
    hidden: bool = False
    unseen: bool = False
    notice: bool = False
    note: bool = False
    flag: bool = False  # inside an equation kept as its picture or its original text
    decoration: bool = False  # inside a picture the page says is decoration


@dataclass
class _Run:
    """The line of text being gathered: its words, and the words in it that are for a
    voice only or for the eyes only, which are lines of their own."""

    words: list[str] = field(default_factory=list)
    apart: list[tuple[str, _State]] = field(default_factory=list)
    voice: str | None = None  # the words for a voice only that were just met


class _HtmlReader:
    def __init__(self, output: str) -> None:
        self.inventory = Inventory(output)
        self.page: int | None = None
        self.alone = False  # the last line was a formula or a picture and no words

    def at(self, state: _State) -> tuple[str, int | None]:
        own = _page_of(state.id)
        return state.id, self.page if own is None else own

    def enter(self, node: _Node, state: _State) -> _State:
        """Note what a node is (an id, a mark, a table, a link, a note) and return what
        its children inherit."""
        attrs, classes = node.attrs, set(node.classes())
        node_id = attrs.get("id", "")
        if match := _PAGE_BREAK.fullmatch(node_id):
            self.page = int(match.group(1)) - 1
        inner = _State(
            id=node_id if _page_of(node_id) is not None else state.id,
            part=_part(node, state.part),
            hidden=state.hidden or attrs.get("aria-hidden") == "true",
            unseen=state.unseen or "visually-hidden" in classes,
            notice=state.notice or bool(classes & _NOTICE),
            note=state.note or attrs.get("role") == "doc-footnote",
            flag=state.flag or "flagged-inline" in classes,
            decoration=state.decoration
            or "decorative" in classes
            or attrs.get("role") in ("presentation", "none"),
        )
        inventory = self.inventory
        if node_id:
            inventory.ids.append(node_id)
        if classes & _MARK and _page_of(node_id) is not None:
            inventory.marked.add(node_id)
        if attrs.get("role") == "doc-footnote":
            inventory.notes.append(_all_text(node))
        if node.tag == "a" and "href" in attrs:
            inventory.links.append(Link(attrs["href"], True, self.page))
        if node.tag == "table":
            rows = [n for n in node.walk() if n.tag == "tr" and _table_of(n) is node]
            cells = [
                [c for c in row.children if isinstance(c, _Node) and c.tag in ("td", "th")]
                for row in rows
            ]
            inventory.tables.append(
                Table(
                    *self.at(inner),
                    len(rows),
                    max((len(row) for row in cells), default=0),
                    sum(1 for row in cells for cell in row if cell.tag == "th"),
                    sum(1 for row in cells if row and all(cell.tag == "th" for cell in row)),
                )
            )
        for name in ("aria-label", "title"):
            _note_chars(inventory, attrs.get(name), self.at(inner)[1], inner.id)
        return inner

    def picture(self, node: _Node, state: _State) -> None:
        element_id, page = self.at(state)
        alt = node.attrs.get("alt")
        _note_chars(self.inventory, alt, page, element_id)
        if state.flag:  # an equation kept as its picture
            self.inventory.equations.append(
                Equation(element_id, page, False, bool(clean(alt or "")))
            )
            return
        skipped = state.hidden or state.decoration
        self.inventory.pictures.append(
            Picture(element_id, page, alt, alt is not None and not alt.strip() and skipped)
        )

    def inline(self, node: _Node, state: _State, run: _Run, line_of: _Node) -> None:
        """One element inside the line of `line_of`: its words go to the line; maths and a
        picture stand in it as `THING`."""
        inner = self.enter(node, state)
        if inner.part != state.part:  # a part of the tool's own is a line of its own
            self.close(line_of, state, run)
            own = _Run()
            self.line(node, inner, own)
            self.close(node, inner, own)
            return
        if node.tag == "math":
            alttext = node.attrs.get("alttext", "")
            element_id, page = self.at(inner)
            _note_chars(self.inventory, alttext, page, element_id)
            self.inventory.equations.append(Equation(element_id, page, True, True))
            run.words.append(THING)
        elif node.tag == "img":
            self.picture(node, inner)
            run.words.append(THING)
        elif node.tag == "svg":
            run.words.append(THING)
        elif node.tag == "br":
            run.words.append(" ")
        elif node.attrs.get("role") == "doc-noteref":
            run.words.append(THING)  # a note's mark: each format numbers its own
        elif inner.flag and not state.flag:
            # A flag inside a line (a kept equation, an unreadable character): what it
            # says is the tool's notice, and a voice adds words of its own after it.
            own = _Run()
            self.line(node, inner, own)
            words = clean("".join(own.words))
            run.words.append(THING)
            run.apart.extend(own.apart)
            if words.replace(THING, "").strip():
                run.apart.append((words, replace(inner, notice=True)))
        elif (inner.hidden, inner.unseen) != (state.hidden, state.unseen):
            # For the eyes only, or for a voice only: a line of its own, so that an output
            # is asked only for the one it can give.
            own = _Run()
            self.line(node, inner, own)
            words = clean("".join(own.words))
            run.words.append(THING)
            run.apart.extend(own.apart)
            if words.replace(THING, "").strip():
                run.apart.append((words, inner))
            if inner.unseen:
                run.voice = words
            elif run.voice is not None:
                element_id, page = self.at(inner)
                self.inventory.twins.append((run.voice, words, page, element_id))
                run.voice = None
        else:
            self.line(node, inner, run)

    def line(self, node: _Node, state: _State, run: _Run) -> None:
        for child in node.children:
            if isinstance(child, str):
                run.words.append(child)
            elif child.tag in _UNREAD:
                continue
            elif child.tag in _INLINE:
                self.inline(child, state, run, node)
            else:  # a block inside a line: its words are a line of their own
                self.close(node, state, run)
                self.block(child, state)

    def close(self, node: _Node, state: _State, run: _Run, *, last: bool = False) -> None:
        """End the line gathered so far inside `node` and keep it as a block."""
        text = clean("".join(run.words))
        apart, run.words, run.apart, run.voice = run.apart, [], [], None
        role = "notice" if state.notice else "note" if state.note else _ROLE.get(node.tag, "text")
        element_id, page = self.at(state)
        empty_heading = last and node.tag in _HEADING and not _all_text(node)
        if text.replace(THING, "").strip() or empty_heading:
            level = _HEADING.get(node.tag, 0)
            self.inventory.blocks.append(
                Block(
                    role,
                    text,
                    level,
                    element_id,
                    page,
                    state.part,
                    state.hidden,
                    self.alone,
                    state.unseen,
                )
            )
            self.alone = False
            _note_chars(self.inventory, text, page, element_id)
        elif THING in text:
            self.alone = True
        for words, inner in apart:
            self.inventory.blocks.append(
                Block(
                    "notice" if inner.notice else role,
                    words,
                    0,
                    *self.at(inner),
                    inner.part,
                    inner.hidden,
                    unseen=inner.unseen,
                )
            )
            _note_chars(self.inventory, words, page, element_id)

    def block(self, node: _Node, state: _State) -> None:
        inner = self.enter(node, state)
        run = _Run()
        self.line(node, inner, run)
        self.close(node, inner, run, last=True)


def _table_of(node: _Node) -> _Node | None:
    parent = node.parent
    while parent is not None and parent.tag != "table":
        parent = parent.parent
    return parent


def _resolve(links: Iterable[Link], ids: Iterable[str], own: Sequence[str] = ("",)) -> list[Link]:
    """Each link with whether it leads somewhere in the file. `own`: the names this file
    goes by in its own links (an EPUB's "content.xhtml")."""
    known = set(ids)
    out = []
    for link in links:
        file, mark, fragment = link.target.partition("#")
        inside = bool(mark) and file in own
        out.append(Link(link.target, fragment in known if inside else True, link.page))
    return out


def read_html(html: str, output: str = HTML) -> Inventory:
    """The student page, or an output that is HTML (the Canvas files, an EPUB's content)."""
    root = _parse(html)
    reader = _HtmlReader(output)
    inventory = reader.inventory
    for node in root.walk():
        if node.tag == "title" and not inventory.title:
            inventory.title = _all_text(node)
        # The page's language is the root element's; a fragment (the Canvas page) states it
        # on the element that wraps it.
        top = node.tag == "html" or node.parent is root
        if top and "html" not in inventory.langs and node.attrs.get("lang", "").strip():
            inventory.langs["html"] = node.attrs["lang"].strip()
    inventory.langs.setdefault("html", "")
    run = _Run()
    reader.line(root, _State(), run)
    reader.close(root, _State(), run)
    inventory.links = _resolve(inventory.links, inventory.ids)
    return inventory


# --------------------------------------------------------------------------- Word

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_WORD_HEADING = re.compile(r"Heading([1-9])")


def _word_text(node: ET.Element) -> str:
    """A paragraph's words in order: runs of text, a tab or break as a space, Office Math
    and a drawing as `THING`."""
    parts: list[str] = []

    def walk(inner: ET.Element) -> None:
        for child in inner:
            if child.tag == f"{_W}t":
                parts.append(child.text or "")
            elif child.tag in (f"{_W}tab", f"{_W}br", f"{_W}cr"):
                parts.append(" ")
            elif child.tag in (
                f"{_M}oMath",
                f"{_M}oMathPara",
                f"{_W}drawing",
                f"{_W}footnoteReference",
            ):
                parts.append(THING)
            elif child.tag not in (f"{_W}pPr", f"{_W}rPr"):
                walk(child)

    walk(node)
    return clean("".join(parts))


def read_docx(data: bytes) -> Inventory:
    """A Word file: `word/document.xml`, `word/footnotes.xml` and the core properties.
    A footnote with no words is a mark that leads nowhere: a dead link."""
    inventory = Inventory(DOCX)
    empty_notes = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        document = ET.fromstring(archive.read("word/document.xml"))
        if "docProps/core.xml" in names:
            core = ET.fromstring(archive.read("docProps/core.xml"))
            inventory.title = clean(core.findtext(f"{_DC}title") or "")
            inventory.langs["core"] = (core.findtext(f"{_DC}language") or "").strip()
        else:
            inventory.langs["core"] = ""
        if "word/footnotes.xml" in names:
            for note in ET.fromstring(archive.read("word/footnotes.xml")):
                if note.get(f"{_W}type") is not None:
                    continue  # the separators Word draws
                if text := _word_text(note):
                    inventory.notes.append(text)
                    _note_chars(inventory, text, None, "")
                else:
                    empty_notes += 1
    body = document.find(f"{_W}body")
    if body is None:
        return inventory
    place = ""  # the last bookmark that is an element id
    alone = False  # the last paragraph was a formula or a picture and no words

    def paragraph(node: ET.Element, role: str) -> None:
        nonlocal place, alone
        for mark in node.iter(f"{_W}bookmarkStart"):
            name = mark.get(f"{_W}name", "")
            inventory.ids.append(name)
            if _page_of(name) is not None:
                place = name
        page = _page_of(place)
        style = node.find(f"{_W}pPr/{_W}pStyle")
        heading = _WORD_HEADING.fullmatch(style.get(f"{_W}val", "") if style is not None else "")
        if node.find(f"{_W}pPr/{_W}numPr") is not None and role == "text":
            role = "item"
        text = _word_text(node)
        worded = bool(text.replace(THING, "").strip())
        if style is not None and style.get(f"{_W}val") == "Title":
            # The title Word shows at the top: the first heading a reader meets.
            inventory.blocks.append(Block("heading", text, 1, place, page, "title"))
        elif heading:
            inventory.blocks.append(Block("heading", text, int(heading.group(1)), place, page))
        elif worded:
            inventory.blocks.append(Block(role, text, 0, place, page, follows=alone))
        alone = bool(text) and not worded
        _note_chars(inventory, text, page, place)
        for link in node.iter(f"{_W}hyperlink"):
            anchor = link.get(f"{_W}anchor")
            inventory.links.append(Link(f"#{anchor}" if anchor else "out", True, page))
        for _ in node.iter(f"{_M}oMath"):
            inventory.equations.append(Equation(place, page, True, True))
        for drawing in node.iter(f"{_WP}docPr"):
            alt = drawing.get("descr")
            decorative = any(
                child.tag.endswith("}decorative") and child.get("val") == "1"
                for child in drawing.iter()
            )
            inventory.pictures.append(Picture(place, page, alt, decorative))
            _note_chars(inventory, alt, page, place)

    def walk(node: ET.Element, role: str) -> None:
        nonlocal place
        for child in node:
            if child.tag == f"{_W}bookmarkStart":
                name = child.get(f"{_W}name", "")
                inventory.ids.append(name)
                if _page_of(name) is not None:
                    place = name
            elif child.tag == f"{_W}p":
                paragraph(child, role)
            elif child.tag == f"{_W}tbl":
                rows = child.findall(f"{_W}tr")
                cells = [row.findall(f"{_W}tc") for row in rows]
                heads = [
                    row_cells
                    for row, row_cells in zip(rows, cells, strict=True)
                    if row.find(f"{_W}trPr/{_W}tblHeader") is not None
                ]
                inventory.tables.append(
                    Table(
                        place,
                        _page_of(place),
                        len(rows),
                        max(map(len, cells), default=0),
                        sum(map(len, heads)),
                        len(heads),
                    )
                )
                for row_cells in cells:
                    for cell in row_cells:
                        walk(cell, "cell")
            elif child.tag in (f"{_W}sdt", f"{_W}sdtContent"):
                walk(child, role)

    walk(body, "text")
    inventory.links = _resolve(inventory.links, inventory.ids)
    inventory.links += [Link("footnote", False, None)] * empty_notes
    return inventory


# --------------------------------------------------------------------------- EPUB, Cartridge

_OPF_DC = "{http://purl.org/dc/elements/1.1/}"
_CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_OPF = "{http://www.idpf.org/2007/opf}"


def read_epub(data: bytes) -> Inventory:
    """An EPUB: its content documents in the order of the spine, the links of its nav, and
    the language its package states."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        root_file = container.find(f".//{_CONTAINER}rootfile")
        package_path = root_file.get("full-path", "") if root_file is not None else ""
        package = ET.fromstring(archive.read(package_path))
        folder = package_path.rpartition("/")[0]
        items = {item.get("id"): item for item in package.iter(f"{_OPF}item")}
        spine = [
            items[ref.get("idref")]
            for ref in package.iter(f"{_OPF}itemref")
            if ref.get("idref") in items
        ]
        navs = [item for item in items.values() if "nav" in (item.get("properties") or "").split()]

        def part(item: ET.Element) -> tuple[str, str]:
            href = item.get("href", "")
            return href, archive.read(f"{folder}/{href}" if folder else href).decode("utf-8")

        inventory = Inventory(EPUB)
        names: list[str] = []
        for item in spine:
            href, text = part(item)
            names.append(href)
            read = read_html(text, EPUB)
            inventory.title = inventory.title or read.title
            inventory.langs.setdefault("html", read.langs.get("html", ""))
            for name in ("blocks", "equations", "pictures", "tables", "links", "notes", "ids"):
                getattr(inventory, name).extend(getattr(read, name))
            inventory.marked |= read.marked
            inventory.not_text.extend(read.not_text)
        for item in navs:
            if item in spine:
                continue
            _, text = part(item)
            inventory.links.extend(read_html(text, EPUB).links)
        inventory.langs["package"] = (package.findtext(f".//{_OPF_DC}language") or "").strip()
    inventory.links = _resolve(inventory.links, inventory.ids, ("", *names))
    return inventory


def read_cartridge(data: bytes) -> Inventory:
    """A Common Cartridge: the page it carries (the first web page its manifest names)
    and the language the manifest states for its title."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = ET.fromstring(archive.read("imsmanifest.xml"))
        pages = [
            node.get("href", "")
            for node in manifest.iter()
            if node.tag.endswith("}resource") and node.get("href", "").endswith(".html")
        ]
        inventory = (
            read_html(archive.read(pages[0]).decode("utf-8"), CARTRIDGE)
            if pages
            else Inventory(CARTRIDGE)
        )
        stated = [
            node.get("language", "") for node in manifest.iter() if node.tag.endswith("}string")
        ]
        inventory.langs["manifest"] = stated[0].strip() if stated else ""
    return inventory


# --------------------------------------------------------------------------- transcript


_SPOKEN_PICTURES = frozenset({"Figure", "Image", "Image, not converted"})


def read_transcript(items: Sequence[object], *, no_reading: str, no_description: str) -> Inventory:
    """The Listen transcript (`web.listen.build_transcript`): what a voice says, item by
    item. An item is anything with `role`, `text`, `say`, `kinds` and `lang`.
    `no_reading` and `no_description` are the words the transcript says where it has no
    reading of an equation and no alt text of a picture."""
    inventory = Inventory(TRANSCRIPT)
    page: int | None = None
    alone = False  # the last item was a formula by itself
    for item in items:
        role, text = str(getattr(item, "role", "")), clean(str(getattr(item, "text", "")))
        kinds = tuple(getattr(item, "kinds", ()))
        say = clean(str(getattr(item, "say", "")))
        first = kinds[0] if kinds else ""
        if first == "title":
            inventory.title = text
            continue
        if role == "Page language":
            inventory.langs["page"] = str(getattr(item, "lang", "")).strip()
            continue
        if first == "page-break":
            page = 0 if page is None else page + 1
            continue
        if "equation" in kinds:
            inventory.equations.append(Equation("", page, True, no_reading not in say))
        if role in _SPOKEN_PICTURES:
            alt = None if text == no_description else text
            inventory.pictures.append(Picture("", page, alt, False))
        if first == "note":
            inventory.notes.append(text)
        level = re.fullmatch(r"Heading level (\d)", role)
        block_role = "heading" if level else "note" if first == "note" else "text"
        if text or level:
            inventory.blocks.append(
                Block(
                    block_role, text, int(level.group(1)) if level else 0, "", page, follows=alone
                )
            )
        alone = kinds == ("equation",)
        _note_chars(inventory, f"{text} {say}", page, "")
    inventory.langs.setdefault("page", "")
    return inventory


# --------------------------------------------------------------------------- the counts


@dataclass(frozen=True)
class Facts:
    """What the stored result says the page must hold."""

    lang: str
    order: tuple[str, ...]  # the id of every element with a place of its own, stored order
    open: frozenset[str]  # elements with a decision still waiting
    kept: frozenset[str]  # elements kept as their picture or original text
    # An element written inside another (an equation that is a table's cell) has no place
    # of its own: its id, and the id of the element whose place it is at.
    inside: Mapping[str, str] = field(default_factory=dict)


def facts_of(result: DocumentResult) -> Facts:
    kept_statuses = (Status.FAILED, Status.ABSTAINED, Status.REJECTED)
    inside = {
        cell.equation_id: element.id
        for element in result.elements
        if element.table is not None
        for cell in element.table.cells
        if cell.equation_id
    }
    return Facts(
        lang=result.lang,
        order=tuple(
            e.id for e in sorted(result.elements, key=lambda e: e.order) if e.id not in inside
        ),
        open=frozenset(f.element_id for f in result.fixes if f.pending),
        kept=frozenset(f.element_id for f in result.fixes if f.status in kept_statuses),
        inside=inside,
    )


def _worded(picture: Picture) -> bool:
    return bool(picture.alt and picture.alt.strip())


def _primary(lang: str) -> str:
    return lang.strip().lower().replace("_", "-").partition("-")[0]


def _words(text: str) -> int:
    return len(text.replace(THING, " ").split())


_WORD = re.compile(r"\w+")


def _squeezed(text: str) -> str:
    return "".join(text.split())


def _segments(text: str) -> list[str]:
    """The runs of words of a block, apart at each formula or picture inside it. A run of
    punctuation alone is not looked for."""
    return [part for part in (clean(p) for p in text.split(THING)) if _WORD.search(part)]


def _letters(text: str) -> str:
    """The letters and digits of some words, with nothing between them. The word a voice
    puts before a link's name is left out, on both sides of a comparison."""
    return "".join(word for word in _WORD.findall(text.casefold()) if word != "link")


class _Said:
    """Whether a run of words is said by a spoken output. A voice adds words of its own
    ("link" before a link's name, a row's column names) and breaks a line where it likes,
    so the run is looked for by its letters, in what all the items say one after another."""

    def __init__(self, blocks: Iterable[Block]) -> None:
        self.all = "".join(_letters(block.text) for block in blocks)

    def __contains__(self, segment: str) -> bool:
        return _letters(segment) in self.all


def _out_of_order(positions: Sequence[int]) -> int:
    """How many of `positions` are not on the longest run that goes up: the fewest blocks
    that would have to move."""
    import bisect

    tails: list[int] = []
    for value in positions:
        at = bisect.bisect_left(tails, value)
        if at == len(tails):
            tails.append(value)
        else:
            tails[at] = value
    return len(positions) - len(tails)


def _gap(block: Block) -> bool:
    """Whether a block of the document's own words has a gap in a sentence. A line straight
    after a formula that stands alone may open with punctuation: the formula ended the
    sentence before it."""
    text = (THING if block.follows else "") + block.text
    return block.part in ("", "about") and has_hole(text)


def _own_counts(inventory: Inventory, lang: str) -> Iterator[Count]:
    """What is wrong in an output by itself, whatever it was made from."""
    out = inventory.output
    for page, where in dict.fromkeys(inventory.not_text):
        yield Count("out_char", out, page, where)
    for link in inventory.links:
        if not link.found:
            yield Count("out_link_dead", out, link.page)
    if out == HTML:  # an export is held to the page's decoration (`_against_page`)
        for picture in inventory.pictures:
            if not _worded(picture) and not picture.decorative:
                yield Count("out_alt_missing", out, picture.page, picture.id)
    for equation in inventory.equations:
        if not equation.spoken:
            yield Count("out_math_fallback", out, equation.page, equation.id)
    headings = [block for block in inventory.blocks if block.role == "heading" and not block.hidden]
    # The title is the first heading. The same words as the next heading are the title said
    # twice; a heading that repeats further on (the slides of one topic) is the document's.
    for at, heading in enumerate(headings):
        before = headings[at - 1] if at else None
        text = heading.text.replace(THING, "x")
        wrong = (
            not text
            or (before is not None and heading.level > before.level + 1)
            or (at == 1 and before is not None and text.casefold() == before.text.casefold())
        )
        if wrong:
            yield Count("out_heading", out, heading.page, heading.id)
    for place, stated in inventory.langs.items():
        if not stated or (lang and _primary(stated) != _primary(lang)):
            yield Count("out_lang", out, None, place)
    for block in inventory.blocks:
        # Punctuation that opens the line after a displayed formula is the formula's.
        # The transcript closes the space before a full stop that the rule looks for, so
        # its gaps are judged at the page's place (`_against_page`).
        if out != TRANSCRIPT and _gap(block):
            yield Count("out_hole", out, block.page, block.id)


def _against_result(page: Inventory, facts: Facts) -> Iterator[Count]:
    """The student page against the stored result."""
    seen = Counter(page.ids)
    for element_id in facts.order:
        if not seen[element_id]:
            yield Count("out_text_lost", HTML, _page_of(element_id), element_id)
        elif seen[element_id] > 1:
            yield Count("out_text_twice", HTML, _page_of(element_id), element_id)
    wanted = {element_id: at for at, element_id in enumerate(facts.order)}
    met = [wanted[i] for i in dict.fromkeys(page.ids) if i in wanted]
    for _ in range(_out_of_order(met)):
        yield Count("out_order", HTML)
    for table in page.tables:
        if table.rows and not table.header_cells and table.id not in page.marked:
            yield Count("out_table_header", HTML, table.page, table.id)
    for element_id in sorted(facts.open | facts.kept):
        place = facts.inside.get(element_id, element_id)
        if seen[place] and place not in page.marked:
            yield Count("out_notice_missing", HTML, _page_of(element_id), element_id)


def _against_page(output: Inventory, page: Inventory) -> Iterator[Count]:
    """An export against the student page it was made from."""
    out = output.output
    absent = ABSENT_BY_DESIGN.get(out, frozenset())
    spoken = out == TRANSCRIPT
    flat = out == DOCX  # a file that shows every word it holds
    whole = _Said(output.blocks) if spoken else "\n".join([output.text, *output.notes])
    wanted = [
        block
        for block in page.blocks
        if block.part not in absent
        and not (spoken and block.hidden)
        and not (flat and block.unseen)
    ]
    for block in wanted:
        if all(segment in whole for segment in _segments(block.text)):
            continue
        code = {"notice": "out_notice_missing", "note": "out_note_lost"}.get(
            block.role, "out_text_lost"
        )
        yield Count(code, out, block.page, block.id)
    if spoken:  # a sentence with a gap on the page is said with its gap
        for block in wanted:
            if _gap(block):
                yield Count("out_hole", out, block.page, block.id)

    def long(blocks: Iterable[Block]) -> Counter[str]:
        return Counter(
            b.text
            for b in blocks
            if _words(b.text) >= MIN_WORDS and THING not in b.text and b.part != "title"
        )

    on_page, in_output = long(page.blocks), long(output.blocks)
    once = [b for b in wanted if on_page[b.text] == 1]
    for block in once:
        if in_output[block.text] > 1:
            yield Count("out_text_twice", out, block.page, block.id)
    if flat:  # the words for a voice, shown beside what they say
        cells = {_squeezed(b.text) for b in output.blocks}
        for voice, seen, page_no, element_id in page.twins:
            if _squeezed(voice + seen) in cells:
                yield Count("out_text_twice", out, page_no, element_id)
    at = {b.text: i for i, b in enumerate(output.blocks) if in_output[b.text] == 1}
    for _ in range(_out_of_order([at[b.text] for b in once if b.text in at])):
        yield Count("out_order", out)
    if not spoken:  # a voice says a line with two formulas as one item
        lost = sum(e.math for e in page.equations) - sum(e.math for e in output.equations)
        for _ in range(max(0, lost)):
            yield Count("out_math_fallback", out)
    # A picture with no words is right where the page says it is decoration: by its place
    # where the output keeps places, by number where it keeps none.
    decoration = Counter(p.id for p in page.pictures if p.decorative)
    placed = any(p.id for p in output.pictures)
    spare = 0 if placed else sum(decoration.values())
    for picture in output.pictures:
        if _worded(picture):
            continue
        if placed and decoration[picture.id] > 0:
            decoration[picture.id] -= 1
        elif spare > 0:
            spare -= 1
        else:
            yield Count("out_alt_missing", out, picture.page, picture.id)
    for mine, theirs in zip(output.tables, page.tables, strict=False):
        # A Word table can mark a row as a header and nothing else, so a header column of
        # the page is not asked of the Word file: it is a limit of the format, stated in
        # docs/eval.md, and not a place the export got wrong.
        lost = (
            theirs.header_rows and not mine.header_rows
            if flat
            else theirs.header_cells and not mine.header_cells
        )
        if lost:
            yield Count("out_table_header", out, theirs.page, theirs.id)


def audit(output: Inventory, page: Inventory, facts: Facts) -> list[Count]:
    """The wrong places of one output. `page` is the inventory of the student page the
    output was made from (the same inventory when the output is the page itself)."""
    counts = list(_own_counts(output, facts.lang))
    if output.output == HTML:
        counts += _against_result(output, facts)
    else:
        counts += _against_page(output, page)
    return counts


def totals(counts: Iterable[Count]) -> dict[str, dict[str, int]]:
    """Counts by code and by output, every code and every output present (0 where clean)."""
    table = {code: dict.fromkeys(OUTPUTS, 0) for code in CODES}
    for count in counts:
        table[count.code][count.output] += 1
    return table
