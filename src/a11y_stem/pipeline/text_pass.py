"""The text pass: links, cross-references, language and coloured text (pipeline 0.4.0).

It runs once per conversion, right after layout (`run._convert`, after
`_with_page_images`, before any step), reads the PDF once more for what layout does not
keep, and adds to the elements without changing their text or anything a model sees:

- `Element.links`, "uri" and "internal": the PDF's own link annotations
  (`Page.get_links`). Web and email links (http, https, mailto) are kept as they are;
  a link to a place in this document points at the element there (`target_id`); any
  other link (ftp, a file, a program to launch) is dropped and its text stays, with the
  notice `link_scheme_dropped`. URLs are never fetched.
- `Element.links`, "crossref": "Figure 2.3", "Fig. 2", "Table 1", "Equation (3)",
  "Eq. 3", "Eqs. (3) and (4)", and a bare "(3)" only when a display equation has exactly
  that number printed beside it (and not at the start of a paragraph, where it numbers
  the paragraph). Each links to the one figure, table or equation with that number; a
  number that two targets share, or none has, gets no link.
- `DocumentResult.lang` and `lang_source`: the PDF's /Lang ("pdf"), else detected from
  the body text ("detected", notice `lang_detected`), else "en" ("default", notice
  `lang_assumed`); and `Element.lang` on a paragraph or list item confidently in
  another language (`pipeline.language`).
- `Element.colour_runs` (only with FEATURE_COLOUR): text printed in a colour other than
  the body's, and the notice `colour_reference` when text near coloured text refers to
  a colour ("shown in red"). The assembler writes the colour, made dark enough for
  4.5:1 contrast (`pipeline.contrast`, `pipeline.inline_text`).
- the notice `link_text_unclear` for a link whose text says nothing on its own ("click
  here", a long bare URL with next to no text around it).

Every notice is optional ("Worth checking"); nothing here adds a decision.

How a rectangle on the page becomes offsets into `Element.text`: layout does not keep
where each character of an element's text was printed (the text is cleaned, joined
across lines and dehyphenated), so the pass reads the page's glyphs again
(`layout.page_glyphs`, with their colours) and aligns the element's text with the
glyphs inside its box (`align`: `difflib.SequenceMatcher` on the characters, spaces and
inline markers left out). A link covers the characters whose glyph box is at least half
inside its rectangle (`MIN_GLYPH_OVERLAP`), so a rectangle that grazes the next word does
not take it; a character the alignment cannot place is never linked or coloured on its
own. A link never covers an inline equation (a wide equation is a tab stop of its own,
and a tab stop cannot sit inside a link): it is split around it.

Privacy: notices are codes, pages and element ids; the log gets exception types only.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

import pymupdf

from a11y_stem.core.types import (
    INLINE_MARKER_RE,
    BBox,
    ColourRun,
    DocumentResult,
    Element,
    Kind,
    LangSource,
    Link,
    Notice,
    is_safe_href,
)
from a11y_stem.pipeline import contrast, language, layout
from a11y_stem.pipeline.layout import Glyph

log = logging.getLogger(__name__)

# Text that can carry links (the assembler writes these through `inline_text`).
LINK_KINDS = (Kind.PARAGRAPH, Kind.LIST_ITEM, Kind.HEADING)
# Running text: cross-references, colour runs and passage languages.
RUNNING_KINDS = (Kind.PARAGRAPH, Kind.LIST_ITEM)

MIN_GLYPH_OVERLAP = 0.5  # share of a glyph's box inside a link rectangle to be linked
BOX_SLACK = 1.5  # pt: a glyph belongs to an element when its centre is this close to its box
TARGET_SLACK = 2.0  # pt: a destination point this close to an element's box is on it
TARGET_X_SLACK = 6.0  # pt: ... and this close in x is in its column
MAX_ALIGN_CHARS = 20_000  # longer texts are not aligned (cost; no real paragraph is this long)

# Link text that says nothing on its own (WCAG 2.4.4), compared case-folded with the
# punctuation around it removed; and a bare URL longer than `LONG_URL_CHARS` as the whole
# link text, when its element has fewer than `URL_CONTEXT_WORDS` other words. A URL after
# a reference ("Smith (2024). Title. Retrieved from https://...") or inside a sentence has
# its purpose given by the text around it, so a reference list is not flagged entry by
# entry.
UNCLEAR_LINK_TEXT = frozenset(
    {
        "click",
        "click here",
        "click this link",
        "go",
        "here",
        "link",
        "more",
        "read more",
        "see here",
        "this",
        "this link",
        "this page",
    }
)
LONG_URL_CHARS = 40
URL_CONTEXT_WORDS = 3
_URL_TEXT = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_CONTEXT_WORD = re.compile(r"[^\W\d_]{2,}")

# Colours of text that are not runs: near black and dark greys read as the body text
# (the output writes them in its own body colour). OKLCH lightness below
# `BLACKISH_LIGHTNESS` is black whatever its tint; a colour with less chroma than
# `GREY_CHROMA` is a grey, kept only when it is too light to read (light grey hints are a
# contrast problem, so they are kept and made darker); near-white text was printed on a
# dark fill the output does not have, so it becomes body text too.
BLACKISH_LIGHTNESS = 0.2
GREY_CHROMA = 0.035
WHITEISH_LIGHTNESS = 0.96

# Text that refers to a colour: "in red", "shown in blue", "highlighted in yellow", "the
# green curve", "red arrows".
_COLOURS = r"red|blue|green|yellow|orange|purple|violet|pink|magenta|cyan|brown|grey|gray|black"
_SHAPES = (
    r"curves?|lines?|arrows?|text|terms?|words?|letters?|parts?|regions?|areas?|boxe?s?"
    r"|dots?|points?|bars?|squares?|circles?|highlights?|labels?|entries|entry|cells?"
)
COLOUR_REFERENCE = re.compile(
    rf"\b(?:in|by)\s+(?:the\s+)?(?:colou?r\s+)?(?:{_COLOURS})\b"
    rf"|\b(?:{_COLOURS})(?:-coloured|-colored)?\s+(?:{_SHAPES})\b"
    rf"|\b(?:colou?red|highlighted|shaded|printed|marked)\s+(?:{_COLOURS})\b",
    re.IGNORECASE,
)
COLOUR_REFERENCE_REACH = 2  # running-text elements before or after a coloured one


# ============================================================================ PDF reading


@dataclass(frozen=True)
class PdfLink:
    """One link annotation, in display coordinates (the frame of `Element.bbox`).

    `kind`: "uri" (`uri` is the address, safe or not), "goto" (a place in this document:
    `dest_page`, and `dest_point` when the PDF gives one) or "other" (a file, a program,
    another PDF, a named action)."""

    page: int
    rect: BBox
    kind: Literal["uri", "goto", "other"]
    uri: str | None = None
    dest_page: int | None = None
    dest_point: tuple[float, float] | None = None


@dataclass
class PdfText:
    """What the text pass reads from the PDF itself: /Lang (when present and well formed),
    the link annotations and, per page, the glyphs with their colours."""

    lang: str | None = None
    links: dict[int, list[PdfLink]] = field(default_factory=dict)
    glyphs: dict[int, list[Glyph]] = field(default_factory=dict)
    annotations: int = 0  # link annotations of any kind on the analysed pages


def _catalog_lang(doc: pymupdf.Document) -> str | None:
    try:
        kind, value = doc.xref_get_key(doc.pdf_catalog(), "Lang")
    except Exception:  # a malformed catalog is a missing /Lang
        return None
    if kind not in ("string", "name"):
        return None
    return language.document_tag(value.strip().lstrip("/"))


def _pdf_links(
    doc: pymupdf.Document, number: int, annotations: list[dict], pages: set[int]
) -> list[PdfLink]:
    """The page's link annotations (`Page.get_links`) as the pass uses them; links to a
    page that was not analysed, and named actions ("next page"), are left out."""
    out: list[PdfLink] = []
    for raw in annotations:
        rect = raw.get("from")
        if rect is None:
            continue
        box = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        kind = raw.get("kind")
        if kind == pymupdf.LINK_URI:
            out.append(PdfLink(number, box, "uri", uri=str(raw.get("uri") or "")))
            continue
        dest = raw.get("page")
        if kind in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED) and isinstance(dest, int):
            if dest not in pages:
                continue  # a page that was not converted: nothing to point at
            point = raw.get("to")
            spot: tuple[float, float] | None = None
            if point is not None and kind == pymupdf.LINK_GOTO:
                spot = (float(point.x), float(point.y))  # already display coordinates
            elif point is not None:  # a named destination: PDF coordinates (y up)
                target = doc[dest]
                shown = pymupdf.Point(point) * target.transformation_matrix
                shown = shown * target.rotation_matrix
                spot = (float(shown.x), float(shown.y))
            out.append(PdfLink(number, box, "goto", dest_page=dest, dest_point=spot))
            continue
        if kind == pymupdf.LINK_NAMED and dest is None:
            continue  # a named action ("next page"): no address and nothing to keep
        out.append(PdfLink(number, box, "other"))
    return out


def read_pdf(pdf_bytes: bytes, pages: Iterable[int], *, colour: bool) -> PdfText:
    """/Lang, links and glyphs of the analysed pages (0-based).

    Glyphs are read only where they are needed: pages with links, and every page when
    colour runs are on. `layout.analyze` has already rendered these pages in its crash
    probe, so reading them in this process is safe; PyMuPDF is not thread-safe, so this
    holds layout's lock like every other PyMuPDF call. Raises whatever PyMuPDF raises.
    """
    wanted = sorted(set(pages))
    found = PdfText()
    lock = getattr(layout, "_LOCK", None)
    with lock if lock is not None else contextlib.nullcontext():
        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)
        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            try:
                found.lang = _catalog_lang(doc)
                in_doc = {p for p in wanted if 0 <= p < doc.page_count}
                for number in sorted(in_doc):
                    annotations = doc[number].get_links()
                    found.annotations += len(annotations)
                    links = _pdf_links(doc, number, annotations, in_doc)
                    if links:
                        found.links[number] = links
                    if colour or links:
                        found.glyphs[number] = layout.page_glyphs(doc[number])
            finally:
                doc.close()
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()  # the warning store can quote the PDF
    return found


def link_count(doc: pymupdf.Document, pages: Iterable[int]) -> int:
    """Link annotations (of any kind) on these 0-based pages of an open PyMuPDF document:
    `SourceFacts.links`. The caller holds layout's lock."""
    return sum(len(doc[n].get_links()) for n in set(pages) if 0 <= n < doc.page_count)


