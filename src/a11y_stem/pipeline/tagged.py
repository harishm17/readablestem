"""What a tagged PDF says about its own structure: heading levels and figure alt text.

A PDF exported from Word or PowerPoint with "tag" turned on carries a structure tree: each
heading is an `H1` to `H6` element, each picture a `Figure` element with the author's
`Alt` text and a `BBox`. `source_facts` counts them for the before/after card; this module
uses them as proposals (T2-18, docs/layout.md "Tagged PDFs"):

- a tagged heading is evidence for the line it covers (`REASON_TAGGED`, "tagged heading
  h2"): the line is a heading even in body-size regular type, and its level is the tag's.
  The tag is the author's own statement, so a heading with it is not an uncertain one
  (`heading_step`). Only a line whose text is the tag's text is touched; a tag over text
  that layout split or joined differently is ignored.
- a picture's alt text goes onto the figure element (`Element.source_alt`) when it says
  something (`validate.check_alt` finds no error; Word's "A picture containing text,
  Description automatically generated" and "Picture 3" are placeholders that do not).
  The figure step shows it to the model as context and offers it to a reviewer when the
  model gives no usable description. It never goes into the output on its own.

Reading never fails a conversion: a PDF whose tags cannot be read gives none. Headings come
from MuPDF's structured text (the text of each tagged heading, with the roles mapped to the
standard types); figures from the structure elements themselves.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import pymupdf

from a11y_stem.core.types import BBox, Element, Kind
from a11y_stem.pipeline import layout, validate
from a11y_stem.pipeline.layout import REASON_TAGGED

log = logging.getLogger(__name__)

_HEADING_TYPE = re.compile(r"H([1-6])")
MAX_HEADING_CHARS = 200  # a tag over a longer paragraph is a tagging slip, not a heading
MIN_OVERLAP = 0.3  # share of the smaller box a figure and its tag must share
# The tags of a document of this many structure elements are not walked (a hostile file).
_MAX_OBJECTS = 250_000
# Word and PowerPoint fill an empty alt text with a sentence like this one.
_AUTO_ALT = re.compile(r"description automatically generated|alt text for this image", re.I)


@dataclass(frozen=True)
class TaggedHeading:
    page: int
    level: int
    text: str
    bbox: BBox


@dataclass(frozen=True)
class TaggedFigure:
    page: int
    alt: str
    bbox: BBox | None  # in the displayed frame, None when the tag has none


@dataclass
class TaggedPdf:
    headings: list[TaggedHeading] = field(default_factory=list)
    figures: list[TaggedFigure] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.headings or self.figures)


def _key(text: str) -> str:
    return "".join(c for c in text.casefold() if c.isalnum())


# --------------------------------------------------------------------------- reading


def _struct_headings(page: pymupdf.Page, number: int) -> list[TaggedHeading]:
    out: list[TaggedHeading] = []

    def text_of(blocks: Iterable[dict]) -> str:
        parts = []
        for block in blocks:
            if block.get("type") == 0:
                parts.extend(
                    span.get("text", "")
                    for line in block.get("lines", ())
                    for span in line.get("spans", ())
                )
            elif block.get("type") == 2:
                parts.append(text_of(block.get("blocks", ())))
        return " ".join("".join(parts).split())

    def walk(blocks: Iterable[dict], depth: int) -> None:
        for block in blocks:
            if block.get("type") != 2 or depth > 64:
                continue
            match = _HEADING_TYPE.fullmatch(str(block.get("std", "")))
            if match:
                text = text_of(block.get("blocks", ()))
                if text:
                    out.append(
                        TaggedHeading(number, int(match.group(1)), text, tuple(block["bbox"]))
                    )
            else:
                walk(block.get("blocks", ()), depth + 1)

    flags = pymupdf.TEXT_COLLECT_STRUCTURE | pymupdf.TEXT_PRESERVE_WHITESPACE
    walk(page.get_text("dict", flags=flags).get("blocks", ()), 0)
    return out


def _key_of(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        kind, value = doc.xref_get_key(xref, key)
    except Exception:
        return "null", ""
    return str(kind), str(value)


def _figure_roles(doc: pymupdf.Document) -> set[str]:
    roles = {"Figure"}
    kind, value = _key_of(doc, doc.pdf_catalog(), "StructTreeRoot/RoleMap")
    pattern = re.compile(r"/([A-Za-z0-9_.#-]{1,64})\s*/Figure\b")
    if kind == "dict":
        roles.update(pattern.findall(value))
    elif kind == "xref":
        with contextlib.suppress(Exception):
            xref = int(value.split()[0])
            roles.update(pattern.findall(doc.xref_object(xref, compressed=True)))
    return roles


def _struct_figures(doc: pymupdf.Document, pages: set[int]) -> list[TaggedFigure]:
    count = doc.xref_length()
    if count > _MAX_OBJECTS:
        return []
    page_of = {doc.page_xref(n): n for n in pages if 0 <= n < doc.page_count}
    roles = _figure_roles(doc)
    found: list[TaggedFigure] = []
    for xref in range(1, count):
        kind, value = _key_of(doc, xref, "S")
        if kind != "name" or value.lstrip("/") not in roles:
            continue
        alt_kind, alt = _key_of(doc, xref, "Alt")
        if alt_kind != "string" or not alt.strip():
            continue
        page_kind, page_ref = _key_of(doc, xref, "Pg")
        if page_kind != "xref":
            continue
        number = page_of.get(int(page_ref.split()[0]))
        if number is None:
            continue
        box = None
        box_kind, raw = _key_of(doc, xref, "A/BBox")
        if box_kind == "array":
            with contextlib.suppress(ValueError):
                x0, y0, x1, y1 = (float(v) for v in raw.strip("[] ").split())
                page = doc[number]
                if not page.rotation % 360:  # the tag's frame is the page's unrotated one
                    rect = pymupdf.Rect(x0, y0, x1, y1) * page.transformation_matrix
                    box = (rect.x0, rect.y0, rect.x1, rect.y1)
        found.append(TaggedFigure(number, alt, box))
    return found


def read(pdf_bytes: bytes, pages: Iterable[int]) -> TaggedPdf:
    """The tagged headings and figure alt texts of the analysed `pages` (0-based); empty
    for an untagged PDF. Never raises. Holds layout's PyMuPDF lock."""
    wanted = set(pages)
    lock = getattr(layout, "_LOCK", None)
    with lock if lock is not None else contextlib.nullcontext():
        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)
        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            log.warning("tagged: the PDF could not be opened: %s", type(exc).__name__)
            return TaggedPdf()
        try:
            kind, value = _key_of(doc, doc.pdf_catalog(), "MarkInfo/Marked")
            kind_root, _ = _key_of(doc, doc.pdf_catalog(), "StructTreeRoot")
            if not (kind == "bool" and value.strip() == "true") or kind_root == "null":
                return TaggedPdf()
            tagged = TaggedPdf()
            for number in sorted(wanted):
                if 0 <= number < doc.page_count:
                    try:
                        tagged.headings.extend(_struct_headings(doc[number], number))
                    except Exception as exc:  # one page's tags unreadable: the others count
                        log.warning("tagged: a page could not be read: %s", type(exc).__name__)
            try:
                tagged.figures = _struct_figures(doc, wanted)
            except Exception as exc:
                log.warning("tagged: figures could not be read: %s", type(exc).__name__)
            return tagged
        except Exception as exc:
            log.warning("tagged: the PDF could not be read: %s", type(exc).__name__)
            return TaggedPdf()
        finally:
            doc.close()
            pymupdf.TOOLS.reset_mupdf_warnings()


