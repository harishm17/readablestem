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

`pairs`, `pairs_ok`, `stitched`, `split` and `lost` are the first scorer's element-level
counts, kept so old numbers stay comparable.

Optional gold fields: `mtext` (the block's text with tokens that are not source words
replaced by `zqzq`, skipped everywhere), `float` (a caption the typesetter moved: scored for
being whole, not for order), `entry` and `unit` (index or glossary entries), `coarse` (a
block only checked for presence, like a table); and on the page, `stamps` and `numrun`.
Gold without them scores exactly as scorer version 3 did.
"""

from __future__ import annotations

import html.parser
import itertools
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable

VERSION = 4
MASK = "zqzq"
COARSE_ROLES = ("table", "figure", "form")

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


class _Heard(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = 1
        self.pages: Pages = defaultdict(list)
        self.open: list[list] = []  # block elements not yet closed: [tag, texts]
        self.in_main = False
        self.skipping = 0
        self.tables = 0

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
        if self.skipping or given.get("aria-hidden") == "true" or tag in _UNHEARD:
            self.skipping += 0 if tag in _VOID else 1
            return
        if tag == "table":
            self.tables += 1
            if self.tables == 1:
                self._start(tag)
        elif tag in _BLOCK_TAGS and not self.tables:
            self._start(tag)

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
        if tag == "table":
            self.tables -= 1
            if self.tables == 0:
                self._end("table")
        elif tag in _BLOCK_TAGS and not self.tables:
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
        if self.open:
            self.open[-1][1].append(data)
        elif data.strip():
            self.pages[self.page].append({"kind": "text", "text": data.strip()})


def elements_from_html(document: str) -> Pages:
    """Elements by page from the student HTML, with pages cut at the page-break markers."""
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


def score_blocks(blocks: list[dict], elements: list[Element]) -> dict:
    """One page's counts (see the module text). `detail` lists each fault found."""
    heard = []
    for order, element in enumerate(elements):
        tokens = _plain_words(element["text"])
        heard.append({**element, "order": order, "tk": tokens, "sh": _shingles(tokens)})
    row = _element_counts(blocks, heard)

    stream: list[str] = []
    owner: list[int] = []
    for order, element in enumerate(heard):
        mine = words(element["text"])
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
            for gram in set(kept):
                if len(places[gram]) == 1 and in_blocks[gram] == 1:
                    blocks_in_element[owner[places[gram][0]]].add(index)
        positions.append((index, statistics.median(spots) if spots else None))

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


def score_page(page: dict, elements: list[Element]) -> dict:
    """Every count for one gold page against the elements heard on it."""
    row = score_blocks(page["blocks"], elements)
    row.update(_stamps(page, elements))
    row.update(_number_run(page, elements))
    faults = row["stamp_in_text"] or row["ejoin"] or row["numrun_bad"]
    row["right"] = int(row["clean"] and not faults)
    return row


def score_document(gold: dict, pages: Pages) -> list[dict]:
    """One row per gold page of a document; a page the output lacks scores as empty."""
    rows = []
    for page in gold["pages"]:
        row = score_page(page, pages.get(page["page"], []))
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
