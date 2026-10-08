"""What is on a page, coarsely: text, only a picture of a page (a scan), or nothing.

A scanned page is one image covering the page and no text layer. The layout step drops
an image that large as a page background (OCR is out of scope), so without this check a
scanned page silently produced nothing and a fully scanned PDF came back as an empty
"finished" document. This module is used twice:

- the upload probe (`web.pdf_probe`) refuses a PDF in which no page has text or
  anything else to convert, before the page quota is spent;
- `pipeline.run` flags the image-only pages of an otherwise convertible PDF, keeps each
  one in the output as an image marked "not converted", and records their numbers on
  `DocumentResult.image_only_pages`.

Content-free: it returns page kinds, never text. PyMuPDF only; the caller decides which
thread or process this runs in (it is not thread-safe).
"""

from __future__ import annotations

from enum import StrEnum

import pymupdf

# The share of the page an image must cover to count as "a picture of the page". Same
# threshold as layout's page-background rule, so the two agree on what was dropped.
FULL_PAGE_FRACTION = 0.85


class PageKind(StrEnum):
    TEXT = "text"  # has a text layer: the normal case
    IMAGE_ONLY = "image_only"  # no text, one image covering the page: a scan
    GRAPHICS = "graphics"  # no text, but smaller images or vector drawings (figures)
    BLANK = "blank"  # nothing at all


def page_kind(page: pymupdf.Page) -> PageKind:
    """Classify one page (see `PageKind`)."""
    if page.get_text("text").strip():
        return PageKind.TEXT
    # get_image_info boxes are in unrotated page coordinates.
    unrotated = (page.rect * page.derotation_matrix).normalize()
    area = unrotated.width * unrotated.height
    images = page.get_image_info()
    for info in images:
        rect = pymupdf.Rect(info["bbox"]) & unrotated
        if not rect.is_empty and rect.width * rect.height >= FULL_PAGE_FRACTION * area:
            return PageKind.IMAGE_ONLY
    if images or page.get_drawings():
        return PageKind.GRAPHICS
    return PageKind.BLANK


def page_kinds(doc: pymupdf.Document, pages: list[int] | None = None) -> dict[int, PageKind]:
    """`page_kind` for the given 0-based pages (all by default)."""
    numbers = range(doc.page_count) if pages is None else pages
    return {number: page_kind(doc[number]) for number in numbers}


def nothing_to_convert(kinds: dict[int, PageKind]) -> str | None:
    """Why a whole document cannot be converted, or None if some page can.

    "scanned": no page has text or figures, and at least one is a picture of a page.
    "no_content": every page is blank.
    """
    values = set(kinds.values())
    if not values or values & {PageKind.TEXT, PageKind.GRAPHICS}:
        return None
    return "scanned" if PageKind.IMAGE_ONLY in values else "no_content"
