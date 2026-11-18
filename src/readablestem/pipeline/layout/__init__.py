"""PDF -> document layout: elements in reading order. Deterministic; no model calls.

How a page is read (thresholds and known failure modes: docs/layout.md):

1. Characters come from PyMuPDF's raw text dictionary with accurate glyph boxes, so the
   numerator and denominator rows of a fraction do not overlap. All geometry is turned
   into display coordinates (the page's /Rotate applied), the space `crop` clips in.
2. Decoration is dropped: running headers/footers (text in the top/bottom 7 % of the
   page that repeats on at least half of the pages, digits normalised, unless it is
   larger or bolder than body text and changes from page to page; and a small line in the
   top/bottom 10 % at the same place on at least half of the left (or right) pages, whatever
   its words, so a head whose text changes with the section goes too), bare page numbers,
   images and drawings repeated in those bands (logos), and watermarks (diagonal text in
   a large font, or rotated text repeated at the same place).
3. Columns: a vertical white gap in the x-coverage of narrow lines splits a page into
   two columns. Lines that cross it are full-width and split the reading flow, and so are
   narrow lines at a section break across both columns (nothing beside them, both
   columns resuming below them at the same height).
4. PyMuPDF lines on one baseline are merged into rows (it splits justified text and
   inline maths into several "lines"). Rows are put in reading order and grouped into
   runs: paragraphs, list items, headings, display equations, captions. PyMuPDF's own
   blocks are not trusted: they glue captions onto the next paragraph and a fraction's
   denominator onto the text below it. A row starting with a bullet, a drawn bullet or
   an ordered marker confirmed by a sibling starts a list item (`_list_markers`).
5. Equations: rows that score as maths (`parse.equation_score`) and contain no prose word
   are display-equation rows. Rows on one fraction bar or radical, or side by side on one
   baseline, form lines; stacked lines form one display equation when one continues the
   other (starts with "="), is not a whole equation, or has its relation aligned with it
   (`_equation_regions`). An equation number beside it ("(2.4)", "Eq. 2.4", or a bare
   "2.4" to its right) becomes `Element.equation_label`, outside the box and the text.
   Inside paragraphs and list items, runs of maths characters (maths fonts, math-italic
   letters, Greek, maths symbols) or an italic single letter next to an operator become
   inline equations, and the text gets `core.types.inline_marker(id)` in their place.
6. Figures: raster images and clusters of vector drawings, minus equation rules, text
   underlines, page borders and boxes or tables drawn around text (rounded or not).
   Short labels inside or right next to a figure belong to it; running text never does,
   and an image with running text on it is a background, not a figure. A "Figure N"
   caption within reach is attached.
7. Text is document content: the inline-marker brackets are replaced by look-alikes
   wherever they occur in it, so only real inline equations have markers.
8. Slide decks (`pipeline/slides.py`): a Beamer file, or slide-shaped pages that share page
   labels, is read frame by frame: overlay steps that a frame's last page repeats are not
   read, frame titles are headings, the headline and slide numbers are dropped, and short
   two-column frames are read left then right (docs/layout.md "Slide decks").

Untrusted input: MuPDF is C code parsing bytes from the internet, and some damaged files
make it abort the whole process (a glibc double free while rendering). Before a page is
extracted or rendered in this process, `analyze` and `crop` have a child process do the
same once (`_probe_pages`); a child that dies or hangs makes the file
`LayoutError("unreadable")`. Before that, `analyze` counts how far the selected pages'
drawing instructions inflate and refuses them over the content budget
(`core.content_budget`, `LayoutError("content_too_large")`), since every step parses them.
MuPDF's own error and warning printing is switched off: it quotes content-stream tokens
and resource names from the PDF, which must not reach the logs.

Thread safety: PyMuPDF is not thread-safe, so every PyMuPDF call here runs under one
module lock. `crop` keeps a small cache of opened documents keyed by the PDF bytes, which
expires after `DOCUMENT_CACHE_TTL_S` of disuse; call `release(pdf_bytes)` when a document
is finished so its content does not stay in memory even that long.
"""

from __future__ import annotations

