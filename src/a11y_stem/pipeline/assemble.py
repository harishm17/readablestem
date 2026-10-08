"""Elements + fixes -> one self-contained accessible HTML file.

What the output looks like and why is in docs/output.md. In short:

- One HTML5 document: `<html lang>`, `<meta charset>`, a viewport, `<title>`, a small
  inline stylesheet and one `<main>`. No scripts and no external resources, so the file
  works offline, inside an LMS page, and saved to disk.
- Content in reading order (`Element.order`): headings from heading fixes, paragraphs as
  `<p>` with inline equations placed at their markers, block equations as
  `<math display="block">`, figures as `<figure><img alt>` + `<figcaption>`.
- Anything not converted (ABSTAINED, FAILED, REJECTED, or MathML the sanitiser refused)
  keeps the original crop as an image with a fixed alt text and a visible note, marked
  with a border *and* a text label, never by colour alone.
- A closing "About this conversion" section with counts by kind and status.

Security (rule 2 of docs/design/beta-architecture.md): all document and model text is
escaped with `html.escape`. MathML is never pasted as a string: `sanitize_mathml` parses
it with defusedxml and re-serialises an allowlisted tree of presentation elements and
attributes. Text this module writes itself carries the `AUTHORED_CLASS` class so the
wording check in `html_checks` can tell it apart from the instructor's text.
"""

from __future__ import annotations

import base64
import html
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal
from xml.etree.ElementTree import Element as XmlElement
from xml.etree.ElementTree import ParseError

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

from a11y_stem import __version__
from a11y_stem.core.types import INLINE_MARKER, Element, Fix, Kind, Status

Display = Literal["block", "inline"]

MATHML_NS = "http://www.w3.org/1998/Math/MathML"

# Class on every element whose text this module wrote (notes, labels, the about section).
AUTHORED_CLASS = "a11y-stem"

DEFAULT_TITLE = "Converted document"
EQUATION_NOT_CONVERTED_ALT = "Equation not converted"
FIGURE_NOT_DESCRIBED_ALT = "Figure without a description"
ABOUT_WORDING = (
    "This file was produced with a11y-stem, which assists with accessibility; "
    "review it before publishing."
)
EQUATION_NOTE = (
    "This equation is shown as an image of the original, so screen readers cannot read "
    "it. Review it before publishing."
)
FIGURE_NOTE = "This figure has no text description yet. Review it before publishing."

# Fix statuses whose proposal is not used: the original stays, flagged.
NOT_CONVERTED = frozenset({Status.ABSTAINED, Status.FAILED, Status.REJECTED})

ABOUT_ID = "about-conversion"
ABOUT_TITLE_ID = "about-conversion-title"

KIND_LABELS = {
    Kind.HEADING: "Headings",
    Kind.EQUATION: "Equations",
    Kind.FIGURE: "Figures",
    Kind.PARAGRAPH: "Paragraphs",
}
STATUS_LABELS = {
    Status.ACCEPTED: "accepted",
    Status.NEEDS_REVIEW: "needs review",
    Status.EDITED: "edited by a reviewer",
    Status.ABSTAINED: "not converted (model unsure)",
    Status.FAILED: "not converted (error or spending limit)",
    Status.REJECTED: "rejected by a reviewer",
}

_MARKER_OPEN, _MARKER_CLOSE = INLINE_MARKER.split("{id}")
_MARKER = re.compile(
    re.escape(_MARKER_OPEN)
    + r"([^\s"
    + re.escape(_MARKER_OPEN + _MARKER_CLOSE)
    + r"]{1,64})"
    + re.escape(_MARKER_CLOSE)
)
# BCP 47-ish: "en", "en-US", "zh-Hant-TW". Anything else falls back to "en".
_LANG = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")
# C0 controls other than tab/newline/carriage return are HTML parse errors.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_REASON = re.compile(r"[a-z0-9_]{1,64}")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PT_TO_CSS_PX = 96 / 72  # a PDF point is 1/72 in; a CSS pixel is 1/96 in


# --------------------------------------------------------------------------- MathML

