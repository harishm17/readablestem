"""A listening transcript: roughly what a blind student's screen reader says for a converted
document, worked out from the final HTML, so an instructor can hear it before sharing it.

Three blind reviewers asked for this. It is built on the server from the same HTML the student
gets, in reading order, with no model call:

- landmarks (main, navigation, footer, regions), headings with their level ("Heading level 2:
  Kinematics"), paragraphs, lists ("List, 3 items", then each item), links ("link" before the
  text, as screen readers say it) and the page title;
- equations spoken by the speech rules the review screen uses (`pipeline.speech`): the
  stored reading of the equation when the job has one (the same string "Read aloud roughly
  as" shows, `speeches`), else the `alttext` the file carries, else a fresh reading of the
  MathML. Never LaTeX: a reading that looks like LaTeX is dropped (`speakable`, the rule
  the review screen applies too);
- figures (alt text, caption, long description), tables (caption, size, column headers, then
  rows read with their headers, capped with "...N more rows"), pictures kept as images read as
  "not converted", page breaks and language changes.

It is an approximation: NVDA, JAWS and VoiceOver each speak differently (the page says so).
Nothing is stored or logged: the transcript is worked out when it is asked for, kept in
memory per job result version, and never put in the downloaded or exported files.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from threading import Lock

logger = logging.getLogger(__name__)

MAX_TABLE_ROWS = 8  # body rows read per table; the rest is "...N more rows"
MAX_ITEMS = 6000  # a transcript stops here, with a last item saying so
MAX_DEPTH = 60  # nesting followed; a deeper tree is read flat
CACHE_SIZE = 32

# What an unreadable equation is called, so the transcript never goes quiet about one.
NO_READING = "no spoken reading available"


def speakable(text: str | None) -> str:
    """A spoken reading to show, or "" when the text is not one. Speech is words ("v equals d
    over t"); a string with a backslash, braces, a caret or an underscore is LaTeX that stood
    in for a reading, and no screen may present that as what a person hears. The review
    screen's "Read aloud roughly as" and this transcript apply the same rule."""
    value = (text or "").strip()
    return "" if not value or re.search(r"[\\{}^_]", value) else value


@dataclass(frozen=True)
class Item:
    """One thing a screen reader would say, with its role.

    `say` is the words a voice speaks (a paragraph is just its text; a heading adds its
    level). `kinds` are what the item is, for "next heading" and "next equation": its own
    kind first ("heading", "equation", "paragraph", ...), then any others it holds (a paragraph
    with an equation inside also lists "equation")."""

    role: str
    text: str
    say: str
    kinds: tuple[str, ...]
    lang: str = ""

    @property
    def kind(self) -> str:
        return self.kinds[0]


# --------------------------------------------------------------------------- a small tree

VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)
SKIPPED = frozenset({"head", "style", "script", "template", "noscript", "title", "svg"})
INLINE = frozenset(
    {
        "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "del", "dfn", "em", "i",
        "img", "ins", "kbd", "label", "mark", "math", "q", "s", "samp", "small", "span", "strong",
        "sub", "sup", "time", "u", "var", "wbr",
    }
)  # fmt: skip
# End tags a start tag closes (a <li> opened while another is open).
_CLOSES = {
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "tr": {"tr", "td", "th"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "p": {"p"},
    "option": {"option"},
}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)

    def get(self, name: str) -> str:
        return self.attrs.get(name, "")

    def has_class(self, name: str) -> bool:
        return name in self.get("class").split()


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]
        self.ids: dict[str, Node] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        closed = self._implied(tag)
        if closed:
            self._close_to(closed)
        node = Node(tag, {k: (v or "") for k, v in attrs})
        if node.get("id"):
            self.ids.setdefault(node.get("id"), node)
        self.stack[-1].children.append(node)
        if tag == "title":
            self._in_title = True
        if tag not in VOID and len(self.stack) < MAX_DEPTH + 40:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if self._open(tag):
            self._close_to(tag)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        self.stack[-1].children.append(data)

    def _open(self, tag: str) -> bool:
        return any(n.tag == tag for n in self.stack[1:])

    def _implied(self, tag: str) -> str | None:
        """The open tag a start tag ends (an <li> after an unclosed <li>), found through
        inline elements only: a <li> inside a nested list never ends the outer one."""
        wanted = _CLOSES.get(tag, set())
        for node in reversed(self.stack[1:]):
            if node.tag in wanted:
                return node.tag
            if node.tag not in INLINE:
                return None
        return None

    def _close_to(self, tag: str) -> None:
        while len(self.stack) > 1:
            node = self.stack.pop()
            if node.tag == tag:
                return


