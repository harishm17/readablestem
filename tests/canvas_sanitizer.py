"""A Python model of what Canvas's HTML sanitizer does to a page, for the Canvas export tests.

Canvas runs a page's body through the `sanitize` gem with the configuration in
`gems/canvas_sanitize/lib/canvas_sanitize/canvas_sanitize.rb` (WikiPage:
`sanitize_field :body, CanvasSanitize::SANITIZE`). The allowlist is the fixture
`fixtures/canvas/canvas_sanitize_allowlist.json`; where it came from is in its `source`.

This is a model, not Canvas. It repeats the rules that decide what survives:

- an element that is not allowed is removed; its children stay in its place, unless it is
  one of `remove_contents_when_removed` (script, style, svg, ...), which go with their
  contents. So `<main>`, `<html>`, `<head>` and `<title>` disappear and leave their text
  behind (the HTML5 fragment parser Canvas uses ignores html, head and body tags but
  keeps `<title>`, which the sanitizer then unwraps, so a title's text turns up in the
  page);
- an attribute that is not allowed for the element, or for every element, is removed
  (`data-*` only where the name matches the Ruby `REGEX_DATA_ATTR`);
- a URL attribute with a rule in `protocols` is removed when its scheme is not listed
  (`data:` is not, for img) or when it has none and `:relative` is not listed; the
  scheme is found with Sanitize's REGEX_PROTOCOL;
- a `style` attribute keeps only the declarations whose property is in `css_properties`
  (and a `url()` only with an allowed scheme); it is dropped when nothing is left;
- comments are removed; an id with spaces loses them.

What it does not model: the browser-side editor (TinyMCE) that may re-clean a page when
someone opens it for editing, Canvas's rewriting of links on the way out, and its MathJax.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ALLOWLIST_FILE = Path(__file__).parent / "fixtures" / "canvas" / "canvas_sanitize_allowlist.json"
VOID = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)
# Sanitize::REGEX_PROTOCOL
REGEX_PROTOCOL = re.compile(r"\A\s*([^/#]*?)(?::|&#0*58|&#x0*3a)", re.I)


@cache
def allowlist() -> dict[str, Any]:
    return json.loads(ALLOWLIST_FILE.read_text(encoding="utf-8"))


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)

    def walk(self):
        """Every element below this one, in document order."""
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def find_all(self, *tags: str) -> list[Node]:
        return [n for n in self.walk() if n.tag in tags]

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#fragment")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, {k: (v or "") for k, v in attrs}))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse(html: str) -> Node:
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return builder.root


# ------------------------------------------------------------------------- the rules


def _protocol_ok(value: str, allowed: list[str]) -> bool:
    match = REGEX_PROTOCOL.match(value)
    if match:
        return match.group(1).lower() in allowed
    return ":relative" in allowed


def _split_declarations(css: str) -> list[tuple[str, str]]:
    """`name: value` pairs; a `;` inside quotes or parentheses does not end one."""
    out, depth, quote, start = [], 0, "", 0
    for i, ch in enumerate([*css, ";"]):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ";" and depth == 0:
            piece = css[start:i]
            start = i + 1
            if ":" in piece:
                name, _, value = piece.partition(":")
                out.append((name.strip().lower(), value.strip()))
    return out


def clean_style(css: str) -> str:
    rules = allowlist()
    kept = []
    for name, value in _split_declarations(css):
        if name not in rules["css_properties"]:
            continue
        lowered = value.lower()
        if "expression" in lowered:
            continue
        bad_url = any(
            not _protocol_ok(m.group(1).strip("\"' "), rules["css_protocols"])
            for m in re.finditer(r"url\(([^)]*)\)", value, re.I)
        )
        if bad_url:
            continue
        kept.append(f"{name}: {value};")
    return " ".join(kept)


def _clean_attrs(node: Node) -> None:
    rules = allowlist()
    allowed = set(rules["attributes"].get(node.tag, [])) | set(rules["attributes"]["*"])
    data_ok = ":data" in allowed
    protocols = rules["protocols"].get(node.tag, {})
    kept: dict[str, str] = {}
    for raw_name, value in node.attrs.items():
        name = raw_name.lower()
        if name not in allowed and not (
            data_ok and name.startswith("data-") and re.match(_data_regex(), name)
        ):
            continue
        if name in protocols:
            if not _protocol_ok(value, protocols[name]):
                continue
            value = value.strip()
        if name == "style":
            value = clean_style(value)
            if not value.strip():
                continue
        if name == "id" and re.search(r"\s", value):
            value = re.sub(r"\s+", "", value)
        kept[name] = value
    node.attrs = kept


@cache
def _data_regex() -> re.Pattern[str]:
    # Ruby's \A ... \z become ^ ... $ with fullmatch semantics.
    source = allowlist()["data_attribute_regex"].replace(r"\A", "^").replace(r"\z", "$")
    return re.compile(source)


def sanitize(root: Node) -> Node:
    """The fragment as Canvas would store it."""
    rules = allowlist()
    allowed_elements = set(rules["elements"])
    drop_with_contents = set(rules["remove_contents_when_removed"])

    def clean(children: list[Node | str]) -> list[Node | str]:
        out: list[Node | str] = []
        for child in children:
            if isinstance(child, str):
                out.append(child)
                continue
            if child.tag not in allowed_elements:
                if child.tag not in drop_with_contents:
                    out.extend(clean(child.children))
                continue
            child.children = clean(child.children)
            if child.tag == "iframe":
                child.children = []
            _clean_attrs(child)
            out.append(child)
        return out

    clone = parse(serialize(root))  # leave the caller's tree alone
    clone.children = clean(clone.children)
    return clone


def sanitize_html(html: str) -> Node:
    return sanitize(parse(html))


def canonical_style(css: str) -> str:
    """A style attribute written one way (`name: value;` pairs), so two spellings compare equal."""
    return " ".join(f"{name}: {value};" for name, value in _split_declarations(css))


def serialize(node: Node, *, canonical: bool = False) -> str:
    """The tree as HTML; `canonical` also writes every style attribute the same way."""
    from html import escape

    def one(n: Node | str) -> str:
        if isinstance(n, str):
            return escape(n, quote=False)
        attrs = "".join(
            f' {k}="{escape(canonical_style(v) if canonical and k == "style" else v, quote=True)}"'
            for k, v in n.attrs.items()
        )
        if n.tag in VOID:
            return f"<{n.tag}{attrs}>"
        return f"<{n.tag}{attrs}>{''.join(one(c) for c in n.children)}</{n.tag}>"

    return "".join(one(c) for c in node.children)


# ------------------------------------------------------------------------- what to compare


@dataclass
class Content:
    """The parts of a page whose loss would matter to a reader, from a parsed tree."""

    images: list[tuple[str, str | None]]  # (src, alt); alt None when there is no alt attribute
    math: list[str]  # each <math>, serialised
    headings: list[tuple[int, str]]
    tables: list[dict[str, Any]]
    links: list[tuple[str, str]]  # (href, text)
    text: str
    ids: set[str]
    langs: list[str]


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def content(root: Node) -> Content:
    """What a reader gets. `<head>`, `<style>` and `<title>` are not content (a page's title
    is the page's name in Canvas, and a stylesheet is not text)."""

    def body_text(n: Node | str) -> str:
        if isinstance(n, str):
            return n
        if n.tag in ("head", "style", "title", "script"):
            return ""
        return "".join(body_text(c) for c in n.children)

    tables = []
    for table in root.find_all("table"):
        caption = table.find_all("caption")
        tables.append(
            {
                "caption": _squash(caption[0].text()) if caption else None,
                "rows": len(table.find_all("tr")),
                "headers": [
                    (th.tag, th.attrs.get("scope"), _squash(th.text()))
                    for th in table.find_all("th")
                ],
                "cells": [_squash(td.text()) for td in table.find_all("td")],
                "spans": [
                    (c.attrs.get("colspan"), c.attrs.get("rowspan"))
                    for c in table.find_all("td", "th")
                ],
            }
        )
    return Content(
        images=[(i.attrs.get("src", ""), i.attrs.get("alt")) for i in root.find_all("img")],
        math=[serialize(Node("#f", children=[m])) for m in root.find_all("math")],
        headings=[
            (int(h.tag[1]), _squash(h.text()))
            for h in root.find_all("h1", "h2", "h3", "h4", "h5", "h6")
        ],
        tables=tables,
        links=[(a.attrs.get("href", ""), _squash(a.text())) for a in root.find_all("a")],
        text=_squash("".join(body_text(c) for c in root.children)),
        ids={n.attrs["id"] for n in root.walk() if "id" in n.attrs},
        langs=[n.attrs["lang"] for n in root.walk() if "lang" in n.attrs],
    )
