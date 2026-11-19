"""The document: `analyze` (the public entry), `_analyze_document` and the steps it takes in
order (read each page, tables, margins, order, blocks, joins, notices, elements), slide decks,
the page picture a failed page is kept as, and the heading levels read from the document.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from functools import partial
from typing import Any

import pymupdf

from readablestem.core import content_budget
from readablestem.core.types import BBox, Element, Kind
from readablestem.pipeline import (
    glyph_strokes,
    hyphens,
    named_links,
    outline,
    page_labels,
    page_type,
    say_once,
    slides,
    tables,
)
from readablestem.pipeline.layout.access import (
    _LOCK,
    _check_content,
    _digest,
    _document_lang,
    _ensure_probed,
    _open,
    _quiet_mupdf,
    _select_pages,
)
from readablestem.pipeline.layout.blocks import (
    MAX_HEADING_CHARS,
    REASON_LARGER_FONT,
    REASON_LETTER,
    _letter_rows,
    section_number,
)
from readablestem.pipeline.layout.bold import _glossary_entries, _lead_entries, _settle_bold_letters
from readablestem.pipeline.layout.context import Layout, LayoutError, _Document, _PageGuard
from readablestem.pipeline.layout.drawings import _page_paths
from readablestem.pipeline.layout.elements import _contents_links, _footnote_links, _to_elements
from readablestem.pipeline.layout.figures import (
    _answer_fields,
    _image_infos,
    _mark_plots,
    _plain_image,
)
from readablestem.pipeline.layout.furniture import (
    _box_zone,
    _margin_filter,
    _MarginFilter,
    _page_pictures,
)
from readablestem.pipeline.layout.glyphs import _clean
from readablestem.pipeline.layout.joins import (
    _body_and_pitch,
    _join_page_splits,
    _mark_run_in_labels,
    _promote_numbered_lines,
)
from readablestem.pipeline.layout.lists import _list_markers
from readablestem.pipeline.layout.notices import (
    _FootnoteMark,
    _hidden_text_notices,
    _ink_notices,
    _layout_notices,
    _turned_notices,
)
from readablestem.pipeline.layout.order import Gutter
from readablestem.pipeline.layout.page import (
    _covered_points,
    _label_keys,
    _loose_strokes,
    _page_blocks,
    _painted,
    _prepare_rows,
)
from readablestem.pipeline.layout.rows import (
    _field_rows,
    _merge_baselines,
    _page_rows,
    _raw_blocks,
    take_form_fields,
)
from readablestem.pipeline.layout.shared import _Display, _PageBlocks, _Path, _Row
from readablestem.pipeline.layout.tables import _claim_tables

HEADING_LEVEL_TOLERANCE = 0.05  # heading sizes within 5 % of each other share a level

#                         least MIN_IMAGE_PT) from its box's edge: double lines, rounded
#                         corners, corner ticks and curls
# Page-type reasons for ink on a page: a page-filling drawing there is never a figure for
# the model (it may be a page of notes). When the ink was not confirmed (rejected or not
# analysed) the safety net keeps the page image instead; on a typed page with
# handwriting (mixed_ink) the handwriting is cut out already and the rest is left out.
INK_REASONS = frozenset({"ink_rejected", "ink_not_analysed", "ink_some", "ink_writing"})

# The PDF's own tags call this line a heading, "tagged heading h2" (`pipeline.tagged`).
REASON_TAGGED = "tagged heading h"

# A heading read from an image of a page (ADR 0007): the level the page reader gave it.
REASON_IMAGE_HEADING = "image heading h"

_JUNK_TITLE = re.compile(
    r"^(?:untitled|document\s*\d*$|microsoft\s+(?:word|powerpoint)|powerpoint presentation"
    r"|slide\s*\d+$|title$|no title$)|\.(?:pdf|docx?|pptx?|tex|odt|rtf|txt|html?|key)$",
    re.IGNORECASE,
)


def analyze(
    pdf_bytes: bytes,
    *,
    pages: list[int] | None = None,
    max_pages: int | None = None,
    page_math: Any = None,
    display_grouping: bool = False,
    region_tree: bool = False,
) -> Layout:
    """Elements of the given 0-based pages (all by default), ids "p{page+1}-e{index}".

    `index` counts from 0 per page in reading order; an inline equation comes right after
    the paragraph that contains it. `Element.order` is the position in the whole document.
    Pages are analysed in ascending order (duplicates ignored). Raises `LayoutError`.

    The selected pages are first rendered once in a child process (see `_probe_pages`);
    the document is opened for this call only and closed again, so analysis leaves no
    user content in this module (only `crop` caches documents).

    `page_math` (ADR 0006): an object with `on_page(number, rows, ordered, rules)` and
    `after_regions(number, rows)` (`pipeline.page_math_reconcile.PageMathHook`). It is shown
    each page's text rows before the display equations are grouped, and may return them
    cut and marked by a verified plan. None (the default) is layout's own rules alone.

    `display_grouping` (`LAYOUT_DISPLAY_GROUPING`, off): the rules of "One printed display, one
    element" in docs/layout.md. Off, the output is what it was before they existed.

    `region_tree` (`REGION_TREE`, off): the page's columns are found by a tree of regions built
    from the unmerged lines ("The region tree" in docs/layout.md). Off, the output is what it
    was before the tree existed.
    """
    with _LOCK:
        _quiet_mupdf()
        try:
            doc = _open(pdf_bytes)
            try:
                _check_content(lambda: content_budget.check_objects(doc))  # before the page tree
                selected = _select_pages(doc.page_count, pages, max_pages)
                _check_content(lambda: content_budget.check(doc, selected))
                _ensure_probed(pdf_bytes, _digest(pdf_bytes), selected)
                return _analyze_document(
                    doc,
                    selected,
                    page_math,
                    _Document(grouping=display_grouping, region_tree=region_tree),
                )
            except (RuntimeError, pymupdf.mupdf.FzErrorBase) as exc:  # damaged content
                raise LayoutError("unreadable", "the PDF could not be parsed") from exc
            finally:
                doc.close()
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()  # the warning store quotes PDF content


def tagged_level(element: Element) -> int | None:
    """The heading level the PDF's tags gave this element, else the level of its bookmark
    (`outline.REASON_OUTLINE`), else the level the page reader gave a heading read from an
    image of a page (`REASON_IMAGE_HEADING`), or None. A tag wins over a bookmark."""
    for prefix in (REASON_TAGGED, outline.REASON_OUTLINE, REASON_IMAGE_HEADING):
        for reason in element.reasons:
            if reason.startswith(prefix) and reason[len(prefix) :].isdigit():
                return int(reason[len(prefix) :])
    return None


def heading_levels(elements: Sequence[Element]) -> dict[str, int]:
    """Raw outline level (1-6) per heading element id, before outline repair.

    Sizes of size-based headings are clustered (within `HEADING_LEVEL_TOLERANCE`), the
    largest cluster is level 1. Bold body-size and plain numbered headings sit one level
    below the smallest size cluster; among them, deeper section numbers ("2.3.1" under
    "2.3") go one level deeper per extra number. A heading the PDF's tags gave a level
    (`REASON_TAGGED`) has that level. Used by heading_step and for the title.
    """
    headings = [e for e in elements if e.kind == Kind.HEADING]
    # Sizes are judged per slide deck (`slides.DECK_REASON`; every heading of a file with no
    # deck is in the one group None): a deck's title is level 1 and the rest of its headings
    # are under it, whatever sizes another deck uses.
    group_of = {e.id: slides.deck_number(e.reasons) for e in headings}
    level_of: dict[tuple[int | None, float], int] = {}
    below: dict[int | None, int] = {}
    for group in set(group_of.values()):
        in_group = [e for e in headings if group_of[e.id] == group]
        titled = any(slides.DECK_TITLE_REASON in e.reasons for e in in_group)
        sized = sorted(
            {
                round(e.font_size or 0.0, 1)
                for e in in_group
                if REASON_LARGER_FONT in e.reasons and slides.DECK_TITLE_REASON not in e.reasons
            },
            reverse=True,
        )
        level, top = int(titled), None  # the deck's title is level 1: the rest start at 2
        for size in sized:
            if top is None or size < top * (1 - HEADING_LEVEL_TOLERANCE) - 0.05:
                level, top = level + 1, size
            level_of[group, size] = level
        below[group] = level + 1
    depth = {e.id: _section_depth(e) for e in headings if REASON_LARGER_FONT not in e.reasons}
    shallowest = min((d for d in depth.values() if d), default=1)
    levels: dict[str, int] = {}
    for e in headings:
        group = group_of[e.id]
        if slides.DECK_TITLE_REASON in e.reasons:
            raw = 1
        elif REASON_LARGER_FONT in e.reasons:
            raw = level_of[group, round(e.font_size or 0.0, 1)]
        else:
            raw = below[group] + max(0, (depth[e.id] or shallowest) - shallowest)
        levels[e.id] = max(1, min(6, tagged_level(e) or outline.weak_level(e) or raw))
    section = None  # the level of the last heading that is not a letter of an index
    for e in sorted(headings, key=lambda e: e.order):
        if REASON_LETTER not in e.reasons:
            section = levels[e.id]
        elif section is not None:
            levels[e.id] = min(6, section + 1)  # one level under the section it heads
    return levels


def _section_depth(element: Element) -> int | None:
    """Depth of a real section number ("2.3.1" -> 3); list-style "1." does not count."""
    number = section_number(element.text or "")
    return number[0] if number and not number[1] else None


def _meaningful_title(raw: str | None) -> str | None:
    """Metadata title unless it is empty, a file name or a tool default ("Untitled")."""
    title = _clean(raw or "")
    if sum(c.isalpha() for c in title) < 3 or _JUNK_TITLE.search(title):
        return None
    return title[:MAX_HEADING_CHARS]


def _read_page(
    page: pymupdf.Page, document: _Document, producer: str
) -> tuple[list[_Row], list[_Path], page_type.PageTypeResult]:
    """The page's rows and paths, and its page type, from one text extraction and one
    drawings list (`pipeline.page_type` reads the same two). A page saved sideways with no
    /Rotate is turned first (`page_type.turn_page`): its rows are horizontal and every later
    step, here and in the readers that open the file again, sees the display frame."""
    turn = page_type.turn_page(page)
    fields = take_form_fields(page)  # before any read: what a field draws is not the page's
    raw = _raw_blocks(page)
    drawn = page.get_cdrawings()
    strokes = glyph_strokes.strokes(page, raw)
    # What a reviewer's annotations paint (an icon, a caret, a highlight) is not the page's art:
    # figures and handwriting are read without it. The hidden-text check is given the whole list
    # (`covers`): an annotation can be drawn over words to hide them.
    drawings = [*page_type.without_annotation_art(page, drawn), *strokes]
    covers = [*drawn, *strokes] if len(drawings) != len(drawn) + len(strokes) else None
    images = page_type.image_boxes(page)
    if page_type.too_complex(drawings):  # PAGE_MAX_PATHS: no path is read (`kept_reason`)
        kind = page_type.classify(
            page, raw, drawings, budget=document.ink, producer=producer, images=images,
            covers=covers,
        )  # fmt: skip
        kind.turn = turn
        rows = _page_rows(page, raw, kind.text.covered)
        return rows + _field_rows(fields, rows), [], kind
    seen = page_type.paint_visibility(drawings, images[0])  # once: the page type reads it too
    kind = page_type.classify(
        page, raw, drawings, budget=document.ink, producer=producer, images=images, seen=seen,
        covers=covers,
    )  # fmt: skip
    kind.turn = turn
    rows = _page_rows(page, raw, kind.text.covered)
    return rows + _field_rows(fields, rows), _page_paths(page, drawings, seen), kind


def _analyze_document(
    doc: pymupdf.Document,
    selected: list[int],
    page_math: Any,
    document: _Document,
) -> Layout:
    frags: dict[int, list[_Row]] = {}
    paths: dict[int, list[_Path]] = {}
    types: dict[int, page_type.PageTypeResult] = {}
    producer = page_type.producer_hint(doc)
    document.tex_made = producer == "tex"
    guard = _PageGuard()
    guard.pages = list(selected)

    def forget() -> None:
        # A page that failed in a step is not in the later ones: whatever the steps before
        # stored for it goes (`prepared`, `found` and `types` are indexed by page, and the
        # document-wide steps after this one read every page in them).
        for number in guard.failed:
            for stored in (frags, paths, types, found, prepared, sources):
                stored.pop(number, None)

    def keep_failed() -> None:
        for number in guard.failed:
            if number in selected:  # a failed reference page of the margin filter is not one
                kept[number] = page_type.PAGE_READ_FAILED

    found: dict[int, list[tables.PageTable]] = {}
    prepared: dict[int, tuple[list[_Row], Gutter, list[_Row]]] = {}
    sources: dict[int, say_once.PageSource] = {}
    for number in selected:
        with guard(number):
            rows, drawn, kind = _read_page(doc[number], document, producer)
            if _kept_whole(kind):
                # Nothing but the header and footer bands of its paths is read again (by the
                # margin filter, before this page is dropped): a page of tens of thousands of
                # strokes must not stay in memory as objects.
                height = doc[number].rect.height
                drawn = [p for p in drawn if _box_zone(p.bbox, height) is not None]
            frags[number], paths[number], types[number] = rows, drawn, kind
    guard.settle()
    deck = _read_deck(doc, guard.live(selected), frags, types, paths, guard)
    forget()
    guard.settle()
    document.slide_deck = deck is not None
    if deck is not None and deck.dropped:
        # An earlier step of a frame has nothing the frame's last page lacks: it is not read
        # again, so it costs no crop, no model call and no line of the output.
        for number in deck.dropped:
            frags.pop(number), paths.pop(number), types.pop(number)
        selected = [n for n in selected if n not in deck.dropped]
        guard.pages = list(selected)
    # Pages that failed are not neighbours either (`_reference_pages`): they would be read
    # again as a reference page.
    skip = {*(deck.dropped if deck else ()), *guard.failed}
    margins = _margin_filter(
        doc, guard.live(selected), frags, paths, skip, guard, document=document
    )
    forget()
    guard.settle()
    # Handwritten and scanned pages are kept as their page image: nothing on them is read
    # (there is no handwriting or scan conversion). On a typed page with handwriting on
    # it, the ink stays out of the figures; each region of it is cut out instead. A page kept
    # as an image that has some visible typed text and nothing hidden (a title slide on a
    # photo, a page of notes not analysed in time under its typed title, a watermark over a
    # photographed page) keeps that text; nothing drawn on it goes to the figure model. The
    # image page pass, when it reads such a page, replaces that text.
    kept: dict[int, str] = {}
    text_kept: set[int] = set()
    for number in guard.live(selected):
        with guard(number):
            # A thin text layer over a page-sized picture makes the picture the page's content
            # (`page_type.TEXT_NOT_COVERING`) unless the picture is the template's background
            # or one plain colour: a title slide on a repeated background is typed.
            result = types[number]
            if (
                page_type.TEXT_NOT_COVERING in result.reasons
                and result.kind in ("scan_no_text", "scan_ocr")
                and not _page_picture(doc[number], margins)
            ):
                result = types[number] = page_type.without_picture(result)
            if (reason := page_type.kept_reason(result)) is not None:
                kept[number] = reason
                if page_type.keeps_text(result):
                    text_kept.add(number)
    forget()
    for number in guard.failed:  # (`keep_failed` sets the reason for these, later)
        kept.pop(number, None)
        text_kept.discard(number)
    guard.on_purpose = {n for n in kept if n not in text_kept}
    guard.settle()
    for number in guard.live(selected):
        with guard(number):
            if number in kept and number not in text_kept:
                frags[number], paths[number], found[number] = [], [], []
                continue
            if number in text_kept:
                paths[number] = []
            if types[number].kind == "mixed_ink":
                ink = types[number].ink.ink_drawings
                paths[number] = [p for p in paths[number] if p.source not in ink]
            # A page kept as its image beside its text reads no drawings (no rules to judge a
            # grid by), and the detector is the slowest step on the pen-stroke pages these are.
            # `previous` is the page before's tables, unless that page failed: a page that is
            # now a picture takes no row the break split.
            unclaimed = frags[number]
            found[number] = (
                []
                if number in text_kept
                else _claim_tables(
                    doc[number],
                    number,
                    frags,
                    paths,
                    found.get(number - 1) if guard.ok(number - 1) else None,
                    document=document,
                )
            )
            sources[number] = say_once.PageSource()
            sources[number].leave_out("hidden", types[number].text.left_out)
            # the rows a table claimed are said by the table, or by the text it becomes
            still = {id(f) for f in frags[number]}
            for row in _merge_baselines([f for f in unclaimed if id(f) not in still], None):
                sources[number].add_row(row.chars)
            prepared[number] = _prepare_rows(
                doc[number],
                frags[number],
                margins,
                paths[number],
                sources[number],
                document=document,
            )
            # Axes with tick labels, an axis title or a curve from their corner are a plot's:
            # the rows the header and footer rules dropped count (tick labels in the bands).
            # Rulers with no labels whose ticks are an answer field's cells are not rulers.
            page_rows = [*prepared[number][0], *prepared[number][2]]
            _answer_fields(paths[number], page_rows)
            _mark_plots(paths[number], page_rows)
    forget()
    guard.settle()
    ctx = _body_and_pitch({n: rows for n, (rows, _, _) in prepared.items()})
    ctx.notes = named_links.read(doc, list(selected)) or None
    ctx.region_tree = document.region_tree
    document.compounds, document.whole_words = hyphens.document_spellings(
        row.text for rows, _, _ in prepared.values() for row in rows
    )
    _list_markers({n: rows for n, (rows, _, _) in prepared.items()}, paths, ctx, guard)
    _letter_rows({n: rows for n, (rows, _, _) in prepared.items()}, ctx)
    forget()
    guard.settle()
    label_keys = _label_keys(
        doc, list(prepared), prepared, paths, ctx, margins, guard, document=document
    )
    forget()
    guard.settle()
    pages: list[_PageBlocks] = []
    for n in selected:
        read: _PageBlocks | None = None
        if guard.ok(n) and (n not in kept or n in text_kept):
            with guard(n):
                read = _page_blocks(
                    doc[n],
                    n,
                    prepared[n][0],
                    prepared[n][1],
                    ctx,
                    paths[n],
                    margins,
                    found[n],
                    page_type.ink_regions(types[n]),
                    page_sized=not INK_REASONS & set(types[n].reasons),
                    little_text=types[n].text.visible_chars < page_type.TEXT_MIN,
                    strokes=_light_ink(types[n]),
                    dropped=prepared[n][2],
                    ink_sources=types[n].ink.ink_drawings,
                    mixed_ink=types[n].kind == "mixed_ink",
                    label_keys=label_keys,
                    covers=_covered_points(doc[n], types[n]),
                    page_math=page_math,
                    source=sources.get(n),
                    document=document,
                )
        pages.append(read if read is not None else _kept_page(doc[n], n))
    forget()
    keep_failed()
    guard.settle()
    _glossary_entries(pages, document=document)
    _lead_entries(pages, document=document)
    _settle_bold_letters(pages)
    _promote_numbered_lines(pages, ctx)
    _mark_run_in_labels(pages, ctx)
    _join_page_splits(pages, ctx)
    block_ids: dict[int, str] = {}
    elements = outline.apply(
        _to_elements(pages, ctx, block_ids, guard, document=document), outline.read(doc), selected
    )
    keep_failed()
    guard.settle()
    # A page whose blocks could not be written as elements is its picture, as the others
    # that failed (the notices below read only the pages that stay).
    pages = [p if guard.ok(p.number) else _kept_page(doc[p.number], p.number) for p in pages]
    footnote_marks: dict[str, _FootnoteMark] = {}
    notices = _layout_notices(pages, ctx, block_ids, footnote_marks)
    elements = _contents_links(_footnote_links(elements, footnote_marks))
    # The safety net (independent of the page types; docs/layout.md "The safety net"): a
    # page keeps its page image, with a decision, when something painted on it was read
    # into nothing. The elements read from it stay.
    boxes: dict[int, list[BBox]] = {}
    for e in elements:
        boxes.setdefault(e.page, []).append(e.bbox)
    for page in pages:  # the later part of a paragraph joined over a page break is read
        boxes.setdefault(page.number, []).extend(b.bbox for b in page.blocks if b.joined)
    for page in pages:
        n = page.number
        if n in kept:
            continue
        with guard(n):
            if n not in boxes:
                unread = _painted(doc[n], paths[n], margins=margins)
            else:
                left_out = bool(page.left_out) and types[n].kind != "mixed_ink"
                unread = left_out or page.unread or _unread_ink(types[n], boxes[n], page.size)
            # A picture covering the page under its typed text (a scan with a stamp, a slide
            # on a full-bleed photo, ink drawn on a transparent overlay): never a figure, and
            # not to be dropped as a background unless it is one.
            if unread or _page_picture(doc[n], margins):
                kept[n] = page_type.UNREAD_PAGE
    keep_failed()  # these keep the elements read from them, with the picture beside them
    guard.settle()
    if deck is not None:
        elements = slides.finish(
            elements, deck, {p.number: p.size for p in pages}, (REASON_LARGER_FONT,)
        )
    title = _meaningful_title(doc.metadata.get("title") if doc.metadata else None)
    if title is None:
        levels = heading_levels(elements)
        title = next((e.text for e in elements if levels.get(e.id) == 1), None)
    return Layout(
        elements=elements,
        page_count=doc.page_count,
        pages=list(selected),
        page_sizes=[p.size for p in pages],
        title=title,
        lang=_document_lang(doc),
        notices=(
            notices
            + _hidden_text_notices(types, kept)
            + _ink_notices(types, kept)
            + _turned_notices(types)
        ),
        page_types=types,
        kept_pages=dict(sorted(kept.items())),
        sources=sources,
    )


def _read_deck(
    doc: pymupdf.Document,
    selected: list[int],
    frags: dict[int, list[_Row]],
    types: dict[int, page_type.PageTypeResult],
    paths: dict[int, list[_Path]],
    guard: _PageGuard,
) -> slides.Deck | None:
    """The slide deck the selected pages make up (`slides.detect`), or None. A page kept whole
    as its image is shown to it with no text, so it is never taken for an earlier step. A
    page whose view raises (`guard`) is left out of the deck, as a page that was not selected."""
    views: list[slides.PageView] = []
    for number in selected:
        with guard(number):
            page = doc[number]
            whole = _kept_whole(types[number])
            rows = (
                []
                if whole
                else [
                    slides.RowView(r.clean, r.bbox, r.size, r.vertical)
                    for r in frags[number]
                    if r.clean.strip()
                ]
            )
            rect = page.rect
            views.append(
                slides.PageView(
                    number,
                    rect.width,
                    rect.height,
                    page_labels.pdf_label(page),
                    rows,
                    slides.LazyMarks(partial(_marks_of, doc, number, paths.get(number, []), guard)),
                )
            )
    creator = doc.metadata.get("creator") if doc.metadata else None
    deck = slides.detect(creator, views)
    if any(not guard.ok(v.number) for v in views):
        # A page whose marks raised, when `detect` asked for them, is left out of the deck as a
        # page whose view raised (its marks were read with the view before).
        deck = slides.detect(creator, [v for v in views if guard.ok(v.number)])
    return deck


def _marks_of(
    doc: pymupdf.Document, number: int, paths: list[_Path], guard: _PageGuard
) -> list[slides.Mark]:
    """The page's slide marks, read when `detect` first needs them (`slides.LazyMarks`); none
    when they cannot be read (`guard` has the page then, and `_read_deck` runs `detect` again
    without it). The page is opened here, so a long file's pages are not all held open while
    its views wait."""
    marks: list[slides.Mark] = []
    with guard(number):
        page = doc[number]
        marks = _slide_marks(page, paths, page.rect.height)
    return marks


def _slide_marks(page: pymupdf.Page, paths: list[_Path], height: float) -> list[slides.Mark]:
    """Every raster image on the slide (by a digest of its pixels) and every vector drawing
    on the page (by its colours and the shape of its path, `_drawing_key`), for the test that
    a step's pictures are all on the frame's last page. Drawings near the top and bottom edges
    count too (a figure can sit there); only a theme's thin progress bar is left out, since it
    grows on every page and would make every step differ."""
    marks: list[slides.Mark] = []
    display = _Display.of(page)
    for info in _image_infos(page, hashes=True):
        if info["bbox"][2] - info["bbox"][0] <= 0 or info["bbox"][3] - info["bbox"][1] <= 0:
            continue  # an image with no area shows nothing (an overlay's hidden item)
        digest = info.get("digest")
        key = (
            digest.hex()
            if isinstance(digest, bytes)
            else f"{info.get('width')}x{info.get('height')}"
        )
        marks.append(slides.Mark("image", key, display.box(info["bbox"])))
    drawings = page_type.without_annotation_art(page, page.get_cdrawings()) if paths else []
    width = page.rect.width
    for path in paths:
        centre = (path.bbox[1] + path.bbox[3]) / 2
        in_body = 0.2 * height <= centre <= (1 - NUMBER_BODY_BAND) * height
        thin_bar = path.bbox[3] - path.bbox[1] <= PROGRESS_BAR_PT and (
            path.bbox[2] - path.bbox[0] >= 0.25 * width
        )
        if not in_body and thin_bar:
            continue  # a theme's progress bar grows on every page, steps included
        marks.append(slides.Mark("drawing", _drawing_key(path, drawings), path.bbox))
    return marks


NUMBER_BODY_BAND = 0.12  # the footline's share of a slide (bare slide numbers live there)
PROGRESS_BAR_PT = 4.0  # a headline or footline bar at most this tall is furniture


def _drawing_key(path: _Path, drawings: list[dict[str, Any]]) -> str:
    """What a drawing looks like, for "is this drawing also on the last page": its stroke and
    fill colours and the shape of its path (every point, to the point), so two different
    drawings in the same box never pass as one. Falls back to the path's summary when the
    raw drawing is not known (`source` -1)."""
    summary = f"{path.kind}{int(path.curved)}{int(path.filled)}"
    if not 0 <= path.source < len(drawings):
        return summary + "".join(f"{round(c, 2)}" for c in path.colour)
    raw = drawings[path.source]
    parts = [summary, repr(raw.get("fill")), repr(raw.get("color")), repr(raw.get("width"))]
    for item in raw.get("items", ()):
        parts.append(str(item[0]))
        for value in item[1:]:
            if isinstance(value, (tuple, list)):
                parts.append(",".join(f"{round(float(v))}" for v in _flat(value)))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _flat(value: Any) -> list[float]:
    """The numbers of a drawing item's point, rect or quad, in order."""
    if isinstance(value, (int, float)):
        return [float(value)]
    out: list[float] = []
    for part in value:
        out.extend(_flat(part))
    return out


