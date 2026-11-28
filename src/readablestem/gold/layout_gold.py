"""Reading-order gold from a document's source, with no model and no hand labelling.

The source gives the units (headings, paragraphs, list items, captions, footnotes) in reading
order: the HTML LaTeXML makes from a paper's LaTeX (`units_latexml`), the XHTML of an OpenStax
section (`units_openstax`), or the unit list written next to a file made here. The PDF gives
the characters: every text line of a page is matched to the unit that holds its words
(`match_page`). A gold block is the run of lines of one unit in one column of one page
(`gold_page`), so a paragraph a page or a column cuts is two blocks, and a cut anywhere else
is a reader's error. Blocks are ordered by the source. Footnotes go to the end of the page.
The caption of a float (a LaTeX figure or table, which the typesetter moves) is marked `float`
and is scored for being whole and unjoined, not for its place.

Each block carries

- `role`: heading, body, list, caption or footnote;
- `text`: the PDF's own text of its lines, so the scorer compares like with like;
- `mtext`: the same with every token that is not a source word replaced by `zqzq` (the glyphs
  of an inline formula, a list number the source generates), which the scorer skips;
- `boxes`: its line boxes, for the overlay a person checks (`overlay`);
- `unit` and `pos`: where in the source it stands;
- `level` (a heading only, where the source gives one): the source's heading depth, the tag
  LaTeXML or the OpenStax archive gives it (1 to 6). It is a depth, not what a reader should
  write: `eval/order.py` compares it with the heading levels of the student page by the
  difference the document keeps, so a document that starts at `h2` is not wrong.

`eval/order.py` scores a reader's output against these pages. The builders (`paper_gold`,
`textbook_gold`, `generated_gold`) choose pages by rules that look only at the gold's own
coverage, and `keep_pages` applies the page rules. A set of documents is a folder outside the
repository (`files/<id>.pdf`, `gold/<id>.json`) and a registry of hashes in the repository
(`write_registry`, `verify_set`): the text of someone else's document is never committed.

Limits. A table's cells and a display equation are not units: the source gives no line order
for them that the PDF must follow. A page whose text layer is empty or garbled matches
nothing and is not kept. The match is by words, so two units with the same words (a running
title that is also a heading) are told apart only by their neighbours.
"""

from __future__ import annotations

import collections
import difflib
import hashlib
import json
import re
import statistics
import unicodedata
from collections.abc import Collection, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pymupdf

if TYPE_CHECKING:
    import lxml.html

TOK = re.compile(r"[^\W\d_]+|\d+")
# A token of a block that is not a word of its source unit. `eval/order.py` skips it.
MASK = "zqzq"
# A break in a unit's text (a formula, a table, a note): word n-grams do not cross it.
BRK = "§"
SOFT_HYPHEN = chr(0xAD)
_LINE_END_HYPHEN = re.compile(rf"[-{SOFT_HYPHEN}]\s*$")
_ROMAN = re.compile(r"^[ivxlc]+$")
_NUMBERED = re.compile(r"^(\d{1,3})\.(?:\s|$)")
FLOW_ROLES = ("body", "heading", "list", "front")

Unit = dict[str, Any]
Line = dict[str, Any]
Assigned = list[tuple[int, float] | None]


def margin_lines(page: pymupdf.Page) -> list[str]:
    """The lines printed in a page's top and bottom margins, and the lines set at an angle
    at one of its edges: where running heads, page numbers and stamps stand. (A line set at
    an angle inside the page is a drawing's label.) `eval/order.py` counts an element that
    reads one of them and holds no word of a gold block."""
    lines, stamps, (width, height) = pdf_lines(page)

    def at_an_edge(box: tuple[float, float, float, float]) -> bool:
        across = box[2] < 0.07 * width or box[0] > 0.93 * width
        return across or box[3] < 0.07 * height or box[1] > 0.94 * height

    in_bands = [line["text"] for line in lines if line.get("zone")]
    return in_bands + [stamp["text"] for stamp in stamps if at_an_edge(stamp["box"])]


def tok(text: str | None) -> list[str]:
    return TOK.findall(unicodedata.normalize("NFKC", text or "").lower())


def unit_tokens(text: str) -> list[str | None]:
    """A unit's words, with `None` where the text has a break."""
    out: list[str | None] = []
    for n, chunk in enumerate(text.split(BRK)):
        if n:
            out.append(None)
        out.extend(tok(chunk))
    return out


def _parse(html: str) -> lxml.html.HtmlElement:
    """lxml is a development dependency and the image does not carry it, so it is loaded
    here and not with the module: the command line imports this module at its start."""
    import lxml.html

    return lxml.html.fromstring(html)


def _classes(node: lxml.html.HtmlElement) -> set[str]:
    return set((node.get("class") or "").split())


def _has_words(text: str) -> bool:
    return len(tok(text.replace(BRK, " "))) >= 1


def _wordy(text: str) -> bool:
    """Two or more words that are not numbers or single letters."""
    words = tok(text.replace(BRK, " "))
    return sum(1 for word in words if len(word) >= 2 and not word.isdigit()) >= 2


# ---------------------------------------------------------------------- units: LaTeXML

_LTX_BREAK_TAGS = ("table", "figure", "svg", "img", "pre")
_LTX_BREAK_CLASSES = {
    "ltx_equation",
    "ltx_equationgroup",
    "ltx_listing",
    "ltx_tabular",
    "ltx_ERROR",
}
_LTX_SKIP_TAGS = ("nav", "footer", "script", "style", "math", "svg", "pre")
_LTX_SKIP_CLASSES = {
    "ltx_equation",
    "ltx_equationgroup",
    "ltx_tabular",
    "ltx_listing",
    "ltx_page_footer",
    "ltx_TOC",
    "ltx_pagination",
}
_LTX_RUN_IN = {
    "ltx_title_paragraph",
    "ltx_title_subparagraph",
    "ltx_title_theorem",
    "ltx_title_proof",
}
_LTX_PLACED = {"ltx_keywords", "ltx_classification", "ltx_dates"}
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
# Titles whose tag says nothing of the document's outline (LaTeXML makes "Abstract" an h6),
# and run-in titles, which stand inside their paragraph.
_LTX_NO_LEVEL = {
    "ltx_title_abstract",
    "ltx_title_acknowledgements",
    "ltx_title_keywords",
    "ltx_title_theorem",
    "ltx_title_proof",
}