# Presentation MathML kept in the output: MathML Core plus `menclose` (latex2mathml uses
# it for \boxed and \cancel). Any other element makes the whole equation unusable, so it
# is shown as the original image instead of as maths with a silent hole in it.
MATHML_ELEMENTS = frozenset(
    {
        "math", "mrow", "mi", "mn", "mo", "mtext", "ms", "mspace", "msub", "msup",
        "msubsup", "munder", "mover", "munderover", "mmultiscripts", "mprescripts", "none",
        "mfrac", "msqrt", "mroot", "mstyle", "merror", "mpadded", "mphantom", "mtable",
        "mtr", "mtd", "menclose",
    }
)  # fmt: skip
# Wrappers replaced by their first child, which is what a browser shows anyway:
# `semantics` (the rest are annotations) and `maction` (the rest are alternatives).
MATHML_UNWRAP = frozenset({"semantics", "maction"})
# Layout attributes only. No href, event handlers, style, class, id or colour.
MATHML_ATTRIBUTES = frozenset(
    {
        "mathvariant", "displaystyle", "scriptlevel", "dir", "form", "fence", "separator",
        "lspace", "rspace", "stretchy", "symmetric", "maxsize", "minsize", "largeop",
        "movablelimits", "accent", "accentunder", "linethickness", "width", "height",
        "depth", "voffset", "notation", "columnalign", "rowalign", "columnspan", "rowspan",
        "columnlines", "rowlines", "columnspacing", "rowspacing", "frame", "framespacing",
        "align", "linebreak", "lquote", "rquote",
    }
)  # fmt: skip
_ATTR_VALUE = re.compile(r"[A-Za-z0-9 .%+\-]{0,64}")
MAX_MATHML_DEPTH = 64
MAX_MATHML_NODES = 5000


class _Unusable(Exception):
    """MathML that cannot be shown faithfully (unknown element, too deep, too big)."""


def _split_tag(tag: str) -> tuple[str, str]:
    """ElementTree tag "{ns}name" -> (ns, name)."""
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return "", tag


@dataclass
class _MathWriter:
    nodes: int = 0
    has_text: bool = False

    def text(self, value: str | None) -> str:
        if not value:
            return ""
        if value.strip():
            self.has_text = True
        return _escape(value)

    def element(self, node: XmlElement, depth: int) -> str:
        self.nodes += 1
        if depth > MAX_MATHML_DEPTH or self.nodes > MAX_MATHML_NODES:
            raise _Unusable("too deep or too large")
        if not isinstance(node.tag, str):  # comment or processing instruction
            return ""
        ns, name = _split_tag(node.tag)
        if ns not in ("", MATHML_NS):
            raise _Unusable("foreign namespace")
        if name in MATHML_UNWRAP:
            first = next(iter(node), None)
            return "" if first is None else self.element(first, depth + 1)
        if name not in MATHML_ELEMENTS or name == "math":
            raise _Unusable("element not allowed")
        return f"<{name}{_attributes(node)}>{self.children(node, depth)}</{name}>"

    def children(self, node: XmlElement, depth: int) -> str:
        parts = [self.text(node.text)]
        for child in node:
            parts.append(self.element(child, depth + 1))
            parts.append(self.text(child.tail))
        return "".join(parts)


def _attributes(node: XmlElement) -> str:
    kept = []
    for key, value in sorted(node.attrib.items()):
        if key in MATHML_ATTRIBUTES and _ATTR_VALUE.fullmatch(value):
            kept.append(f' {key}="{_escape(value)}"')
    return "".join(kept)


def sanitize_mathml(mathml: str, *, display: Display, element_id: str | None = None) -> str | None:
    """Re-serialise MathML from an allowlisted tree, or None if it cannot be shown.

    The input comes from a converter fed by model output, or from a reviewer's edit, so
    it is untrusted. It is parsed with defusedxml (no DTDs, entities or external
    references), and only allowlisted presentation elements and attributes are written
    back, with all text escaped. Unknown attributes are dropped; an unknown element
    rejects the whole equation (the caller then shows the original image). `display`
    is forced to where the equation is placed; `element_id` becomes the root id.
    """
    try:
        root = SafeET.fromstring(mathml, forbid_dtd=True)
    except (ParseError, DefusedXmlException, ValueError):
        return None
    ns, name = _split_tag(root.tag)
    if name != "math" or ns not in ("", MATHML_NS):
        return None
    writer = _MathWriter()
    try:
        inner = writer.children(root, depth=1)
    except (_Unusable, RecursionError):
        return None
    if not writer.has_text:  # nothing a screen reader could speak
        return None
    id_attr = f' id="{_escape(element_id)}"' if element_id else ""
    return f'<math display="{display}"{id_attr}>{inner}</math>'


