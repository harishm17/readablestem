"""PDF -> document layout: elements in reading order (WP2 implements).

Builds on `pipeline.parse` (PyMuPDF text dictionary). Responsibilities:
- headings: font-size/weight clustering into levels 1-6 (`Kind.HEADING`, `Element.font_size`),
- paragraphs (`Kind.PARAGRAPH`), merged per block, hyphenation repaired,
- equations (`Kind.EQUATION`): text-line candidates from `parse.equation_score`, plus
  vector-drawn maths (fraction bars, radicals via `page.get_drawings()`), merged with
  neighbouring math lines into one region; `display` = "block" when the region stands
  alone on its line(s), else "inline",
- figures (`Kind.FIGURE`): raster images and clusters of vector drawings, with the nearby
  "Figure N" caption text in `Element.caption`,
- reading order: column detection (1-2 columns), top-to-bottom within a column,
- running headers/footers and page numbers dropped (repeated text in page margins).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from a11y_stem.core.types import Element


class LayoutError(ValueError):
    """The PDF cannot be processed. `code` is a category, safe to log."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # "unreadable" | "encrypted" | "too_many_pages" | "no_pages"


@dataclass
class Layout:
    elements: list[Element]
    page_count: int  # pages in the file
    pages: list[int]  # 0-based pages analysed
    page_sizes: list[tuple[float, float]] = field(default_factory=list)  # per analysed page
    title: str | None = None  # PDF metadata title, else the first level-1 heading
    lang: str = "en"  # PDF /Lang if present, else "en"


def analyze(
    pdf_bytes: bytes, *, pages: list[int] | None = None, max_pages: int | None = None
) -> Layout:
    """Elements of the given 0-based pages (all by default), ids "p{page+1}-e{index}"."""
    raise NotImplementedError("WP2")


def crop(
    pdf_bytes: bytes,
    element: Element,
    *,
    dpi: int = 200,
    pad: float = 2.0,
    max_side_px: int = 2048,
) -> bytes:
    """PNG of the element's region (padded), long side capped at `max_side_px`."""
    raise NotImplementedError("WP2")
