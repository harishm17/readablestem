# ruff: noqa: RUF001
# (look-alike dashes and the multiplication sign are what this module handles)
"""Scorers for the dev-v2 classes that have gold but had no scorer: sub- and superscript runs
in prose, code blocks, glossary entries and words hyphenated at a line end.

The extended development set (`data/dev-v2`, backlog T2-0) labels these items but the
equation, heading, figure, table and document tasks do not look at them, so a builder
working on T2-4, T2-5, T2-16 or T1-9 had nothing to measure. Each scorer here reads the
converted HTML (`DocumentResult.html`, what the document task already has) and the gold
records of the same page:

- `script` records (classes `prose_unit_power`, `prose_chem_script`, `prose_nuclide`,
  `prose_script_other`): every run is found by the letters around it and judged by how the
  output shows it: `sub_sup` (a `<sub>` or `<sup>` element), `math` (a script inside inline
  MathML), `flattened` (the context is there, the digits are glued on as plain text:
  "SO42-", "cm3"), or `lost`. `scripted` is `sub_sup` plus `math`: a reader hears or sees
  a script either way. Context is the letters and digits only, so a multiplication sign
  between a number and its power does not matter.
- `code` records: `exact` (a `<pre>` holds the block line for line with its indentation),
  `in_pre` (a `<pre>` holds it but lines or indentation differ), `text_only` (the lines
  are in the page, not in a `<pre>`), `missing`.
- `glossary` records: `definition_list` (a `<dt>` with the term and a `<dd>` with the
  definition), `text_block` (term and definition in one paragraph or list item),
  `found_apart` (both in the page, in different places), `missing`.
- `hyphen_break` records: `joined` (the word stands whole in the output; a compound word
  keeps its hyphen), `kept_hyphen` (the line-end hyphen is still there, "inves-tigates"),
  `split_words` ("inves tigates"), or `missing`. Words that occur more than once are judged
  by count, and a wrong form anywhere in the page counts against the word, so an
  unbroken copy elsewhere cannot hide a broken one.

`self_check` renders each gold record into the markup the scorers want and scores that: it
must give 1.0 on every metric (wiring and rules, not quality). The metrics are the
`prose_classes` block of the document task's report (`pipeline_eval.document_metrics`) and
`readablestem eval classes --set dev-v2 --self-check`.

Pure functions of HTML text and gold records; no model, no network.
"""

from __future__ import annotations

import difflib
import html as html_lib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser

KINDS = ("script", "code", "glossary", "hyphen_break")
SCRIPT_CLASSES = ("prose_unit_power", "prose_chem_script", "prose_nuclide", "prose_script_other")
SCRIPT_RESULTS = ("sub_sup", "math", "flattened", "lost")
CODE_RESULTS = ("exact", "in_pre", "text_only", "missing")
GLOSSARY_RESULTS = ("definition_list", "text_block", "found_apart", "missing")
HYPHEN_RESULTS = ("joined", "kept_hyphen", "split_words", "missing")

BEFORE_CHARS = 8  # letters and digits of context before a script run
AFTER_CHARS = 4  # ... and after it
CODE_SIMILAR = 0.9  # a block that is this similar (whitespace collapsed) is the same code
DEFINITION_SIMILAR = 0.9  # share of a definition found in order in a block
LINES_FOUND = 0.9  # share of a code block's lines found in the page text
MINUS_LIKE = "‐‑‒–—−﹣－"
HYPHENS = "-‐‑‒­"
_VOID = frozenset({"br", "hr", "img", "meta", "link", "input", "col", "area", "base", "wbr"})
_SKIP = frozenset({"script", "style", "head", "title"})
_BLOCKS = frozenset(
    {"p", "li", "div", "td", "th", "dd", "dt", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
     "section", "article", "figure", "figcaption", "blockquote", "tr", "ul", "ol", "dl",
     "table", "body", "caption", "header", "footer", "main", "nav", "aside"}
)  # fmt: skip


# ---------------------------------------------------------------------------- DOM


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)
    parent: Node | None = None


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: v or "" for k, v in attrs}, parent=self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.cur.children.append(Node(tag, {k: v or "" for k, v in attrs}, parent=self.cur))

    def handle_endtag(self, tag: str) -> None:
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent or self.root
        if node is not self.root:
            self.cur = node.parent or self.root

    def handle_data(self, data: str) -> None:
        self.cur.children.append(data)