def _ltx_text(element: lxml.html.HtmlElement, notes: list | None = None) -> str:
    parts: list[str] = []

    def walk(node: Any, top: bool = False) -> None:
        if not isinstance(node.tag, str):
            if not top:
                parts.append(node.tail or "")
            return
        classes = _classes(node)
        if node.tag == "math" or node.tag in _LTX_BREAK_TAGS or classes & _LTX_BREAK_CLASSES:
            parts.append(f" {BRK} ")
        elif classes & {"ltx_note", "ltx_author_notes"}:
            if notes is not None:
                notes.append(node)
            parts.append(" ")
        elif classes & {"ltx_note_mark", "ltx_tag_note"}:
            parts.append(" ")
        else:
            parts.append(node.text or "")
            for child in node:
                walk(child)
        if not top:
            parts.append(node.tail or "")

    walk(element, True)
    return " ".join("".join(parts).split())


_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")


def units_latexml(html: str) -> list[Unit]:
    """The units of a paper, from the HTML (or XHTML) LaTeXML makes of its source."""
    document = _parse(_XML_DECLARATION.sub("", html, count=1))
    articles = document.xpath("//article")
    root = articles[0] if articles else document
    units: list[Unit] = []

    def add(role: str, text: str, **more: Any) -> None:
        if _has_words(text):
            units.append(dict(role=role, text=text, **more))

    def add_notes(notes: list) -> None:
        for note in notes:
            inner = note.xpath('.//*[contains(concat(" ", @class, " "), " ltx_note_content ")]')
            add("footnote", _ltx_text(inner[0] if inner else note, []))

    def walk(node: Any, in_float: bool = False, in_item: bool = False) -> None:
        if not isinstance(node.tag, str):
            return
        classes, tag = _classes(node), node.tag
        if tag in _LTX_SKIP_TAGS or classes & _LTX_SKIP_CLASSES:
            return
        if "ltx_authors" in classes:
            notes: list = []
            add("front", _ltx_text(node, notes), coarse=True)
            add_notes(notes)
            return
        if tag in _HEADINGS and "ltx_title" in classes:
            if in_float:
                return
            notes = []
            more = {} if classes & (_LTX_NO_LEVEL | _LTX_RUN_IN) else {"level": int(tag[1])}
            add("heading", _ltx_text(node, notes), runin=bool(classes & _LTX_RUN_IN), **more)
            add_notes(notes)
            return
        if tag == "figcaption":
            notes = []
            add("caption", _ltx_text(node, notes), float=True)
            add_notes(notes)
            return
        if tag == "li" and "ltx_bibitem" in classes:
            add("list", _ltx_text(node), bib=True)
            return
        if tag == "p" and "ltx_p" in classes:
            if in_float:
                return
            notes = []
            add("list" if in_item else "body", _ltx_text(node, notes))
            add_notes(notes)
            return
        if tag in ("td", "th", "table"):
            return
        if classes & (_LTX_PLACED | {"ltx_acknowledgements"}) and not node.xpath(".//p"):
            # Front matter the class file places (top of the page, its foot): its place is
            # not scored.
            add("front" if classes & _LTX_PLACED else "body", _ltx_text(node))
            return
        floating = (
            in_float or tag == "figure" or bool(classes & {"ltx_figure", "ltx_table", "ltx_float"})
        )
        item = in_item or (tag == "li" and "ltx_item" in classes)
        for child in node:
            walk(child, floating, item)

    walk(root)
    return units


# ---------------------------------------------------------------------- units: OpenStax

_OS_BLOCK_TAGS = {
    "p",
    "ul",
    "ol",
    "dl",
    "div",
    "table",
    "figure",
    "section",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "aside",
}
_OS_HOLDS_BLOCKS = ".//p|.//div|.//h1|.//h2|.//h3|.//h4|.//h5|.//ul|.//ol|.//dl|.//table"
_OS_TEXT_TAGS = ("p", "li", "div", "dd", "blockquote", "section", "td", "span", "a", "body", "html")
_OS_NOTE_LINK = (
    './a[@role="doc-noteref" or @data-type="footnote-number" or @data-type="footnote-link"]'
)


def _os_text(element: lxml.html.HtmlElement) -> str:
    parts: list[str] = []

    def walk(node: Any, top: bool = False) -> None:
        if not isinstance(node.tag, str):
            if not top:
                parts.append(node.tail or "")
            return
        kind = node.get("data-type") or ""
        if node.tag in ("math", "table", "img", "svg", "figure") or kind in (
            "equation",
            "media",
            "footnote-number",
        ):
            parts.append(f" {BRK} ")
        elif node.tag == "sup" and node.xpath(_OS_NOTE_LINK):
            parts.append(" ")
        else:
            parts.append(node.text or "")
            for child in node:
                walk(child)
        if not top:
            parts.append(node.tail or "")

    walk(element, True)
    return " ".join("".join(parts).split())


def units_openstax(xhtml: str, section: str) -> list[Unit]:
    """The units of one OpenStax section, from the archive's XHTML."""
    document = _parse(xhtml)
    units: list[Unit] = []

    def add(role: str, text: str, **more: Any) -> None:
        if _has_words(text):
            units.append(dict(role=role, text=text, section=section, **more))

    def blockish(child: Any) -> bool:
        return isinstance(child.tag, str) and (
            child.tag in _OS_BLOCK_TAGS or bool(child.xpath(_OS_HOLDS_BLOCKS))
        )

    def walk(node: Any, in_item: bool = False) -> None:
        if not isinstance(node.tag, str):
            return
        classes, tag, kind = _classes(node), node.tag, node.get("data-type") or ""
        if tag in ("script", "style", "math", "svg", "img", "nav", "head") or kind in (
            "equation",
            "media",
            "metadata",
        ):
            return
        if tag == "table":
            return
        if "os-caption-container" in classes or tag in ("figcaption", "caption"):
            add("caption", _os_text(node))
            return
        if "os-index-item" in classes:
            term = node.xpath('.//*[contains(concat(" ", @class, " "), " os-term ")]')
            if term:
                add("list", _os_text(term[0]), index=True)
            return
        if "group-label" in classes:
            add("heading", _os_text(node), index=True)
            return
        if tag in _HEADINGS:
            add("heading", _os_text(node), level=int(tag[1]))
            return
        if tag == "dl":
            for child in node:
                if isinstance(child.tag, str) and child.tag == "dt":
                    meaning = child.getnext()
                    told = _os_text(meaning) if meaning is not None and meaning.tag == "dd" else ""
                    add("list", _os_text(child) + " " + told, term=True)
            return
        if kind == "footnote-refs" or (tag == "aside" and node.get("role") == "doc-footnote"):
            for note in node.xpath(".//li") or [node]:
                add("footnote", _os_text(note))
            return
        children = [child for child in node if isinstance(child.tag, str)]
        item = in_item or tag == "li"
        if not any(blockish(child) for child in children):
            if tag in _OS_TEXT_TAGS:
                text = _os_text(node)
                if tag in ("p", "li") or _wordy(text):
                    add("list" if item else "body", text)
            return
        # Mixed content: a run of text between block children is a paragraph of its own.
        run = [node.text or ""]

        def flush() -> None:
            text = " ".join("".join(run).split())
            if _wordy(text):
                add("list" if item else "body", text)
            run.clear()

        for child in node:
            if not isinstance(child.tag, str):
                run.append(child.tail or "")
                continue
            if blockish(child):
                flush()
                walk(child, item)
                run.append(child.tail or "")
            else:
                child_kind = child.get("data-type") or ""
                broken = child.tag == "math" or child_kind in ("media", "equation")
                run.append((f" {BRK} " if broken else _os_text(child)) + (child.tail or ""))
        flush()

    walk(document)
    return units