from readablestem.pipeline.layout.access import (
    _CACHE,
    _LOCK,
    crop,
    crop_line,
    line_across,
    page_ink,
    release,
    render_page,
)
from readablestem.pipeline.layout.blocks import (
    REASON_BOLD_BODY,
    REASON_LARGER_FONT,
    REASON_LIST_NUMBER,
    REASON_NUMBERED,
    REASON_NUMBERED_LINE,
    REASON_OPTION_LABELS,
    REASON_RTL_ORDER,
    _line_break,
    _strip_teacher_tags,
    _wraps_here,
)
from readablestem.pipeline.layout.context import (
    Layout,
    LayoutError,
    _Document,
)
from readablestem.pipeline.layout.display import (
    _beside_row,
    _equation_regions,
    _Line,
    _mark_case_rows,
    _merge_continued_regions,
)
from readablestem.pipeline.layout.document import (
    REASON_IMAGE_HEADING,
    REASON_TAGGED,
    _meaningful_title,
    analyze,
    heading_levels,
    tagged_level,
)
from readablestem.pipeline.layout.drawings import (
    MIN_IMAGE_PT,
    _page_paths,
)
from readablestem.pipeline.layout.elements import (
    _footnote_links,
)
from readablestem.pipeline.layout.figures import (
    _cluster,
    _cluster_scan,
)
from readablestem.pipeline.layout.glyphs import (
    _BOLD_FONT,
    _TEXT_MAP,
    _Char,
    _regular_letters,
    _union,
    glyph_style,
)
from readablestem.pipeline.layout.inline_runs import (
    _classify_row,
    _inline_runs,
    _join_signatures,
    _trim_run,
)
from readablestem.pipeline.layout.joins import (
    REASON_NUMBERED_UNCONFIRMED,
    REASON_RUN_IN_LABEL,
)
from readablestem.pipeline.layout.notices import (
    _FootnoteMark,
)
from readablestem.pipeline.layout.order import (
    _reading_order,
)
from readablestem.pipeline.layout.page import (
    REASON_HALF_FRACTION,
    _loose_strokes,
)
from readablestem.pipeline.layout.rows import (
    FormField,
    Glyph,
    Strike,
    _page_rows,
    line_glyphs,
    page_glyphs,
    page_strikes,
    take_form_fields,
)
from readablestem.pipeline.layout.shared import (
    REASON_DRAWING_FRAGMENTS,
    _CharInfo,
    _Inline,
    _Region,
    _Row,
)
from readablestem.pipeline.layout.tables import (
    TABLE_PREFILTER,
    _cell_maths,
)

__all__ = [
    "MIN_IMAGE_PT",
    "REASON_BOLD_BODY",
    "REASON_DRAWING_FRAGMENTS",
    "REASON_HALF_FRACTION",
    "REASON_IMAGE_HEADING",
    "REASON_LARGER_FONT",
    "REASON_LIST_NUMBER",
    "REASON_NUMBERED",
    "REASON_NUMBERED_LINE",
    "REASON_NUMBERED_UNCONFIRMED",
    "REASON_OPTION_LABELS",
    "REASON_RTL_ORDER",
    "REASON_RUN_IN_LABEL",
    "REASON_TAGGED",
    "TABLE_PREFILTER",
    "_BOLD_FONT",
    "_CACHE",
    "_LOCK",
    "_TEXT_MAP",
    "FormField",
    "Glyph",
    "Layout",
    "LayoutError",
    "Strike",
    "_Char",
    "_CharInfo",
    "_Document",
    "_FootnoteMark",
    "_Inline",
    "_Line",
    "_Region",
    "_Row",
    "_beside_row",
    "_cell_maths",
    "_classify_row",
    "_cluster",
    "_cluster_scan",
    "_equation_regions",
    "_footnote_links",
    "_inline_runs",
    "_join_signatures",
    "_line_break",
    "_loose_strokes",
    "_mark_case_rows",
    "_meaningful_title",
    "_merge_continued_regions",
    "_page_paths",
    "_page_rows",
    "_reading_order",
    "_regular_letters",
    "_strip_teacher_tags",
    "_trim_run",
    "_union",
    "_wraps_here",
    "analyze",
    "crop",
    "crop_line",
    "glyph_style",
    "heading_levels",
    "line_across",
    "line_glyphs",
    "page_glyphs",
    "page_ink",
    "page_strikes",
    "release",
    "render_page",
    "tagged_level",
    "take_form_fields",
]