def parse(html: str) -> _Builder:
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return builder


# ----------------------------------------------------------------------------- the words

_LANDMARKS = {
    "main": "Main landmark",
    "nav": "Navigation landmark",
    "aside": "Complementary landmark",
    "footer": "Content information landmark",
    "header": "Banner landmark",
}
_ROLE_LANDMARKS = {
    "main": "Main landmark",
    "navigation": "Navigation landmark",
    "complementary": "Complementary landmark",
    "contentinfo": "Content information landmark",
    "banner": "Banner landmark",
    "region": "Region landmark",
    "search": "Search landmark",
}
_SPACE = re.compile(r"\s+")


_BEFORE_PUNCT = re.compile(r" ([.,;:!?)])")


def _words(text: str) -> str:
    return _BEFORE_PUNCT.sub(r"\1", _SPACE.sub(" ", text).strip())


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _language_name(tag: str) -> str:
    from readablestem.pipeline.language import language_name

    return language_name(tag)


def _primary(tag: str) -> str:
    return tag.lower().replace("_", "-").split("-")[0]


def _xml(node: Node | str) -> str:
    """A MathML subtree as XML text, for `pipeline.speech` (annotations dropped: a
    TeX annotation is not a reading)."""
    if isinstance(node, str):
        return escape(node)
    if node.tag in ("annotation", "annotation-xml"):
        return ""
    attrs = "".join(
        f' {k}="{escape(v, quote=True)}"'
        for k, v in node.attrs.items()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", k) and k not in ("id", "style", "class")
    )
    return f"<{node.tag}{attrs}>{''.join(_xml(c) for c in node.children)}</{node.tag}>"