def parse_html(source: str) -> Node:
    parser = _Parser()
    parser.feed(source)
    parser.close()
    return parser.root


def _iter(node: Node) -> Iterable[Node]:
    for child in node.children:
        if isinstance(child, Node):
            yield child
            yield from _iter(child)


def _raw_text(node: Node, *, pre: bool = False) -> str:
    """The text of a subtree; `<br>` is a line break, a `<pre>` keeps its whitespace."""
    out: list[str] = []

    def walk(n: Node) -> None:
        if n.tag in _SKIP:
            return
        for child in n.children:
            if isinstance(child, str):
                out.append(child)
            elif child.tag == "br":
                out.append("\n")
            else:
                walk(child)

    walk(node)
    return "".join(out)


def _collapse(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _text(node: Node) -> str:
    return _collapse(_raw_text(node))


def _norm_value(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text)
    for ch in MINUS_LIKE:
        folded = folded.replace(ch, "-")
    return "".join(folded.split()).replace("{", "").replace("}", "")


def _alnum(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if ch.isalnum())


def _containment(part: str, whole: str) -> float:
    """Share of `part`'s characters found in order in `whole` (0 when either is empty)."""
    if not part or not whole:
        return 0.0
    matcher = difflib.SequenceMatcher(None, part, whole, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks()) / len(part)


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


# ------------------------------------------------------------------- script runs


@dataclass
class Marker:
    pos: int  # index into `Flat.plain` where the script's own letters start
    kind: str  # "sub" or "sup"
    value: str  # normalised
    length: int  # letters and digits the script adds to `plain`
    source: str  # "html", "math" or "gold"
    used: bool = False


@dataclass
class Flat:
    """The letters and digits of a text in reading order, and where the scripts are."""

    plain: str = ""
    markers: list[Marker] = field(default_factory=list)

    def add(self, text: str) -> None:
        self.plain += _alnum(text)

    def boundary(self) -> None:
        if self.plain and not self.plain.endswith("|"):
            self.plain += "|"

    def script(self, kind: str, text: str, source: str) -> None:
        start = len(self.plain)
        self.add(text)
        self.markers.append(Marker(start, kind, _norm_value(text), len(self.plain) - start, source))


def _walk_math(node: Node, flat: Flat) -> None:
    kids = [c for c in node.children if isinstance(c, Node)]
    tag = node.tag
    if tag in {"msub", "msup", "msubsup"} and len(kids) >= 2:
        _walk_math(kids[0], flat)
        if tag == "msub":
            flat.script("sub", _text(kids[1]), "math")
        elif tag == "msup":
            flat.script("sup", _text(kids[1]), "math")
        elif len(kids) >= 3:
            flat.script("sub", _text(kids[1]), "math")
            flat.script("sup", _text(kids[2]), "math")
        return
    if tag == "mmultiscripts" and kids:
        split = next((i for i, k in enumerate(kids) if k.tag == "mprescripts"), len(kids))
        post, pre = kids[1:split], kids[split + 1 :]
        for i in range(0, len(pre) - 1, 2):
            for kind, k in (("sup", pre[i + 1]), ("sub", pre[i])):
                if k.tag != "none":
                    flat.script(kind, _text(k), "math")
        _walk_math(kids[0], flat)
        for i in range(0, len(post) - 1, 2):
            for kind, k in (("sub", post[i]), ("sup", post[i + 1])):
                if k.tag != "none":
                    flat.script(kind, _text(k), "math")
        return
    if tag in {"mi", "mn", "mo", "mtext", "ms"}:
        flat.add(_text(node))
        return
    for child in node.children:
        if isinstance(child, str):
            flat.add(child)
        elif child.tag not in _SKIP and child.tag not in {"annotation", "annotation-xml"}:
            _walk_math(child, flat)


def flatten_output(root: Node, *, math_scripts: bool = True) -> Flat:
    """The converted page as letters and digits with its script markers: `<sub>`/`<sup>`
    always, scripts inside inline MathML when `math_scripts`."""
    flat = Flat()

    def walk(node: Node) -> None:
        if node.tag in _SKIP:
            return
        block = node.tag in _BLOCKS
        if block:
            flat.boundary()
        if node.tag in {"sub", "sup"}:
            flat.script(node.tag, _raw_text(node), "html")
        elif node.tag == "math":
            if math_scripts:
                _walk_math(node, flat)
            else:
                flat.add(_raw_text(node))
        else:
            for child in node.children:
                if isinstance(child, str):
                    flat.add(child)
                else:
                    walk(child)
        if block:
            flat.boundary()

    walk(root)
    return flat


_MARK = re.compile(r"([\^_])\{")


def flatten_gold(text: str) -> Flat:
    """A gold `text` with its `^{...}` / `_{...}` markers as a `Flat` (`[math]` dropped)."""
    flat = Flat()
    text = text.replace("[math]", " ")
    pos = 0
    while True:
        match = _MARK.search(text, pos)
        if match is None:
            flat.add(text[pos:])
            return flat
        flat.add(text[pos : match.start()])
        depth, end = 1, match.end()
        while end < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[end], 0)
            end += 1
        inner = text[match.end() : end - 1]
        flat.script("sup" if match.group(1) == "^" else "sub", inner, "gold")
        pos = end


