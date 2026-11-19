"""Score a reading order against gold: is each block whole, unjoined and in its place?

The input is, for one page, the gold blocks in reading order (`corpus.load_gold`) and the
output's elements in the order a listener hears them: `{"kind", "text"}` dicts made from a
layout dump (`elements_from_layout`) or from the student HTML (`elements_from_html`). The
scorer never looks at boxes or at how the output was produced, so any reader can be scored.

Per page, over the gold blocks whose role is not `dropped`:

- `found` of `blocks`: at least half of the block's words are on the page.
- `flow_ok` of `flow_tot`: the block's word 4-grams that appear, unbroken, in the page's
  word stream (every element's words, in output order). A sentence cut by another column's
  line, or read in the wrong order inside a block, loses 4-grams.
- `broken`: blocks of 6 or more words that are found but have other text inside them or keep
  under 80% of their 4-grams. A listener hears a cut sentence.
- `opairs_ok` of `opairs`: neighbouring found blocks whose places in the stream come out in
  gold order. `tau_ok` of `tau_tot` is the same over all pairs.
- `xjoin`: elements holding text of two gold blocks that are not neighbours (a column joined
  to a sidebar, a margin note inside a paragraph).
- `leaked` of `dropped`: text that must not be read (a running header, a page number) that
  is in the output.
- `clean`: every found block unbroken, every neighbouring pair in order, no cross-join, and
  at least half of the blocks found.
- `right`: clean, and also no margin stamp read inside the text, no index or glossary
  entries joined, and numbered blanks in order, on pages whose gold lists those things.

Five further counts name what a page that is `right` can still have wrong. Each is counted
on every page, and `faultless` is `right` with none of them (on gold made from a source,
which has a `coverage`, with no `furniture` in place of no `stray`):

- `stray`: text elements none of whose words any gold block accounts for: a running head,
  a page number, a stamp, a paragraph read a second time, a drawing's lettering read as
  paragraphs where the gold has no figure block that holds it. A gold block accounts for the
  copy of its words nearest its place, so a running head that repeats a heading is still
  stray. A piece of a block read apart from the rest (a paragraph's last line, a contents
  entry's page number) is not stray: the block is cut or broken, and is counted there. Gold
  made from a document's source does not hold every word of the page (a table's cells, a
  formula's line, an author's address), so there `stray` also counts text that belongs on
  the page.
- `furniture`: the stray elements that are the page's furniture: the words of a `dropped`
  block, or of a line printed in the page's margins (the page's `margins`, read from the
  file by `gold.layout_gold.margin_lines`: the top and bottom bands, and a line set at an
  angle at the page's edge). This is the count of running heads, page numbers and stamps a
  listener hears, on every kind of gold. A footnote printed in the bottom band that the
  gold does not hold is counted too.
- `cut`: unbroken blocks of 6 or more words heard in two or more elements. A display
  equation standing between the two parts is not a cut. Where the gold was labeled by hand
  (`by_hand`: a block may hold two paragraphs), only a cut inside a sentence is counted.
- `merged`: neighbouring gold blocks heard in one element (two list items as one
  paragraph), and `heading_merged`, the part of them where one of the two is a heading.
  Not counted: two pieces of one source unit (a paragraph a column cuts; in gold labeled
  by hand, a body block that ends inside a sentence and the one after it), two index
  entries (that is `ejoin`), and an element that is a table. A block of one to three words
  whose place is inside another block's words is not counted: a lost heading whose word
  occurs in a sentence is lost, not merged.
- `caption_wrong` of `caption_judged`: captions that stand next to a figure or table block
  in the gold and are heard with, or nearest to, another picture or table. Judged only when
  the page has as many pictures (or tables) in the output as in the gold, and at least two.

What the five do not see: a formula missing from a sentence or from a line of its own (the
gold masks the tokens that are not source words, but a mask is also a drawing's label or
another column's word on the same line, so it does not say where a formula stands), a wrong
word inside a block that keeps its 4-grams, and the role an element was given.
"""

from __future__ import annotations

import html.parser
import itertools
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

VERSION = 5
MASK = "zqzq"
COARSE_ROLES = ("table", "figure", "form")
EQUATIONS = ("equation", "math")
PICTURES = ("figure", "image")
NOT_TEXT = (*EQUATIONS, *PICTURES, "table")
FURTHER = ("stray", "cut", "merged", "caption_wrong")

