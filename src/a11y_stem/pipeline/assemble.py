"""Elements + fixes -> one self-contained accessible HTML file.

What the output looks like and why is in docs/output.md. In short:

- One HTML5 document: `<html lang>`, `<meta charset>`, a viewport, `<title>`, a small
  inline stylesheet and one `<main>`. No scripts and no external resources, so the file
  works offline, inside an LMS page, and saved to disk.
- Content in reading order (`Element.order`): headings from heading fixes, paragraphs as
  `<p>` with inline equations placed at their markers, consecutive list items as nested
  `<ul>`/`<ol>`, block equations as `<math display="block">` (with their equation number
  after them), figures as `<figure><img alt>` + `<figcaption>`.
- Anything not converted (ABSTAINED, FAILED, REJECTED, or MathML the sanitiser refused)
  keeps the original crop as an image with a fixed alt text and a visible note, marked
  with a border *and* a text label, never by colour alone.
- A short note for students after `<main>` ("About this version", a `<footer>`): what
  the file is, that some parts have not been checked by the instructor yet or the date
  the instructor made the last decision (`checked_on`), and to tell the instructor about
  anything hard to read. No counts: those are for the instructor, on the job page. An
  optional `notice` at the top (e.g. "offline test run": placeholders, not conversions).

Security (rule 2 of docs/design/beta-architecture.md): all document and model text is
escaped with `html.escape`. MathML is never pasted as a string: `sanitize_mathml` parses
it with defusedxml and re-serialises an allowlisted tree of presentation elements and
attributes (both live in `safe_html`, shared with `table_html`). Text this module writes
itself carries the `AUTHORED_CLASS` class so the wording check in `html_checks` can tell
it apart from the instructor's text.

Extension points (docs/design/content-features.md): TABLE elements are written by
`assemble_tables.render_table_element`, and inline text (with its markers, and later its
links, colours and language) by `inline_text.render_inline_text`, so features that change
those land in their own modules. CSS a feature needs is added only to documents that use
it (`_Renderer.add_style`), so documents without those features are written exactly as
before.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from typing import Literal
from xml.etree.ElementTree import Element as XmlElement
from xml.etree.ElementTree import ParseError

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

from a11y_stem import __version__
from a11y_stem.core.types import INLINE_MARKER_RE as INLINE_MARKER_RE
from a11y_stem.core.types import KEPT_ORIGINAL_STATUSES, Element, Fix, Kind, Status
from a11y_stem.pipeline import assemble_tables, inline_text
from a11y_stem.pipeline.safe_html import MATHML_ATTRIBUTES as MATHML_ATTRIBUTES
from a11y_stem.pipeline.safe_html import MATHML_ELEMENTS as MATHML_ELEMENTS
from a11y_stem.pipeline.safe_html import MATHML_NS as MATHML_NS
from a11y_stem.pipeline.safe_html import MATHML_UNWRAP as MATHML_UNWRAP
from a11y_stem.pipeline.safe_html import MAX_MATHML_DEPTH as MAX_MATHML_DEPTH
from a11y_stem.pipeline.safe_html import MAX_MATHML_NODES as MAX_MATHML_NODES
from a11y_stem.pipeline.safe_html import Display, sanitize_mathml
from a11y_stem.pipeline.safe_html import escape_text as _escape
from a11y_stem.pipeline.safe_html import split_tag as _split_tag

# Class on every element whose text this module wrote (notes, labels, the about section).
AUTHORED_CLASS = "a11y-stem"

DEFAULT_TITLE = "Converted document"
EQUATION_NOT_CONVERTED_ALT = "Equation not converted"
FIGURE_NOT_DESCRIBED_ALT = "Figure without a description"
# The student note (`_about_section`). It speaks to students: what this file is and who
# to tell about problems. TODO(Harish): confirm this wording, and whether to name the
# screen readers that read MathML aloud (NVDA with MathCAT, JAWS, VoiceOver).
ABOUT_TITLE = "About this version"
ABOUT_WORDING = (
    "This is a web version of a PDF from your course, made with a11y-stem, a tool that "
    "assists with accessibility."
)
MATHS_NOTE = "Its equations are written so that screen readers can read them aloud."
NOT_CHECKED_YET = (
    "Some parts were made by the tool and have not been checked by your instructor yet."
)
CHECKED = "Your instructor checked the parts the tool was unsure about"
FLAGGED_NOTE = (
    "Some parts could not be converted. They are shown as pictures of the original, "
    "marked &ldquo;Not converted&rdquo; or &ldquo;Needs a description&rdquo;: ask your "
    "instructor for a version you can read."
)
CONTACT = "If something is hard to read or looks wrong, tell your instructor."
EQUATION_NOTE = (
    "This equation is shown as an image of the original, so screen readers cannot read "
    "it. Review it before publishing."
)
FIGURE_NOTE = "This figure has no text description yet. Review it before publishing."

# Fix statuses whose proposal is not used: the original stays, flagged.
NOT_CONVERTED = KEPT_ORIGINAL_STATUSES

ABOUT_ID = "about-conversion"
ABOUT_TITLE_ID = "about-conversion-title"


# BCP 47-ish: "en", "en-US", "zh-Hant-TW". Anything else falls back to "en".
_LANG = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")
_REASON = re.compile(r"[a-z0-9_]{1,64}")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# An ordered list marker as layout reports it: "3.", "b)", "(iv)".
_ORDERED_MARKER = re.compile(
    r"(?:\((?P<inner>[0-9A-Za-z]{1,6})\)|(?P<plain>[0-9A-Za-z]{1,6})(?P<sep>[.)]))"
)
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
_PT_TO_CSS_PX = 96 / 72  # a PDF point is 1/72 in; a CSS pixel is 1/96 in


# --------------------------------------------------------------------------- width

# Rough advance widths in em, for deciding which display equations may be wider than a
# phone screen (see `WIDE_EQUATION_EM`). Erring wide is cheap: one more tab stop.
_RELATIONS = frozenset(
    "=<>\u2264\u2265\u2260\u2248\u2261\u2245\u223c\u221d"
    "\u2192\u2190\u2194\u21d2\u21d0\u21d4\u2208\u2209\u2282\u2286\u2283\u2287"
)
_BINARY = frozenset("+-\u2212\u00d7\u00b7\u00f7\u00b1\u2213\u2218\u2217\u222a\u2229\u2227\u2228")
_SCRIPT_SCALE = 0.71  # scriptlevel 1 is about 71% of the base size


def _token_em(name: str, text: str) -> float:
    chars = len(text.strip())
    if name == "mo":
        stripped = text.strip()
        if stripped in _RELATIONS:
            return 0.6 + 0.56  # a thick space either side
        if stripped in _BINARY:
            return 0.6 + 0.44  # a medium space either side
        return 0.45 * chars + (0.17 if stripped in {",", ";"} else 0.0)
    if name == "ms":
        chars += 2  # the quotes
    return 0.6 * chars + 0.25 * text.strip().count(" ")


def _width_em(node: XmlElement) -> float:
    _, name = _split_tag(node.tag) if isinstance(node.tag, str) else ("", "")
    children = list(node)
    if name in {"mi", "mn", "mo", "mtext", "ms"}:
        return _token_em(name, "".join(node.itertext()))
    if name == "mspace":
        width = node.attrib.get("width", "")
        return float(width[:-2]) if re.fullmatch(r"\d+(\.\d+)?em", width) else 0.0
    widths = [_width_em(child) for child in children]
    if not widths:
        return 0.0
    if name == "mfrac":
        return max(widths) + 0.2
    if name in {"msub", "msup"}:
        return widths[0] + _SCRIPT_SCALE * sum(widths[1:])
    if name == "msubsup":
        return widths[0] + _SCRIPT_SCALE * max(widths[1:], default=0.0)
    if name in {"munder", "mover", "munderover"}:
        return max([widths[0], *(_SCRIPT_SCALE * w for w in widths[1:])])
    if name == "msqrt":
        return sum(widths) + 1.0
    if name == "mroot":
        return widths[0] + _SCRIPT_SCALE * sum(widths[1:]) + 1.0
    if name == "menclose":
        return sum(widths) + 0.5
    if name == "mmultiscripts":
        return widths[0] + _SCRIPT_SCALE * sum(widths[1:]) / 2
    if name == "mtable":
        columns: list[float] = []  # widest cell per column
        for row in children:
            for i, cell in enumerate(row):
                if i == len(columns):
                    columns.append(0.0)
                columns[i] = max(columns[i], _width_em(cell))
        return sum(columns) + 0.8 * max(0, len(columns) - 1)
    return sum(widths)  # rows, styles and anything else laid out in a line


def estimate_width_em(mathml: str) -> float:
    """Approximate rendered width of (sanitised) MathML in em of its own font size."""
    try:
        root = SafeET.fromstring(mathml, forbid_dtd=True)
    except (ParseError, DefusedXmlException, ValueError):
        return 0.0
    try:
        return _width_em(root)
    except RecursionError:  # the sanitiser caps depth at 64; this is belt and braces
        return float("inf")


# --------------------------------------------------------------------------- helpers


def _clean_lang(lang: str) -> str:
    lang = (lang or "").strip()
    return lang if _LANG.fullmatch(lang) else "en"


def _clean_title(title: str | None) -> str:
    text = INLINE_MARKER_RE.sub("[equation]", title or "")
    text = " ".join(text.split())
    return text or DEFAULT_TITLE


def _is_png(data: bytes | None) -> bool:
    return data is not None and data.startswith(_PNG_SIGNATURE)


def _png_data_uri(png: bytes | None) -> str | None:
    if png is None or not _is_png(png):
        return None
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _size_attrs(element: Element) -> str:
    """Display the crop at its printed size (a crop is rendered at ~200 dpi)."""
    x0, y0, x1, y1 = element.bbox
    width, height = round((x1 - x0) * _PT_TO_CSS_PX), round((y1 - y0) * _PT_TO_CSS_PX)
    if 0 < width <= 4000 and 0 < height <= 4000:
        return f' width="{width}" height="{height}"'
    return ""


def _equation_label(label: str | None) -> str:
    """The equation's number ("(2.4)", "2.4"), written after its maths. Screen readers
    hear "Equation 2.4" (the word is visually hidden); sighted readers see the number
    as printed, at the right of the equation."""
    if not label or not label.strip():
        return ""
    return (
        '<span class="equation-label">'
        f'<span class="visually-hidden {AUTHORED_CLASS}">Equation </span>'
        f"{_escape(' '.join(label.split()))}</span>"
    )


# A display equation estimated at least this wide (em of its own font) may not fit a
# 320 CSS px screen: the column is then 288 px and the maths 19.8 px, so 14.5 em fit.
# The estimate came within -18% / +44% of Chromium's widths for the gold equations, and
# maths fonts differ between systems, hence the margin.
WIDE_EQUATION_EM = 11.0


def _scroller_attrs(mathml: str, label: str | None) -> str:
    """Attributes that make a wide equation's box a named tab stop ("" if not wide).

    `.equation` (display) and `.equation-scroll` (inline) scroll sideways when the
    maths is wider than the screen (WCAG 1.4.10: the equation scrolls, not the page).
    Keyboard users can only scroll a box that can take focus (axe
    `scrollable-region-focusable`), but a tab stop on every equation would make a long
    document tedious to tab through, so only equations that may be too wide for a
    phone get one, named "Equation" (plus its number) for the focus.
    """
    if estimate_width_em(mathml) < WIDE_EQUATION_EM:
        return ""
    name = f"Equation {' '.join(label.split())}" if label and label.strip() else "Equation"
    return f' tabindex="0" role="group" aria-label="{_escape(name)}"'


def _fix_attrs(fix: Fix | None, reason: str | None) -> str:
    """Content-free status/reason codes, for tooling that reads the output."""
    attrs = f' data-status="{fix.status.value}"' if fix is not None else ""
    if reason and _REASON.fullmatch(reason):
        attrs += f' data-reason="{reason}"'
    return attrs


# --------------------------------------------------------------------------- lists


def _roman(text: str) -> int | None:
    """Value of a canonical roman numeral below 400 ("iv" -> 4), else None. (Layout has the
    same reading; it is repeated here so assembly does not import the PDF code.)"""
    lower = text.lower()
    if not lower or any(c not in _ROMAN for c in lower) or not (text.islower() or text.isupper()):
        return None
    values = [_ROMAN[c] for c in lower]
    total = sum(
        -v if i + 1 < len(values) and values[i + 1] > v else v for i, v in enumerate(values)
    )
    canonical, rest = "", total
    for number, letters in ((100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"),
                            (5, "v"), (4, "iv"), (1, "i")):  # fmt: skip
        while rest >= number:
            canonical, rest = canonical + letters, rest - number
    return total if 0 < total < 400 and canonical == lower else None


@dataclass(frozen=True)
class _ListStyle:
    """How one run of sibling list items is written."""

    tag: Literal["ul", "ol"]
    family: tuple[str, ...]  # siblings of one family share a list
    type: str | None = None  # <ol type>: "a", "A", "i" or "I" ("1" is the default)
    start: int | None = None  # <ol start> when the first value is not 1
    printed: bool = False  # irregular markers: list-style none, the marker printed


def _marker_parts(marker: str) -> tuple[str, str] | None:
    """("{}." | "{})" | "({})", body) of an ordered marker, or None."""
    match = _ORDERED_MARKER.fullmatch(marker.strip())
    if not match:
        return None
    if match["inner"] is not None:
        return "({})", match["inner"]
    return "{}" + match["sep"], match["plain"]


def _family(item: Element) -> tuple[str, ...]:
    """Items that can share one list: bullets with bullets; ordered items with the same
    kind of marker (numbers or letters) printed the same way ("a." and "b." but not "(c)")."""
    parts = _marker_parts(item.list_marker or "") if item.list_ordered else None
    if parts is None:
        return ("bullet",) if not item.list_ordered else ("other",)
    form, body = parts
    return ("decimal" if body.isdigit() else "letter", form)


def _sequence(bodies: list[str]) -> tuple[str | None, int] | None:
    """(<ol type>, first value) when the bodies count up by one, else None.

    Single letters that count up are letters ("a", "b", "c"; "h", "i", "j"; a lone "c"),
    otherwise roman numerals that count up ("i", "ii", "iii"; "iv", "v").
    """
    if all(b.isdigit() for b in bodies):
        values = [int(b) for b in bodies]
        return (None, values[0]) if _counts_up(values) else None
    if not (all(b.islower() for b in bodies) or all(b.isupper() for b in bodies)):
        return None
    lower = bodies[0].islower()
    if all(len(b) == 1 and b.isascii() and b.isalpha() for b in bodies):
        letters = [ord(b.lower()) - ord("a") + 1 for b in bodies]
        if _counts_up(letters):
            return ("a" if lower else "A"), letters[0]
    romans = [_roman(b) for b in bodies]
    numbers = [r for r in romans if r is not None]
    if len(numbers) == len(bodies) and _counts_up(numbers):
        return ("i" if lower else "I"), numbers[0]
    return None


def _counts_up(values: list[int]) -> bool:
    return all(b == a + 1 for a, b in pairwise(values))


def _list_style(items: list[Element]) -> _ListStyle:
    family = _family(items[0])
    if family == ("bullet",):
        return _ListStyle("ul", family)
    parts = [_marker_parts(item.list_marker or "") for item in items]
    bodies = [p[1] for p in parts if p is not None]
    sequence = _sequence(bodies) if len(bodies) == len(items) else None
    if sequence is None or family[1] != "{}.":
        return _ListStyle("ol", family, printed=True)  # "(a)", "3)", or a broken sequence
    kind, first = sequence
    return _ListStyle("ol", family, type=kind, start=first if first != 1 else None)


def _list_levels(items: list[Element]) -> list[int]:
    """Levels relative to the first item, never more than one deeper than the item before
    (a deeper item needs a parent item to nest in)."""
    base = items[0].list_level or 1
    levels: list[int] = []
    for item in items:
        level = max(1, (item.list_level or 1) - base + 1)
        levels.append(min(level, levels[-1] + 1) if levels else 1)
    return levels


# --------------------------------------------------------------------------- headings


def heading_parts(element: Element, fix: Fix | None) -> tuple[int | None, str]:
    """(level, text) for a heading element; level None means it is written as <p>
    (no fix, rejected or not converted, or no text)."""
    if fix is None or fix.heading is None or fix.status in NOT_CONVERTED:
        return None, element.text or ""
    text = fix.heading.text if fix.heading.text.strip() else element.text or ""
    return (fix.heading.level if text.strip() else None), text


@dataclass
class Outline:
    """The outline repair: the level each heading is written at; never skips down.

    A rejected or failed heading (written as a paragraph) can open a gap: h1, [h2],
    h3. Clamping to "previous heading + 1" closes it but turns siblings into parent
    and child (h1, [h2], h3 a, h3 b -> h1, h2 a, h3 b). So headings still open are
    kept on a stack: a heading first closes those at its own level or deeper, then
    goes at most one level below the one left open. Siblings get the same level, and
    the children of a rejected heading all move up together. The first heading keeps
    its level.

    The review screen uses `would_write` to refuse a level this repair would change.
    """

    # Open headings as (level from the fix, level written), outermost first. Both
    # increase strictly from the bottom of the stack to the top.
    open: list[tuple[int, int]] = field(default_factory=list)

    def would_write(self, level: int) -> int:
        """The level a heading at `level` would be written at next (no change)."""
        parents = [entry for entry in self.open if entry[0] < level]
        return min(level, parents[-1][1] + 1) if parents else level

    def write(self, level: int) -> int:
        """Write the next heading: its level in the output."""
        written = self.would_write(level)
        while self.open and self.open[-1][0] >= level:
            self.open.pop()
        self.open.append((level, written))
        return written


def outline_before(elements: list[Element], fixes: list[Fix], element_id: str) -> Outline:
    """The outline as `render_html` has it just before the element `element_id` (reading
    order; the first fix for an id wins, as in `render_html`)."""
    first: dict[str, Fix] = {}
    for fix in fixes:
        first.setdefault(fix.element_id, fix)
    outline = Outline()
    for element in sorted(elements, key=lambda e: e.order):
        if element.id == element_id:
            break
        if element.kind is Kind.HEADING:
            level, _ = heading_parts(element, first.get(element.id))
            if level is not None:
                outline.write(level)
    return outline


# --------------------------------------------------------------------------- renderer


@dataclass
class _Renderer:
    fixes: dict[str, Fix]
    crops: dict[str, bytes]
    equations: dict[str, Element]
    used_ids: set[str] = field(default_factory=lambda: {ABOUT_ID, ABOUT_TITLE_ID})
    placed: set[str] = field(default_factory=set)  # equations rendered inside text
    flagged: set[str] = field(default_factory=set)  # ids shown as originals
    outline: Outline = field(default_factory=Outline)
    # CSS a feature added for this document (`add_style`), by name.
    styles: dict[str, str] = field(default_factory=dict)

    def add_style(self, name: str, css: str) -> None:
        """Add CSS to this document's stylesheet, once per name. Features add their CSS
        only when a document uses them, so other documents are written as before."""
        self.styles.setdefault(name, css)

    # ----- ids and text

    def claim_id(self, element_id: str) -> str | None:
        """Each element id is used once; a repeat (a layout bug) gets no id."""
        if not element_id or element_id in self.used_ids:
            return None
        if any(c.isspace() for c in element_id):
            return None
        self.used_ids.add(element_id)
        return element_id

    def id_attr(self, element_id: str) -> str:
        claimed = self.claim_id(element_id)
        return f' id="{_escape(claimed)}"' if claimed else ""

    def rich(self, text: str, element: Element | None = None) -> str:
        """Escaped text with inline-equation markers replaced by maths (or a flag).

        `element` is given when `text` is that element's stored text, so its links and
        colour runs can apply (`inline_text.render_inline_text`)."""
        if element is not None and text != element.text:
            element = None
        return inline_text.render_inline_text(
            text, element=element, equation=self.marker_html, add_style=self.add_style
        )

    def marker_html(self, element_id: str) -> str | None:
        equation = self.equations.get(element_id)
        return None if equation is None else self.inline_equation(equation)

    def heading_parts(self, element: Element) -> tuple[int | None, str]:
        return heading_parts(element, self.fixes.get(element.id))

    # ----- elements

    def render_all(self, ordered: list[Element]) -> list[str]:
        """Every element in reading order; consecutive list items become one list.

        A display equation between two list items belongs to the item before it (layout
        does not end a list there either) and is written inside that item, so the list
        and its nesting go on after it. One after the last item stays outside the list.
        """
        chunks: list[str] = []
        i = 0
        while i < len(ordered):
            if ordered[i].kind is Kind.LIST_ITEM:
                items: list[Element] = []
                inside: dict[str, list[Element]] = {}
                while i < len(ordered):
                    element = ordered[i]
                    if element.kind is Kind.LIST_ITEM:
                        items.append(element)
                    elif element.id in self.placed:
                        pass  # an item's inline equations are written inside it
                    elif element.kind is Kind.EQUATION and self.list_goes_on(ordered, i):
                        inside.setdefault(items[-1].id, []).append(element)
                    else:
                        break
                    i += 1
                chunks.append(self.lists(items, inside))
                continue
            chunk = self.render(ordered[i])
            if chunk:
                chunks.append(chunk)
            i += 1
        return chunks

    def list_goes_on(self, ordered: list[Element], start: int) -> bool:
        """Is the next element after the equations from `start` on a list item?"""
        for element in ordered[start:]:
            if element.kind is not Kind.EQUATION:
                return element.kind is Kind.LIST_ITEM
        return False

    def render(self, element: Element) -> str:
        if element.kind is Kind.HEADING:
            return self.heading(element)
        if element.kind is Kind.PARAGRAPH:
            return self.paragraph(element, element.text or "")
        if element.kind is Kind.LIST_ITEM:
            return self.lists([element])
        if element.kind is Kind.EQUATION:
            return "" if element.id in self.placed else self.block_equation(element)
        if element.kind is Kind.FIGURE:
            return self.figure(element)
        if element.kind is Kind.TABLE:
            return assemble_tables.render_table_element(element, self.fixes.get(element.id), self)
        return ""

    def paragraph(self, element: Element, text: str) -> str:
        if not text.strip():
            return ""
        attrs = self.id_attr(element.id) + inline_text.passage_attrs(element)
        return f"<p{attrs}>{self.rich(text, element)}</p>"

    def lists(self, items: list[Element], inside: dict[str, list[Element]] | None = None) -> str:
        """Consecutive list items as nested lists.

        A deeper item nests inside the item before it (`_list_levels`), after the
        display equations `inside` puts in that item. At one level, siblings of one
        family (`_family`) share a list. A regular ordered list ("a.", "b.", ... or "3.",
        "4.", ...) is an `<ol>` with `type` and `start`, and the browser prints the
        markers; any other ordered list ("(a)", "3)", a gap in the numbering) keeps its
        printed markers as text in a list with `list-style: none` and `role="list"`
        (Safari drops list semantics without a list style otherwise).
        """
        levels = _list_levels(items)
        inside = inside or {}

        def build(start: int, level: int) -> tuple[str, int]:
            out, i = [], start
            while i < len(items) and levels[i] == level:
                family = _family(items[i])
                members: list[tuple[Element, str]] = []
                while i < len(items) and levels[i] == level and _family(items[i]) == family:
                    item, i = items[i], i + 1
                    nested = "".join(self.block_equation(e) for e in inside.get(item.id, []))
                    if i < len(items) and levels[i] > level:
                        deeper, i = build(i, level + 1)
                        nested += deeper
                    members.append((item, nested))
                out.append(self.one_list([m for m, _ in members], [n for _, n in members]))
            return "".join(out), i

        html, i = "", 0
        while i < len(items):
            chunk, i = build(i, levels[i])
            html += chunk
        return html

    def one_list(self, items: list[Element], nested: list[str]) -> str:
        style = _list_style(items)
        attrs = ""
        if style.type:
            attrs += f' type="{style.type}"'
        if style.start is not None:
            attrs += f' start="{style.start}"'
        if style.printed:
            attrs += ' class="printed-markers" role="list"'
        lis = []
        for item, inner in zip(items, nested, strict=True):
            marker = (
                f'<span class="list-marker">{_escape(item.list_marker or "")}</span> '
                if style.printed
                else ""
            )
            text = self.rich(item.text or "", item)
            item_attrs = self.id_attr(item.id) + inline_text.passage_attrs(item)
            lis.append(f"<li{item_attrs}>{marker}{text}{inner}</li>")
        return f"<{style.tag}{attrs}>{''.join(lis)}</{style.tag}>"

    def heading(self, element: Element) -> str:
        level, text = self.heading_parts(element)
        if level is None:
            fix = self.fixes.get(element.id)
            if element.bold and fix is not None and fix.status is Status.REJECTED and text.strip():
                # A reviewer said this bold line is a label, not a heading ("Make them
                # bold text"): it stays bold, as in the PDF.
                attrs = self.id_attr(element.id) + inline_text.passage_attrs(element)
                return f"<p{attrs}><strong>{self.rich(text, element)}</strong></p>"
            return self.paragraph(element, text)
        level = self.outline.write(level)
        attrs = self.id_attr(element.id) + inline_text.passage_attrs(element)
        return f"<h{level}{attrs}>{self.rich(text, element)}</h{level}>"

    def converted_mathml(
        self, element: Element, display: Display, *, with_id: bool
    ) -> tuple[str | None, str]:
        """(sanitised MathML or None, reason code when None)."""
        fix = self.fixes.get(element.id)
        if fix is None:
            return None, "no_fix"
        if fix.status in NOT_CONVERTED:
            return None, fix.reason or ""  # data-status already says why
        if fix.equation is None or not fix.equation.mathml.strip():
            return None, "no_output"
        claimed = self.claim_id(element.id) if with_id else None
        mathml = sanitize_mathml(fix.equation.mathml, display=display, element_id=claimed)
        if mathml is None:
            if claimed:
                self.used_ids.discard(claimed)  # the flagged fallback carries it instead
            return None, "mathml_rejected"
        return mathml, ""

    def block_equation(self, element: Element) -> str:
        mathml, reason = self.converted_mathml(element, "block", with_id=False)
        label = _equation_label(element.equation_label)
        if mathml is not None:
            css = "equation equation-labelled" if label else "equation"
            attrs = self.id_attr(element.id) + _scroller_attrs(mathml, element.equation_label)
            return f'<div class="{css}"{attrs}>{mathml}{label}</div>'
        original = self.original(element, EQUATION_NOT_CONVERTED_ALT) + label
        return self.flag_block(element, reason, original, "Not converted.", EQUATION_NOTE)

    def inline_equation(self, element: Element) -> str:
        mathml, reason = self.converted_mathml(element, "inline", with_id=True)
        if mathml is not None:
            # A long one would widen the page: it scrolls in a box of its own instead
            # (only those, since a scroll box sits on the text's baseline differently).
            scroller = _scroller_attrs(mathml, None)
            if scroller:
                return f'<span class="equation-scroll"{scroller}>{mathml}</span>'
            return mathml
        self.flagged.add(element.id)
        attrs = self.id_attr(element.id) + _fix_attrs(self.fixes.get(element.id), reason)
        src = _png_data_uri(self.crops.get(element.id))
        if src is not None:
            image = f'<img src="{src}" alt="{EQUATION_NOT_CONVERTED_ALT}"{_size_attrs(element)}>'
            label = f'<span class="flag-label {AUTHORED_CLASS}" aria-hidden="true">'
        else:
            image = _escape(element.text or "")
            label = f'<span class="flag-label {AUTHORED_CLASS}">'
        return (
            f'<span class="flagged-inline"{attrs}>{image}{label}(equation not converted)'
            "</span></span>"
        )

    def figure(self, element: Element) -> str:
        fix = self.fixes.get(element.id)
        caption = (
            f"<figcaption>{self.rich(element.caption)}</figcaption>"
            if element.caption and element.caption.strip()
            else ""
        )
        if fix is None:
            reason = "no_fix"
        elif fix.status in NOT_CONVERTED:
            reason = fix.reason or ""
        elif fix.figure is None or not (fix.figure.decorative or fix.figure.alt.strip()):
            reason = "no_output"
        else:
            alt = "" if fix.figure.decorative else fix.figure.alt.strip()
            if _is_png(self.crops.get(element.id)):
                image = self.original(element, alt)
            else:  # no crop to show: the description itself is the content
                image = f"<p>{_escape(alt)}</p>" if alt else ""
            if not alt and not caption:  # decorative: no empty "figure" for screen readers
                return f'<div class="decorative"{self.id_attr(element.id)}>{image}</div>'
            return f"<figure{self.id_attr(element.id)}>{image}{caption}</figure>"
        figure = f"<figure>{self.original(element, FIGURE_NOT_DESCRIBED_ALT)}{caption}</figure>"
        return self.flag_block(element, reason, figure, "Needs a description.", FIGURE_NOTE)

    def original(self, element: Element, alt: str) -> str:
        """The crop as an <img>, or the extracted text when there is no image."""
        src = _png_data_uri(self.crops.get(element.id))
        if src is not None:
            return f'<img src="{src}" alt="{_escape(alt)}"{_size_attrs(element)}>'
        text = (element.text or "").strip()
        unavailable = f'<p class="{AUTHORED_CLASS}">The original image is not available.</p>'
        if text:
            return f'<p class="original-text">{_escape(text)}</p>{unavailable}'
        return unavailable

    def flag_block(self, element: Element, reason: str, body: str, label: str, note: str) -> str:
        self.flagged.add(element.id)
        fix = self.fixes.get(element.id)
        attrs = self.id_attr(element.id) + _fix_attrs(fix, reason)
        return (
            f'<div class="flagged"{attrs}>{body}'
            f'<p class="flag-note {AUTHORED_CLASS}" role="note">'
            f'<span class="flag-label">{label}</span> {note}</p></div>'
        )


# --------------------------------------------------------------------------- document


def _placed_equations(
    elements: list[Element], renderer: _Renderer, equation_ids: set[str]
) -> set[str]:
    """Equations whose marker appears in text that will be rendered (rendered inline)."""
    texts: list[str] = []
    for element in elements:
        if element.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM):
            texts.append(element.text or "")
        elif element.kind is Kind.HEADING:
            texts.append(renderer.heading_parts(element)[1])
        elif element.kind is Kind.FIGURE:
            texts.append(element.caption or "")
    found = {m.group(1) for text in texts for m in INLINE_MARKER_RE.finditer(text)}
    return found & equation_ids


def _notice(text: str | None) -> str | None:
    """A note at the top of `<main>`, outlined and labelled (not colour alone)."""
    if not text or not text.strip():
        return None
    return (
        f'<p class="notice {AUTHORED_CLASS}" role="note">'
        f'<span class="flag-label">Note:</span> {_escape(" ".join(text.split()))}</p>'
    )


def long_date(day: date) -> str:
    """The date as "9 October 2026", read the same on either side of the Atlantic."""
    return f"{day.day} {day:%B %Y}"


def _review_line(fixes: list[Fix], checked_on: date | None) -> str:
    """Whether the instructor has checked what the tool asked about. Nothing when the
    tool asked nothing and nobody changed anything; "not checked yet" while a decision
    is open; once every decision is made, "checked", with the date when it is known."""
    asked = any(f.review == "required" or f.decided_by_reviewer for f in fixes)
    if not asked:
        return ""
    if any(f.pending for f in fixes):
        return f"<p>{NOT_CHECKED_YET}</p>\n"
    when = f" on {long_date(checked_on)}" if checked_on is not None else ""
    return f"<p>{CHECKED}{when}.</p>\n"


def _about_section(
    fixes: list[Fix], flagged: int, source_note: str | None, checked_on: date | None = None
) -> str:
    """The note for students at the end of the file (MF7 of the UX review): what this
    is, whether the instructor has checked it, who to tell about problems. It is written
    for the people who read the file, so it has no counts and no review vocabulary
    ("needs review", "rejected"): the instructor's numbers are on the job page."""
    maths = any(f.kind is Kind.EQUATION and f.status not in NOT_CONVERTED for f in fixes)
    what = f"{ABOUT_WORDING} {MATHS_NOTE}" if maths else ABOUT_WORDING
    flagged_note = f"<p>{FLAGGED_NOTE}</p>\n" if flagged else ""
    source = f"<p>{_escape(source_note)}</p>\n" if source_note and source_note.strip() else ""
    return (
        f'<footer id="{ABOUT_ID}" class="about {AUTHORED_CLASS}" '
        f'aria-labelledby="{ABOUT_TITLE_ID}">\n'
        f'<h2 id="{ABOUT_TITLE_ID}">{ABOUT_TITLE}</h2>\n'
        f"<p>{what}</p>\n{_review_line(fixes, checked_on)}{flagged_note}"
        f"<p>{CONTACT}</p>\n{source}</footer>"
    )