def _find_all(haystack: str, needle: str) -> Iterable[int]:
    start = haystack.find(needle)
    while start != -1:
        yield start
        start = haystack.find(needle, start + 1)


def _judge_run(gold: Flat, marker: Marker, output: Flat) -> str:
    before = gold.plain[max(0, marker.pos - BEFORE_CHARS) : marker.pos]
    own = gold.plain[marker.pos : marker.pos + marker.length]
    after_start = marker.pos + marker.length
    after_end = after_start + AFTER_CHARS
    following = [m.pos for m in gold.markers if m.pos >= after_start and m is not marker]
    if following:
        after_end = min(after_end, max(after_start, min(following)))
    after = gold.plain[after_start:after_end]
    if "|" in after:
        after = after.split("|")[0]
    needle = before + own + after
    if len(_alnum(needle)) == 0:
        return "lost"
    found = False
    for start in _find_all(output.plain, needle):
        found = True
        at = start + len(before)
        for candidate in output.markers:
            if (
                not candidate.used
                and candidate.pos == at
                and candidate.kind == marker.kind
                and candidate.value == marker.value
            ):
                candidate.used = True
                return "sub_sup" if candidate.source == "html" else "math"
    return "flattened" if found else "lost"


def score_scripts(html_root: Node, records: Sequence[dict]) -> list[dict]:
    """One row per gold run: its class, kind, value and how the output shows it."""
    output = flatten_output(html_root, math_scripts=True)
    rows = []
    for record in records:
        if record.get("kind") != "script":
            continue
        gold = flatten_gold(record.get("text", ""))
        runs = record.get("runs", [])
        for index, (marker, run) in enumerate(zip(gold.markers, runs, strict=False)):
            rows.append(
                {
                    "gold_id": record["id"],
                    "run": index,
                    "class": run.get("class") or (record.get("classes") or ["other"])[0],
                    "script": run.get("script"),
                    "value": run.get("value"),
                    "result": _judge_run(gold, marker, output),
                }
            )
    return rows


# --------------------------------------------------------------------------- code


def _code_lines(text: str) -> list[str]:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def score_code(root: Node, records: Sequence[dict]) -> tuple[list[dict], int]:
    """(one row per gold code block, number of `<pre>` blocks matching no gold block)."""
    pres = [(n, _code_lines(_raw_text(n, pre=True))) for n in _iter(root) if n.tag == "pre"]
    page_text = _collapse(_raw_text(root))
    used: set[int] = set()
    rows = []
    for record in records:
        if record.get("kind") != "code":
            continue
        gold_lines = _code_lines(record.get("text", ""))
        gold_flat = _collapse("\n".join(gold_lines))
        best, best_sim = None, 0.0
        for i, (_, lines) in enumerate(pres):
            if i in used:
                continue
            sim = _similarity(gold_flat, _collapse("\n".join(lines)))
            if sim > best_sim:
                best, best_sim = i, sim
        result = "missing"
        if best is not None and best_sim >= CODE_SIMILAR:
            used.add(best)
            result = "exact" if pres[best][1] == gold_lines else "in_pre"
        else:
            wanted = [_collapse(line) for line in gold_lines if line.strip()]
            present = sum(line in page_text for line in wanted)
            if wanted and present / len(wanted) >= LINES_FOUND:
                result = "text_only"
        rows.append({"gold_id": record["id"], "language": record.get("language"), "result": result})
    return rows, len(pres) - len(used)


# ------------------------------------------------------------------------ glossary


def _term_key(text: str) -> str:
    return _alnum(text).casefold()