# ============================================================================ alignment


def _marker_ranges(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in INLINE_MARKER_RE.finditer(text)]


def align(text: str, glyphs: Sequence[Glyph]) -> list[int | None]:
    """For each character of `text`, the index of the glyph in `glyphs` that printed it,
    or None (spaces, inline markers, and characters the alignment could not place)."""
    out: list[int | None] = [None] * len(text)
    if not text or not glyphs or len(text) > MAX_ALIGN_CHARS:
        return out
    masked = [False] * len(text)
    for start, end in _marker_ranges(text):
        masked[start:end] = [True] * (end - start)
    offsets = [i for i, c in enumerate(text) if not masked[i] and not c.isspace()]
    ours = "".join(text[i] for i in offsets)
    theirs = "".join(g.c for g in glyphs)
    matcher = SequenceMatcher(None, ours, theirs, autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            out[offsets[a + k]] = b + k
    return out


def _centre_in(box: BBox, area: BBox, slack: float) -> bool:
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return area[0] - slack <= x <= area[2] + slack and area[1] - slack <= y <= area[3] + slack


def _overlaps(a: BBox, b: BBox, slack: float = 0.0) -> bool:
    across = a[0] <= b[2] + slack and b[0] <= a[2] + slack
    return across and a[1] <= b[3] + slack and b[1] <= a[3] + slack


class _Aligner:
    """Per element: the glyphs inside its box and the alignment of its text with them
    (computed once, only for elements that need it)."""

    def __init__(self, glyphs: dict[int, list[Glyph]]) -> None:
        self._glyphs = glyphs
        self._cache: dict[str, tuple[list[Glyph], list[int | None]]] = {}

    def glyphs_in(self, element: Element) -> list[Glyph]:
        page = self._glyphs.get(element.page, [])
        return [g for g in page if _centre_in(g.bbox, element.bbox, BOX_SLACK)]

    def of(self, element: Element) -> tuple[list[Glyph], list[int | None]]:
        if element.id not in self._cache:
            glyphs = self.glyphs_in(element)
            self._cache[element.id] = (glyphs, align(element.text or "", glyphs))
        return self._cache[element.id]


def labelled_runs[T](
    text: str,
    alignment: Sequence[int | None],
    label: Callable[[int], T | None],
    *,
    across_markers: bool,
) -> list[tuple[int, int, T]]:
    """Maximal spans of `text` whose placed characters all have the same label.

    `label(glyph index)` is the label of a placed character (None: none). Spaces and
    characters the alignment did not place neither start nor end a span, so a span runs
    from its first to its last labelled character. An inline marker ends the span unless
    `across_markers` and the characters on both sides have the same label.
    """
    runs: list[tuple[int, int, T]] = []
    current: list | None = None  # [start, end, label]
    markers = {start: end for start, end in _marker_ranges(text)}
    i = 0
    while i < len(text):
        if i in markers:
            if not across_markers and current is not None:
                runs.append((current[0], current[1], current[2]))
                current = None
            i = markers[i]
            continue
        glyph = alignment[i] if i < len(alignment) else None
        if glyph is None:
            i += 1
            continue
        value = label(glyph)
        if current is not None and value is not None and value == current[2]:
            current[1] = i + 1
        else:
            if current is not None:
                runs.append((current[0], current[1], current[2]))
            current = [i, i + 1, value] if value is not None else None
        i += 1
    if current is not None:
        runs.append((current[0], current[1], current[2]))
    return runs


# ============================================================================ links


def _covered(glyph: Glyph, rect: BBox) -> bool:
    """At least `MIN_GLYPH_OVERLAP` of the glyph's box lies inside `rect`."""
    x0, y0, x1, y1 = glyph.bbox
    area = (x1 - x0) * (y1 - y0)
    if area <= 0:
        return _centre_in(glyph.bbox, rect, 0.0)
    w = min(x1, rect[2]) - max(x0, rect[0])
    h = min(y1, rect[3]) - max(y0, rect[1])
    return w > 0 and h > 0 and w * h >= MIN_GLYPH_OVERLAP * area


def _linkable(element: Element) -> bool:
    """Does the assembler write this element with its id (so "#id" goes somewhere)?"""
    if element.kind in (Kind.HEADING, Kind.PARAGRAPH):
        return bool((element.text or "").strip())
    if element.kind is Kind.EQUATION:
        return element.display != "inline"
    return element.kind in (Kind.LIST_ITEM, Kind.FIGURE, Kind.TABLE)


def target_at(elements: Sequence[Element], point: tuple[float, float] | None) -> Element | None:
    """The element a destination point on a page names: the one whose box holds it, else
    the nearest below it (a PDF puts a destination a little above its target, and a
    figure's at its caption, under the figure), in the point's column when one is.

    Elements above the point count double their distance. No point: the first element
    of the page."""
    candidates = [e for e in elements if _linkable(e)]
    if not candidates:
        return None
    if point is None:
        return min(candidates, key=lambda e: e.order)
    x, y = point

    def score(element: Element) -> tuple[int, float, int]:
        x0, y0, x1, y1 = element.bbox
        if y0 - TARGET_SLACK <= y <= y1 + TARGET_SLACK:
            dy = 0.0
        elif y < y0:
            dy = y0 - y
        else:
            dy = 2 * (y - y1)
        column = 0 if x0 - TARGET_X_SLACK <= x <= x1 + TARGET_X_SLACK else 1
        return column, dy, element.order

    return min(candidates, key=score)


@dataclass
class _Found:
    """Links found so far, per element id, and the notices of the ones dropped."""

    links: defaultdict[str, list[Link]] = field(default_factory=lambda: defaultdict(list))
    dropped: Counter[tuple[int, str | None]] = field(default_factory=Counter)


def _link_spans(element: Element, aligner: _Aligner, rect: BBox) -> list[tuple[int, int]]:
    glyphs, alignment = aligner.of(element)
    if not any(_covered(g, rect) for g in glyphs):
        return []
    covered = {i for i, g in enumerate(glyphs) if _covered(g, rect)}
    runs = labelled_runs(
        element.text or "",
        alignment,
        lambda i: True if i in covered else None,
        across_markers=False,
    )
    return [(start, end) for start, end, _ in runs]


def _pdf_links_onto(
    elements: list[Element], pdf: PdfText, aligner: _Aligner, found: _Found
) -> None:
    """Each PDF link annotation onto the text it covers (see the module docstring)."""
    by_page: defaultdict[int, list[Element]] = defaultdict(list)
    for element in elements:
        by_page[element.page].append(element)
    parents = {e.id: e for e in elements}
    for page, links in sorted(pdf.links.items()):
        texts = [e for e in by_page[page] if e.kind in LINK_KINDS and e.text]
        for link in links:
            spans = [
                (element, span)
                for element in texts
                if _overlaps(element.bbox, link.rect, BOX_SLACK)
                for span in _link_spans(element, aligner, link.rect)
            ]
            if link.kind == "uri" and is_safe_href(link.uri or ""):
                for element, (start, end) in spans:
                    found.links[element.id].append(
                        Link(start=start, end=end, kind="uri", href=link.uri)
                    )
            elif link.kind == "goto":
                target = target_at(by_page[link.dest_page or 0], link.dest_point)
                if target is not None and target.kind is Kind.EQUATION and target.parent_id:
                    target = parents.get(target.parent_id, target)
                for element, (start, end) in spans:
                    if target is None or target.id == element.id:
                        continue  # nowhere to go, or to itself: the text stays
                    found.links[element.id].append(
                        Link(start=start, end=end, kind="internal", target_id=target.id)
                    )
            else:  # not a web or email address: the text stays, without the link
                owner = spans[0][0].id if spans else None
                found.dropped[(page, owner)] += 1


def _tidy_links(text: str, links: list[Link]) -> list[Link]:
    """Sorted, without overlaps (the first wins), and a link split across lines (one
    annotation per line, as hyperref writes a broken URL) joined again."""
    out: list[Link] = []
    for link in sorted(links, key=lambda k: (k.start, k.end)):
        if out and link.start < out[-1].end:
            continue
        prev = out[-1] if out else None
        if (
            prev is not None
            and (prev.kind, prev.href, prev.target_id) == (link.kind, link.href, link.target_id)
            and not text[prev.end : link.start].strip()
        ):
            out[-1] = prev.model_copy(update={"end": link.end})
            continue
        out.append(link)
    return out


def visible_text(text: str, start: int, end: int) -> str:
    """The text of a span as a reader sees it (an inline equation as "[equation]")."""
    return " ".join(INLINE_MARKER_RE.sub("[equation]", text[start:end]).split())


def context_words(text: str, start: int, end: int) -> int:
    """Words of `text` outside the span [start, end): inline equations, URLs, numbers and
    single letters left out."""
    rest = INLINE_MARKER_RE.sub(" ", f"{text[:start]} {text[end:]}")
    return len(_CONTEXT_WORD.findall(_URL_TEXT.sub(" ", rest)))


def unclear_link_text(text: str, context: int = 0) -> bool:
    """Link text that does not say where the link goes: "click here" and the like, or a
    long bare URL with fewer than `URL_CONTEXT_WORDS` words around it (`context`, from
    `context_words`; see `UNCLEAR_LINK_TEXT`)."""
    plain = " ".join(text.split())
    if _URL_TEXT.fullmatch(plain.replace(" ", "")) and len(plain) > LONG_URL_CHARS:
        return context < URL_CONTEXT_WORDS
    words = re.sub(r"[^\w\s]", " ", plain.casefold())
    return " ".join(words.split()) in UNCLEAR_LINK_TEXT


# ============================================================================ cross-references

# A reference word, then a number, then more numbers joined by "," "and" "or" "&" "to" or a
# dash ("Eqs. (3) and (4)", "Figures 2, 3 and 5"). Equation numbers may be bracketed.
_NUMBER = r"\(\s*\d+(?:[.\-]\d+)*[a-z]?\s*\)|\d+(?:[.\-]\d+)*[a-z]?"
_JOIN = r"\s*(?:,|and|or|&|to|\u2013|-)\s*"
CROSSREF = re.compile(
    r"\b(?P<word>figures?|figs?\.|tables?|equations?|eqns?\.?|eqs?\.?)\s*"
    rf"(?P<first>{_NUMBER})(?P<rest>(?:{_JOIN}(?:{_NUMBER}))*)(?![\w.]?\w)",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(_NUMBER, re.IGNORECASE)
# "(3)" alone, not right after a letter, digit or bracket ("f(3)", "x_(3)" are maths).
BARE_EQUATION_REF = re.compile(r"(?<![\w)\]\u27e7])\((?P<num>\d+(?:\.\d+)*[a-z]?)\)(?!\w)")
# A caption's own number ("Figure 2.3", "Fig. 4:", "Table 1.") at the start of a caption.
_CAPTION_NUMBER = {
    "figure": re.compile(r"^\s*(?:figure|fig\.?)\s*(\d+(?:[.\-]\d+)*[a-z]?)\b", re.IGNORECASE),
    "table": re.compile(r"^\s*table\s*(\d+(?:[.\-]\d+)*[a-z]?)\b", re.IGNORECASE),
}
_LABEL_NUMBER = re.compile(r"(\()?\s*(\d+(?:[.\-]\d+)*[a-z]?)\s*(\))?")
# A paragraph that starts like a caption ("Figure 2: ...", "Table 1. ..."): its number is
# the caption's own, not a reference.
_CAPTION_START = re.compile(r"\s*[.:|\u2013\u2014]")


TargetKind = Literal["figure", "table", "equation"]


def _key(number: str) -> str:
    """ "(2-3)" -> "2.3": one key for a number however it is bracketed or separated."""
    return number.strip().strip("()").strip().replace("-", ".").lower()


def _word_kind(word: str) -> TargetKind:
    lowered = word.lower()
    if lowered.startswith("fig"):
        return "figure"
    if lowered.startswith("tab"):
        return "table"
    return "equation"


@dataclass
class _Targets:
    """Number key -> element ids, per kind of target; and the equation numbers printed
    in brackets (only those make a bare "(3)" a reference)."""

    ids: dict[str, defaultdict[str, set[str]]] = field(
        default_factory=lambda: {k: defaultdict(set) for k in ("figure", "table", "equation")}
    )
    bracketed: set[str] = field(default_factory=set)

    def unique(self, kind: str, number: str) -> str | None:
        found = self.ids[kind].get(_key(number), set())
        return next(iter(found)) if len(found) == 1 else None


def crossref_targets(elements: Iterable[Element]) -> _Targets:
    targets = _Targets()
    for element in elements:
        if element.kind in (Kind.FIGURE, Kind.TABLE) and element.caption:
            for kind, pattern in _CAPTION_NUMBER.items():
                if match := pattern.match(element.caption):
                    targets.ids[kind][_key(match.group(1))].add(element.id)
        elif element.kind is Kind.EQUATION and element.display != "inline":
            for match in _LABEL_NUMBER.finditer(element.equation_label or ""):
                key = _key(match.group(2))
                targets.ids["equation"][key].add(element.id)
                if match.group(1) and match.group(3):
                    targets.bracketed.add(key)
    return targets


@dataclass(frozen=True)
class Reference:
    """Text that refers to a figure, table or equation by number: "Figure 2.3" (the word
    and the first number), each further number of a list ("(4)" in "Eqs. (3) and (4)"),
    or a bare "(3)" (`bare`, a reference only when an equation prints that label)."""

    start: int
    end: int
    kind: TargetKind
    number: str
    bare: bool = False


def references(text: str) -> list[Reference]:
    """Every reference-shaped piece of `text`, in order (targets are not looked up)."""
    found: list[Reference] = []
    for match in CROSSREF.finditer(text):
        if match.start() == 0 and _CAPTION_START.match(text, match.end()):
            continue  # an unattached caption's own number
        kind = _word_kind(match.group("word"))
        found.append(Reference(match.start(), match.end("first"), kind, match.group("first")))
        offset = match.start("rest")
        for number in _NUMBER_RE.finditer(match.group("rest")):
            start, end = offset + number.start(), offset + number.end()
            found.append(Reference(start, end, kind, number.group()))
    taken = [(r.start, r.end) for r in found]
    for match in BARE_EQUATION_REF.finditer(text):
        if not text[: match.start()].strip():
            continue  # "(1) Measure the speed": an enumeration label, not a reference
        if not any(s < match.end() and match.start() < e for s, e in taken):
            found.append(Reference(match.start(), match.end(), "equation", match.group(), True))
    return sorted(found, key=lambda r: r.start)


def widen_internal(text: str, links: list[Link]) -> list[Link]:
    """A PDF link on just the number of a reference ("Figure 1", hyperref links only the
    "1") is widened to the whole reference, so the link text says what it points at. The
    PDF's own target is kept; a reference holding more than one link is left alone."""
    out = list(links)
    for ref in references(text):
        inside = [i for i, k in enumerate(out) if ref.start <= k.start and k.end <= ref.end]
        crossing = [
            k
            for k in out
            if k.start < ref.end
            and ref.start < k.end
            and not (ref.start <= k.start and k.end <= ref.end)
        ]
        if len(inside) == 1 and not crossing and out[inside[0]].kind == "internal":
            out[inside[0]] = out[inside[0]].model_copy(update={"start": ref.start, "end": ref.end})
    return out


def crossrefs(text: str, targets: _Targets, taken: Sequence[Link] = ()) -> list[Link]:
    """Cross-reference links in one element's text, never overlapping `taken` links or
    inline markers, and none where the number names no target or more than one."""
    markers = _marker_ranges(text)
    found: list[Link] = []
    for ref in references(text):
        if ref.bare and _key(ref.number) not in targets.bracketed:
            continue
        target = targets.unique(ref.kind, ref.number)
        spans = [*((k.start, k.end) for k in (*taken, *found)), *markers]
        if target is None or any(s < ref.end and ref.start < e for s, e in spans):
            continue
        found.append(Link(start=ref.start, end=ref.end, kind="crossref", target_id=target))
    return found


# ============================================================================ colour


def body_colour(glyphs: Iterable[Glyph]) -> tuple[int, int, int] | None:
    """The colour most of the text is printed in, when it is dark enough to read as body
    text (else None: light body text is kept as runs and made darker)."""
    counts = Counter(g.rgb for g in glyphs)
    if not counts:
        return None
    rgb = counts.most_common(1)[0][0]
    ok = contrast.contrast_ratio(rgb, contrast.OUTPUT_BACKGROUND) >= contrast.MIN_TEXT_CONTRAST
    return rgb if ok else None


def is_run_colour(rgb: tuple[int, int, int], body: tuple[int, int, int] | None) -> bool:
    """Is text in this colour a colour run (see `BLACKISH_LIGHTNESS` and the rest)?"""
    if rgb == body:
        return False
    lightness, chroma, _ = contrast.oklch(rgb)
    if lightness < BLACKISH_LIGHTNESS or lightness >= WHITEISH_LIGHTNESS:
        return False
    if chroma < GREY_CHROMA:
        readable = contrast.contrast_ratio(rgb, contrast.OUTPUT_BACKGROUND)
        return readable < contrast.MIN_TEXT_CONTRAST
    return True


def _without(
    runs: list[tuple[int, int, tuple[int, int, int]]], links: Sequence[Link], text: str
) -> list[ColourRun]:
    """Colour runs minus the linked text (a link is written in the link colour), each
    piece trimmed of spaces and kept only when it has a letter or digit."""
    out: list[ColourRun] = []
    for start, end, rgb in runs:
        pieces = [(start, end)]
        for link in links:
            pieces = [
                part
                for s, e in pieces
                for part in ((s, min(e, link.start)), (max(s, link.end), e))
                if part[0] < part[1]
            ]
        for s, e in pieces:
            while s < e and text[s].isspace():
                s += 1
            while e > s and text[e - 1].isspace():
                e -= 1
            piece = text[s:e]
            if INLINE_MARKER_RE.search(piece) or any(c.isalnum() for c in piece):
                out.append(ColourRun(start=s, end=e, rgb=rgb))
    return out


def colour_runs(
    element: Element, aligner: _Aligner, body: tuple[int, int, int] | None, links: Sequence[Link]
) -> list[ColourRun]:
    """The element's coloured runs (offsets into its text), links left out."""
    candidates = aligner.glyphs_in(element)
    if not any(is_run_colour(g.rgb, body) for g in candidates):
        return []
    glyphs, alignment = aligner.of(element)
    runs = labelled_runs(
        element.text or "",
        alignment,
        lambda i: glyphs[i].rgb if is_run_colour(glyphs[i].rgb, body) else None,
        across_markers=True,
    )
    return _without(runs, links, element.text or "")


def _colour_reference_notices(elements: list[Element], coloured: set[str]) -> list[Notice]:
    """`colour_reference` for running text that names a colour, on or near coloured text
    (the same element, or one of the `COLOUR_REFERENCE_REACH` before or after it on the
    same page)."""
    running = [e for e in elements if e.kind in RUNNING_KINDS and e.text]
    notices = []
    for index, element in enumerate(running):
        if not COLOUR_REFERENCE.search(INLINE_MARKER_RE.sub(" ", element.text or "")):
            continue
        reach = running[max(0, index - COLOUR_REFERENCE_REACH) : index + COLOUR_REFERENCE_REACH + 1]
        if any(e.id in coloured and e.page == element.page for e in reach):
            notices.append(
                Notice(code="colour_reference", page=element.page, element_id=element.id)
            )
    return notices


# ============================================================================ language


def _document_language(
    elements: Sequence[Element], pdf_lang: str | None, layout_lang: str
) -> tuple[str, LangSource, list[Notice]]:
    if pdf_lang:
        return pdf_lang, "pdf", []
    if layout_lang and layout_lang != language.DEFAULT_LANG:
        # Layout only reports a language other than the default from the PDF's /Lang.
        return language.document_tag(layout_lang) or language.DEFAULT_LANG, "pdf", []
    texts = [e.text or "" for e in elements if e.kind in (*RUNNING_KINDS, Kind.HEADING)]
    detected = language.document_language(texts)
    if detected is not None:
        return detected, "detected", [Notice(code="lang_detected")]
    return language.DEFAULT_LANG, "default", [Notice(code="lang_assumed")]


def passages(elements: Sequence[Element], document_lang: str) -> dict[str, str | None]:
    """Element id -> the passage's language when it differs from the document's, for
    every paragraph and list item (None where it does not)."""
    return {
        e.id: language.passage_language(e.text or "", document_lang)
        for e in elements
        if e.kind in RUNNING_KINDS and e.text
    }


# ============================================================================ the pass


@dataclass
class TextPassResult:
    """The elements with links, colour runs and passage languages; the document language
    and where it came from; the pass's notices; and the PDF's link annotations count."""

    elements: list[Element]
    lang: str
    lang_source: LangSource
    notices: list[Notice]
    annotations: int = 0

    def counts(self) -> dict[str, int]:
        """Content-free numbers for the trace."""
        links = [k for e in self.elements for k in e.links]
        return {
            "links": sum(1 for k in links if k.kind != "crossref"),
            "crossrefs": sum(1 for k in links if k.kind == "crossref"),
            "colour_runs": sum(len(e.colour_runs) for e in self.elements),
            "passages": sum(1 for e in self.elements if e.lang),
            "notices": len(self.notices),
        }


def annotate(
    elements: list[Element], pdf: PdfText, *, layout_lang: str = "en", colour: bool = False
) -> TextPassResult:
    """The pass on elements and what was read from the PDF (pure; see `apply`)."""
    aligner = _Aligner(pdf.glyphs)
    found = _Found()
    _pdf_links_onto(elements, pdf, aligner, found)
    targets = crossref_targets(elements)
    links: dict[str, list[Link]] = {}
    notices: list[Notice] = []
    unclear: Counter[tuple[int, str]] = Counter()
    for element in elements:
        if element.kind not in LINK_KINDS or not element.text:
            continue
        own = _tidy_links(element.text, found.links.get(element.id, []))
        if element.kind in RUNNING_KINDS:
            own = widen_internal(element.text, own)
        for link in own:
            shown = visible_text(element.text, link.start, link.end)
            if unclear_link_text(shown, context_words(element.text, link.start, link.end)):
                unclear[(element.page, element.id)] += 1
        if element.kind in RUNNING_KINDS:
            own = sorted([*own, *crossrefs(element.text, targets, own)], key=lambda k: k.start)
        if own:
            links[element.id] = own
    runs: dict[str, list[ColourRun]] = {}
    if colour:
        body = body_colour(g for glyphs in pdf.glyphs.values() for g in glyphs)
        for element in elements:
            if element.kind in RUNNING_KINDS and element.text:
                found_runs = colour_runs(element, aligner, body, links.get(element.id, []))
                if found_runs:
                    runs[element.id] = found_runs
    lang, source, lang_notices = _document_language(elements, pdf.lang, layout_lang)
    langs = passages(elements, lang)
    updated = []
    for element in elements:
        update: dict[str, object] = {}
        if element.id in links:
            update["links"] = links[element.id]
        if element.id in runs:
            update["colour_runs"] = runs[element.id]
        if langs.get(element.id):
            update["lang"] = langs[element.id]
        updated.append(element.model_copy(update=update) if update else element)
    for (page, owner), count in sorted(found.dropped.items(), key=lambda kv: str(kv[0])):
        notices.append(Notice(code="link_scheme_dropped", page=page, element_id=owner, count=count))
    for (page, owner), count in sorted(unclear.items()):
        notices.append(Notice(code="link_text_unclear", page=page, element_id=owner, count=count))
    if colour:
        notices.extend(_colour_reference_notices(updated, set(runs)))
    notices.extend(lang_notices)
    return TextPassResult(updated, lang, source, notices, annotations=pdf.annotations)


def apply(
    pdf_bytes: bytes,
    elements: list[Element],
    *,
    pages: Iterable[int],
    layout_lang: str = "en",
    colour: bool = False,
) -> TextPassResult:
    """The text pass for one conversion (the module docstring says what it adds).

    `pages` are the analysed pages (0-based), `layout_lang` the language layout reported
    (`Layout.lang`: the PDF's /Lang, else "en"), `colour` the FEATURE_COLOUR flag in
    force. Never raises: a PDF that cannot be read again gives no links or colours (the
    cross-references and the language still come from the text), and a failure of the
    pass itself leaves the elements as they were, with the language layout reported.
    """
    try:
        pdf = read_pdf(pdf_bytes, pages, colour=colour)
    except Exception as exc:  # a reading problem never fails the conversion
        log.warning("text pass could not read the PDF: %s", type(exc).__name__)
        pdf = PdfText()
    try:
        return annotate(elements, pdf, layout_lang=layout_lang, colour=colour)
    except Exception as exc:
        log.warning("text pass failed: %s", type(exc).__name__)
        if pdf.lang or layout_lang != language.DEFAULT_LANG:
            return TextPassResult(list(elements), pdf.lang or layout_lang, "pdf", [])
        # Nothing says which language this is: assumed, and the job page asks.
        notices = [Notice(code="lang_assumed")]
        return TextPassResult(list(elements), language.DEFAULT_LANG, "default", notices)


# ============================================================================ language change


def set_document_language(result: DocumentResult, tag: str) -> DocumentResult:
    """The stored result with the document language the instructor chose on the job page.

    `tag` is a BCP 47 tag ("fr", "pt_br") or the English name of a language in
    `language.LANGUAGE_NAMES` ("French"); `lang` becomes its usual form ("fr", "pt-BR")
    and `lang_source` "user". The `lang_detected` / `lang_assumed` notices go (the
    instructor has answered them), and the passage languages are worked out again
    against the new language, so a passage is marked only where it differs from it.
    Raises ValueError for anything else, including tags that name no language ("und",
    "x-none"). Fixes are unchanged and so is the input result; the caller re-renders the
    HTML from the returned result (`web.review.rerender_html`) and stores both, as after
    a review decision (bumping `jobs.result_version` like one).
    """
    lang = language.document_tag(tag)
    if lang is None:
        raise ValueError("not a language tag")
    langs = passages(result.elements, lang)
    elements = [
        e.model_copy(update={"lang": langs.get(e.id)}) if e.id in langs or e.lang else e
        for e in result.elements
    ]
    notices = [n for n in result.notices if n.code not in ("lang_detected", "lang_assumed")]
    return result.model_copy(
        update={"lang": lang, "lang_source": "user", "elements": elements, "notices": notices}
    )