# ---------------------------------------------------------------------- the PDF's lines


def pdf_lines(page: pymupdf.Page) -> tuple[list[Line], list[Line], tuple[float, float]]:
    """The page's text lines as PyMuPDF reports them, the lines set at an angle (stamps), and
    the page's size. No reading order is taken from this."""
    height, width = page.rect.height, page.rect.width
    lines: list[Line] = []
    stamps: list[Line] = []
    flags = pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_PRESERVE_WHITESPACE
    for block in page.get_text("dict", flags=flags)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            text = "".join(span["text"] for span in line["spans"])
            if not text.strip():
                continue
            box = tuple(line["bbox"])
            across, _down = line.get("dir", (1, 0))
            size = max((s["size"] for s in line["spans"] if s["text"].strip()), default=10.0)
            record: Line = dict(box=box, text=" ".join(text.split()), size=size, toks=tok(text))
            if abs(across) < 0.95:
                stamps.append(record)
                continue
            if box[3] < 0.07 * height:
                record["zone"] = "top"
            elif box[1] > 0.94 * height:
                record["zone"] = "bottom"
            else:
                record["zone"] = None
            lines.append(record)
    return lines, stamps, (width, height)


# ---------------------------------------------------------------------- matching


def _core(tokens: Iterable[str]) -> list[str]:
    """The tokens without numbering in front (a number, a roman numeral, a single letter)."""
    rest = list(tokens)
    while rest and (
        rest[0].isdigit()
        or (_ROMAN.match(rest[0]) and len(rest) > 1)
        or (len(rest[0]) == 1 and len(rest) > 1)
    ):
        rest = rest[1:]
    return rest


class Index:
    """A document's units, indexed by their word 3-grams and 2-grams."""

    def __init__(self, units: list[Unit]) -> None:
        self.units = units
        self.ut = [unit_tokens(unit["text"]) for unit in units]
        self.g3: dict[tuple, list[tuple[int, int]]] = collections.defaultdict(list)
        self.g2: dict[tuple, list[tuple[int, int]]] = collections.defaultdict(list)
        for u, tokens in enumerate(self.ut):
            for i in range(len(tokens) - 2):
                gram = (tokens[i], tokens[i + 1], tokens[i + 2])
                if None not in gram:
                    self.g3[gram].append((u, i))
            for i in range(len(tokens) - 1):
                pair = (tokens[i], tokens[i + 1])
                if None not in pair:
                    self.g2[pair].append((u, i))
        self.sets = [{word for word in tokens if word} for tokens in self.ut]
        # Whole short units, numbering stripped.
        self.short: dict[tuple, list[int]] = collections.defaultdict(list)
        for u in range(len(units)):
            core = _core(self.words(u))
            if 1 <= len(core) <= 8:
                self.short[tuple(core)].append(u)
        # An index entry stands alone: no neighbouring line is attached to it.
        self.noattach = {u for u, unit in enumerate(units) if unit.get("index")}

    def words(self, u: int) -> list[str]:
        return [word for word in self.ut[u] if word]


def _short_candidates(
    ix: Index, tokens: list[str], allow: Collection[int] | None, used: Collection[int] = ()
) -> list[int]:
    """Units that are exactly this line (numbering in front, page numbers behind, stripped)."""
    core = _core(tokens)
    out = list(ix.short.get(tuple(core), ())) if core else []
    tail = list(core)
    while tail and tail[-1].isdigit():
        tail = tail[:-1]
    if tail and tail != core:
        out += [u for u in ix.short.get(tuple(tail), ()) if u in ix.noattach]
    return [u for u in out if (allow is None or u in allow) and u not in used]


def _vote(ix: Index, tokens: list[str]) -> tuple[collections.Counter, dict[int, list[int]]]:
    votes: collections.Counter = collections.Counter()
    places: dict[int, list[int]] = collections.defaultdict(list)
    for i in range(len(tokens) - 2):
        hits = ix.g3.get((tokens[i], tokens[i + 1], tokens[i + 2]), ())
        if len(hits) > 12:
            continue
        seen = set()
        for u, p in hits:
            if u not in seen:
                votes[u] += 1
                seen.add(u)
            places[u].append(p - i)
    return votes, places


def _far_from_flow(candidates: list[int], flow: list[int], index_page: bool) -> bool:
    if index_page or len(candidates) != 1 or not flow:
        return False
    return not (min(flow) - 2 <= candidates[0] <= max(flow) + 2)


def _gutter(a: tuple, b: tuple, lines: list[Line]) -> bool:
    """The white space between two boxes on one row is a column gutter: the rows just above
    and below also keep it clear and have text on both sides. (A wide word space in a
    justified line is crossed by the words of the neighbouring rows.)"""
    if a[0] > b[0]:
        a, b = b, a
    left_edge, right_edge = a[2], b[0]
    if right_edge - left_edge < 6:
        return False
    height = max(a[3] - a[1], b[3] - b[1])
    middle = (min(a[1], b[1]) + max(a[3], b[3])) / 2
    left = right = 0
    for line in lines:
        q = line["box"]
        q_middle = (q[1] + q[3]) / 2
        if abs(q_middle - middle) <= 0.5 * height or abs(q_middle - middle) > 3.6 * height:
            continue
        if q[0] < right_edge - 1 and q[2] > left_edge + 1:
            return False
        if q[2] <= left_edge + 1 and q[2] > left_edge - 120:
            left += 1
        elif q[0] >= right_edge - 1 and q[0] < right_edge + 120:
            right += 1
    return left >= 2 and right >= 2