class _Transcript:
    def __init__(self, builder: _Builder, speeches: Mapping[str, str] | None, max_rows: int):
        self.ids = builder.ids
        self.speeches = speeches or {}
        self.max_rows = max_rows
        self.items: list[Item] = []
        self.lang = ""
        self.full = False
        self._label: str | None = None  # the role the next paragraph takes ("List item 2 of 5")
        self._details = ""  # the role of text inside a <details> ("Long description")
        self._flagged = False  # inside a block kept as an image
        self._held: set[str] = set()  # kinds met inside the text being collected

    # ---------------------------------------------------------------- items

    def add(self, role: str, text: str, kind: str, *, say: str | None = None, held=()) -> None:
        if self.full:
            return
        if len(self.items) >= MAX_ITEMS:
            self.full = True
            note = "The transcript stops here; the file goes on."
            self.items.append(Item("Note", note, note, ("note",), self.lang))
            return
        text = _words(text)
        if say is None:
            say = f"{role}: {text}" if text else role
        kinds = (kind, *(k for k in dict.fromkeys(sorted(held)) if k != kind))
        self.items.append(Item(role, text, say, kinds, self.lang))

    def paragraph(self, text: str, kind: str = "paragraph", role: str = "Paragraph") -> None:
        text = _words(text)
        held = self._held
        self._held = set()
        if not text:
            return
        label, self._label = self._label, None
        if self._details:
            self.add(self._details, text, "figure", say=text, held=held)
        elif label:
            self.add(label, text, "list-item", say=text, held=held)
        else:
            self.add(role, text, kind, say=text, held=held)

    # ---------------------------------------------------------------- equations

    def math_speech(self, node: Node, element_id: str = "") -> str:
        """The reading of a <math> element: the job's stored reading, the alttext, or a
        fresh one from the MathML; "" when none is words."""
        for key in (element_id, node.get("id")):
            said = speakable(self.speeches.get(key, "")) if key else ""
            if said:
                return said
        said = speakable(node.get("alttext"))
        if said:
            return said
        from readablestem.pipeline import speech

        try:
            return speakable(speech.mathml_to_speech(_xml(node)))
        except Exception as exc:  # a reading aid: one bad equation never stops the page
            logger.warning("listen_speech_failed", extra={"exc_type": type(exc).__name__})
            return ""

    # ---------------------------------------------------------------- inline text

    def inline(self, node: Node | str, depth: int = 0) -> str:
        if isinstance(node, str):
            return node
        if self._hidden(node) or node.tag in SKIPPED:
            return ""
        tag = node.tag
        if tag == "math":
            self._held.add("equation")
            return f" {self.math_speech(node) or NO_READING} "
        if tag == "br":
            return " "
        if tag == "img":
            return f" {self.image_words(node)} " if node.get("alt").strip() else ""
        if tag == "hr":
            return ""
        inner = self._children_text(node, depth)
        if node.has_class("flagged-inline"):
            self._held.add("not-converted")
            return f" {_words(inner)}, not converted "
        if tag == "a" and node.get("href"):
            self._held.add("link")
            name = _words(inner) or _words(node.get("aria-label")) or ""
            return f" link {name} " if name else " link "
        return self._with_language(node, inner)

    def _children_text(self, node: Node, depth: int) -> str:
        if depth > MAX_DEPTH:
            return ""
        return "".join(self.inline(c, depth + 1) for c in node.children)

    def _with_language(self, node: Node, text: str) -> str:
        lang = node.get("lang")
        if not lang or not _words(text) or _primary(lang) == _primary(self.lang or lang):
            return text
        name, back = _language_name(lang), _language_name(self.lang) if self.lang else "the page"
        return f" Language changes to {name}: {_words(text)}. Language changes back to {back}. "

    def image_words(self, node: Node) -> str:
        return f"graphic, {_words(node.get('alt'))}"

    @staticmethod
    def _hidden(node: Node) -> bool:
        return (
            "hidden" in node.attrs
            or node.get("aria-hidden") == "true"
            or (node.get("role") in ("presentation", "none") and node.tag in ("img", "hr"))
        )

    def name_of(self, node: Node) -> str:
        label = _words(node.get("aria-label"))
        if label:
            return label
        for ref in node.get("aria-labelledby").split():
            target = self.ids.get(ref)
            if target is not None:
                return _words(self.inline(target))
        return ""

    # ---------------------------------------------------------------- blocks

    def walk(self, node: Node, depth: int = 0) -> None:
        """Say the children of `node` in order: runs of inline content become one item,
        each block says itself."""
        run: list[Node | str] = []

        def flush() -> None:
            if run:
                self.paragraph("".join(self.inline(c) for c in run))
                run.clear()

        for child in node.children:
            if isinstance(child, str) or (child.tag in INLINE and not self._block_like(child)):
                run.append(child)
                continue
            flush()
            if depth <= MAX_DEPTH:
                self.block(child, depth + 1)
        flush()

    @staticmethod
    def _block_like(node: Node) -> bool:
        """An inline element that holds blocks (a link around a figure, a span that wraps a
        paragraph) is walked as a block."""
        return any(isinstance(c, Node) and c.tag not in INLINE for c in node.children) or (
            node.tag == "span" and node.has_class("equation-scroll")
        )

    def block(self, node: Node, depth: int) -> None:
        tag = node.tag
        if tag in SKIPPED or self._hidden(node) or self.full:
            return
        role = node.get("role")
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return self.heading(node, int(tag[1]))
        if tag == "p":
            return self.p(node)
        if tag in ("ul", "ol"):
            return self.list(node, depth)
        if tag == "table" and role not in ("presentation", "none"):
            return self.table(node)
        if tag == "figure":
            return self.figure(node, depth)
        if tag == "details":
            return self.details(node, depth)
        if tag == "summary":
            return self.paragraph(self.inline(node), "expandable", "Expandable section")
        if tag == "hr":
            if role == "doc-pagebreak" and node.get("aria-label"):
                self.add("Page break", node.get("aria-label"), "page-break")
            return
        if tag == "pre":
            return self.add("Code", self._plain(node), "code")
        if tag == "blockquote":
            self.add("Quote", "", "quote")
            return self.walk(node, depth)
        if tag == "dl":
            return self.definitions(node, depth)
        if tag == "img":
            return self.picture(node)
        if tag == "math":
            if node.get("display") == "block":
                return self.equation(node, "")
            self._held.add("equation")
            return self.paragraph(self.inline(node))
        if tag == "div" and node.has_class("equation"):
            return self.equation_block(node)
        if node.has_class("flagged"):
            return self.flagged(node, depth)
        if role == "note" and tag != "p":
            self.add("Note", "", "note")
            return self.walk(node, depth)
        self.container(node, depth)

    def container(self, node: Node, depth: int) -> None:
        landmark = _ROLE_LANDMARKS.get(node.get("role")) or _LANDMARKS.get(node.tag)
        if (
            node.tag == "section"
            and not landmark
            and (node.get("aria-label") or node.get("aria-labelledby"))
        ):
            landmark = _ROLE_LANDMARKS["region"]
        # Only the page's own header and footer are landmarks, not those of a section.
        own = node.tag not in ("header", "footer") or node.get("role") or not self._sectioned
        landmark = landmark if own else None
        name = self.name_of(node) if landmark else ""
        if landmark:
            self.add(landmark, name, "landmark", say=f"{landmark}, {name}" if name else landmark)
        was, was_label = self.lang, self._label
        lang = node.get("lang")
        changed = bool(lang) and _primary(lang) != _primary(self.lang or lang)
        if changed:
            self.add(
                "Language change",
                _language_name(lang),
                "language",
                say=f"Language changes to {_language_name(lang)}",
            )
        if lang:
            self.lang = lang
        self._label = None if node.tag in ("ul", "ol") else was_label
        sectioned = self._sectioned
        self._sectioned = sectioned or node.tag in ("article", "section", "aside", "nav", "main")
        self.walk(node, 0)
        self._sectioned = sectioned
        self._label = was_label
        if changed:
            back = _language_name(was) if was else "the page language"
            self.add("Language change", back, "language", say=f"Language changes back to {back}")
        self.lang = was

    _sectioned = False
    _lists = 0  # lists open around this point

    def _plain(self, node: Node) -> str:
        return "".join(
            c if isinstance(c, str) else self._plain(c)
            for c in node.children
            if not isinstance(c, Node) or c.tag not in SKIPPED
        )

    def heading(self, node: Node, level: int) -> None:
        self._held = set()
        text = _words(self.inline(node))
        held, self._held = self._held, set()
        if text:
            role = f"Heading level {level}"
            self.add(role, text, "heading", held=held)

    def p(self, node: Node) -> None:
        role = node.get("role")
        text = self.inline(node)
        if role == "doc-endnote" or node.has_class("flag-note") or role == "note":
            held, self._held = self._held, set()
            if _words(text):
                self.add("Note", text, "note", held=held)
            return
        if node.has_class("original-text"):
            return self.paragraph(text, "not-converted", "Not converted")
        lang = node.get("lang")
        if lang and _primary(lang) != _primary(self.lang or lang):
            self.add(
                "Language change", _language_name(lang), "language",
                say=f"Language changes to {_language_name(lang)}",
            )  # fmt: skip
        self.paragraph(text)

    def definitions(self, node: Node, depth: int) -> None:
        for child in node.children:
            if isinstance(child, Node) and child.tag in ("dt", "dd"):
                role = "Term" if child.tag == "dt" else "Definition"
                self.add(role, self.inline(child), "paragraph", held=self._take_held())
            elif isinstance(child, Node):
                self.block(child, depth + 1)

    def _take_held(self) -> set[str]:
        held, self._held = self._held, set()
        return held

    # ---------------------------------------------------------------- lists

    def list(self, node: Node, depth: int) -> None:
        entries = [c for c in node.children if isinstance(c, Node) and c.tag == "li"]
        n = len(entries)
        count = _plural(n, "item")
        nested = ", nested" if self._lists else ""
        self.add("List", count + nested, "list", say=f"List, {count}{nested}")
        outer = self._label
        self._lists += 1
        for k, entry in enumerate(entries, 1):
            self._label = f"List item {k} of {n}"
            self.walk(entry, depth)
            self._label = None
        self._lists -= 1
        self._label = outer
        for child in node.children:  # stray content between items is read, not lost
            if isinstance(child, Node) and child.tag not in ("li",):
                self.block(child, depth + 1)

    # ---------------------------------------------------------------- equations

    def equation_block(self, node: Node) -> None:
        math = self._find(node, "math")
        label = ""
        for tag in self._iter(node):
            if tag.has_class("equation-label"):
                label = self._equation_label(tag)
        if math is None:
            return self.walk(node, 0)
        self.equation(math, node.get("id"), label)

    def equation(self, math: Node, element_id: str, label: str = "") -> None:
        text = self.math_speech(math, element_id) or NO_READING
        role = f"Equation {label}" if label else "Equation"
        self.add(role, text, "equation", say=f"{role}: {text}")

    @staticmethod
    def _equation_label(node: Node) -> str:
        text = _words("".join(_text_all(node)))
        text = re.sub(r"^Equation\s*", "", text)
        return text.strip("() ")

    def _find(self, node: Node, tag: str) -> Node | None:
        return next((n for n in self._iter(node) if n.tag == tag), None)

    def _iter(self, node: Node):
        for child in node.children:
            if isinstance(child, Node):
                yield child
                yield from self._iter(child)

    # ---------------------------------------------------------------- pictures

    def picture(self, node: Node, role: str = "Image") -> None:
        alt = _words(node.get("alt"))
        if self._flagged:
            return self.add(
                "Image, not converted", alt or "no description", "not-converted",
                say=f"Image, not converted: {alt or 'no description'}",
            )  # fmt: skip
        if "alt" in node.attrs and not alt:
            return  # decorative: a screen reader skips it
        self.add(role, alt or "no description", "figure", say=f"{role}: {alt or 'no description'}")

    def figure(self, node: Node, depth: int) -> None:
        for child in node.children:
            if not isinstance(child, Node):
                continue
            if child.tag == "figcaption":
                self.paragraph(self.inline(child), "caption", "Caption")
            elif child.tag == "img":
                self.picture(child, "Figure")
            elif not self._hidden(child):
                self.block(child, depth + 1)

    def flagged(self, node: Node, depth: int) -> None:
        """A block kept as an image (an equation, a figure, a page, a table): what a screen
        reader gets is the picture's alt text, and the note that it was not converted."""
        was, self._flagged = self._flagged, True
        try:
            self.walk(node, depth)
        finally:
            self._flagged = was

    def details(self, node: Node, depth: int) -> None:
        summary = next(
            (c for c in node.children if isinstance(c, Node) and c.tag == "summary"), None
        )
        label = _words(self.inline(summary)) if summary is not None else ""
        self.add("Expandable section, collapsed", label, "expandable")
        was = self._details
        self._details = "Long description" if node.has_class("figure-description") else ""
        for child in node.children:
            if isinstance(child, Node) and child.tag == "summary":
                continue
            if isinstance(child, str):
                self.paragraph(child)
            else:
                self.block(child, depth + 1)
        self._details = was

    # ---------------------------------------------------------------- tables

    def table(self, node: Node) -> None:
        grid = _grid(node)
        caption = ""
        for child in self._iter(node):
            if child.tag == "caption":
                caption = _words(self.inline(child))
                break
        rows = grid.rows
        columns = grid.columns
        size = f"{_plural(len(rows), 'row')}, {_plural(columns, 'column')}"
        heads = [h for c in range(columns) if (h := self.column_name(grid, c))]
        said = f"{caption}, {size}" if caption else size
        if heads:
            said += ". Column headers: " + ", ".join(heads)
        self.add("Table", said, "table", say=f"Table, {said}")
        body = grid.body_rows()
        for number, r in enumerate(body[: self.max_rows], 1):
            self.add(
                f"Row {number} of {len(body)}", self.row_words(grid, r), "table",
                say=f"Row {number} of {len(body)}: {self.row_words(grid, r)}",
            )  # fmt: skip
        if len(body) > self.max_rows:
            more = f"…{_plural(len(body) - self.max_rows, 'more row')}"
            self.add("Table", more, "table", say=more)

    def column_name(self, grid: _Grid, col: int) -> str:
        """The column's header cells, top to bottom, each said once."""
        texts = (_words(self.inline(h.node)) for h in grid.column_header_cells(col))
        return " ".join(dict.fromkeys(t for t in texts if t))

    def row_words(self, grid: _Grid, r: int) -> str:
        parts: list[str] = []
        for cell in grid.row_header_cells(r):
            if text := _words(self.inline(cell.node)):
                parts.append(text)
        for cell in grid.cells_in_row(r):
            if cell.header and cell.row_header:
                continue
            value = _words(self.inline(cell.node))
            if not value:
                continue
            names = self.column_name(grid, cell.col)
            parts.append(f"{names}: {value}" if names else value)
        return "; ".join(parts) if parts else "empty row"


