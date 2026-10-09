"""Content-free facts about the input PDF, for the job page's before/after card.

`measure` reads the PDF once more after the steps have run (layout has already rendered
every analysed page in its crash probe, so reading them here is safe) and counts what a
screen-reader user gets from it: whether it is tagged, has a language and a title,
bookmarks, pictures (and, in a tagged PDF, pictures with alt text), links, and from the
elements the equations and tables layout found. Numbers and flags only
(`core.types.SourceFacts`): nothing here is document text, so the facts can be stored,
shown and counted like codes.

Two equation facts say what the text layer, the only thing a screen reader gets from an
untagged PDF, loses:

- `equations_flattened`: equations with 2-D structure (a fraction, a root, a power or
  other script, limits above and below, rows and columns, a mark over a letter). The
  text layer is one line of characters, so all of that is gone ("v 2 over" for
  v squared). Structure is read from the converted MathML when there is one
  (`structure_parts`), else from layout's own evidence (a fraction bar or radical it
  drew, script-size characters). A script the text layer does keep, as a Unicode
  superscript or subscript character ("x²"), does not count.
- `bold_maths_letters`: letters in equations drawn in a bold font but stored as plain
  characters (TeX's bold d is the letter "d" in CMBX). The text layer drops the bold, so
  a vector reads as a scalar. A PDF that stores the styled letter itself (Chromium's
  U+1D41D) keeps it and is not counted.

Every fact degrades on its own: a PDF that cannot be opened, or a part of it that cannot
be read, leaves that fact at its default (`tagged=None`, counts from the elements only).
A detection problem never fails a conversion, and nothing from the PDF is logged.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Iterable
from xml.etree.ElementTree import Element as XmlElement

import defusedxml.ElementTree as SafeET
import pymupdf

from a11y_stem.core.types import KEPT_ORIGINAL_STATUSES, Element, Fix, Kind, SourceFacts
from a11y_stem.pipeline import layout

log = logging.getLogger(__name__)

# Structure codes, in the order the job page names them.
STRUCTURE_PARTS = (
    *("fraction", "root", "power", "superscript"),
    *("subscript", "limits", "rows", "accent"),
)
# Layout's evidence codes on an equation (`Element.reasons`) that mean 2-D structure.
_LAYOUT_STRUCTURE = {
    "fraction bar": "fraction",
    "radical sign": "root",
    "superscript": "superscript",
    "letter with sub/superscript": "subscript",
}
_SCRIPT_CODES = frozenset({"power", "superscript", "subscript"})
# Unicode super- and subscript characters: a script the text layer keeps ("x²").
_UNICODE_SCRIPTS = re.compile("[²³¹⁰-ₜ]")
_PRIMES = frozenset("′″‴'")
_MAX_MATHML_CHARS = 200_000
# Bold faces as layout recognises them: font flags bit 4, or the font's name.
_BOLD_FLAG = 16
# Walking every object of a hostile file is bounded: past this many, alt text is
# reported as unknown rather than counted.
_MAX_OBJECTS = 250_000
_ROLE = re.compile(r"/([A-Za-z0-9_.#-]{1,64})\s*/Figure\b")


# --------------------------------------------------------------------------- equations


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _token_text(node: XmlElement) -> str:
    return "".join(node.itertext()).strip()


def structure_parts(mathml: str) -> list[str]:
    """What 2-D structure this MathML has, as codes in `STRUCTURE_PARTS` order (each
    once): "fraction", "root", "power" (a number raised), "superscript" (anything else
    raised, primes excepted), "subscript", "limits" (above or below an operator),
    "rows" (a table: matrices, cases, aligned rows) and "accent" (a mark over a letter:
    a vector arrow, a hat). Empty for linear maths or MathML that does not parse."""
    if not mathml or len(mathml) > _MAX_MATHML_CHARS:
        return []
    try:
        root = SafeET.fromstring(mathml)
    except Exception:  # defusedxml raises several types; unparseable means unknown
        return []
    found: set[str] = set()
    for node in root.iter():
        name = _local(node.tag)
        children = list(node)
        if name == "mfrac":
            found.add("fraction")
        elif name in ("msqrt", "mroot"):
            found.add("root")
        elif name in ("msup", "msubsup") and len(children) >= 2:
            sup = children[-1]
            text = _token_text(sup)
            if text and all(ch in _PRIMES for ch in text):
                pass  # f′: a prime reads the same on one line
            elif _local(sup.tag) == "mn":
                found.add("power")
            else:
                found.add("superscript")
            if name == "msubsup":
                found.add("subscript")
        elif name == "msub":
            found.add("subscript")
        elif name in ("munder", "munderover"):
            found.add("limits")
        elif name == "mover":
            found.add("accent")
        elif name == "mtable":
            found.add("rows")
    return [part for part in STRUCTURE_PARTS if part in found]


def _layout_parts(element: Element) -> list[str]:
    found = {_LAYOUT_STRUCTURE[r] for r in element.reasons if r in _LAYOUT_STRUCTURE}
    return [part for part in STRUCTURE_PARTS if part in found]


def flattened_parts(element: Element, fix: Fix | None) -> list[str]:
    """The structure of this equation that its text layer loses (codes as in
    `structure_parts`). From the converted MathML when the output uses it, else from
    layout's evidence. Scripts the text layer keeps as Unicode characters ("x²") are not
    lost."""
    equation = fix.equation if fix is not None else None
    used = fix is not None and fix.status not in KEPT_ORIGINAL_STATUSES
    parts = structure_parts(equation.mathml) if equation is not None and used else []
    if not parts:
        parts = _layout_parts(element)
    if _UNICODE_SCRIPTS.search(element.text or ""):
        parts = [p for p in parts if p not in _SCRIPT_CODES]
    return parts


# --------------------------------------------------------------------------- the PDF


def _key(doc, xref: int, key: str) -> tuple[str, str]:
    try:
        kind, value = doc.xref_get_key(xref, key)
    except Exception:
        return "null", ""
    return str(kind), str(value)


def _tagged(doc) -> bool:
    kind, value = _key(doc, doc.pdf_catalog(), "MarkInfo/Marked")
    return kind == "bool" and value.strip() == "true"


def _has_lang(doc) -> bool:
    kind, value = _key(doc, doc.pdf_catalog(), "Lang")
    return kind in ("string", "name") and bool(value.strip().lstrip("/"))


def _has_title(doc) -> bool:
    """A metadata title a reader can use: not empty, a file name or a tool's default
    (layout's own test, the one that decides the HTML's title)."""
    return layout._meaningful_title((doc.metadata or {}).get("title")) is not None


def _figure_roles(doc) -> set[str]:
    """Structure types that mean a figure: /Figure and any role mapped to it."""
    roles = {"Figure"}
    kind, value = _key(doc, doc.pdf_catalog(), "StructTreeRoot/RoleMap")
    if kind == "dict":
        roles.update(_ROLE.findall(value))
    elif kind == "xref":
        with contextlib.suppress(Exception):
            xref = int(value.split()[0])
            roles.update(_ROLE.findall(doc.xref_object(xref, compressed=True)))
    return roles


def _figures_with_alt(doc) -> int | None:
    """Structure elements of a figure type with alt text, or None when the file has too
    many objects to look at. Every object is looked at once (no tree walk, so a cycle in
    a hostile structure tree cannot loop)."""
    count = doc.xref_length()
    if count > _MAX_OBJECTS:
        return None
    roles = _figure_roles(doc)
    found = 0
    for xref in range(1, count):
        kind, value = _key(doc, xref, "S")
        if kind != "name" or value.lstrip("/") not in roles:
            continue
        alt_kind, alt = _key(doc, xref, "Alt")
        if alt_kind == "string" and alt.strip():
            found += 1
    return found


def _pictures(page) -> int:
    """Raster images drawn on the page at a readable size (layout's decoration limit)."""
    least = layout.MIN_IMAGE_PT
    return sum(
        1
        for info in page.get_image_info()
        if (box := info.get("bbox")) and box[2] - box[0] >= least and box[3] - box[1] >= least
    )


def _bold_plain_letters(page, element: Element) -> int:
    """Letters inside the equation's box that a bold font draws but the text layer
    stores as plain characters."""
    rect = pymupdf.Rect(element.bbox)
    if page.rotation % 360:
        rect = rect * page.derotation_matrix  # element boxes are in the displayed frame
    count = 0
    for block in page.get_text("rawdict", clip=rect)["blocks"]:
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                font = str(span.get("font", "")).split("+", 1)[-1]
                flags = int(span.get("flags", 0))
                bold = bool(flags & _BOLD_FLAG) or bool(layout._BOLD_FONT.search(font))
                if not bold:
                    continue
                count += sum(
                    1 for ch in span.get("chars", ()) if ch.get("c", "").isascii()
                    and ch.get("c", "").isalpha()
                )  # fmt: skip
    return count


def _from_pdf(
    pdf_bytes: bytes, pages: list[int], equations: list[Element], facts: SourceFacts
) -> SourceFacts:
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        log.warning("source facts: the PDF could not be opened: %s", type(exc).__name__)
        return facts
    try:
        tagged = _tagged(doc)
        update: dict[str, object] = {
            "tagged": tagged,
            "has_lang": _has_lang(doc),
            "has_title": _has_title(doc),
        }
        with contextlib.suppress(Exception):
            update["bookmarks"] = len(doc.get_toc(simple=True))
        images = links = bold = 0
        by_page: dict[int, list[Element]] = {}
        for element in equations:
            by_page.setdefault(element.page, []).append(element)
        for number in pages:
            if not 0 <= number < doc.page_count:
                continue
            try:
                page = doc[number]
                images += _pictures(page)
                links += len(page.get_links())
                bold += sum(_bold_plain_letters(page, e) for e in by_page.get(number, ()))
            except Exception as exc:  # one unreadable page leaves the others counted
                log.warning("source facts: a page could not be read: %s", type(exc).__name__)
        update |= {"images": images, "links": links, "bold_maths_letters": bold}
        if tagged:
            with_alt = _figures_with_alt(doc)
            update["images_with_alt"] = None if with_alt is None else min(with_alt, images)
        return facts.model_copy(update=update)
    except Exception as exc:
        log.warning("source facts: the PDF could not be read: %s", type(exc).__name__)
        return facts
    finally:
        doc.close()


def measure(
    pdf_bytes: bytes,
    *,
    pages: Iterable[int],
    elements: Iterable[Element],
    fixes: Iterable[Fix],
) -> SourceFacts:
    """The facts for the analysed `pages` (0-based) of this PDF, given what layout found
    (`elements`) and the steps proposed (`fixes`, the first per element). Never raises.

    PyMuPDF is not thread-safe: this holds layout's lock (when it has one), like every
    other PyMuPDF call in the pipeline, and quiets MuPDF's messages, which can quote the
    file.
    """
    elements = list(elements)
    first_fix: dict[str, Fix] = {}
    for fix in fixes:
        first_fix.setdefault(fix.element_id, fix)
    equations = [e for e in elements if e.kind is Kind.EQUATION]
    facts = SourceFacts(
        equations=len(equations),
        equations_flattened=sum(1 for e in equations if flattened_parts(e, first_fix.get(e.id))),
        tables=sum(1 for e in elements if e.kind is Kind.TABLE),
    )
    lock = getattr(layout, "_LOCK", None)
    with lock if lock is not None else contextlib.nullcontext():
        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)
        try:
            return _from_pdf(pdf_bytes, sorted(set(pages)), equations, facts)
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()  # the warning store can quote the PDF