_LEAD = re.compile(
    r"^\s*(?:\(?(?:\d{1,3}(?:\.\d{1,3})*|[A-Za-z]|[ivxIVX]{1,4})[.):]"
    r"|\d{1,3}(?:\.\d{1,3})*(?=\s+[A-Z])|[•●▪–☐□-])\s+"  # noqa: RUF001
)
_TRAIL = re.compile(r"(?:\s*[.…· ]{2,}\s*|\s+)\d{1,4}\s*$")
_HYPHENATED = re.compile(r"([A-Za-z]+)[-­]\s+([a-z]+)")
_MASKS = re.compile(rf"(?:{MASK})+")
_PLACEHOLDER = re.compile(r"⟦([^⟧]+)⟧")
_MARKED = re.compile(r"^\(?([A-Za-z]|\d{1,2})[.)]\s")
_NUMBER = re.compile(r"(?<![\d.])(\d{1,3})\.(?!\d)")
_ENDS = re.compile(r"[.!?:;][\"'”’)\]]*\s*$")  # noqa: RUF001

Element = dict
Pages = dict[int, list[Element]]


def words(text: str) -> list[str]:
    """Lower-case words and numbers, without a leading list marker or a trailing page number.

    The output keeps list markers outside the text and the gold keeps them in.
    """
    text = _TRAIL.sub("", _LEAD.sub("", text or "", count=1))
    return re.findall(r"[^\W\d_]+|\d+", unicodedata.normalize("NFKC", text).lower())


def grams(tokens: list[str], n: int = 4) -> list[tuple[str, ...]]:
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def _every_word(text: str) -> list[str]:
    """`words` without the stripping: a list marker and a page number are words too."""
    return re.findall(r"[^\W\d_]+|\d+", unicodedata.normalize("NFKC", text or "").lower())


def _count(hay: list[str], needle: list[str], inside: set[int] | None = None) -> int:
    """How often `needle` occurs in `hay` (at places that are all in `inside`)."""
    n = len(needle)
    return sum(
        hay[at : at + n] == needle and (inside is None or inside.issuperset(range(at, at + n)))
        for at in range(len(hay) - n + 1)
    )


def _text(block: dict) -> str:
    return block.get("text") or block.get("start") or ""


# --- What a listener hears, from a layout dump or the student HTML ----------------------


def elements_from_layout(records: Iterable[dict]) -> Pages:
    """Elements by page from layout records (`kind`, `page`, `order`, `text`, ...).

    An equation placeholder inside text takes the equation's text, as the HTML does, and
    that equation is not heard a second time. A figure is heard with its caption.
    """
    records = [dict(record) for record in records]
    equations = {r["id"]: r.get("text") or "" for r in records if r["kind"] == "equation"}
    inlined: set[str] = set()

    def inline(match: re.Match) -> str:
        inlined.add(match.group(1))
        return " " + equations.get(match.group(1), "") + " "

    for record in records:
        record["text"] = _PLACEHOLDER.sub(inline, record.get("text") or "")
    pages: Pages = defaultdict(list)
    for record in records:
        if record["kind"] == "equation" and record["id"] in inlined:
            continue
        parts = (record["text"], record.get("table_text") or "", record.get("caption") or "")
        pages[record["page"]].append(
            {
                "kind": record["kind"],
                "text": " ".join(parts),
                "order": record["order"],
                "marker": record.get("list_marker") or "",
            }
        )
    for elements in pages.values():
        elements.sort(key=lambda e: e["order"])
    return pages


def elements_from_result(elements: Iterable[Any]) -> Pages:
    """Elements by page from a conversion's elements (`DocumentResult.elements`): what the
    pipeline holds before any file is written. A table with a grid is heard as the grid
    that is written, row by row, and then its caption; the words the file has in its
    region are not heard a second time."""
    records = []
    for element in elements:
        record = {
            "id": element.id,
            "kind": str(element.kind),
            "page": element.page + 1,
            "order": element.order,
            "text": element.text,
            "caption": element.caption,
            "list_marker": element.list_marker,
        }
        if element.table is not None:
            cells = sorted(element.table.cells, key=lambda cell: (cell.row, cell.col))
            record["text"] = " ".join(cell.text for cell in cells)
            record["caption"] = element.table.caption
        records.append(record)
    return elements_from_layout(records)


_BLOCK_TAGS = frozenset(
    [
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "figcaption",
        "caption",
        "table",
        "dt",
        "dd",
        "pre",
        "blockquote",
        "aside",
        "td",
        "th",
    ]
)
_VOID = ("br", "img", "hr", "input", "meta", "link")
_UNHEARD = ("script", "style", "annotation", "annotation-xml", "button", "details")
_MATH_WORDS = ("mtext", "ms")  # words inside a formula, which stand apart from its letters
# Invisible times, separator and plus: the page prints the letters they join side by side.
_UNSEEN = str.maketrans("", "", "\u2062\u2063\u2064")