def _text_all(node: Node | str) -> list[str]:
    if isinstance(node, str):
        return [node]
    return [t for c in node.children for t in _text_all(c)]


# -------------------------------------------------------------------------- table grids


@dataclass
class _Cell:
    node: Node
    row: int
    col: int
    rows: int
    cols: int
    header: bool
    in_head: bool
    scope: str

    @property
    def row_header(self) -> bool:
        return self.header and (
            self.scope in ("row", "rowgroup")
            or (not self.scope and not self.in_head and self.col == 0)
        )

    @property
    def column_header(self) -> bool:
        return self.header and not self.row_header


class _Grid:
    def __init__(self) -> None:
        self.cells: list[_Cell] = []
        self.header_rows = 0
        self.rows: list[int] = []
        self.columns = 0

    def column_header_cells(self, col: int) -> list[_Cell]:
        return [
            c for c in self.cells
            if c.column_header and c.col <= col < c.col + c.cols and c.row < self.header_rows
        ]  # fmt: skip

    def row_header_cells(self, r: int) -> list[_Cell]:
        return [c for c in self.cells if c.row_header and c.row <= r < c.row + c.rows]

    def cells_in_row(self, r: int) -> list[_Cell]:
        return sorted((c for c in self.cells if c.row == r), key=lambda c: c.col)

    def body_rows(self) -> list[int]:
        return [r for r in self.rows if r >= self.header_rows]


