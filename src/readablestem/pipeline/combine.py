"""One file from the parts of a long source (T3-3 converts a long PDF in parts of at most 30
pages, each its own document).

`combine` takes the finished parts, in any order, with each part's elements, decisions and
pictures and the pages of the source it holds, and writes one HTML file with the same
assembler as any other: so there is one contents list built from the headings of the whole
(and none when a part brought the PDF's own contents page), page markers that continue from
part to part, one student note, and the equations, figures and tables written exactly as in
each part. What changes between a part and the whole is only addresses:

- **Page order.** A part's pages are numbered from the start of the part's own PDF. Each
  page is renumbered to its place in the source (`Part.pages`), so reading order, the page
  markers and the "Page N of the original PDF" notes agree with the source.
- **Ids.** Every part has a `p1-e0`. An id `p{page}-e{n}` is rewritten to the source page
  (`p31-e0`), which no other part can have; an id of another shape gets the part's number
  in front. The same rewrite is applied everywhere an id is named: the element and its
  fix, its container, links and cross-references, the equation markers inside text (their
  length changes, so the offsets of links, colour runs, raised and lowered runs and a term's
  end are moved with them) and the pictures.
- **Gaps.** Source pages between the first and last part that no part here holds (a part
  deleted, or not finished) are named in the student note as left out of this file, so the
  file never reads as complete when it is not.

Nothing here touches the database or the PDF; `web.combine` loads the parts of one person.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from readablestem.core.types import (
    INLINE_MARKER_RE,
    ColourRun,
    DocumentResult,
    Element,
    Fix,
    HeadingFix,
    Link,
    ScriptRun,
    inline_marker,
)
from readablestem.pipeline import assemble

_PAGE_ID = re.compile(r"p(\d+)-(.+)")


class CombineError(ValueError):
    """The parts cannot be joined. `code` is a category, never document text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Part:
    """A finished part: its result, its pictures by element id, and `pages`, the 0-based
    page of the source that each of the part's own pages (0, 1, ...) is."""

    result: DocumentResult
    crops: dict[str, bytes]
    pages: Sequence[int]


@dataclass
class Joined:
    """The parts as one document's inputs to `assemble.render_html`."""

    elements: list[Element]
    fixes: list[Fix]
    crops: dict[str, bytes]
    page_labels: dict[int, str]
    footnote_ids: list[str]
    omitted_pages: list[int]
    lang: str


def _id_mapper(part: Part, number: int) -> Callable[[str], str]:
    local = {local: source for local, source in enumerate(part.pages)}

    def map_id(old: str) -> str:
        match = _PAGE_ID.fullmatch(old)
        if match and (int(match[1]) - 1) in local:
            return f"p{local[int(match[1]) - 1] + 1}-{match[2]}"
        return f"s{number}-{old}"

    return map_id


def _text_map(text: str, map_id: Callable[[str], str]) -> tuple[str, Callable[[int], int]]:
    """`text` with the ids inside its equation markers rewritten, and the function that
    moves an offset into the old text to the same place in the new one."""
    ends: list[tuple[int, int]] = []  # (end of a marker in the old text, change in length)
    out: list[str] = []
    last = 0
    for match in INLINE_MARKER_RE.finditer(text):
        new = inline_marker(map_id(match.group(1)))
        out += [text[last : match.start()], new]
        last = match.end()
        ends.append((match.end(), len(new) - (match.end() - match.start())))
    out.append(text[last:])

    def move(offset: int) -> int:
        return offset + sum(delta for end, delta in ends if end <= offset)

    return "".join(out), move