def match_page(ix: Index, lines: list[Line], allow: Collection[int] | None = None) -> Assigned:
    """Give each line its unit. The result is parallel to `lines`: `(unit, place in the unit)`
    or `None` for a line no unit accounts for."""
    asg: Assigned = [None] * len(lines)
    tied: dict[int, list[tuple[int, float]]] = {}
    for li, line in enumerate(lines):
        t = line["toks"]
        if line.get("zone") == "top" or len(t) < 3:
            continue
        votes, places = _vote(ix, t)
        if allow is not None:
            votes = collections.Counter({u: v for u, v in votes.items() if u in allow})
        if not votes:
            continue
        (top, v1), *rest = votes.most_common(2)
        v2 = rest[0][1] if rest else 0
        n3 = len(t) - 2
        if v1 >= min(2, n3) and (v2 <= 0.5 * v1 or (v1 >= 0.6 * n3 and v1 >= v2 + 2)):
            if line.get("zone") == "bottom" and ix.units[top]["role"] != "footnote":
                continue
            asg[li] = (top, statistics.median(places[top]))
        elif v1 >= min(2, n3) and not line.get("zone"):
            tied[li] = [
                (u, statistics.median(places[u])) for u, v in votes.items() if v >= 0.8 * v1
            ]

    # Index entries: each is a whole line that occurs once. Only on a page that is an index
    # (two in five of its lines are entries), so that a table cell or a wrapped line that
    # happens to equal an index term is left alone.
    hits: dict[int, int] = {}
    used = {a[0] for a in asg if a}
    for li, line in enumerate(lines):
        if asg[li] is not None or line.get("zone") or not line["toks"]:
            continue
        entries = [u for u in _short_candidates(ix, line["toks"], allow, used) if u in ix.noattach]
        if len(entries) == 1:
            hits[li] = entries[0]
    body_lines = sum(1 for line in lines if not line.get("zone") and line["toks"])
    index_page = bool(hits) and len(hits) >= 0.4 * body_lines
    # A page holds a run of the source. A line whose first-pass unit lies far from the run the
    # other lines form (an address that is also in the acknowledgements, a phrase that is also
    # a later title) is left unmatched rather than placed out of order.
    flow_units = sorted(a[0] for a in asg if a and ix.units[a[0]]["role"] in FLOW_ROLES)
    if len(flow_units) >= 6 and not index_page:
        runs, current = [], [flow_units[0]]
        for u in flow_units[1:]:
            if u - current[-1] > 30:
                runs.append(current)
                current = [u]
            else:
                current.append(u)
        runs.append(current)
        main = max(runs, key=len)
        if len(main) >= 0.6 * len(flow_units):
            lo, hi = main[0] - 30, main[-1] + 30
            for li, a in enumerate(asg):
                if a and ix.units[a[0]]["role"] in FLOW_ROLES and not lo <= a[0] <= hi:
                    asg[li] = None
            tied = {li: [(u, p) for u, p in c if lo <= u <= hi] for li, c in tied.items()}
    if index_page:
        for li, u in hits.items():
            asg[li] = (u, 0)

    def lone(u: int) -> bool:
        if u in ix.noattach:
            return index_page
        return ix.units[u]["role"] == "heading"

    def row_span(lj: int) -> tuple[float, float]:
        u = asg[lj][0]  # ty: ignore[not-subscriptable]
        o = lines[lj]["box"]
        ends, starts = [], []
        for lk, a in enumerate(asg):
            if a is None or a[0] != u:
                continue
            q = lines[lk]["box"]
            if min(o[3], q[3]) - max(o[1], q[1]) >= 0.5 * min(o[3] - o[1], q[3] - q[1]):
                ends.append(a[1] + len(lines[lk]["toks"]))
                starts.append(a[1])
        return min(starts), max(ends)

    # Second pass: the lines the first pass left (short, full of mathematics, repeated), by
    # their neighbours.
    for _ in range(8):
        changed = False
        for li, line in enumerate(lines):
            if asg[li] is not None or line.get("zone") or not line["toks"]:
                continue
            b = line["box"]
            h = b[3] - b[1]
            near: list[tuple[int, str]] = []
            for lj, other in enumerate(lines):
                if asg[lj] is None or lj == li:
                    continue
                o = other["box"]
                vov = min(b[3], o[3]) - max(b[1], o[1])
                hov = min(b[2], o[2]) - max(b[0], o[0])
                same_row = (
                    vov >= 0.5 * min(h, o[3] - o[1]) and hov >= -45 and not _gutter(b, o, lines)
                )
                stacked = hov >= 0.3 * min(b[2] - b[0], o[2] - o[0]) and -1.7 * max(
                    h, o[3] - o[1]
                ) <= vov < 0.5 * min(h, o[3] - o[1])
                if same_row or stacked:
                    near.append((lj, "row" if same_row else ("above" if o[1] < b[1] else "below")))
            t = line["toks"]
            wordy = [x for x in t if len(x) >= 2]
            best: tuple[float, int, float] | None = None
            for lj, side in near:
                u, p = asg[lj]  # ty: ignore[not-iterable]
                if u in ix.noattach:
                    continue
                nj = len(lines[lj]["toks"])
                start, end = row_span(lj)
                # Where in the unit this line would stand if it continues its neighbour.
                if side == "above":
                    lo, hi = end - 4, end + len(t) + 10
                    p, nj = end, 0
                elif side == "below":
                    lo, hi = start - len(t) - 10, start + 4
                    p = start
                else:
                    lo, hi = p - len(t) - 12, p + nj + len(t) + 12
                window = {x for x in ix.ut[u][max(0, int(lo)) : max(0, int(hi))] if x}
                if wordy:
                    score = sum(x in window for x in wordy) / len(wordy)
                    if score >= 0.5 and (best is None or score > best[0]):
                        if side == "above":
                            pos = p + nj
                        elif side == "below":
                            pos = p - len(t)
                        else:
                            pos = p + 0.5 if lines[lj]["box"][0] <= b[0] else p - 0.5
                        best = (score, u, pos)
            if best is None and not wordy:
                # A fragment of symbols: only between lines of one unit.
                units_near = {asg[lj][0] for lj, _ in near}  # ty: ignore[not-subscriptable]
                sides = {side for _, side in near}
                if len(units_near) == 1 and len(sides) >= 2 and not (units_near & ix.noattach):
                    lj = near[0][0]
                    ref = [
                        asg[j][1] + len(lines[j]["toks"])  # ty: ignore[not-subscriptable]
                        for j, side in near
                        if side in ("above", "row")
                    ]
                    first = asg[lj]
                    assert first is not None
                    best = (0.5, first[0], max(ref) if ref else first[1] - 1)
            if best is None and li in tied:
                # The first pass found two or more units with these words: the one next to a
                # neighbour's unit in the source.
                neighbours = [asg[lj][0] for lj, side in near if side != "row"]  # ty: ignore[not-subscriptable]
                pick = [(u, p) for u, p in tied[li] if any(abs(x - u) <= 3 for x in neighbours)]
                if len(pick) == 1:
                    best = (1.0, pick[0][0], pick[0][1])
            if best is None:
                # A whole short heading: the candidate just before a neighbour's unit.
                candidates = [u for u in _short_candidates(ix, t, allow) if lone(u)]
                below = [asg[lj][0] for lj, side in near if side == "below"]  # ty: ignore[not-subscriptable]
                above = [asg[lj][0] for lj, side in near if side == "above"]  # ty: ignore[not-subscriptable]
                chosen = [
                    u
                    for u in candidates
                    if any(0 < x - u <= 3 for x in below) or any(0 < u - x <= 3 for x in above)
                ]
                if len(chosen) == 1:
                    best = (1.0, chosen[0], 0)
            if best is not None:
                asg[li] = (best[1], best[2])
                changed = True
        if not changed:
            break
    # Third pass: whole short headings standing alone.
    for li, line in enumerate(lines):
        if asg[li] is not None or line.get("zone") or not line["toks"]:
            continue
        used = {a[0] for a in asg if a}
        flow = [u for u in used if ix.units[u]["role"] in ("body", "heading", "list")]
        candidates = [u for u in _short_candidates(ix, line["toks"], allow, used) if lone(u)]
        # A heading belongs between the units this page already holds. (A figure label that
        # happens to equal a later section's title is not that heading.)
        if _far_from_flow(candidates, flow, index_page):
            continue
        if len(candidates) == 1:
            asg[li] = (candidates[0], 0)
    return asg


