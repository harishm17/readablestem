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
| html.math_display | math_missing_display | `<math>` with no block/inline display (warning) |
| html.self_contained | external_resource | scripts, links, frames, or any non-data: URL |
| html.wording | banned_wording | "compliant"/"compliance" in text the tool wrote |

The wording rule only reads text inside elements carrying `assemble.AUTHORED_CLASS` (the
notes and the about section): a mechanics text about "compliant mechanisms" is the
instructor's content, not a claim by this tool.

`axe_checks` runs the vendored axe-core (vendor/README.md) in headless Chromium when the
`browser` dependency group and its browser are installed, and returns one skipped
warning otherwise. Results carry rule ids and impact only, never page content.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
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

    # ----- helpers

    def _start(self, tag: str, attrs: dict[str, str | None], *, void: bool) -> None:
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
        _check(
            "html.math_display",
            doc.maths_without_display == 0,
            "math_missing_display",
            severity="warning",
        ),
        _check("html.self_contained", doc.external == 0, "external_resource"),
        _check("html.wording", not _BANNED.search(authored), "banned_wording"),
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
    except PlaywrightError as exc:
        log.warning("axe_checks failed: %s", type(exc).__name__)
        return [Check(name="axe", passed=False, severity="warning", code="axe_error")]
    return violations_to_checks(violations)