def _element(element: Element, map_id: Callable[[str], str], pages: Sequence[int], shift: int):
    if element.page >= len(pages):
        raise CombineError("page_outside_part")
    update: dict[str, object] = {
        "id": map_id(element.id),
        "page": pages[element.page],
        "order": element.order + shift,
        "parent_id": map_id(element.parent_id) if element.parent_id else None,
    }
    move: Callable[[int], int] = lambda offset: offset  # noqa: E731
    if element.text:
        update["text"], move = _text_map(element.text, map_id)
    if element.caption_marked:
        update["caption_marked"] = _text_map(element.caption_marked, map_id)[0]
    if element.term_end is not None:
        update["term_end"] = move(element.term_end)
    update["links"] = [
        Link(
            start=move(k.start),
            end=move(k.end),
            kind=k.kind,
            href=k.href,
            target_id=map_id(k.target_id) if k.target_id else None,
        )
        for k in element.links
    ]
    update["colour_runs"] = [
        ColourRun(start=move(r.start), end=move(r.end), rgb=r.rgb) for r in element.colour_runs
    ]
    update["script_runs"] = [
        ScriptRun(start=move(r.start), end=move(r.end), kind=r.kind) for r in element.script_runs
    ]
    return element.model_copy(update=update)


def _fix(fix: Fix, map_id: Callable[[str], str]) -> Fix:
    update: dict[str, object] = {"element_id": map_id(fix.element_id)}
    if fix.heading is not None:
        update["heading"] = HeadingFix(
            level=fix.heading.level, text=_text_map(fix.heading.text, map_id)[0]
        )
    if fix.group_key:
        update["group_key"] = None  # the review screen's grouping; the whole has no review
    return fix.model_copy(update=update)


def join(parts: Sequence[Part]) -> Joined:
    """The parts as one document, in the order of their pages. Raises `CombineError`:
    "no_parts", "pages_overlap" (a source page in two parts), "page_outside_part" (an
    element on a page the part's page list does not have), "ids_clash"."""
    if not parts:
        raise CombineError("no_parts")
    ordered = sorted(parts, key=lambda p: min(p.pages, default=0))
    seen = Counter(page for part in ordered for page in part.pages)
    if any(count > 1 for count in seen.values()):
        raise CombineError("pages_overlap")

    elements: list[Element] = []
    fixes: list[Fix] = []
    crops: dict[str, bytes] = {}
    labels: dict[int, str] = {}
    footnotes: list[str] = []
    omitted: set[int] = set()
    shift = 0
    for number, part in enumerate(ordered, start=1):
        map_id = _id_mapper(part, number)
        mine = [_element(e, map_id, part.pages, shift) for e in part.result.elements]
        shift = max((e.order for e in mine), default=shift - 1) + 1
        elements += mine
        first: set[str] = set()
        for fix in part.result.fixes:  # the first fix for an element wins, as everywhere
            if fix.element_id not in first:
                first.add(fix.element_id)
                fixes.append(_fix(fix, map_id))
        crops |= {map_id(element_id): png for element_id, png in part.crops.items()}
        for local, label in part.result.page_labels.items():
            if local < len(part.pages):
                labels[part.pages[local]] = label
        footnotes += [
            map_id(n.element_id)
            for n in part.result.notices
            if n.code == "footnote" and n.element_id
        ]
        omitted |= {part.pages[p] for p in part.result.omitted_pages if p < len(part.pages)}
    ids = [e.id for e in elements]
    if len(ids) != len(set(ids)):
        raise CombineError("ids_clash")

    held = set(seen)
    omitted |= set(range(min(held), max(held) + 1)) - held  # parts that are not here
    langs = Counter(part.result.lang for part in ordered)
    return Joined(
        elements=elements,
        fixes=fixes,
        crops=crops,
        page_labels=labels,
        footnote_ids=footnotes,
        omitted_pages=sorted(omitted),
        lang=langs.most_common(1)[0][0],
    )


def combine(
    parts: Sequence[Part],
    *,
    title: str,
    contact: str | None = None,
    math_alttext: bool | None = None,
) -> str:
    """The one HTML file for `parts` (see the module docstring). `title` is written as typed,
    like a title the instructor chose."""
    joined = join(parts)
    return assemble.render_html(
        title=title,
        title_set=True,
        lang=joined.lang,
        elements=joined.elements,
        fixes=joined.fixes,
        crops=joined.crops,
        contact=contact,
        math_alttext=math_alttext,
        omitted_pages=joined.omitted_pages or None,
        page_labels=joined.page_labels,
        footnote_ids=joined.footnote_ids,
    )