# --------------------------------------------------------------------------- helpers


def _escape(text: str) -> str:
    return html.escape(_CONTROL.sub("", text), quote=True)


def _clean_lang(lang: str) -> str:
    lang = (lang or "").strip()
    return lang if _LANG.fullmatch(lang) else "en"


def _clean_title(title: str | None) -> str:
    text = _MARKER.sub("[equation]", title or "")
    text = " ".join(text.split())
    return text or DEFAULT_TITLE


def _png_data_uri(png: bytes | None) -> str | None:
    if not png or not png.startswith(_PNG_SIGNATURE):
        return None
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _size_attrs(element: Element) -> str:
    """Display the crop at its printed size (a crop is rendered at ~200 dpi)."""
    x0, y0, x1, y1 = element.bbox
    width, height = round((x1 - x0) * _PT_TO_CSS_PX), round((y1 - y0) * _PT_TO_CSS_PX)
    if 0 < width <= 4000 and 0 < height <= 4000:
        return f' width="{width}" height="{height}"'
    return ""


def _fix_attrs(fix: Fix | None, reason: str | None) -> str:
    """Content-free status/reason codes, for tooling that reads the output."""
    attrs = f' data-status="{fix.status.value}"' if fix is not None else ""
    if reason and _REASON.fullmatch(reason):
        attrs += f' data-reason="{reason}"'
    return attrs


# --------------------------------------------------------------------------- renderer


@dataclass
class _Renderer:
    fixes: dict[str, Fix]
    crops: dict[str, bytes]
    equations: dict[str, Element]
    used_ids: set[str] = field(default_factory=lambda: {ABOUT_ID, ABOUT_TITLE_ID})
    placed: set[str] = field(default_factory=set)  # equations rendered inside text
    flagged: int = 0
    last_heading: int = 0

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

    def rich(self, text: str) -> str:
        """Escaped text with inline-equation markers replaced by maths (or a flag)."""
        out, pos = [], 0
        for match in _MARKER.finditer(text):
            equation = self.equations.get(match.group(1))
            if equation is None:
                continue  # not one of ours: leave the characters as text
            out.append(_escape(text[pos : match.start()]))
            out.append(self.inline_equation(equation))
            pos = match.end()
        out.append(_escape(text[pos:]))
        return "".join(out)

    def heading_parts(self, element: Element) -> tuple[int | None, str]:
        """(level, text) for a heading element; level None means render it as <p>."""
        fix = self.fixes.get(element.id)
        if fix is None or fix.heading is None or fix.status in NOT_CONVERTED:
            return None, element.text or ""
        text = fix.heading.text if fix.heading.text.strip() else element.text or ""
        return (fix.heading.level if text.strip() else None), text

    # ----- elements

    def render(self, element: Element) -> str:
        if element.kind is Kind.HEADING:
            return self.heading(element)
        if element.kind is Kind.PARAGRAPH:
            return self.paragraph(element, element.text or "")
        if element.kind is Kind.EQUATION:
            return "" if element.id in self.placed else self.block_equation(element)
        if element.kind is Kind.FIGURE:
            return self.figure(element)
        return ""

    def paragraph(self, element: Element, text: str) -> str:
        if not text.strip():
            return ""
        return f"<p{self.id_attr(element.id)}>{self.rich(text)}</p>"

    def heading(self, element: Element) -> str:
        level, text = self.heading_parts(element)
        if level is None:
            return self.paragraph(element, text)
        # A rejected heading can open a gap in the outline (h1 -> h3); never skip down.
        if self.last_heading:
            level = min(level, self.last_heading + 1)
        self.last_heading = level
        return f"<h{level}{self.id_attr(element.id)}>{self.rich(text)}</h{level}>"

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
        if mathml is not None:
            return f'<div class="equation"{self.id_attr(element.id)}>{mathml}</div>'
        original = self.original(element, EQUATION_NOT_CONVERTED_ALT)
        return self.flag_block(element, reason, original, "Not converted.", EQUATION_NOTE)

    def inline_equation(self, element: Element) -> str:
        mathml, reason = self.converted_mathml(element, "inline", with_id=True)
        if mathml is not None:
            return mathml
        self.flagged += 1
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
            if _png_data_uri(self.crops.get(element.id)) is not None:
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
        self.flagged += 1
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
        if element.kind is Kind.PARAGRAPH:
            texts.append(element.text or "")
        elif element.kind is Kind.HEADING:
            texts.append(renderer.heading_parts(element)[1])
        elif element.kind is Kind.FIGURE:
            texts.append(element.caption or "")
    found = {m.group(1) for text in texts for m in _MARKER.finditer(text)}
    return found & equation_ids


