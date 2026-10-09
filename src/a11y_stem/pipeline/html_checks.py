"""Checks on generated HTML.

`static_checks` is pure Python (`html.parser`), fast enough to run on every conversion.
It returns one `Check` per rule, passed or not, so a report shows what was checked:

| Check name | Code when it fails | What it catches |
|---|---|---|
| html.lang | missing_lang | no `lang` on `<html>` (screen readers pick the wrong voice) |
| html.title | missing_title | empty or missing `<title>` |
| html.main | main_count | not exactly one `<main>` landmark |
| html.heading_order | heading_skip | a heading more than one level below the previous one |
| html.heading_text | empty_heading | a heading with no text (or img alt) in it |
| html.img_alt | img_missing_alt | `<img>` without an `alt` attribute (`alt=""` is allowed) |
| html.unique_ids | duplicate_id | an id used twice |
| html.list_structure | list_structure | an `<li>` outside a list, or anything but `<li>` in one |
| html.math_display | math_missing_display | `<math>` with no block/inline display (warning) |
| html.self_contained | external_resource | scripts, links, frames, or any non-data: URL |
| html.wording | banned_wording | a compliance claim (`_BANNED`) in text the tool wrote |

The wording rule (docs/design/beta-architecture.md rule 7) only reads text inside
elements carrying `assemble.AUTHORED_CLASS` (the notes and the about section). Course
text is the instructor's, not a claim by this tool: mechanics uses "compliance" for the
flexibility of a beam.

Documents with a `<table>` get five more rules (`_table_rules`, docs/tables.md), so a
document without one gets exactly the rules above:

| Check name | Code when it fails | What it catches |
|---|---|---|
| html.table_headers | table_no_headers | a data table without a single `<th>` |
| html.table_header_scope | th_without_scope | a `<th>` with no `scope` (see below) |
| html.table_header_text | empty_th | a `<th>` with no text (or img alt) in it |
| html.table_caption | table_caption_missing | a caption left in the text (warning, below) |
| html.table_layout | layout_table | a table with `role="presentation"` or `"none"` |

A `<th>` without `scope` passes when it has an id and its table uses `headers` (the
serialiser's irregular tables). The caption rule fails for a data table without
`<caption>` right beside a "Table N ..." paragraph (layout's caption pattern, so "Table 2
shows that ..." is not one). The output never uses a table for layout, so the last rule
only fails on HTML made some other way.

`axe_checks` runs the vendored axe-core (vendor/README.md) in headless Chromium when the
`browser` dependency group and its browser are installed, and returns one skipped
warning otherwise. Results carry rule ids and impact only, never page content.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from html.parser import HTMLParser
from importlib import resources
from itertools import pairwise
from typing import Any, Literal

from a11y_stem.core.types import Check
from a11y_stem.pipeline.assemble import AUTHORED_CLASS

log = logging.getLogger(__name__)

Severity = Literal["error", "warning"]

VOID_ELEMENTS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
     "track", "wbr"}
)  # fmt: skip
# Elements that load or run something; none belongs in a self-contained output file.
EXTERNAL_ELEMENTS = frozenset({"script", "link", "iframe", "frame", "object", "embed", "base"})
URL_ATTRIBUTES = frozenset({"src", "href", "srcset", "poster", "data", "action", "formaction"})
_HEADING = re.compile(r"h([1-6])")
LISTS = frozenset({"ul", "ol", "menu"})
LIST_CHILDREN = frozenset({"li", "script", "template"})
_BANNED = re.compile(r"\bcomplian(?:t|ce)\b", re.IGNORECASE)
_CSS_EXTERNAL = re.compile(r"@import|url\(\s*['\"]?(?!data:)", re.IGNORECASE)

AXE_VERSION = "4.14.0"
AXE_SHA256 = "20c09fe157a8a34a30e241aaa1fcdade657734f08ab379ecfbeb7d45cc46e878"
AXE_TAGS = ("wcag2a", "wcag2aa", "wcag21aa")
AXE_ERROR_IMPACTS = frozenset({"serious", "critical"})
AXE_SKIPPED = Check(name="axe", passed=True, severity="warning", code="skipped")
_AXE_RULE_ID = re.compile(r"[a-z0-9-]{1,64}")
# Only rule ids, impact and node counts leave the page: never node HTML or selectors.
_AXE_RUN = """async (tags) => {
  const results = await axe.run(document, {
    runOnly: { type: "tag", values: tags },
    resultTypes: ["violations"],
  });
  return results.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length }));
}"""


# --------------------------------------------------------------------------- static


class _Collector(HTMLParser):
    """One pass over the document, gathering what the static rules need."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang: str | None = None
        self.title: list[str] = []
        self.mains = 0
        self.headings: list[tuple[int, str]] = []
        self.imgs_without_alt = 0
        self.ids: Counter[str] = Counter()
        self.maths_without_display = 0
        self.external = 0
        self.authored_text: list[str] = []
        self.list_errors = 0  # <li> outside a list, or other content directly in one
        self._stack: list[tuple[str, bool]] = []  # (tag, inside authored text)
        self._heading: tuple[int, list[str]] | None = None
        self._in_title = False
        self._in_style = False

    # ----- parser callbacks

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, dict(attrs), void=tag in VOID_ELEMENTS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, dict(attrs), void=True)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "style":
            self._in_style = False
        elif self._heading is not None and tag == f"h{self._heading[0]}":
            level, parts = self._heading
            self.headings.append((level, "".join(parts)))
            self._heading = None
        names = [name for name, _ in self._stack]
        if tag in names:  # pop to the matching start tag (closes anything left open)
            del self._stack[len(names) - 1 - names[::-1].index(tag) :]

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data)
        if self._in_style and _CSS_EXTERNAL.search(data):
            self.external += 1
        if self._heading is not None:
            self._heading[1].append(data)
        if self._stack and self._stack[-1][1]:
            self.authored_text.append(data)
        if self._stack and self._stack[-1][0] in LISTS and data.strip():
            self.list_errors += 1  # text directly inside <ul>/<ol>

    # ----- helpers

    def _start(self, tag: str, attrs: dict[str, str | None], *, void: bool) -> None:
        parent = self._stack[-1][0] if self._stack else None
        if (tag == "li" and parent not in LISTS) or (parent in LISTS and tag not in LIST_CHILDREN):
            self.list_errors += 1
        if tag == "html":
            self.lang = attrs.get("lang")
        elif tag == "title":
            self._in_title = True
        elif tag == "style":
            self._in_style = True
        elif tag == "img":
            self._img(attrs)
        elif tag == "math" and attrs.get("display") not in ("block", "inline"):
            self.maths_without_display += 1
        if tag == "main" or attrs.get("role") == "main":
            self.mains += 1
        if (match := _HEADING.fullmatch(tag)) and self._heading is None:
            self._heading = (int(match.group(1)), [])
        if element_id := (attrs.get("id") or "").strip():
            self.ids[element_id] += 1
        self._external(tag, attrs)
        if not void:
            classes = (attrs.get("class") or "").split()
            inherited = bool(self._stack) and self._stack[-1][1]
            self._stack.append((tag, inherited or AUTHORED_CLASS in classes))

    def _img(self, attrs: dict[str, str | None]) -> None:
        alt = attrs.get("alt")
        if alt is None:
            self.imgs_without_alt += 1
        elif self._heading is not None:
            self._heading[1].append(alt)

    def _external(self, tag: str, attrs: dict[str, str | None]) -> None:
        if tag in EXTERNAL_ELEMENTS:
            self.external += 1
            return
        for name in URL_ATTRIBUTES & attrs.keys():
            value = (attrs[name] or "").strip().lower()
            if value and not value.startswith(("data:", "#")):
                self.external += 1
        if _CSS_EXTERNAL.search(attrs.get("style") or ""):
            self.external += 1