def _pieces(members: Iterable[int], lines: list[Line]) -> list[list[int]]:
    """One unit's lines on a page, split into column pieces; each piece in row order."""
    members = list(members)
    parent = {i: i for i in members}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for first in members:
        for second in members:
            if first >= second:
                continue
            a, b = lines[first]["box"], lines[second]["box"]
            ha, hb = a[3] - a[1], b[3] - b[1]
            vov = min(a[3], b[3]) - max(a[1], b[1])
            hov = min(a[2], b[2]) - max(a[0], b[0])
            same_row = vov >= 0.5 * min(ha, hb) and hov >= -45 and not _gutter(a, b, lines)
            stacked = hov >= 0.3 * min(a[2] - a[0], b[2] - b[0]) and vov >= -2.2 * max(ha, hb)
            if same_row or stacked:
                parent[find(first)] = find(second)
    groups: dict[int, list[int]] = collections.defaultdict(list)
    for i in members:
        groups[find(i)].append(i)
    out = []
    for group in groups.values():
        group.sort(
            key=lambda i: ((lines[i]["box"][1] + lines[i]["box"][3]) / 2, lines[i]["box"][0])
        )
        rows: list[tuple[float, list[int]]] = []
        for i in group:
            middle = (lines[i]["box"][1] + lines[i]["box"][3]) / 2
            height = lines[i]["box"][3] - lines[i]["box"][1]
            if rows and abs(middle - rows[-1][0]) <= 0.45 * height:
                rows[-1][1].append(i)
            else:
                rows.append((middle, [i]))
        out.append([i for _, row in rows for i in sorted(row, key=lambda i: lines[i]["box"][0])])
    return out


def _texts(ix: Index, u: int, seq: list[int], lines: list[Line]) -> tuple[str, str, int, int]:
    """A piece's text, its masked text, and how many of its tokens are the unit's words."""
    flat: list[str] = []
    owner: list[tuple[int, int]] = []
    for i in seq:
        for k, token in enumerate(lines[i]["toks"]):
            flat.append(token)
            owner.append((i, k))
    unit_words = ix.words(u)
    unit_set = ix.sets[u]
    # A word broken at a line end counts as the joined word.
    joined: list[str] = []
    back: list[tuple[int, ...]] = []
    k = 0
    while k < len(flat):
        i, place = owner[k]
        last = place == len(lines[i]["toks"]) - 1
        if (
            last
            and k + 1 < len(flat)
            and _LINE_END_HYPHEN.search(lines[i]["text"])
            and flat[k] + flat[k + 1] in unit_set
        ):
            joined.append(flat[k] + flat[k + 1])
            back.append((k, k + 1))
            k += 2
        else:
            joined.append(flat[k])
            back.append((k,))
            k += 1
    matcher = difflib.SequenceMatcher(None, joined, unit_words, autojunk=False)
    ok = [False] * len(flat)
    for block in matcher.get_matching_blocks():
        for j in range(block.a, block.a + block.size):
            for f in back[j]:
                ok[f] = True
    texts, masked = [], []
    at = 0
    member = set(seq)
    # Every line that is not this piece's: left unmatched, or given to another unit (a
    # one-word fragment the neighbour pass put with the paragraph above).
    loose = [
        k
        for k in range(len(lines))
        if k not in member and not lines[k].get("zone") and lines[k]["text"].strip()
    ]
    x0 = min(lines[i]["box"][0] for i in seq)
    x1 = max(lines[i]["box"][2] for i in seq)
    for n, i in enumerate(seq):
        count = len(lines[i]["toks"])
        flags = iter(ok[at : at + count])
        at += count
        raw = unicodedata.normalize("NFKC", lines[i]["text"])
        mtext = TOK.sub(lambda m, flags=flags: m.group(0) if next(flags, True) else MASK, raw)
        # A gap the source does not account for (the glyphs of an inline formula, a short
        # fragment between two formulas) sits between this line and the next. It is marked,
        # so that word runs across the gap are not scored.
        if n + 1 < len(seq):
            a, b = lines[i]["box"], lines[seq[n + 1]]["box"]
            top, bottom = min(a[1], b[1]), max(a[3], b[3])
            same_row = min(a[3], b[3]) - max(a[1], b[1]) >= 0.5 * min(a[3] - a[1], b[3] - b[1])
            gap = 0
            for k in loose:
                q = lines[k]["box"]
                middle = (q[1] + q[3]) / 2
                if not (top < middle < bottom and q[2] > x0 and q[0] < x1):
                    continue
                if same_row:
                    hit = min(a[2], b[2]) - 1 <= (q[0] + q[2]) / 2 <= max(a[0], b[0]) + 1
                else:
                    # Between the two rows, or on A's row after A, or on B's row before B.
                    on_a = min(a[3], q[3]) - max(a[1], q[1]) >= 0.5 * min(a[3] - a[1], q[3] - q[1])
                    on_b = min(b[3], q[3]) - max(b[1], q[1]) >= 0.5 * min(b[3] - b[1], q[3] - q[1])
                    hit = (
                        (on_a and q[0] >= a[2] - 1)
                        or (on_b and q[2] <= b[0] + 1)
                        or (not on_a and not on_b)
                    )
                if hit:
                    gap += max(1, len(lines[k]["toks"]))
            if gap:
                mtext += f" {MASK}" * min(gap, 40)
        masked.append(mtext)
        texts.append(raw)
    return " ".join(texts), " ".join(masked), sum(ok), len(flat)