STYLE = """\
:root { color-scheme: light; }
body { margin: 0; background: #ffffff; color: #1b1b1b;
  font: 1.125rem/1.6 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
main { max-width: 42rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; overflow-wrap: break-word; }
h1, h2, h3, h4, h5, h6 { line-height: 1.25; margin: 1.6em 0 0.5em; }
p { margin: 0 0 1em; }
img { max-width: 100%; height: auto; }
.equation { margin: 1em 0; padding: 0.25em 0; text-align: center; position: relative;
  overflow-x: auto; overflow-y: hidden; }
.equation-scroll { display: inline-block; max-width: 100%; vertical-align: middle;
  overflow-x: auto; overflow-y: hidden; }
.equation-labelled { display: flex; flex-wrap: wrap; align-items: center; gap: 0.25em 1em; }
.equation-labelled > math { flex: 1 0 auto; }
.equation-label { flex: none; margin-left: auto; }
math { font-size: 1.1em; }
math[display="block"] { margin: 0 auto; }
ul, ol { margin: 0 0 1em; padding-left: 1.75em; }
li > ul, li > ol { margin: 0.25em 0 0; }
.printed-markers { list-style: none; }
.printed-markers > li { text-indent: -1.75em; padding-left: 1.75em; }
.printed-markers > li > * { text-indent: 0; }
.list-marker { display: inline-block; min-width: 1.5em; text-indent: 0; }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden;
  clip-path: inset(50%); white-space: nowrap; }
figure { margin: 1.5em 0; }
figure img { display: block; }
figcaption { margin-top: 0.5em; }
.flagged { border: 3px dashed #8a3c00; border-radius: 4px; padding: 0.75rem; margin: 1.5em 0; }
.notice { border: 3px solid #8a3c00; border-radius: 4px; padding: 0.75rem; }
.flagged figure { margin: 0; }
.flagged > img { display: block; margin: 0 auto; }
.flagged-inline { border: 2px dashed #8a3c00; padding: 0 0.2em; }
.flagged-inline img { vertical-align: middle; }
.flag-note { margin: 0.75em 0 0; }
.flag-label { font-weight: 700; }
.original-text { font-family: ui-monospace, Menlo, Consolas, monospace; }
.about { max-width: 42rem; margin: 0 1rem; padding: 0.5em 0 3rem;
  border-top: 2px solid #1b1b1b; }
@media (min-width: 44rem) { .about { margin: 0 auto; } }
:focus-visible { outline: 3px solid #0b57d0; outline-offset: 2px; }
@media print { .flagged { break-inside: avoid; } }
"""