def _check(name: str, passed: bool, code: str, severity: Severity = "error") -> Check:
    return Check(name=name, passed=passed, severity=severity, code=None if passed else code)


def _skips_a_level(levels: list[int]) -> bool:
    """True if any heading is more than one level below the heading before it."""
    return any(b > a + 1 for a, b in pairwise(levels))


def static_checks(html: str) -> list[Check]:
    """One Check per rule (see the module docstring). Content-free: codes only."""
    doc = _Collector()
    doc.feed(html)
    doc.close()
    authored = " ".join(doc.authored_text)
    return [
        _check("html.lang", bool((doc.lang or "").strip()), "missing_lang"),
        _check("html.title", bool("".join(doc.title).strip()), "missing_title"),
        _check("html.main", doc.mains == 1, "main_count"),
        _check(
            "html.heading_order",
            not _skips_a_level([level for level, _ in doc.headings]),
            "heading_skip",
        ),
        _check("html.heading_text", all(text.strip() for _, text in doc.headings), "empty_heading"),
        _check("html.img_alt", doc.imgs_without_alt == 0, "img_missing_alt"),
        _check("html.unique_ids", all(n == 1 for n in doc.ids.values()), "duplicate_id"),
        _check("html.list_structure", doc.list_errors == 0, "list_structure"),
        _check(
            "html.math_display",
            doc.maths_without_display == 0,
            "math_missing_display",
            severity="warning",
        ),
        _check("html.self_contained", doc.external == 0, "external_resource"),
        _check("html.wording", not _BANNED.search(authored), "banned_wording"),
        *_table_rules(html),
    ]