# -------------------------------------------------------------------------- applying


def _overlap(a: BBox, b: BBox) -> float:
    """Share of the smaller box that both boxes cover."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (w * h) / smaller if smaller > 0 else 0.0


def usable_alt(alt: str) -> str | None:
    """The tag's alt text cleaned, or None when it says nothing (a placeholder, a file
    name, a sentence the program wrote for the author) or fails an error check."""
    text = validate.clean_alt(alt)
    if not text or _AUTO_ALT.search(text):
        return None
    if validate.has_errors(validate.check_alt(text, decorative=False)):
        return None
    return text


def apply(elements: list[Element], tagged: TaggedPdf) -> list[Element]:
    """The elements with the PDF's tags put on them (see the module docstring)."""
    if not tagged:
        return elements
    updates: dict[str, dict] = {}
    for heading in tagged.headings:
        wanted = _key(heading.text)
        match = next(
            (
                e
                for e in elements
                if e.page == heading.page
                and e.id not in updates
                and e.kind in (Kind.HEADING, Kind.PARAGRAPH)
                and e.text
                and len(e.text) <= MAX_HEADING_CHARS
                and _key(e.text) == wanted
            ),
            None,
        )
        if match is None:
            continue
        reason = f"{REASON_TAGGED}{heading.level}"
        update: dict = {"reasons": [*match.reasons, reason]}
        if match.kind == Kind.PARAGRAPH:
            update["kind"] = Kind.HEADING
        updates[match.id] = update
    figures = [e for e in elements if e.kind == Kind.FIGURE]
    for tag in tagged.figures:
        text = usable_alt(tag.alt)
        if text is None:
            continue
        on_page = [e for e in figures if e.page == tag.page and e.id not in updates]
        if tag.bbox is not None:
            best = max(on_page, key=lambda e: _overlap(e.bbox, tag.bbox or e.bbox), default=None)
            if best is not None and _overlap(best.bbox, tag.bbox) >= MIN_OVERLAP:
                updates[best.id] = {"source_alt": text}
        elif len(on_page) == 1 and sum(1 for t in tagged.figures if t.page == tag.page) == 1:
            updates[on_page[0].id] = {"source_alt": text}
    return [e.model_copy(update=updates[e.id]) if e.id in updates else e for e in elements]