def _next_definitions(dt: Node) -> str:
    """The text of the `<dd>` elements following a `<dt>` (up to the next `<dt>`)."""
    parent = dt.parent
    if parent is None:
        return ""
    siblings = [c for c in parent.children if isinstance(c, Node)]
    parts: list[str] = []
    for node in siblings[siblings.index(dt) + 1 :]:
        if node.tag == "dt":
            break
        if node.tag == "dd":
            parts.append(_text(node))
    return " ".join(parts)


def score_glossary(root: Node, records: Sequence[dict]) -> list[dict]:
    nodes = list(_iter(root))
    dts = [(n, _term_key(_text(n))) for n in nodes if n.tag == "dt"]
    blocks = [(n, _text(n)) for n in nodes if n.tag in {"p", "li", "td", "div", "dd", "tr"}]
    page_key = _alnum(_collapse(_raw_text(root))).casefold()
    rows = []
    for record in records:
        if record.get("kind") != "glossary":
            continue
        term, definition = record.get("term", ""), _collapse(record.get("definition", ""))
        key = _term_key(term)
        result = "missing"
        if any(
            tkey == key and _containment(definition, _next_definitions(dt)) >= DEFINITION_SIMILAR
            for dt, tkey in dts
        ):
            result = "definition_list"
        elif any(
            key
            and _term_key(text[: len(term) + 4]).startswith(key)
            and _containment(definition, text) >= DEFINITION_SIMILAR
            for _, text in blocks
            if len(text) < 4 * (len(term) + len(definition)) + 40
        ):
            result = "text_block"
        else:
            def_key = _alnum(definition).casefold()
            head = def_key[:50]
            if key and key in page_key and head and head in page_key:
                result = "found_apart"
        rows.append({"gold_id": record["id"], "result": result})
    return rows


# ------------------------------------------------------------------ hyphen breaks


def _page_words(root: Node) -> str:
    text = _raw_text(root)
    text = unicodedata.normalize("NFKC", text)
    for ch in HYPHENS[1:]:
        text = text.replace(ch, "" if ch == "­" else "-")
    return " " + " ".join(text.split()).casefold() + " "


def _count(pattern: str, text: str) -> int:
    return len(re.findall(r"(?<![\w-])" + pattern + r"(?![\w-])", text))


def score_hyphen_breaks(root: Node, records: Sequence[dict]) -> list[dict]:
    text = _page_words(root)
    by_word: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        if record.get("kind") == "hyphen_break":
            by_word[record["word"].casefold()].append(record)
    results: dict[str, str] = {}
    for word, group in by_word.items():
        first = group[0]
        left, right = first["left"].casefold(), first["right"].casefold()
        compound = bool(first.get("compound"))
        whole = re.escape(word)
        kept = _count(re.escape(left) + r"-\s?" + re.escape(right), text) if not compound else 0
        split = _count(re.escape(left) + r"\s+" + re.escape(right), text)
        glued = _count(re.escape(left + right), text) if compound else 0
        wrong = kept + split + glued
        joined = _count(whole, text)
        n = len(group)
        ok = max(0, min(n, joined, n - wrong))
        remaining = n - ok
        labels = []
        for label, count in (("kept_hyphen", kept + glued), ("split_words", split)):
            take = min(remaining - len(labels), count)
            labels += [label] * max(take, 0)
        labels += ["missing"] * (remaining - len(labels))
        outcome = ["joined"] * ok + labels
        for record, result in zip(group, outcome, strict=True):
            results[record["id"]] = result
    return [
        {"gold_id": r["id"], "word": r["word"], "compound": bool(r.get("compound")),
         "result": results[r["id"]]}
        for r in records
        if r.get("kind") == "hyphen_break"
    ]  # fmt: skip


# --------------------------------------------------------------------------- page


def score_page(html: str, records: Sequence[dict]) -> dict:
    """The rows of every class this page's gold has; {} when it has none."""
    wanted = [r for r in records if r.get("kind") in KINDS]
    if not wanted:
        return {}
    root = parse_html(html)
    item: dict = {}
    if any(r["kind"] == "script" for r in wanted):
        item["scripts"] = score_scripts(root, wanted)
    if any(r["kind"] == "code" for r in wanted):
        rows, extra = score_code(root, wanted)
        item["code"] = {"rows": rows, "extra_pre": extra}
    if any(r["kind"] == "glossary" for r in wanted):
        item["glossary"] = score_glossary(root, wanted)
    if any(r["kind"] == "hyphen_break" for r in wanted):
        item["hyphen_breaks"] = score_hyphen_breaks(root, wanted)
    return item