# --------------------------------------------------------------------------- tables

# "Table 2.1 Speeds of ...", "TABLE 3: ...", but not "Table 2 shows that ..." (layout's
# "Figure N" caption rule, for tables).
TABLE_CAPTION = re.compile(
    r"^(?:Table|TABLE|Tab\.)\s*\d+[A-Za-z]?(?:[.\-\u2013]\d+[A-Za-z]?)*"
    r"(?:\s*[.:|\u2013\u2014-]\s*|\s+(?=[A-Z(\"'\u201c])|\s*$)"
)
PRESENTATIONAL_ROLES = frozenset({"presentation", "none"})
# Blocks that can sit between a paragraph and a table (wrapper <div>s do not count: a
# rebuilt table sits in one).
_TABLE_NEIGHBOUR_BLOCKS = frozenset(
    {"h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "dl", "figure", "pre", "blockquote",
     "hr", "img", "math", "aside", "section"}
)  # fmt: skip


@dataclass
class _HeaderCell:
    scope: bool
    has_id: bool
    text: list[str] = field(default_factory=list)


@dataclass
class _TableFacts:
    presentational: bool
    caption: bool = False
    uses_headers: bool = False  # a cell lists its headers (`headers`/`id` instead of scope)
    headers: list[_HeaderCell] = field(default_factory=list)