def _own_text(alt: str) -> str:
    """The PDF's own text of an equation kept as a picture, which is that picture's alt
    text without the writer's words around it; "" for any other picture."""
    from readablestem.pipeline import assemble

    return assemble.equation_alt_text(alt)


def _is_writers(tag: str, given: dict) -> bool:
    """Whether an element of the student page holds the writer's own words and none of the
    document's: a note, a label or a page marker, which carry the writer's class, and the
    title the writer puts above a document that has no first-level heading (every heading
    of the document has an id, and that title has none)."""
    from readablestem.pipeline import assemble

    if assemble.AUTHORED_CLASS in (given.get("class") or "").split():
        return True
    return tag == "h1" and not given.get("id")


class _Heard(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = 1
        self.pages: Pages = defaultdict(list)
        self.open: list[list] = []  # block elements not yet closed: [tag, texts]
        self.in_main = False
        self.skipping = 0
        self.tables = 0
        self.math: list[str] | None = None  # the tokens of the formula being read
        self.tag = ""  # the tag just opened

    def handle_starttag(self, tag: str, attrs: list) -> None:
        given = dict(attrs)
        if tag == "main":
            self.in_main = True
        if not self.in_main:
            return
        if tag == "hr" and given.get("role") == "doc-pagebreak":
            number = re.search(r"(\d+)", given.get("id") or given.get("aria-label") or "")
            if number:
                self.page = int(number.group(1))
            return
        if (
            self.skipping
            or given.get("aria-hidden") == "true"
            or tag in _UNHEARD
            or _is_writers(tag, given)
        ):
            self.skipping += 0 if tag in _VOID else 1
            return
        self.tag = tag
        if tag == "img":
            said = _own_text(given.get("alt") or "")
            if said and (self.open or self.tables):
                self._say(f" {said} ")
            elif said:
                self.pages[self.page].append({"kind": "math", "text": said})
            return
        if tag == "math" and self.math is None:
            self.math = []
        if tag == "table":
            self.tables += 1
            if self.tables == 1:
                self._start(tag)
        elif self.tables:
            return
        elif tag in _BLOCK_TAGS or (tag == "math" and not self.open):
            self._start(tag)
        elif tag == "figure":
            self.pages[self.page].append({"kind": "figure", "text": ""})

    def _start(self, tag: str) -> None:
        # A block opened inside another (a nested list in a list item): the parent's text so
        # far is heard first.
        if self.open and " ".join(self.open[-1][1]).strip():
            text = " ".join(self.open[-1][1]).strip()
            self.pages[self.page].append({"kind": self.open[-1][0], "text": text})
            self.open[-1][1] = []
        self.open.append([tag, []])

    def handle_endtag(self, tag: str) -> None:
        if tag == "main":
            self.in_main = False
        if not self.in_main:
            return
        if self.skipping:
            if tag not in ("br", "img", "hr", "input"):
                self.skipping -= 1
            return
        self.tag = ""
        if tag == "math" and self.math is not None:
            said, self.math = "".join(self.math).translate(_UNSEEN), None
            self._say(f" {said} ")
        if tag == "table":
            self.tables -= 1
            if self.tables == 0:
                self._end("table")
        elif (tag in _BLOCK_TAGS or tag == "math") and not self.tables:
            self._end(tag)

    def _end(self, tag: str) -> None:
        for at in range(len(self.open) - 1, -1, -1):
            if self.open[at][0] == tag:
                kind, texts = self.open.pop(at)
                text = " ".join(texts).strip()
                if text:
                    self.pages[self.page].append({"kind": kind, "text": text})
                return

    def handle_data(self, data: str) -> None:
        if not self.in_main or self.skipping:
            return
        if self.math is not None:
            # A formula's letters and digits stand side by side, as the page prints them.
            self.math.append(f" {data} " if self.tag in _MATH_WORDS else data.strip())
            return
        self._say(data)

    def _say(self, data: str) -> None:
        if self.open:
            self.open[-1][1].append(data)
        elif data.strip():
            self.pages[self.page].append({"kind": "text", "text": data.strip()})


def elements_from_html(document: str) -> Pages:
    """Elements by page from the student HTML, with pages cut at the page-break markers.

    A display equation is one element of kind `math`, and a `<figure>` is an element of kind
    `figure` with no words: the description a listener hears there is not the document's.
    A formula's letters and digits are heard side by side, as the page prints them ("CH3"
    and not "C H 3"). The picture of an equation that was not converted is heard as the
    PDF's own text of it, which is its alt text; no other picture's alt text is heard.
    The writer's own words are not heard either (a note that an equation was not converted,
    a label, the title it adds above a document without one): they are a listener's to
    hear and not a block of any page.
    """
    parser = _Heard()
    parser.feed(document)
    for elements in parser.pages.values():
        for order, element in enumerate(elements):
            element["order"] = order
    return parser.pages


# --- The first scorer's element-level counts --------------------------------------------


def _plain_words(text: str) -> list[str]:
    return re.findall(r"\w+", (text or "").lower())


def _gold_words(block: dict) -> list[str]:
    tokens = _plain_words(_text(block))
    if block["role"] == "list" and len(tokens) > 1 and _MARKED.match(_text(block).strip()):
        return tokens[1:]  # the output keeps the list marker in its own field
    return tokens


def _shingles(tokens: list[str]) -> set[tuple[str, ...]]:
    return set(grams(tokens, 3))


def _holds(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return any(hay[i : i + n] == needle for i in range(len(hay) - n + 1))


def _is_coarse(block: dict) -> bool:
    return bool(block.get("coarse")) or block["role"] in COARSE_ROLES


def _element_counts(blocks: list[dict], elements: list[dict]) -> dict:
    read = [b for b in blocks if b["role"] != "dropped"]
    row = dict.fromkeys(
        ("found", "lost", "split", "pairs", "pairs_ok", "stitched", "dropped", "leaked"), 0
    )
    row.update(blocks=len(read), elements=len(elements))
    page_words = {t for e in elements for t in e["tk"]}
    pictures = [e for e in elements if e["kind"] in ("figure", "image")]
    all_shingles = set().union(*[e["sh"] for e in elements]) if elements else set()
    owners: list[int | None] = []
    for block in read:
        tokens = _gold_words(block)
        if not tokens:
            continue
        owner, found = None, False
        if _is_coarse(block):
            distinct = set(tokens)
            is_figure = block["role"] == "figure" and bool(pictures)
            found = len(distinct & page_words) / len(distinct) >= 0.5 or is_figure
            best = None
            for element in elements:
                overlap = len(distinct & set(element["tk"]))
                if overlap and (best is None or overlap > best[0]):
                    best = (overlap, element["order"])
            owner = best[1] if best else None
            if is_figure and owner is None:
                owner = pictures[0]["order"]
        elif len(tokens) <= 3:
            holders = [e for e in elements if _holds(e["tk"], tokens)]
            if holders:
                found = True
                owner = min(holders, key=lambda e: len(e["tk"]))["order"]
        else:
            shingles = _shingles(tokens)
            shares = []
            for element in elements:
                overlap = len(shingles & element["sh"])
                if overlap:
                    shares.append((overlap / len(shingles), element["order"]))
            found = len(shingles & all_shingles) / len(shingles) >= 0.5
            if found:
                shares.sort(key=lambda share: -share[0])
                owner = shares[0][1]
                # The element holding the block's first three words decides its place.
                first = [e["order"] for e in elements if _holds(e["tk"], tokens[:3])]
                if first:
                    owner = min(first)
                if sum(1 for share, _ in shares if share >= 0.2) >= 2:
                    row["split"] += 1
        row["found" if found else "lost"] += 1
        owners.append(owner if found else None)
    placed = [owner for owner in owners if owner is not None]
    for a, b in itertools.pairwise(placed):
        row["pairs"] += 1
        row["pairs_ok"] += a <= b
    for element in elements:
        if len(element["tk"]) < 2:
            continue
        held = 0
        for block in read:
            tokens = _gold_words(block)
            if not tokens or _is_coarse(block):
                continue
            if len(tokens) <= 3:
                n = len(tokens)
                ends = element["tk"][:n] == tokens or element["tk"][-n:] == tokens
                held += len(element["tk"]) > n and ends and n >= 2
            else:
                shingles = _shingles(tokens)
                held += bool(shingles) and len(shingles & element["sh"]) / len(shingles) >= 0.6
        row["stitched"] += held >= 2
    for block in blocks:
        if block["role"] == "dropped":
            row["dropped"] += 1
            tokens = _gold_words(block)
            row["leaked"] += bool(tokens) and any(_holds(e["tk"], tokens) for e in elements)
    return row


# --- The stream scores ------------------------------------------------------------------


def score_blocks(
    blocks: list[dict],
    elements: list[Element],
    *,
    by_hand: bool = False,
    margins: Iterable[str] = (),
) -> dict:
    """One page's counts (see the module text). `detail` lists each fault found.

    `margins` are the lines printed in the page's margins.
    """
    heard: list[dict[str, Any]] = []
    for order, element in enumerate(elements):
        tokens = _plain_words(element["text"])
        heard.append({**element, "order": order, "tk": tokens, "sh": _shingles(tokens)})
    row = _element_counts(blocks, heard)

    stream: list[str] = []
    owner: list[int] = []
    owned: dict[int, range] = {}
    for order, element in enumerate(heard):
        mine = words(element["text"])
        owned[order] = range(len(stream), len(stream) + len(mine))
        stream += mine
        owner += [order] * len(mine)
    places = defaultdict(list)
    for at, gram in enumerate(grams(stream)):
        places[gram].append(at)
    pair_places = defaultdict(list)
    for at, gram in enumerate(grams(stream, 2)):
        pair_places[gram].append(at)
    page_words = set(stream)

    def join(match: re.Match) -> str:
        # A gold word hyphenated at a line end is joined when the joined word is what the
        # output has; a real compound ("signal-to- noise") stays two words.
        whole = match.group(1) + match.group(2)
        return whole if whole.lower() in page_words else match.group(1) + " " + match.group(2)

    read = []
    for block in blocks:
        if block["role"] != "dropped":
            masked = _MASKS.sub(f" {MASK} ", block.get("mtext") or _text(block))
            read.append({**block, "text": _HYPHENATED.sub(join, masked)})

    in_blocks = Counter(g for b in read for g in set(grams(words(_text(b)))) if MASK not in g)
    detail: list[list] = []
    flow_tot = flow_ok = broken = 0
    positions: list[tuple[int, float | None]] = []
    blocks_in_element = defaultdict(set)
    claims: dict[int, list[int]] = {}  # a block's words in the stream, by block
    short: set[int] = set()  # blocks of one to three words
    whole: set[int] = set()  # blocks of 6 or more words that are found and not broken

    def last_place() -> float:
        return next((place for _, place in reversed(positions) if place is not None), -1)

    for index, block in enumerate(read):
        tokens = words(_text(block))
        block_grams = [g for g in grams(tokens) if MASK not in g]
        real = set(tokens) - {MASK}
        if not real:
            continue
        coverage = len(real & page_words) / len(real)
        if not block_grams:
            # A short block (one to three words) is ambiguous: the first match after the
            # block before it, else the last match before that.
            tokens = [t for t in tokens if t != MASK]
            n = len(tokens)
            hits = [i for i in range(len(stream) - n + 1) if stream[i : i + n] == tokens]
            after = [i for i in hits if i > last_place()]
            positions.append((index, after[0] if after else (hits[-1] if hits else None)))
            # An element that is exactly the block holds it, wherever its words occur again.
            alone = [i for i in hits if owned[owner[i]] == range(i, i + n)]
            place = alone[0] if alone else positions[-1][1]
            if place is not None:
                claims[index] = list(range(int(place), int(place) + n))
                short.add(index)
            continue
        kept = [g for g in block_grams if g in places]
        # A 4-gram that occurs more than once is placed at the occurrence nearest the
        # block's unambiguous 4-grams (or after the block before), so a repeated phrase does
        # not drag a block to the wrong spot.
        unique = [places[g][0] for g in kept if len(places[g]) == 1]
        if unique:
            anchor = statistics.median(unique)
            spots = [min(places[g], key=lambda i: (abs(i - anchor), i)) for g in kept]
        else:
            anchor = last_place() + 0.5
            spots = [next((i for i in places[g] if i > anchor), places[g][-1]) for g in kept]
        if not _is_coarse(block):
            flow_tot += len(block_grams)
            flow_ok += len(kept)
            pairs = [g for g in grams(tokens, 2) if g in pair_places and MASK not in g]
            if coverage >= 0.5 and len(tokens) >= 6 and kept and pairs:
                middle = statistics.median(spots)
                reach = 2 * len(tokens) + 4
                # Word pairs whose nearest copy is far from the block (a formula's "ch 3"
                # that occurs again only in a drawing) do not stretch the span; a block with
                # a fifth of its pairs far away is split, so it counts as broken.
                nearest = [min(pair_places[g], key=lambda i: abs(i - middle)) for g in pairs]
                near = sorted(x for x in nearest if abs(x - middle) <= reach) or [int(middle)]
                far = sum(1 for x in nearest if abs(x - middle) > reach)
                span = near[-1] + 2 - near[0]
                foreign = span - len(tokens)
                if far >= max(3, 0.2 * len(nearest)):
                    foreign = max(foreign, 3)
                    span = max(span, int(1.25 * len(tokens)) + 1)
                cut = foreign >= 3 and span > 1.25 * len(tokens)
                if cut or (coverage >= 0.9 and len(kept) / len(block_grams) < 0.8):
                    broken += 1
                    ends = (owner[near[0]], owner[min(near[-1], len(owner) - 1)])
                    detail.append(["broken", block["role"], _text(block)[:70], *ends])
                else:
                    whole.add(index)
            for gram in set(kept):
                if len(places[gram]) == 1 and in_blocks[gram] == 1:
                    blocks_in_element[owner[places[gram][0]]].add(index)
        positions.append((index, float(statistics.median(spots)) if spots else None))
        if spots:
            middle, reach = statistics.median(spots), 2 * len(tokens) + 4
            near_spots = [spot for spot in spots if abs(spot - middle) <= reach]
            claims[index] = sorted({at for spot in near_spots for at in range(spot, spot + 4)})

    def owner_at(place: float) -> int | None:
        return owner[min(max(int(place), 0), len(owner) - 1)] if owner else None

    def caption_of_its_table(b1: int, p1: float, b2: int, p2: float) -> bool:
        # A caption the reader attached to its own table is announced with the table,
        # whatever the dump order, so that pair is not an order fault.
        roles = {read[b1]["role"], read[b2]["role"]}
        return roles == {"caption", "table"} and owner_at(p1) == owner_at(p2) is not None

    ordered = [(b, p) for b, p in positions if p is not None and not read[b].get("float")]
    opairs = len(ordered) - 1 if ordered else 0
    opairs_ok = 0
    for (b1, p1), (b2, p2) in itertools.pairwise(ordered):
        if p1 < p2 or caption_of_its_table(b1, p1, b2, p2):
            opairs_ok += 1
        else:
            roles = read[b1]["role"] + ">" + read[b2]["role"]
            detail.append(["order", roles, _text(read[b1])[:50], _text(read[b2])[:50]])
    sequence = [p for _, p in ordered]
    count = len(sequence)
    tau_ok = sum(sequence[i] < sequence[j] for i in range(count) for j in range(i + 1, count))

    rank, following = {}, 0
    for index, block in enumerate(read):
        if not block.get("float"):
            rank[index] = following
            following += 1
    xjoin = 0
    for element, held in blocks_in_element.items():
        floats = sorted(b for b in held if read[b].get("float"))
        fixed = sorted(rank[b] for b in held if not read[b].get("float"))
        apart = any(b - a > 1 for a, b in itertools.pairwise(fixed))
        floats_apart = any(
            read[b]["unit"] - read[a]["unit"] > 1 for a, b in itertools.pairwise(floats)
        )
        if apart or (floats and fixed) or floats_apart:
            xjoin += 1
            names = [read[b]["role"] + ":" + _text(read[b])[:30] for b in sorted(held)]
            detail.append(["xjoin", heard[element]["kind"], heard[element]["text"][:70], names])

    entries = defaultdict(set)
    for index, place in positions:
        if place is not None and read[index].get("entry") and 0 <= int(place) < len(owner):
            entries[owner[int(place)]].add(read[index].get("unit", index))
    ejoin = 0
    for element, units in entries.items():
        if len(units) >= 2:
            ejoin += 1
            detail.append(
                ["ejoin", heard[element]["kind"], len(units), heard[element]["text"][:90]]
            )

    places_of = (stream, owner, owned, claims)
    row.update(_further(read, heard, places_of, whole, short, blocks_in_element, detail, by_hand))
    furniture = [_every_word(text) for text in margins]
    furniture += [_every_word(_text(block)) for block in blocks if block["role"] == "dropped"]
    strays = [found for found in detail if found[0] == "stray"]
    row["furniture"] = sum(
        any(_one_line(_every_word(found[2]), line) for line in furniture) for found in strays
    )
    row.update(
        tau_tot=count * (count - 1) // 2,
        tau_ok=tau_ok,
        flow_tot=flow_tot,
        flow_ok=flow_ok,
        broken=broken,
        opairs=opairs,
        opairs_ok=opairs_ok,
        xjoin=xjoin,
        ejoin=ejoin,
        entries=sum(1 for b in read if b.get("entry")),
        detail=detail,
    )
    found_enough = row["found"] >= 0.5 * max(1, row["blocks"])
    row["clean"] = int(found_enough and broken == 0 and opairs_ok == opairs and xjoin == 0)
    return row


def _one_line(said: list[str], line: list[str]) -> bool:
    """Whether an element's words are a furniture line, a part of it of two or more words,
    the whole of it and more, or the number at either end of it."""
    if not said or not line:
        return False
    short, long = sorted((said, line), key=len)
    if len(short) >= 2 or len(long) == 1:
        return _count(long, short) > 0
    return said[0].isdigit() and said[0] in (line[0], line[-1])


def _further(
    read: list[dict],
    heard: list[dict],
    places_of: tuple[list[str], list[int], dict[int, range], dict[int, list[int]]],
    whole: set[int],
    short: set[int],
    blocks_in_element: dict[int, set[int]],
    detail: list[list],
    by_hand: bool,
) -> dict:
    """Stray elements, cut blocks, merged neighbours and captions on the wrong picture."""
    stream, owner, owned, claims = places_of
    out = dict.fromkeys(
        ("stray", "cut", "merged", "heading_merged", "caption_judged", "caption_wrong"), 0
    )

    def texty(order: int) -> bool:
        return heard[order]["kind"] not in NOT_TEXT and bool(owned[order])

    claimed = {at for spots in claims.values() for at in spots}
    coarse_words = {t for b in read if _is_coarse(b) for t in words(_text(b))}
    gold_words = [_every_word(_text(block)) for block in read]
    excused: Counter = Counter()
    for order, element in enumerate(heard):
        if not texty(order) or claimed.intersection(owned[order]):
            continue
        said = stream[owned[order].start : owned[order].stop]
        # A table or a form is checked for presence only, so its words claim no place.
        if 2 * len(set(said) & coarse_words) >= len(set(said)):
            continue
        # A piece of a block read apart from the rest: the gold has these words once more
        # than the blocks' places account for.
        in_gold = sum(_count(tokens, said) for tokens in gold_words)
        if in_gold > _count(stream, said, claimed) + excused[tuple(said)]:
            excused[tuple(said)] += 1
            continue
        out["stray"] += 1
        detail.append(["stray", element["kind"], element["text"]])

    for index in sorted(whole & set(claims)):
        parts = [k for k, _ in itertools.groupby(owner[at] for at in claims[index])]
        parts = [k for k, _ in itertools.groupby(k for k in parts if texty(k))]
        for a, b in itertools.pairwise(parts):
            between = range(min(a, b) + 1, max(a, b))
            equation = any(heard[k]["kind"] in EQUATIONS for k in between)
            if equation and not any(texty(k) for k in between):
                continue
            if not (by_hand and _ENDS.search(heard[min(a, b)]["text"])):
                out["cut"] += 1
                detail.append(["cut", read[index]["role"], _text(read[index])[:70], a, b])
                break

    by_long = {at for index, spots in claims.items() if index not in short for at in spots}

    def holders(index: int) -> set[int]:
        sure = {element for element, held in blocks_in_element.items() if index in held}
        mine = claims.get(index, ())
        if index in short and by_long.intersection(mine):
            return set()
        return sure or {owner[at] for at in mine}

    flowing = [index for index, block in enumerate(read) if not block.get("float")]
    for i, j in itertools.pairwise(flowing):
        a, b = read[i], read[j]
        if _is_coarse(a) or _is_coarse(b) or (a.get("entry") and b.get("entry")):
            continue
        if "unit" in a and a["unit"] == b.get("unit"):
            continue
        runs_on = a["role"] == b["role"] == "body" and not _ENDS.search(_text(a))
        if by_hand and runs_on and _text(b)[:1].islower():
            continue  # a sentence a column cuts is two blocks
        shared = {k for k in holders(i) & holders(j) if heard[k]["kind"] != "table"}
        if shared:
            out["merged"] += 1
            out["heading_merged"] += (a["role"] == "heading") != (b["role"] == "heading")
            detail.append(["merged", a["role"] + "+" + b["role"], _text(a)[:40], _text(b)[:40]])

    pictures = [order for order, e in enumerate(heard) if e["kind"] in PICTURES]
    tables = [order for order, e in enumerate(heard) if e["kind"] == "table"]
    things = pictures + tables
    place: dict[int, int] = {}  # a gold figure or table block -> its element
    for role, found in (("figure", pictures), ("table", tables)):
        wanted = [index for index, block in enumerate(read) if block["role"] == role]
        if len(wanted) == len(found):
            place.update(zip(wanted, found, strict=True))
    taken: set[int] = set()
    for index, block in enumerate(read):
        if block["role"] != "caption":
            continue
        beside = [k for k in (index - 1, index + 1) if 0 <= k < len(read) and k not in taken]
        partner = next((k for k in beside if read[k]["role"] in ("figure", "table")), None)
        if partner is None:
            continue
        taken.add(partner)
        held = holders(index)
        if partner not in place or not held or len(things) < 2:
            continue
        out["caption_judged"] += 1
        on = held & set(things)
        if not on:
            least = min(abs(thing - k) for thing in things for k in held)
            on = {thing for thing in things if any(abs(thing - k) == least for k in held)}
        if place[partner] not in on:
            out["caption_wrong"] += 1
            detail.append(["caption_wrong", _text(block)[:70]])
    return out


def _stamps(page: dict, elements: list[Element]) -> dict:
    """Margin stamps (a sideways identifier, a watermark): read at all, and read in text."""
    out = {"stamp_pages": 0, "stamp_read": 0, "stamp_in_text": 0}
    stamps = [s for s in (words(text) for text in page.get("stamps") or []) if len(s) >= 3]
    if not stamps:
        return out
    out["stamp_pages"] = 1
    gold: set[tuple[str, ...]] = set()
    for block in page["blocks"]:
        gold |= {
            g for g in grams(words(block.get("mtext") or block.get("text") or "")) if MASK not in g
        }
    texts = [words(e["text"]) for e in elements]

    def has_gold(tokens: list[str]) -> bool:
        return any(g in gold for g in grams(tokens))

    for stamp in stamps:
        key = " " + " ".join(stamp) + " "
        stamp_grams = grams(stamp)
        hits = []
        for at, tokens in enumerate(texts):
            shared = sum(g in set(grams(tokens)) for g in stamp_grams)
            whole = key in " " + " ".join(tokens) + " "
            if whole or (stamp_grams and shared >= 0.6 * len(stamp_grams)):
                hits.append(at)
        if not hits:
            continue
        out["stamp_read"] = 1
        for at in hits:
            between = any(map(has_gold, texts[:at])) and any(map(has_gold, texts[at + 1 :]))
            if has_gold(texts[at]) or between:
                out["stamp_in_text"] = 1
    return out


def _number_run(page: dict, elements: list[Element]) -> dict:
    """An answer sheet's numbered blanks must come out 1, 2, 3, ... down each column."""
    out = {"numrun_pages": 0, "numrun_bad": 0}
    run = page.get("numrun")
    if not run:
        return out
    wanted = [number for number, _ in run]
    out["numrun_pages"] = 1
    got: list[int] = []
    for element in elements:
        for match in _NUMBER.finditer(
            (element.get("marker") or "") + " " + (element.get("text") or "")
        ):
            number = int(match.group(1))
            if number in wanted and number not in got:
                got.append(number)
    if got != sorted(got) or len(got) < 0.9 * len(wanted):
        out["numrun_bad"] = 1
    return out


def score_page(page: dict, elements: list[Element], *, by_hand: bool = False) -> dict:
    """Every count for one gold page against the elements heard on it."""
    margins = page.get("margins") or ()
    row = score_blocks(page["blocks"], elements, by_hand=by_hand, margins=margins)
    row.update(_stamps(page, elements))
    row.update(_number_run(page, elements))
    faults = row["stamp_in_text"] or row["ejoin"] or row["numrun_bad"]
    row["right"] = int(row["clean"] and not faults)
    # Gold made from a source does not hold every word of the page: stray text is no fault.
    further = ("furniture" if "coverage" in page else "stray", *FURTHER[1:])
    row["faultless"] = int(row["right"] and not any(row[count] for count in further))
    return row


def score_document(gold: dict, pages: Pages) -> list[dict]:
    """One row per gold page of a document; a page the output lacks scores as empty."""
    rows = []
    for page in gold["pages"]:
        row = score_page(page, pages.get(page["page"], []), by_hand=bool(gold.get("by_hand")))
        rows.append({**row, "id": gold["id"], "page": page["page"]})
    return rows


def total(rows: list[dict]) -> Counter:
    sums: Counter = Counter()
    for row in rows:
        for key, value in row.items():
            if isinstance(value, int | float) and not isinstance(value, bool) and key != "page":
                sums[key] += value
    sums["pages"] = len(rows)
    return sums


HEADER = (
    "| Run | Pages | Found | Flow (4-grams kept) | Broken blocks | Neighbours in order | "
    "All pairs in order | Cross-joins | Leaked | Clean pages | Right pages |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|"
)


def table_row(name: str, sums: Counter) -> str:
    def share(part: float, whole: float) -> str:
        return f"{100 * part / whole:.1f}%" if whole else "-"

    return (
        f"| {name} | {sums['pages']} | {share(sums['found'], sums['blocks'])} | "
        f"{share(sums['flow_ok'], sums['flow_tot'])} | {sums['broken']} | "
        f"{share(sums['opairs_ok'], sums['opairs'])} | {share(sums['tau_ok'], sums['tau_tot'])} | "
        f"{sums['xjoin']} | {sums['leaked']}/{sums['dropped']} | "
        f"{sums['clean']}/{sums['pages']} | {sums['right']}/{sums['pages']} |"
    )
