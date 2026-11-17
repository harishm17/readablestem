"""What the PDF itself says about its footnotes: named destinations and the links to them.

LaTeX's hyperref writes a named destination for every footnote ("Hfootnote.3", the top of
the footnote's line) and a link from the footnote's mark in the text to it. A file that has
them names its footnotes, so layout reads them from the file instead of guessing from a
raised digit and a place on the page (`layout._mark_notes`, `layout._layout_notices`).

This is the one place that reads destinations and the links that point at them from PyMuPDF
(links to named destinations of any other kind would be read here too, not by their own
code). Coordinates are display coordinates (rotation applied), as the rows of layout use.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

BBox = tuple[float, float, float, float]

NOTE_WORD = "footnote"  # a destination whose name holds this names a footnote
MAX_NAMES = 5000  # destinations kept: a crafted name tree is not read without end


@dataclass(frozen=True)
class Destination:
    name: str
    point: tuple[float, float]  # where the destination points on its page


@dataclass(frozen=True)
class Reference:
    name: str  # the destination the link goes to
    box: BBox  # the link's rectangle on its page


@dataclass
class NamedNotes:
    """The footnote destinations of each page (0-based) and the links to them."""

    destinations: dict[int, list[Destination]] = field(default_factory=dict)
    references: dict[int, list[Reference]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.destinations)


def read(doc: pymupdf.Document, pages: list[int]) -> NamedNotes:
    """The footnote destinations on `pages` and the links on them that go to such a
    destination (a mark's page may differ from its footnote's: its links are read from the
    `pages` given). A file with no such destination gives an empty `NamedNotes`. The caller
    holds layout's lock."""
    notes = NamedNotes()
    try:
        named = {n: d for n, d in doc.resolve_names().items() if NOTE_WORD in n.lower()}
        selected = set(pages)
        wanted: set[str] = set()
        for name, dest in sorted(named.items())[:MAX_NAMES]:
            page, to = dest.get("page"), dest.get("to")
            if page not in selected or to is None or not 0 <= page < doc.page_count:
                continue
            target = doc[page]
            shown = pymupdf.Point(to) * target.transformation_matrix * target.rotation_matrix
            notes.destinations.setdefault(page, []).append(
                Destination(name, (float(shown.x), float(shown.y)))
            )
            wanted.add(name)
        if wanted:
            for number in pages:
                for raw in doc[number].get_links():
                    if raw.get("nameddest") in wanted and raw.get("from") is not None:
                        box = pymupdf.Rect(raw["from"]) * doc[number].rotation_matrix
                        notes.references.setdefault(number, []).append(
                            Reference(raw["nameddest"], (box.x0, box.y0, box.x1, box.y1))
                        )
    except (RuntimeError, pymupdf.mupdf.FzErrorBase):  # a broken name tree: no names
        return NamedNotes()
    return notes
