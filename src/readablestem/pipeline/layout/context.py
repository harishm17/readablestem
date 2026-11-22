"""The document context and the types every stage shares: `_Document` (what is known about the
document being read, and its budgets), the result types (`Layout`, `LayoutError`,
`PageStatus`) and the page guard that keeps one failing page from stopping a document. Part of
the layout package: pipeline/layout/__init__.py describes how a page is read.
"""

from __future__ import annotations

import logging
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

from readablestem.config import ConfigError
from readablestem.core.budget import BudgetExceeded
from readablestem.core.types import BBox, Element, Notice, PageStageFact
from readablestem.models.pricing import UnknownModelError
from readablestem.pipeline import page_type, say_once, tables
from readablestem.pipeline.step_facts import PageTrace

log = logging.getLogger("readablestem.pipeline.layout")


@dataclass
class _Document:
    """What is known of the one document being read, made by `analyze` for one call and
    passed to every step that reads it (nothing of it is kept between documents).

    The fields are set by `_analyze_document` as it learns them, and a step that runs before
    that sees the default: a document that is none of these things.

    `tex_made`: the document was typeset by TeX (`page_type.producer_hint`): the conventions
    that come from what a TeX author types (an italic letter is `$x$`, the maths stops before
    a unit or a parenthesis of the sentence) apply only then; a Chromium print of MathML or a
    Word file is read as before.
    `slide_deck`: the document is a slide deck (`slides.detect`): the reading rules that suit
    a slide (a column of two lines is a column; a line of maths that ends a wrapped bullet is
    part of it) apply only then.
    `compounds`, `whole_words`: how the document spells its own compounds
    (`hyphens.document_spellings`), for the hyphen at the end of a line: pairs it hyphenates
    inside a line, words it writes whole.
    `link_cuts`: where the file's own link annotations run across a line break inside a printed
    address (`hyphens.link_cut`), for the join of that break (`hyphens.joins_inside_token`).
    `grouping`: `analyze(display_grouping=...)`.
    `inline_joins`: `analyze(inline_joins=...)`: the rules that keep the whole inline formula in one
    run (docs/layout.md "Inline formulas kept whole").
    `region_tree`: `analyze(region_tree=...)`: the pages are read with a tree of regions
    (`regions.py`) in place of one gutter.
    `panels`: `analyze(join_panels=...)`: the panels of one captioned figure are one figure
    (`figures._join_panels`).
    `trace`: the page trace (`step_facts.PageTrace`): the time of each step on each page.
    `ink`, `detector`: what the ink analysis (`page_type.InkBudget`) and the table detector
    (`tables.DetectorBudget`) may still spend on this document. Only `_read_page` and
    `_claim_tables` use them, and they take the document of the call: `_PLAIN` is for the
    functions that outside code calls on a row or a grid, and nothing spends from it."""

    tex_made: bool = False
    slide_deck: bool = False
    compounds: frozenset[str] = frozenset()
    whole_words: frozenset[str] = frozenset()
    link_cuts: frozenset[tuple[str, str]] = frozenset()
    grouping: bool = False
    inline_joins: bool = False
    region_tree: bool = False
    panels: bool = False
    # Where each page's time goes. `analyze` makes one that times; the default times nothing.
    trace: PageTrace = field(default_factory=lambda: PageTrace(enabled=False))
    ink: page_type.InkBudget = field(default_factory=page_type.InkBudget)
    detector: tables.DetectorBudget = field(default_factory=tables.DetectorBudget)


_PLAIN = _Document()  # never changed: the document of a caller that has none
PageStatus = Literal["span", "layout"]  # `Element.page_status`


