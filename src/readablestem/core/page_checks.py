"""The lines a job page can show about what was checked and what was not, and the one place
that makes them.

A line is a `core.types.Notice`: a code, where it is (a page, or the whole document), how many
times, and a tier. The job page's summary ("Everything was converted", "Nothing in this file
needed your decision") is honest only if everything that was measured can produce a line, and
the code of each line is listed here, once, with its tier and where it is listed. A check that
is not in the table cannot be written: `make` refuses it.

The tier is the notice's `review`, in the vocabulary of `core.triage`:

- "required": not handled in this file, the instructor decides or confirms before sharing;
  `web.states` says which of them hold a finished job at "Ready";
- "optional": a check, listed under "Worth checking";
- "none": information with no decision (what was left out or kept, counted).

`not_checked` marks a line that says something is not in the file or was not checked (words
left out, a page whose drawings were not analyzed, a source never compared with a compiled
copy): the review record and its CSV list it under what was not checked, so that "Every part
the tool found was converted" is never the record's only word on it.

`section` says where the line is listed when that does not follow from the tier: "handled"
for what was found and converted (a footnote kept where it falls: information, never listed
as left out), "not_handled" for a check that is always listed there whatever tier an older
stored line has (the language). None: "optional" is "Worth checking", the others "Not handled
in this file" (`pipeline.notices.section`).

The words are in `web.messages.NOTICE_MESSAGES` (and `HANDLED_MESSAGES` for the handled
section). The caller still decides when a line applies and what its count is; this module only
makes the line and keeps the table. `core.types.NOTICE_CODES` is the set of codes this release
knows, and a test keeps it equal to the table. Content-free: codes, pages, element ids and
counts (the one exception is `names`, the files attached to a PDF).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from readablestem.core.types import Notice, Review

Scope = Literal["page", "document"]
Section = Literal["handled", "not_handled", "worth_checking"]


@dataclass(frozen=True)
class CheckLine:
    """One row: a code, its scope, its tier, and what measures it."""

    code: str
    scope: Scope
    tier: Review
    measured_by: str
    section: Section | None = None  # None: the tier decides
    not_checked: bool = False  # the record lists it under what was not checked


def _page(
    code: str, tier: Review, by: str, section: Section | None = None, *, not_checked: bool = False
) -> CheckLine:
    return CheckLine(code, "page", tier, by, section, not_checked)


def _document(
    code: str, tier: Review, by: str, section: Section | None = None, *, not_checked: bool = False
) -> CheckLine:
    return CheckLine(code, "document", tier, by, section, not_checked)


ROWS: tuple[CheckLine, ...] = (
    # --- what layout and the page types found on a page
    _page("scanned_page", "required", "page type: only a picture of a page"),
    _page("handwritten_page", "required", "page type: handwriting"),
    _page("handwriting_on_page", "required", "ink analysis: handwriting on a typed page"),
    _page("unread_page", "required", "page type: drawings with nothing read from them"),
    _page("page_read_failed", "required", "reading the page raised an error"),
    _page(
        "drawings_not_analysed",
        "optional",
        "ink analysis stopped by its work or time limit",
        not_checked=True,
    ),
    _page("page_turned", "none", "a page saved sideways, turned to read it", "handled"),
    _page("ocr_text_layer_ignored", "none", "hidden text on a page kept as an image"),
    _page(
        "hidden_text_ignored",
        "optional",
        "hidden or covered words on a typed page",
        not_checked=True,
    ),
    _page("scripts_flattened", "optional", "a block with raised or lowered text written flat"),
    _page("footnote", "none", "layout: a footnote kept where it falls", "handled"),
    _page("code_block", "none", "layout: program code kept as plain text", "handled"),
    # --- derived from the fixes and the text as they stand now (`pipeline.notices.current`)
    _page("table_not_rebuilt", "required", "a table fix kept as the image of the original"),
    _page("colour_only", "optional", "a figure that tells things apart by color alone"),
    _page("long_description_missing", "optional", "a chart or diagram without a long description"),
    _page("struck_text", "optional", "words the page strikes through, kept and marked"),
    _page("option_labels", "required", "answer options run together in one block"),
    _page("rtl_text", "required", "right-to-left words in the order the PDF gives them"),
    # --- the text pass
    _page("colour_reference", "optional", "text that refers to a color near a colored run"),
    _document(
        "colour_used",
        "optional",
        "passages printed in color, which the converted file does not keep",
        not_checked=True,
    ),
    _page("link_scheme_dropped", "optional", "a link that is not http, https or mailto"),
    _page("link_text_unclear", "optional", "link text that says nothing on its own"),
    _document("annotations_read", "required", "comments in the PDF's annotations, included"),
    _document(
        "attachments_not_converted", "optional", "files attached to the PDF", not_checked=True
    ),
    _document("form_fields", "none", "a fillable form read as text"),
    _document("lang_detected", "optional", "the language was detected from the text"),
    _document("lang_assumed", "required", "no language chosen or found", "not_handled"),
    _document("lang_mixed", "required", "no one language holds most of the text", "not_handled"),
    # --- scanned and handwritten pages read from an image
    _page("image_page_converted", "optional", "a page read from its image, blocks counted"),
    _document("second_reader_unavailable", "optional", "equations the second reader did not read"),
    _document("second_reader_budget", "optional", "equations left out by the second reader's cap"),
    # --- the file as opened, and the reviewer's edits
    _page("file_cut_off", "required", "the last pages of a repaired PDF are empty"),
    _document("file_repaired", "optional", "a PDF that had to be repaired when opened"),
    _page("page_omitted", "none", "a page the reviewer left out of the student file"),
    # --- a Word or PowerPoint upload (the census) and a Word file read from the file itself
    _document("office_tracked_changes", "required", "the file's tracked changes, still open"),
    _document("office_speaker_notes", "optional", "speaker notes of slides"),
    _document("office_hidden_slides", "optional", "slides hidden in the file"),
    _document("office_comments", "optional", "comments in the file"),
    _document(
        "word_hidden_text", "optional", "Word reader: hidden runs left out", not_checked=True
    ),
    _document("word_text_boxes", "optional", "Word reader: text in boxes", not_checked=True),
    _document(
        "word_headers_footers",
        "optional",
        "Word reader: running headers and footers",
        not_checked=True,
    ),
    _document(
        "word_objects",
        "required",
        "Word reader: SmartArt, charts, embedded objects",
        not_checked=True,
    ),
    _document("word_tracked_changes", "required", "Word reader: tracked changes taken as accepted"),
    _document("word_rtl_text", "optional", "Word reader: right-to-left paragraphs"),
    # --- a source job (LaTeX or Word read from the file, `source.report`)
    _document("source_error", "required", "LaTeX reported a formula it could not read"),
    _document("source_unread", "required", "words or pictures no rule of the reader took"),
    _document(
        "source_unplaced",
        "required",
        "LaTeX errors the reader could not tie to a place",
        not_checked=True,
    ),
    _document(
        "source_figure_missing", "required", "pictures named in the source, not found or read"
    ),
    # No compile witness runs for a source job yet (source.tex_compile is called by no job), so
    # every source job has this line: its text was not compared with a compiled copy.
    _document(
        "source_not_compared",
        "optional",
        "no compile witness ran for this source",
        not_checked=True,
    ),
)

TABLE: dict[str, CheckLine] = {row.code: row for row in ROWS}

# Derived from the table, so a code's place is stated once: the codes listed under "Handled in
# this file", and the ones that are always listed as not handled (the language checks).
HANDLED_CODES = frozenset(r.code for r in ROWS if r.section == "handled")
NOT_HANDLED_CODES = frozenset(r.code for r in ROWS if r.section == "not_handled")
NOT_CHECKED_CODES = frozenset(r.code for r in ROWS if r.not_checked)


def make(
    code: str,
    *,
    page: int | None = None,
    element_id: str | None = None,
    count: int = 1,
    names: Sequence[str] = (),
) -> Notice:
    """The line for `code`, with the tier the table gives it. A code that is not in the table is
    refused (`ValueError`): a line nobody has words and a tier for would be a blank on the
    job page. A line for the whole document takes no page or element."""
    row = TABLE.get(code)
    if row is None:
        raise ValueError(f"not a check line: {code}")
    if row.scope == "document" and (page is not None or element_id is not None):
        raise ValueError(f"{code} is a line for the whole document")
    return Notice(
        code=code,
        page=page,
        element_id=element_id,
        count=count,
        review=row.tier,
        names=list(names),
    )