def _kept_whole(kind: page_type.PageTypeResult) -> bool:
    """The page is kept as its page image and nothing on it is read (a kept page that has
    visible typed text keeps that text, `text_kept` in `_analyze_document`)."""
    return page_type.kept_reason(kind) is not None and not page_type.keeps_text(kind)


def _kept_page(page: pymupdf.Page, number: int) -> _PageBlocks:
    """A page kept as its page image: no blocks."""
    return _PageBlocks(number, (round(page.rect.width, 2), round(page.rect.height, 2)), [])


def _unread_ink(
    kind: page_type.PageTypeResult, boxes: list[BBox], size: tuple[float, float]
) -> bool:
    """INK_MIXED_STROKES or more of the page's handwriting-like strokes (the page type's
    ink analysis, clean or rejected) lie outside every element read from the page and
    outside the header and footer bands: writing layout made nothing of (a figure, a
    table and a cut-out handwriting region each hold their own)."""
    if len(kind.ink.stroke_boxes) < page_type.INK_MIXED_STROKES:
        return False
    return len(_loose_strokes(kind.ink.stroke_boxes, boxes, size, margins=False)) >= (
        page_type.INK_MIXED_STROKES
    )


def _light_ink(kind: page_type.PageTypeResult) -> list[BBox]:
    """The pen strokes of a typed page (a line of visible text or more) too light to make
    it mixed_ink, clean or not (two pen widths in one note fail the clean test): the
    strokes `_page_blocks` cuts out when no figure holds them. None for other pages
    (mixed_ink cuts out all its strokes; a page with less text has the safety net,
    `_painted`)."""
    if kind.kind == "mixed_ink" or kind.text.visible_chars < page_type.TEXT_MIN:
        return []
    if INK_REASONS & set(kind.reasons):
        return []  # rejected or not analysed: the safety net's (`_unread_ink`, `left_out`)
    return kind.ink.stroke_boxes


def _page_picture(page: pymupdf.Page, margins: _MarginFilter) -> bool:
    """A picture covers the page (FULL_PAGE_FRACTION of it) and shows something: it is not
    plain (`_plain_image`: one colour, a smooth gradient, nothing seen) and not the
    template's background repeated on the document's pages (`_MarginFilter.backgrounds`).
    Such a picture is never a figure (`_raster_figures`), so the page keeps its image:
    a scan with 40 or more visible characters (a stamp, a header) and no hidden layer, a
    slide on a full-bleed photo, a typed page under a transparent picture of hand marks."""
    for xref in _page_pictures(page):
        if xref and (xref in margins.backgrounds or _plain_image(page.parent, xref)):
            continue
        return True
    return False