class LayoutError(ValueError):
    """The PDF cannot be processed. `code` is a category, safe to log."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        # "unreadable" | "encrypted" | "too_many_pages" | "no_pages" | "page_out_of_range"
        # | "too_complex" (a page needs more time or memory to read than a conversion has)
        # | "image_too_large" (decoding its pictures took longer than a job may spend)
        # | "content_too_large" (a page's drawing instructions inflate past the content
        # budget, `core.content_budget`: the upload probe refuses these first)
        self.code = code


# Errors that stop a job on purpose: a spend cap or a cancel (`JobCancelled` is a
# `BudgetExceeded`), a configuration error (`run.FATAL_ERRORS`) and a refusal of the file.
# The page guard (`_PageGuard`) lets them through; it keeps a page only for a bug.
_STOPS: tuple[type[Exception], ...] = (
    BudgetExceeded,
    ConfigError,
    UnknownModelError,
    LayoutError,
)


class _PageGuard:
    """The one rule for a page whose reading raises, for every step of `_analyze_document`
    that works on one page at a time (docs/layout.md "A page that raises").

    `with guard(n):` runs the step for page `n`. An error in `_STOPS` goes on up. Any other
    error is a bug on that page: it is logged (the page, the error's class and where it was
    raised, never the message, which can quote the page) and remembered in `failed`, and the
    step goes on with the next page. Later steps read `live(...)`, so a page that failed
    is not read again; `_analyze_document` forgets what the earlier steps stored for it and
    keeps it as its picture (`page_type.PAGE_READ_FAILED`). `settle` is the one place that
    says when the document fails with it: when every page that was to be read has failed,
    counted over the whole document and not per step."""

    def __init__(self) -> None:
        self.failed: dict[int, Exception] = {}  # in the order they failed
        self.pages: list[int] = []  # the pages the document is made of
        self.on_purpose: set[int] = set()  # kept whole by their type: not read, nothing to fail

    @contextmanager
    def __call__(self, number: int) -> Generator[None]:
        try:
            yield
        except _STOPS:
            raise  # a stop on purpose (cancel, spend cap, configuration), not a page's fault
        except Exception as exc:
            if number not in self.failed:
                _log_page_failure(number, exc)
                self.failed[number] = exc

    def ok(self, number: int) -> bool:
        return number not in self.failed

    def live(self, pages: Iterable[int]) -> list[int]:
        """`pages` without those that failed, in their order."""
        return [n for n in pages if n not in self.failed]

    def settle(self) -> None:
        """Fail the document, with the first error, when no page it was to read could be."""
        tried = [n for n in self.pages if n not in self.on_purpose]
        if tried and all(n in self.failed for n in tried):
            raise self.failed[tried[0]]


@dataclass
class Layout:
    elements: list[Element]
    page_count: int  # pages in the file
    pages: list[int]  # 0-based pages analysed
    page_sizes: list[tuple[float, float]] = field(default_factory=list)  # per analysed page
    title: str | None = None  # PDF metadata title, else the first level-1 heading
    lang: str = "en"  # PDF /Lang if present, else "en"
    # Layout facts the job page lists ("Not handled in this file", review "none"): a
    # block of program code, a footnote (`_layout_notices`), hidden text left out
    # (`_hidden_text_notices`), drawings not analysed (`_ink_notices`). Codes, ids and
    # counts only.
    notices: list[Notice] = field(default_factory=list)
    # Per analysed page, what it is (`pipeline.page_type`; `to_record` gives the stored,
    # content-free form).
    page_types: dict[int, page_type.PageTypeResult] = field(default_factory=dict)
    # Pages kept whole as their page image, with the Fix reason ("handwritten_page",
    # "scanned_page", "unread_page"): nothing on them is read, they have no elements, and
    # the pipeline keeps each one as an image marked "Not converted", with a decision.
    kept_pages: dict[int, str] = field(default_factory=dict)
    # Pages whose table search the document's time for it refused (`tables.DetectorBudget`): only
    # the page's own rules and aligned text propose a table there, so a table may be read as
    # text. The conversion writes a "not finished" fact for each, and `notices` has one
    # `tables_not_searched` for each.
    tables_not_searched: list[int] = field(default_factory=list)
    # Per page read for its text, the words the page has to say and what was left out by
    # rule (`pipeline.say_once`): the say-once measure counts them against the elements.
    sources: dict[int, say_once.PageSource] = field(default_factory=dict)
    # Per page, the table proposals the region tree refused (`REGION_TREE`, WS15.2): the box of
    # each and why (`regions.REFUSED_*`). Codes and boxes only; their text stays in the page.
    table_refusals: dict[int, list[tuple[BBox, str]]] = field(default_factory=dict)
    # The page trace: the seconds of each layout step on each page, in `PageStageFact`s (steps:
    # read, tables, rows, figures, equations, order, blocks, elements). The steps of the
    # whole document (margins, list markers, the notices) are in no page's time.
    page_steps: list[PageStageFact] = field(default_factory=list)
    # Page -> its deck's number (`slides.Deck.numbers`), for every page of a file that holds
    # several decks or a deck with a title, a page kept whole as its image included: the
    # headings read from such a page later (`pipeline.image_page`) are put in that deck.
    deck_pages: dict[int, int] = field(default_factory=dict)


def _log_page_failure(number: int, exc: Exception) -> None:
    """Say that a page could not be read: the page, the error's class and where it was
    raised. Never the message or any text of the page (rules: no content in logs)."""
    tb = exc.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    where = f"{tb.tb_frame.f_code.co_name}:{tb.tb_lineno}" if tb is not None else "unknown"
    log.warning("layout could not read page %d: %s at %s", number + 1, type(exc).__name__, where)