def _about_section(fixes: list[Fix], flagged: int, source_note: str | None) -> str:
    counts = Counter((fix.kind, fix.status) for fix in fixes)
    items = []
    for kind, label in KIND_LABELS.items():
        total = sum(n for (k, _), n in counts.items() if k is kind)
        if not total:
            continue
        parts = [
            f"{counts[(kind, status)]} {STATUS_LABELS[status]}"
            for status in Status
            if counts[(kind, status)]
        ]
        items.append(f"<li>{label} ({total}): {', '.join(parts)}</li>")
    summary = (
        f"<ul>{''.join(items)}</ul>"
        if items
        else "<p>No headings, equations or figures were found.</p>"
    )
    flagged_note = (
        f"<p>{flagged} item{'s' if flagged != 1 else ''} could not be converted. "
        "They are shown as images of the original page, outlined and labelled "
        "&ldquo;Not converted&rdquo; or &ldquo;Needs a description&rdquo;.</p>"
        if flagged
        else ""
    )
    source = f"<p>{_escape(source_note)}</p>" if source_note and source_note.strip() else ""
    return (
        f'<section id="{ABOUT_ID}" class="about {AUTHORED_CLASS}" '
        f'aria-labelledby="{ABOUT_TITLE_ID}">\n'
        f'<h2 id="{ABOUT_TITLE_ID}">About this conversion</h2>\n'
        f"<p>{ABOUT_WORDING}</p>\n{summary}\n{flagged_note}{source}\n</section>"
    )


STYLE = """\
:root { color-scheme: light; }
body { margin: 0; background: #ffffff; color: #1b1b1b;
  font: 1.125rem/1.6 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
main { max-width: 42rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; overflow-wrap: break-word; }
h1, h2, h3, h4, h5, h6 { line-height: 1.25; margin: 1.6em 0 0.5em; }
p { margin: 0 0 1em; }
img { max-width: 100%; height: auto; }
.equation { margin: 1em 0; text-align: center; }
math { font-size: 1.1em; }
math[display="block"] { margin: 0 auto; }
figure { margin: 1.5em 0; }
figure img { display: block; }
figcaption { margin-top: 0.5em; }
.flagged { border: 3px dashed #8a3c00; border-radius: 4px; padding: 0.75rem; margin: 1.5em 0; }
.flagged figure { margin: 0; }
.flagged > img { display: block; margin: 0 auto; }
.flagged-inline { border: 2px dashed #8a3c00; padding: 0 0.2em; }
.flagged-inline img { vertical-align: middle; }
.flag-note { margin: 0.75em 0 0; }
.flag-label { font-weight: 700; }
.original-text { font-family: ui-monospace, Menlo, Consolas, monospace; }
.about { border-top: 2px solid #1b1b1b; margin-top: 3em; padding-top: 0.5em; }
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
) -> str:
    """One self-contained HTML5 document for the elements, in reading order.

    `fixes` are matched to elements by id (the last fix for an id wins). `crops` maps
    element ids to PNG bytes; they are embedded as data: URIs only where an image is
    shown (figures, and equations that were not converted). `source_note` is plain text
    for the about section, e.g. an OpenStax attribution.
    """
    ordered = sorted(elements, key=lambda e: e.order)
    equations = {e.id: e for e in ordered if e.kind is Kind.EQUATION}
    renderer = _Renderer(fixes={f.element_id: f for f in fixes}, crops=crops, equations=equations)
    renderer.placed = _placed_equations(ordered, renderer, set(equations))
    body = [chunk for chunk in (renderer.render(e) for e in ordered) if chunk]
    about = _about_section(fixes, renderer.flagged, source_note)
    return "\n".join(
        [
            "<!doctype html>",
            f'<html lang="{_escape(_clean_lang(lang))}">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f'<meta name="generator" content="a11y-stem {_escape(__version__)}">',
            f"<title>{_escape(_clean_title(title))}</title>",
            f"<style>\n{STYLE}</style>",
            "</head>",
            "<body>",
            "<main>",
            *body,
            about,
            "</main>",
            "</body>",
            "</html>",
            "",
        ]
    )