def _grid(table: Node) -> _Grid:
    grid = _Grid()
    trs: list[tuple[Node, bool]] = []

    def collect(node: Node, in_head: bool) -> None:
        for child in node.children:
            if not isinstance(child, Node):
                continue
            if child.tag == "tr":
                trs.append((child, in_head))
            elif child.tag in ("thead", "tbody", "tfoot"):
                collect(child, child.tag == "thead")
            elif child.tag != "table":
                collect(child, in_head)

    collect(table, False)
    taken: set[tuple[int, int]] = set()
    for r, (tr, in_head) in enumerate(trs):
        grid.rows.append(r)
        c = 0
        for cell in tr.children:
            if not isinstance(cell, Node) or cell.tag not in ("td", "th"):
                continue
            while (r, c) in taken:
                c += 1
            rows = _span(cell.get("rowspan"))
            cols = _span(cell.get("colspan"))
            for dr in range(rows):
                for dc in range(cols):
                    taken.add((r + dr, c + dc))
            grid.cells.append(
                _Cell(cell, r, c, rows, cols, cell.tag == "th", in_head, cell.get("scope"))
            )
            c += cols
            grid.columns = max(grid.columns, c)
    heads = sum(1 for _, in_head in trs if in_head)
    if not heads:  # no <thead>: the leading rows made only of <th> are the column headers
        for r in grid.rows:
            row = grid.cells_in_row(r)
            if row and all(x.header and x.scope != "row" for x in row):
                heads += 1
            else:
                break
    grid.header_rows = heads
    for cell in grid.cells:
        if cell.header and cell.row < heads:
            cell.in_head = True
    return grid