def gold_page(
    ix: Index,
    lines: list[Line],
    stamps: list[Line],
    size: tuple[float, float],
    asg: Assigned,
    *,
    floats_ordered: bool = False,
) -> dict[str, Any]:
    """One page's gold: its blocks in reading order, and how much of the page they cover."""
    by_unit: dict[int, list[int]] = collections.defaultdict(list)
    for li, a in enumerate(asg):
        if a is not None:
            by_unit[a[0]].append(li)
    blocks = []
    for u, members in by_unit.items():
        unit = ix.units[u]
        pieces = sorted(_pieces(members, lines), key=lambda seq: -len(seq))
        for n, seq in enumerate(pieces):
            text, mtext, aligned, tokens = _texts(ix, u, seq, lines)
            if aligned == 0:
                continue
            # A second piece of a heading unit is a title's note, set elsewhere.
            extra_heading = unit["role"] == "heading" and n > 0
            pos = statistics.median(asg[i][1] for i in seq)  # ty: ignore[not-subscriptable]
            role = unit["role"]
            if role == "heading" and len(ix.words(u)) > 30:
                # A title's note (thanks, funding) that the source keeps in the title.
                role = "footnote"
            block: dict[str, Any] = dict(
                role={"front": "body"}.get(role, role),
                text=text,
                mtext=mtext,
                start=text[:50],
                unit=u,
                pos=pos,
                aligned=aligned,
                tokens=tokens,
                boxes=[[round(x, 1) for x in lines[i]["box"]] for i in seq],
            )
            if unit.get("coarse"):
                block["coarse"] = True
            if role == "heading" and unit.get("level") and not extra_heading:
                block["level"] = unit["level"]
            if unit.get("index") or unit.get("term") or unit.get("entry"):
                block["entry"] = True
            if role == "front" or extra_heading:
                # Authors, addresses, dates: one coarse unit in the source. Being whole and
                # unjoined is scored; the order of its pieces is not.
                block["float"] = True
            if unit.get("float") and not floats_ordered:
                block["float"] = True
            blocks.append(block)
    footnotes = [block for block in blocks if block["role"] == "footnote"]
    flow = [block for block in blocks if block["role"] != "footnote"]
    flow.sort(key=lambda block: (block["unit"], block["pos"]))
    footnotes.sort(key=lambda block: (block["unit"], block["pos"]))
    # Coverage: of the wordy lines outside the page margins, the share in a gold block.
    total = covered = 0
    line_total = line_covered = 0
    for li, line in enumerate(lines):
        if line.get("zone"):
            continue
        words = sum(1 for x in line["toks"] if len(x) >= 3)
        if words >= 5:
            total += words
            covered += words if asg[li] is not None else 0
        if any(len(x) >= 2 and not x.isdigit() for x in line["toks"]):
            line_total += 1
            line_covered += asg[li] is not None
    return dict(
        blocks=flow + footnotes,
        coverage=round(covered / total, 3) if total else None,
        wordy=total,
        line_cov=round(line_covered / line_total, 3) if line_total else None,
        size=[round(size[0], 1), round(size[1], 1)],
        stamps=[stamp["text"] for stamp in stamps if len(stamp["toks"]) >= 3],
    )


_OVERLAY_COLOURS = {
    "heading": (0.8, 0, 0),
    "body": (0, 0.3, 0.9),
    "list": (0, 0.55, 0.3),
    "caption": (0.75, 0.45, 0),
    "footnote": (0.55, 0, 0.7),
}


def overlay(page: pymupdf.Page, gold: dict[str, Any], path: Path, dpi: int = 80) -> None:
    """The page's picture with the gold blocks drawn and numbered in gold order, for the
    person who checks a sample. A dashed box is a float."""
    shape = page.new_shape()
    for n, block in enumerate(gold["blocks"]):
        colour = _OVERLAY_COLOURS.get(block["role"], (0.3, 0.3, 0.3))
        x0 = min(q[0] for q in block["boxes"])
        y0 = min(q[1] for q in block["boxes"])
        x1 = max(q[2] for q in block["boxes"])
        y1 = max(q[3] for q in block["boxes"])
        shape.draw_rect(pymupdf.Rect(x0 - 1, y0 - 1, x1 + 1, y1 + 1))
        shape.finish(color=colour, width=0.9, dashes="[3 2]" if block.get("float") else None)
        shape.insert_text((max(2, x0 - 13), y0 + 8), str(n + 1), fontsize=8.5, color=colour)
    shape.commit()
    page.get_pixmap(dpi=dpi).save(str(path))


# ---------------------------------------------------------------------- builders

MIN_BLOCKS = 3  # a page needs this many blocks whose order is scored
PAPER_COVERAGE = 0.75  # of a paper page's prose lines, the share the source accounts for
LATEX_KINDS = ("paper", "course")  # gold from LaTeXML's HTML: the coverage rule applies
BOOK_COVERAGE = 0.8
INDEX_LINE_COVERAGE = 0.6
SECTION_PAGES, CHAPTER_END_PAGES = 3, 4


def _page_gold(ix: Index, page: pymupdf.Page, *, floats_ordered: bool = False) -> dict[str, Any]:
    lines, stamps, size = pdf_lines(page)
    asg = match_page(ix, lines)
    return gold_page(ix, lines, stamps, size, asg, floats_ordered=floats_ordered)