class _Tables(HTMLParser):
    """One pass gathering what the table rules need: each table's header cells and
    caption, and the order of paragraphs, tables and other blocks outside tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[_TableFacts] = []
        # ("p", text), ("table", index into `tables`) or ("block", ""), in document order.
        self.blocks: list[tuple[str, str | int]] = []
        self._open: list[int] = []  # tables being read (nested ones too)
        self._header: _HeaderCell | None = None
        self._paragraph: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "table":
            role = (values.get("role") or "").strip().lower()
            self.tables.append(_TableFacts(presentational=role in PRESENTATIONAL_ROLES))
            if not self._open:
                self._end_paragraph()
                self.blocks.append(("table", len(self.tables) - 1))
            self._open.append(len(self.tables) - 1)
        elif self._open:
            self._in_table(tag, values)
        elif tag == "p":
            self._end_paragraph()
            self._paragraph = []
        elif tag in _TABLE_NEIGHBOUR_BLOCKS and self._paragraph is None:
            self.blocks.append(("block", ""))

    def _in_table(self, tag: str, values: dict[str, str | None]) -> None:
        table = self.tables[self._open[-1]]
        if tag == "caption":
            table.caption = True
        if tag in ("td", "th") and (values.get("headers") or "").strip():
            table.uses_headers = True
        if tag == "th":
            self._header = _HeaderCell(
                scope=bool((values.get("scope") or "").strip()),
                has_id=bool((values.get("id") or "").strip()),
            )
            table.headers.append(self._header)
        elif tag == "img" and self._header is not None:
            self._header.text.append(values.get("alt") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._open:
            self._open.pop()
            self._header = None
        elif tag == "th":
            self._header = None
        elif tag == "p":
            self._end_paragraph()

    def handle_data(self, data: str) -> None:
        if self._header is not None:
            self._header.text.append(data)
        if self._paragraph is not None and not self._open:
            self._paragraph.append(data)

    def _end_paragraph(self) -> None:
        if self._paragraph is not None:
            self.blocks.append(("p", " ".join("".join(self._paragraph).split())))
            self._paragraph = None

    def caption_left_in_text(self, index: int) -> bool:
        """Is a "Table N ..." paragraph right before or after table `index`?"""
        at = self.blocks.index(("table", index))
        beside = [self.blocks[i] for i in (at - 1, at + 1) if 0 <= i < len(self.blocks)]
        return any(
            kind == "p" and isinstance(text, str) and bool(TABLE_CAPTION.match(text))
            for kind, text in beside
        )


def _table_rules(html: str) -> list[Check]:
    """The table rules (see the module docstring); none for a document without tables."""
    doc = _Tables()
    doc.feed(html)
    doc.close()
    if not doc.tables:
        return []
    data = [(i, t) for i, t in enumerate(doc.tables) if not t.presentational]
    top_level = {index for kind, index in doc.blocks if kind == "table"}
    return [
        _check("html.table_headers", all(t.headers for _, t in data), "table_no_headers"),
        _check(
            "html.table_header_scope",
            all(h.scope or (t.uses_headers and h.has_id) for _, t in data for h in t.headers),
            "th_without_scope",
        ),
        _check(
            "html.table_header_text",
            all("".join(h.text).strip() for t in doc.tables for h in t.headers),
            "empty_th",
        ),
        _check(
            "html.table_caption",
            not any(
                not t.caption and i in top_level and doc.caption_left_in_text(i) for i, t in data
            ),
            "table_caption_missing",
            severity="warning",
        ),
        _check("html.table_layout", not any(t.presentational for t in doc.tables), "layout_table"),
    ]


# --------------------------------------------------------------------------- axe-core


@cache
def axe_source() -> str:
    """The vendored axe.min.js (see vendor/README.md for version and checksum)."""
    path = resources.files("a11y_stem.pipeline").joinpath("vendor", "axe.min.js")
    return path.read_text(encoding="utf-8")


def axe_source_sha256() -> str:
    return hashlib.sha256(axe_source().encode("utf-8")).hexdigest()


def violations_to_checks(violations: list[dict[str, Any]]) -> list[Check]:
    """axe violations -> Checks. serious/critical are errors, the rest warnings."""
    checks = []
    for violation in violations:
        rule = str(violation.get("id", ""))
        if not _AXE_RULE_ID.fullmatch(rule):
            rule = "unknown"
        severity: Severity = "error" if violation.get("impact") in AXE_ERROR_IMPACTS else "warning"
        checks.append(Check(name=f"axe.{rule}", passed=False, severity=severity, code=rule))
    return checks or [Check(name="axe", passed=True)]


def axe_checks(html: str) -> list[Check]:
    """Run axe-core (WCAG 2.0 A/AA and 2.1 AA rules) on `html` in headless Chromium.

    The page runs offline: the document cannot load anything even if it tried. Returns
    `AXE_SKIPPED` when Playwright or its Chromium is not installed, and a single
    `axe_error` warning if axe itself fails, so a missing browser never fails a
    conversion.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [AXE_SKIPPED]
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except PlaywrightError:  # browser not installed (or cannot start here)
                return [AXE_SKIPPED]
            try:
                context = browser.new_context(offline=True)
                page = context.new_page()
                page.set_content(html, wait_until="load")
                page.add_script_tag(content=axe_source())
                violations = page.evaluate(_AXE_RUN, list(AXE_TAGS))
            finally:
                browser.close()
    except Exception as exc:  # a broken browser setup must never fail a conversion
        log.warning("axe_checks failed: %s", type(exc).__name__)
        return [Check(name="axe", passed=False, severity="warning", code="axe_error")]
    return violations_to_checks(violations)