def _span(value: str) -> int:
    try:
        return max(1, min(int(value), 60))
    except ValueError:
        return 1


# ------------------------------------------------------------------------------ the entry


def build_transcript(
    html: str, speeches: Mapping[str, str] | None = None, *, max_rows: int = MAX_TABLE_ROWS
) -> list[Item]:
    """The listening transcript of a converted document's HTML, in reading order.

    `speeches`: element id -> the stored spoken reading of an equation (the string the review
    screen shows as "Read aloud roughly as"), so the transcript says what the review screen
    says; an equation without one is read from its `alttext` or its MathML."""
    builder = parse(html)
    transcript = _Transcript(builder, speeches, max_rows)
    root = next(
        (c for c in builder.root.children if isinstance(c, Node) and c.tag == "html"), builder.root
    )
    lang = root.get("lang")
    transcript.lang = lang
    title = _words(builder.title)
    if title:
        transcript.add("Page title", title, "title")
    if lang:
        transcript.add(
            "Page language", _language_name(lang), "language",
            say=f"Page language: {_language_name(lang)}",
        )  # fmt: skip
    body = next((c for c in root.children if isinstance(c, Node) and c.tag == "body"), root)
    transcript.walk(body, 0)
    return transcript.items


# ----------------------------------------------------------------------------- the cache