def paper_gold(
    doc_id: str,
    pdf: pymupdf.Document,
    html: str,
    *,
    source: str,
    category: str,
    kind: str = "paper",
) -> dict[str, Any]:
    """Gold for a paper: pages 1, 2 and 3 and the two pages from the middle, each kept when
    it has three blocks or more. A course handout written in LaTeX is built the same way
    (`kind="course"`)."""
    ix = Index(units_latexml(html))
    count = pdf.page_count
    wanted = sorted({1, 2, 3, count // 2 + 1, count // 2 + 2} & set(range(1, count + 1)))
    pages = []
    for number in wanted:
        gold = _page_gold(ix, pdf[number - 1])
        if len(gold["blocks"]) < MIN_BLOCKS:
            continue
        gold["page"] = number
        pages.append(gold)
    return {"id": doc_id, "kind": kind, "source": source, "cat": category, "pages": pages}


def textbook_gold(
    doc_id: str, book: pymupdf.Document, sections: list[dict[str, str]], *, source: str
) -> tuple[dict[str, Any], list[int]]:
    """Gold for a textbook, and the pages of the book to cut out (1-based).

    `sections` are the fetched sections, each `{"role", "slug", "content"}` with role
    `sectionA`, `sectionB`, `chapter_end` or `index`. Kept, by rules that look only at the
    gold's own coverage: per section group the first three pages with a coverage of 0.8 and
    three blocks or more; the first four such chapter-end pages; the second page on which
    six lines in ten are index entries. The cut holds each kept page and one neighbour each
    side, so a repeated page header can still be recognised; a gold page's `page` is its
    number in the cut and `book_page` its number in the book.
    """
    units: list[Unit] = []
    for section in sections:
        for unit in units_openstax(section["content"], section["slug"]):
            unit["srole"] = section["role"]
            units.append(unit)
    ix = Index(units)
    count = book.page_count
    candidates = []
    for number in range(count):
        tokens = tok(" ".join(word[4] for word in book[number].get_text("words")))
        grams = [(tokens[i], tokens[i + 1], tokens[i + 2]) for i in range(len(tokens) - 2)]
        if len(grams) < 30:
            continue
        known = sum(1 for gram in grams if gram in ix.g3)
        if known >= 0.45 * len(grams) or number >= count - 30:
            candidates.append(number)
    found = []
    for number in candidates:
        gold = _page_gold(ix, book[number], floats_ordered=True)
        if len(gold["blocks"]) < MIN_BLOCKS:
            continue
        roles = collections.Counter(units[block["unit"]]["srole"] for block in gold["blocks"])
        gold["srole"] = roles.most_common(1)[0][0]
        gold["book_page"] = number + 1
        found.append(gold)
    kept = []
    taken: collections.Counter = collections.Counter()
    index_pages = 0
    for gold in found:
        role = gold["srole"]
        if role == "index":
            if (gold["line_cov"] or 0) >= INDEX_LINE_COVERAGE:
                index_pages += 1
                if index_pages == 2:
                    kept.append(gold)
            continue
        if (gold["coverage"] or 0) < BOOK_COVERAGE:
            continue
        if taken[role] < (CHAPTER_END_PAGES if role == "chapter_end" else SECTION_PAGES):
            taken[role] += 1
            kept.append(gold)
    kept.sort(key=lambda gold: gold["book_page"])
    cut = sorted(
        {
            near
            for gold in kept
            for near in (gold["book_page"] - 1, gold["book_page"], gold["book_page"] + 1)
            if 1 <= near <= count
        }
    )
    for gold in kept:
        gold["page"] = cut.index(gold["book_page"]) + 1
    return {"id": doc_id, "kind": "textbook", "source": source, "pages": kept}, cut


def cut_pages(book: pymupdf.Document, pages: list[int]) -> bytes:
    """A PDF of these pages of `book` (1-based), unchanged."""
    out = pymupdf.open()
    for number in pages:
        out.insert_pdf(book, from_page=number - 1, to_page=number - 1)
    return out.tobytes(garbage=3, deflate=True)


def generated_gold(
    doc_id: str,
    pdf: pymupdf.Document,
    units: list[Unit],
    *,
    source: str,
    numbered_run: bool = False,
) -> dict[str, Any]:
    """Gold for a file made here, whose unit list was written with its source.

    Two kinds of unit the matcher leaves are given their lines here, because the source says
    exactly what they hold: a table's body (`tbody`: a line whose words all belong to one
    such unit) and a short exam option (`option`: a line that is the whole unit). With
    `numbered_run` (an answer sheet) each page also lists its numbered blanks in number
    order, as `numrun`.
    """
    ix = Index(units)
    pages = []
    for number in range(1, pdf.page_count + 1):
        lines, stamps, size = pdf_lines(pdf[number - 1])
        asg = match_page(ix, lines)
        for li, line in enumerate(lines):
            if asg[li] is not None or line.get("zone") or not line["toks"]:
                continue
            tokens = line["toks"]
            bodies = [
                u for u, unit in enumerate(units) if unit.get("tbody") and set(tokens) <= ix.sets[u]
            ]
            if len(bodies) == 1:
                asg[li] = (bodies[0], ix.words(bodies[0]).index(tokens[0]))
                continue
            core = _core(tokens)
            options = [
                u for u, unit in enumerate(units) if unit.get("option") and ix.words(u) == core
            ]
            if options:
                before = next((asg[k][0] for k in range(li - 1, -1, -1) if asg[k] is not None), 0)  # ty: ignore[not-subscriptable]
                later = [u for u in options if u > before] or options
                asg[li] = (min(later, key=lambda u: abs(u - before)), 0)
        gold = gold_page(ix, lines, stamps, size, asg)
        gold["page"] = number
        if numbered_run:
            run = []
            for line in lines:
                numbered = _NUMBERED.match(line["text"])
                if numbered and not line.get("zone"):
                    run.append([int(numbered.group(1)), [round(x, 1) for x in line["box"]]])
            run.sort()
            gold["numrun"] = run
        if len(ordered_blocks(gold)) < MIN_BLOCKS:
            continue
        pages.append(gold)
    kind = doc_id.split("_")[1] if doc_id.count("_") else doc_id
    return {"id": doc_id, "kind": "selfmade", "source": source, "cat": kind, "pages": pages}


def ordered_blocks(page: dict[str, Any]) -> list[dict[str, Any]]:
    """The page's blocks whose place is scored."""
    return [block for block in page["blocks"] if not block.get("float")]


def keep_pages(gold: dict[str, Any]) -> dict[str, Any] | None:
    """The page rules, fixed before any reader was scored: a page needs three or more blocks
    whose order is scored, and a page of a paper or a course handout needs the source to
    account for three quarters of its prose lines. `None` when no page is left."""
    pages = []
    for page in gold["pages"]:
        if len(ordered_blocks(page)) < MIN_BLOCKS:
            continue
        if gold["kind"] in LATEX_KINDS and (page.get("coverage") or 0) < PAPER_COVERAGE:
            continue
        pages.append(page)
    return {**gold, "pages": pages} if pages else None


# ---------------------------------------------------------------------- a set and its registry


def canonical(gold: dict[str, Any]) -> bytes:
    """The bytes a gold file's hash is taken of."""
    return json.dumps(gold, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def documents(root: Path) -> Iterator[tuple[str, Path, dict[str, Any]]]:
    """A set folder's documents: id, PDF and gold, in id order."""
    for path in sorted((root / "gold").glob("*.json")):
        gold = json.loads(path.read_text(encoding="utf-8"))
        yield gold["id"], root / "files" / f"{gold['id']}.pdf", gold


PASSED_FILE = "passed.json"  # in a set folder: sources its builders visited and did not take
SIDES = ("development", "held-out")


def registry_of(root: Path, name: str, note: str, side: str = "development") -> dict[str, Any]:
    """What the repository keeps about a set: its side of the split, ids, kinds, sources,
    counts and hashes, and the sources that were visited and not taken. No text of any
    document."""
    if side not in SIDES:
        raise ValueError(f"a set is one of {SIDES}, not {side!r}")
    rows = []
    for doc_id, pdf, gold in documents(root):
        rows.append(
            {
                "id": doc_id,
                "kind": gold["kind"],
                "source": gold["source"],
                "pages": len(gold["pages"]),
                "blocks": sum(len(page["blocks"]) for page in gold["pages"]),
                "pdf_sha256": sha256(pdf.read_bytes()),
                "gold_sha256": sha256(canonical(gold)),
            }
        )
    passed_file = root / PASSED_FILE
    passed = json.loads(passed_file.read_text(encoding="utf-8")) if passed_file.is_file() else {}
    return {"set": name, "side": side, "note": note, "documents": rows, "passed": passed}


def verify_set(root: Path, registry: dict[str, Any]) -> list[str]:
    """What differs between a set folder and its registry; empty when nothing does."""
    problems = []
    found = {doc_id: (pdf, gold) for doc_id, pdf, gold in documents(root)}
    listed = {row["id"]: row for row in registry["documents"]}
    for doc_id in sorted(set(found) - set(listed)):
        problems.append(f"{doc_id}: in the folder, not in the registry")
    for doc_id, row in listed.items():
        if doc_id not in found:
            problems.append(f"{doc_id}: no gold file")
            continue
        pdf, gold = found[doc_id]
        if sha256(canonical(gold)) != row["gold_sha256"]:
            problems.append(f"{doc_id}: the gold differs from the registered gold")
        if not pdf.is_file():
            problems.append(f"{doc_id}: no PDF")
        elif sha256(pdf.read_bytes()) != row["pdf_sha256"]:
            problems.append(f"{doc_id}: the PDF differs from the registered PDF")
    return problems


# ---------------------------------------------------------------------- the hand check

SAMPLE_SHARE = 0.1  # 1 page in 10 of each kind is checked by a person
AGREEMENT_BAR = 0.95  # a kind whose sample agrees on fewer blocks is not used as gold


def sample_pages(root: Path, seed: str, share: float = SAMPLE_SHARE) -> list[tuple[str, int]]:
    """The pages a person checks against the page's picture: per kind of document, a seeded
    draw of `share` of its gold pages (at least one), as `(document id, page)`."""
    by_kind: dict[str, list[tuple[str, str, int]]] = collections.defaultdict(list)
    for doc_id, _pdf, gold in documents(root):
        for page in gold["pages"]:
            key = sha256(f"{seed}\0{doc_id}\0{page['page']}".encode())
            by_kind[gold["kind"]].append((key, doc_id, page["page"]))
    drawn = []
    for kind in sorted(by_kind):
        pages = sorted(by_kind[kind])
        count = max(1, -(-len(pages) * round(share * 1000) // 1000))
        drawn.extend((doc_id, page) for _key, doc_id, page in pages[:count])
    return sorted(drawn)


def write_sample(root: Path, seed: str, out: Path, share: float = SAMPLE_SHARE) -> list[dict]:
    """Write, for each sampled page, its picture with the gold blocks drawn on it, and a sheet
    (`sheet.jsonl`) with one row a block for the checker to fill: `ok` is true when the block
    is one whole unit of the page with the right role and stands in its place."""
    out.mkdir(parents=True, exist_ok=True)
    wanted = collections.defaultdict(list)
    for doc_id, page in sample_pages(root, seed, share):
        wanted[doc_id].append(page)
    rows = []
    for doc_id, pdf, gold in documents(root):
        if doc_id not in wanted:
            continue
        with pymupdf.open(pdf) as document:
            for page in gold["pages"]:
                if page["page"] not in wanted[doc_id]:
                    continue
                picture = f"{doc_id}-p{page['page']}.png"
                overlay(document[page["page"] - 1], page, out / picture)
                rows.extend(
                    {
                        "id": doc_id,
                        "kind": gold["kind"],
                        "page": page["page"],
                        "block": n + 1,
                        "role": block["role"],
                        "float": bool(block.get("float")),
                        "picture": picture,
                        "ok": None,
                        "note": "",
                    }
                    for n, block in enumerate(page["blocks"])
                )
    sheet = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    (out / "sheet.jsonl").write_text(sheet, encoding="utf-8")
    return rows


def agreement(rows: Iterable[dict]) -> dict[str, dict[str, Any]]:
    """Per kind, from a filled sheet: blocks checked, blocks right, and whether the kind may
    be used as gold (`AGREEMENT_BAR`). A row left unfilled counts as not checked, and a kind
    with an unfilled row may not be used."""
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in rows:
        counts[row["kind"]]["blocks"] += 1
        counts[row["kind"]]["checked"] += row["ok"] is not None
        counts[row["kind"]]["right"] += row["ok"] is True
    out = {}
    for kind, count in sorted(counts.items()):
        complete = count["checked"] == count["blocks"]
        share = count["right"] / count["checked"] if count["checked"] else 0.0
        out[kind] = {
            "blocks": count["blocks"],
            "checked": count["checked"],
            "right": count["right"],
            "agreement": round(share, 4),
            "usable": complete and share >= AGREEMENT_BAR,
        }
    return out
