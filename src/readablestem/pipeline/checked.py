"""One statement of what was checked: `CheckedSummary` (SW7).

Five surfaces used to write their own sentence about what was converted, checked or
reviewed, each from the facts it happened to hold: the student note said "Your instructor
checked the parts the tool was unsure about" whatever the instructor did (an "Accept
anyway", a picture kept, optional notes nobody opened), and the job page fell through to
"Ready to share. Everything was converted." when it knew only that no outcome code, open
decision or image count was set. Each surface now asks `summarize` and prints what
`pipeline.checked_words` makes of the answer; none composes such a sentence itself, so two
surfaces cannot disagree.

The summary is computed from the stored result (its elements, fixes, pages left out and
the structure checks of its page), the day of the last review decision and the tool's
version. It is never stored: an old job gets the new sentences when its page is made again
(`assemble.RENDER_VERSION`).

What it holds:

1. What was converted, by kind, counted from the stored elements, so a part the tool found
   but never wrote a proposal for is not converted: equations in MathML, figures described
   or marked as decoration, tables rebuilt (or written as text), headings, blocks of text,
   the parts shown as pictures of the original (by kind) and the pages left out.
2. The structure checks of the page (`DocumentResult.html_checks`): how many ran, which
   failed. None when no checks are known (the page being written has not been checked yet).
3. What people did, per decision: `core.triage.review_load` (`ReviewLoad.actions`). The
   summary keeps no count of its own, so the draft gate (`web.output.open_required`), the
   job page and the student note read one number.
4. What is never checked, whatever the document (`checked_words.NEVER_CHECKED`).
5. The day of the last review decision and the version of the tool.

Facts this release does not hold are not in it, and no claim that needs them is made: the
page facts of each page (its type, unread ink, the language found), page cards and where
the content came from (a source file or the page) join it with the items that bring them.

The rules (the claims each surface may make):

- `ready_to_share`: nothing is shown as a picture, nothing is left out, no structure check
  failed and no decision is open. Otherwise the job is "Ready" with what remains. A result
  stored without checks (from before they existed) is not held back for that: its page is
  made again, and checked, when it is next opened (`routes.jobs.current_html`), and no
  sentence says a check passed that is not stored.
- `all_found_converted`: every part the tool found was converted (no picture, no page left
  out). It is said as "every part the tool found", never "everything": parts the tool did not
  find (ink it could not read) are not counted until the page facts exist.
- `reviewed`: a person made a decision. The student note counts reviewed decisions among
  the flagged ones and never counts an optional note nobody opened as reviewed.
- `text_sufficient`: a reader needs nothing but the text (no picture, nothing left out,
  nothing open), for the EPUB's `accessModeSufficient`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from readablestem.core import triage
from readablestem.core.types import (
    IMAGE_PAGE_OMISSION_REASON,
    IMAGE_PAGE_ORIGINAL_REASON,
    IMAGE_PAGE_REASON,
    KEPT_ORIGINAL_STATUSES,
    Check,
    DocumentResult,
    Element,
    Fix,
    Kind,
)
from readablestem.pipeline import assemble_tables, decoration
from readablestem.pipeline.notices import KEPT_IMAGE_CODES

# Kinds of a part shown as a picture, as `checked_words.KIND_WORDS` names them: a page (or
# handwriting) kept as an image is a figure in the data model but not in words.
PAGE_KINDS = frozenset({"page", "handwriting"})


def word_kind(fix: Fix) -> str:
    """The kind a fix is named by: "page" or "handwriting" for one kept as an image before
    any model work, else its kind."""
    if fix.kind is Kind.FIGURE and fix.reason in KEPT_IMAGE_CODES:
        return "handwriting" if fix.reason == "handwriting_on_page" else "page"
    return fix.kind.value


@dataclass(frozen=True)
class Picture:
    """A part shown as a picture of the original: its element id (the place in the page),
    its kind in words and its 1-based page."""

    element_id: str
    kind: str
    page: int


@dataclass(frozen=True)
class Converted:
    """What was converted, counted from the stored elements and their first fix."""

    equations: int = 0
    equations_mathml: int = 0
    figures: int = 0
    figures_described: int = 0
    figures_decorative: int = 0
    tables: int = 0
    tables_rebuilt: int = 0
    tables_as_text: int = 0
    headings: int = 0
    text_blocks: int = 0
    pictures: tuple[Picture, ...] = ()
    left_out_pages: tuple[int, ...] = ()  # 1-based
    pages: int | None = None  # pages in the document, when known

    @property
    def pictures_by_kind(self) -> dict[str, int]:
        return dict(Counter(p.kind for p in self.pictures))

    @property
    def found(self) -> int:
        """Parts the tool found that need converting (equations, figures, tables, and text
        of a page read from its image that was kept as a picture)."""
        texts = sum(1 for p in self.pictures if p.kind == "paragraph")
        return self.equations + self.figures + self.tables + texts


@dataclass(frozen=True)
class CheckedSummary:
    converted: Converted
    load: triage.ReviewLoad
    checks_run: int | None = None  # None: no structure checks known for this page
    checks_failed: tuple[Check, ...] = ()
    reviewed_on: date | None = None
    version: str = ""
    origin: str = "pdf"
    never_checked: tuple[str, ...] = field(default_factory=tuple)

    # ----- what people did

    @property
    def open_required(self) -> int:
        return self.load.open_decisions

    @property
    def reviewed(self) -> bool:
        return self.load.decided > 0

    @property
    def flagged(self) -> int:
        return self.load.flagged

    @property
    def accepted_failing(self) -> int:
        return self.load.actions[triage.ACCEPTED_FAILING]

    @property
    def unopened(self) -> int:
        return self.load.unopened

    # ----- what was converted

    @property
    def pictures(self) -> int:
        return len(self.converted.pictures)

    @property
    def left_out(self) -> int:
        return len(self.converted.left_out_pages)

    @property
    def all_found_converted(self) -> bool:
        return not self.pictures and not self.left_out

    @property
    def checks_passed(self) -> bool:
        """Structure checks ran and none failed. Not known is not passed."""
        return bool(self.checks_run) and not self.checks_failed

    # ----- the rules

    @property
    def ready_to_share(self) -> bool:
        return self.all_found_converted and not self.checks_failed and not self.open_required

    @property
    def text_sufficient(self) -> bool:
        return self.all_found_converted and not self.open_required

    @property
    def needs_notice(self) -> bool:
        """The student page opens with the statement: nobody reviewed it, or a part is a
        picture or left out."""
        return not self.reviewed or bool(self.pictures) or bool(self.left_out)


def _first_fixes(fixes: Iterable[Fix]) -> dict[str, Fix]:
    first: dict[str, Fix] = {}
    for fix in fixes:  # first wins, like DocumentResult.fix_for
        first.setdefault(fix.element_id, fix)
    return first


def _kept(fix: Fix | None) -> bool:
    return fix is None or fix.status in KEPT_ORIGINAL_STATUSES


def converted(
    elements: Sequence[Element],
    fixes: Iterable[Fix],
    left_out_pages: Iterable[int] = (),
    pages: int | None = None,
) -> Converted:
    """What was converted, part by part, as the renderer writes it (`assemble._Renderer`):
    an equation without MathML, a figure without a description or a table not rebuilt is
    shown as its original picture. `left_out_pages` are 0-based."""
    first = _first_fixes(fixes)
    counts: Counter[str] = Counter()
    pictures: list[Picture] = []
    for element in sorted(elements, key=lambda e: e.order):
        fix = first.get(element.id)
        page = element.page + 1
        if element.kind is Kind.EQUATION:
            counts["equations"] += 1
            if not _kept(fix) and fix and fix.equation and fix.equation.mathml.strip():
                counts["equations_mathml"] += 1
            else:
                pictures.append(Picture(element.id, "equation", page))
        elif element.kind is Kind.FIGURE:
            if IMAGE_PAGE_ORIGINAL_REASON in element.reasons:
                continue  # the original of a page converted from its image, shown beside it
            counts["figures"] += 1
            proposal = fix.figure if fix is not None else None
            unproven = proposal is not None and proposal.decorative
            unproven = unproven and not decoration.is_decoration(element, fix)
            if (
                _kept(fix)
                or proposal is None
                or not (proposal.decorative or proposal.alt.strip())
                or unproven
            ):
                kind = word_kind(fix) if fix is not None else "figure"
                if IMAGE_PAGE_OMISSION_REASON in element.reasons:
                    kind = "paragraph"  # writing on a converted page that was left out
                pictures.append(Picture(element.id, kind, page))
            elif proposal.decorative:
                counts["figures_decorative"] += 1
            else:
                counts["figures_described"] += 1
        elif element.kind is Kind.TABLE:
            counts["tables"] += 1
            if assemble_tables.text_grid(fix) is not None:
                counts["tables_as_text"] += 1
            elif assemble_tables.rebuilt_grid(fix) is not None:
                counts["tables_rebuilt"] += 1
            else:
                pictures.append(Picture(element.id, "table", page))
        elif element.kind is Kind.HEADING:
            counts["headings"] += 1
        elif (
            element.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM)
            and IMAGE_PAGE_REASON in element.reasons
            and fix is not None
            and fix.status in KEPT_ORIGINAL_STATUSES
        ):
            pictures.append(Picture(element.id, "paragraph", page))  # kept as its picture
        elif element.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM):
            counts["text_blocks"] += 1
    return Converted(
        **counts,
        pictures=tuple(pictures),
        left_out_pages=tuple(sorted({p + 1 for p in left_out_pages})),
        pages=pages,
    )


def summarize(
    *,
    elements: Sequence[Element],
    fixes: Sequence[Fix],
    left_out_pages: Iterable[int] = (),
    pages: int | None = None,
    html_checks: Sequence[Check] | None = None,
    reviewed_on: date | None = None,
    version: str = "",
    origin: str = "pdf",
) -> CheckedSummary:
    """The summary from the parts of a result. `html_checks` None: the page has not been
    checked yet (the renderer, before the checks run), which is not the same as none run.
    `reviewed_on` is the day of the last review decision, said only once a person made one."""
    from readablestem.pipeline.checked_words import NEVER_CHECKED

    load = triage.review_load(fixes)
    checks = list(html_checks) if html_checks is not None else None
    return CheckedSummary(
        converted=converted(elements, fixes, left_out_pages, pages),
        load=load,
        checks_run=len(checks) if checks is not None else None,
        checks_failed=tuple(c for c in checks or () if not c.passed),
        reviewed_on=reviewed_on if load.decided else None,
        version=version,
        origin=origin,
        never_checked=NEVER_CHECKED,
    )


def of_result(
    result: DocumentResult,
    *,
    reviewed_on: date | None = None,
    with_checks: bool = True,
    version: str = "",
) -> CheckedSummary:
    """The summary of a stored result: what every web surface reads. `with_checks` False
    for the page being written, whose checks are not run yet."""
    return summarize(
        elements=result.elements,
        fixes=result.fixes,
        left_out_pages=result.omitted_pages,
        pages=result.stats.pages or None,
        html_checks=result.html_checks if with_checks else None,
        reviewed_on=reviewed_on,
        version=version or result.pipeline_version,
        origin=result.origin,
    )


def open_required(fixes: Iterable[Fix]) -> int:
    """The decisions still open, read from `review_load` like the summary's own count."""
    return triage.review_load(list(fixes)).open_decisions