class _Cache:
    """The last few transcripts, kept per job result version (a review write bumps it), so
    opening the page again, or going to the next item, builds nothing."""

    def __init__(self, size: int = CACHE_SIZE) -> None:
        self.size = size
        self._items: OrderedDict[tuple[str, int, str], list[Item]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: tuple[str, int, str]) -> list[Item] | None:
        with self._lock:
            found = self._items.get(key)
            if found is not None:
                self._items.move_to_end(key)
            return found

    def put(self, key: tuple[str, int, str], items: list[Item]) -> None:
        with self._lock:
            self._items[key] = items
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


CACHE = _Cache()


def transcript_for(
    job_id: str,
    version: int,
    html: str,
    speeches: Callable[[], Mapping[str, str]] | Mapping[str, str] | None = None,
) -> list[Item]:
    """`build_transcript`, kept per (job, result version, the file's digest). The digest
    guards a file that changed without a version bump. `speeches` may be a function, called
    only when there is nothing kept (reading a stored result is not free)."""
    import hashlib

    key = (job_id, version, hashlib.sha256(html.encode("utf-8")).hexdigest()[:16])
    found = CACHE.get(key)
    if found is None:
        said = speeches if isinstance(speeches, Mapping) or speeches is None else speeches()
        found = build_transcript(html, said)
        CACHE.put(key, found)
    return found


def stored_speeches(result: object) -> dict[str, str]:
    """Element id -> the equation's stored spoken reading, from a `DocumentResult`'s fixes
    (the strings the review screen shows as "Read aloud roughly as")."""
    fixes = getattr(result, "fixes", None) or []
    return {
        f.element_id: f.equation.speech
        for f in fixes
        if getattr(f, "equation", None) is not None and f.equation.speech
    }


# ------------------------------------------------------------------- "Hear this" on a decision


@dataclass(frozen=True)
class Hear:
    """What the review screen's "Hear this" button speaks for one decision: the proposal
    (`a`, with a short lead-in) and, for an equation, how the PDF's own text layer reads
    (`b`), the before and after of the landing page."""

    a: str
    a_lead: str = ""
    b: str = ""
    b_lead: str = ""


def hear_for(
    kind: str,
    *,
    speech: str | None = None,
    alt: str | None = None,
    decorative: bool = False,
    heading_level: int | None = None,
    heading_text: str | None = None,
    text_layer: str = "",
    mathml: str | None = None,
) -> Hear | None:
    """The spoken forms of a decision's proposal, or None when it has none worth playing
    (a table, a paragraph, an equation with no reading). The equation's reading is the
    one "Read aloud roughly as" shows (`speakable`); the PDF's is `speech_diff
    .read_text_layer`, the same speech rules applied to the text layer."""
    if kind == "equation":
        said = speakable(speech)
        if not said and mathml:
            # No stored reading (or one that is LaTeX): speak the MathML, as the transcript does.
            from readablestem.pipeline.speech import SpeechError, mathml_to_speech

            try:
                said = speakable(mathml_to_speech(mathml))
            except (SpeechError, ValueError):
                said = ""
        if not said:
            return None
        from readablestem.web.speech_diff import read_text_layer

        pdf = speakable(read_text_layer(text_layer)) if text_layer else ""
        if text_layer and not pdf:
            return Hear(said, "Converted page")  # too long or unreadable to say the PDF's
        return Hear(said, "Converted page", pdf or "nothing", "PDF text")
    if kind == "figure":
        if decorative:
            return Hear("Marked decorative, so a screen reader skips it.")
        text = _words(alt or "")
        return Hear(text, "Alt text") if text else None
    if kind == "heading" and heading_level and _words(heading_text or ""):
        return Hear(f"Heading level {heading_level}: {_words(heading_text or '')}")
    return None