def _rate(count: int, n: int) -> float | None:
    return round(count / n, 4) if n else None


def _tally(rows: Sequence[dict], results: Sequence[str]) -> dict:
    counts = Counter(r["result"] for r in rows)
    return {"n": len(rows), **{name: counts.get(name, 0) for name in results}}


def summarize(items: Sequence[dict]) -> dict:
    """The `prose_classes` metrics from the per-page items (`score_page`); {} when no page
    had gold for these classes."""
    out: dict = {}
    scripts = [row for i in items for row in i.get("scripts", [])]
    if scripts:
        block = _tally(scripts, SCRIPT_RESULTS)
        block["scripted"] = block["sub_sup"] + block["math"]
        block["scripted_rate"] = _rate(block["scripted"], block["n"])
        by_class = {}
        for name in SCRIPT_CLASSES:
            rows = [r for r in scripts if r["class"] == name]
            if rows:
                part = _tally(rows, SCRIPT_RESULTS)
                part["scripted"] = part["sub_sup"] + part["math"]
                part["scripted_rate"] = _rate(part["scripted"], part["n"])
                by_class[name] = part
        block["by_class"] = by_class
        out["scripts"] = block
    code = [row for i in items for row in i.get("code", {}).get("rows", [])]
    if code:
        block = _tally(code, CODE_RESULTS)
        block["exact_rate"] = _rate(block["exact"], block["n"])
        block["found_rate"] = _rate(block["n"] - block["missing"], block["n"])
        block["extra_pre"] = sum(i.get("code", {}).get("extra_pre", 0) for i in items)
        out["code"] = block
    glossary = [row for i in items for row in i.get("glossary", [])]
    if glossary:
        block = _tally(glossary, GLOSSARY_RESULTS)
        block["structured_rate"] = _rate(block["definition_list"], block["n"])
        block["found_rate"] = _rate(block["n"] - block["missing"], block["n"])
        out["glossary"] = block
    breaks = [row for i in items for row in i.get("hyphen_breaks", [])]
    if breaks:
        block = _tally(breaks, HYPHEN_RESULTS)
        block["joined_rate"] = _rate(block["joined"], block["n"])
        out["hyphen_breaks"] = block
    return out


# ---------------------------------------------------------------------- self-check


def _gold_script_html(text: str) -> str:
    """A gold paragraph as HTML: markers become `<sup>`/`<sub>`, `[math]` becomes a
    multiplication sign."""
    out: list[str] = []
    text = text.replace("[math]", "×")
    pos = 0
    while True:
        match = _MARK.search(text, pos)
        if match is None:
            out.append(html_lib.escape(text[pos:]))
            return "".join(out)
        out.append(html_lib.escape(text[pos : match.start()]))
        depth, end = 1, match.end()
        while end < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[end], 0)
            end += 1
        tag = "sup" if match.group(1) == "^" else "sub"
        out.append(f"<{tag}>{html_lib.escape(text[match.end() : end - 1])}</{tag}>")
        pos = end


def render_gold_html(records: Sequence[dict]) -> str:
    """HTML in the shape the scorers want, made from the gold itself (for `self_check`)."""
    body: list[str] = []
    for record in records:
        kind = record.get("kind")
        if kind == "script":
            body.append(f"<p>{_gold_script_html(record.get('text', ''))}</p>")
        elif kind == "code":
            body.append(f"<pre><code>{html_lib.escape(record.get('text', ''))}</code></pre>")
        elif kind == "glossary":
            term, definition = (
                html_lib.escape(record["term"]),
                html_lib.escape(record["definition"]),
            )
            body.append(f"<dl><dt>{term}</dt><dd>{definition}</dd></dl>")
        elif kind == "hyphen_break":
            body.append(f"<p>{html_lib.escape(record['word'])}</p>")
    return "<html><body>" + "\n".join(body) + "</body></html>"


def self_check(records: Sequence[dict]) -> dict:
    """Score the gold against HTML rendered from the gold: every metric must be 1.0. Pages
    are scored one at a time, as the document task does. Returns the `summarize` block."""
    pages: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        if record.get("kind") in KINDS:
            pages[(record.get("book_slug", ""), record.get("page_slug", ""))].append(record)
    items = [score_page(render_gold_html(page), page) for page in pages.values()]
    return summarize(items)