def render_html(
    *,
    title: str | None,
    lang: str,
    elements: list[Element],
    fixes: list[Fix],
    crops: dict[str, bytes],
    source_note: str | None = None,
    notice: str | None = None,
    checked_on: date | None = None,
) -> str:
    """One self-contained HTML5 document for the elements, in reading order.

    `fixes` are matched to elements by id (the first fix for an id wins, as in
    `DocumentResult.fix_for`; later duplicates are ignored, also in the counts). `crops` maps
    element ids to PNG bytes; they are embedded as data: URIs only where an image is
    shown (figures, tables kept as images, and equations that were not converted).
    `source_note` is plain text for the about section, e.g. an OpenStax attribution.
    `notice` is plain text shown first in `<main>` as a labelled note, for something
    every reader must see before the content (the CLI marks offline test runs, whose
    answers are placeholders). `checked_on` is the day the instructor made the last
    review decision: the student note gives it once no decision is open (the review
    screen passes it when it re-renders the file; the pipeline never does).
    """
    ordered = sorted(elements, key=lambda e: e.order)
    equations = {e.id: e for e in ordered if e.kind is Kind.EQUATION}
    first_fix: dict[str, Fix] = {}
    for fix in fixes:  # first wins, like DocumentResult.fix_for
        first_fix.setdefault(fix.element_id, fix)
    renderer = _Renderer(fixes=first_fix, crops=crops, equations=equations)
    renderer.placed = _placed_equations(ordered, renderer, set(equations))
    body = renderer.render_all(ordered)
    about = _about_section(list(first_fix.values()), len(renderer.flagged), source_note, checked_on)
    # Feature CSS (tables, ...) only in documents that use it, in a fixed order.
    style = STYLE + "".join(css for _, css in sorted(renderer.styles.items()))
    return "\n".join(
        [
            "<!doctype html>",
            f'<html lang="{_escape(_clean_lang(lang))}">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f'<meta name="generator" content="a11y-stem {_escape(__version__)}">',
            f"<title>{_escape(_clean_title(title))}</title>",
            f"<style>\n{style}</style>",
            "</head>",
            "<body>",
            "<main>",
            *([note] if (note := _notice(notice)) else []),
            *body,
            "</main>",
            about,
            "</body>",
            "</html>",
            "",
        ]
    )
