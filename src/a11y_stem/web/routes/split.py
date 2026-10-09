"""The review screen: the page with the fixes marked on it, and one decision at a time.

`GET /jobs/{id}/review` shows, side by side on a wide screen (stacked on a phone, the
decision first):

- the decision at hand ("Decision 1 of 3"), with what to look at: for an equation the
  PDF and the proposal at the same size, the differing symbol outlined in both and the
  difference as a short table (`web.compare`); for a figure its image and the alt text
  field; for repeated headings one card for the whole group. Accept, Save edit and
  Reject post to the same route as the list view (`routes/review.py`), which redirects
  to the next decision still open (`?item=<key>`), where the decision's heading takes
  focus and says what just changed;
- the optional checks (accepted automatically with a note), folded;
- the page the decision is on, as an image rendered on demand from the upload
  (`web.page_images`), with every fix on it marked: a numbered box per decision still
  open, a quieter "Note" box per optional check, and the accepted ones only on request
  ("Show accepted"). Each mark is a link to its item.

Everything is a link or a form, so it works without JavaScript (`static/app.js` only
adds keyboard shortcuts, scrolls the page image to the item, makes "Save edit" the main
button once a field is changed and keeps a draft of what is typed in this browser tab;
htmx checks LaTeX while it is typed, `live_check`). Every form carries the review
version its page was drawn at, so a form from an out-of-date tab is refused, not applied
(`routes/review.py`). The full list of every item is still at `/jobs/{id}/review/list`.

Mark positions come from a stylesheet made per job (`/jobs/{id}/review/marks.css`:
element ids and percentages only), because the CSP allows no inline styles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from markupsafe import Markup
from pydantic import TypeAdapter
from sqlalchemy import select

from a11y_stem.core import triage
from a11y_stem.core.types import DocumentResult, Element, Fix, Kind, Status
from a11y_stem.pipeline.validate import ALT_WARN_CHARS
from a11y_stem.web import marks, page_images, queue
from a11y_stem.web.compare import (
    COMPARE_PX_PER_PT,
    CompareGeometry,
    EquationCompare,
    Suggestion,
    band_clip,
    compare_equation,
    compare_geometry,
    context_clip,
    suggest_fix,
)
from a11y_stem.web.db import Blob, session_scope
from a11y_stem.web.deps import (
    ELEMENT_ID,
    CurrentUser,
    PageError,
    UserDep,
    current_user,
    get_ctx,
    pop_flash,
    render,
    require_csrf,
)
from a11y_stem.web.edit_check import EditCheck, check_equation_edit
from a11y_stem.web.logsafe import log_event
from a11y_stem.web.mathml import try_sanitize_mathml
from a11y_stem.web.messages import explain_check, explain_reason
from a11y_stem.web.review import (
    MAX_LATEX_CHARS,
    FieldError,
    ReviewInvalid,
    heading_places,
    item_numbers,
)
from a11y_stem.web.routes.jobs import NOT_FOUND, owned_job, require_done
from a11y_stem.web.routes.review import (
    GROUP_FLASH,
    HEADING_GROUP_FLASH,
    CheckView,
    HeadingGroupView,
    ReviewItem,
    _heading_group,
    _item,
    _load,
    _load_versioned,
    stale_notice,
    undo_offer,
)
from a11y_stem.web.speech_diff import SpokenDiff, spoken_diff
from a11y_stem.web.views import KIND_LABELS, JobView

logger = logging.getLogger(__name__)
router = APIRouter()

IMAGE_HEADERS = {"Cache-Control": "private, no-store"}
HEADING_REGION_ZOOM = 3.0  # pixels per point for a heading's band
FIGURE_REGION_ZOOM = 2.0
MAX_STEPS = 12  # decisions shown as numbered steps; more become a progress bar

# Short notes for the folded list of optional checks (the card itself has the full
# sentence from web.messages). Codes without one use the full sentence.
SHORT_NOTES = {
    "style_cosmetic": "A letter's style differs slightly; the meaning is the same.",
    "speech_failed": "No spoken version could be made.",
    "speech_empty": "No spoken version could be made.",
    "heading.weak_evidence": "Only weak signs that this is a heading.",
    "heading.level_repaired": "Its level was adjusted to fit the outline.",
    "heading.all_caps": "All capitals: possibly a label, not a heading.",
    "heading.very_short": "Very short: possibly a label, not a heading.",
    "heading.list_like": "Starts like a list item.",
    "too_long": "The alt text is long.",
    "redundant_prefix": "The alt text starts with words like “image of”.",
    "second_check_mismatch": "Worth a look: a second automated check doubts the alt text.",
}

# --------------------------------------------------------------------------- view model


@dataclass
class Box:
    """One mark on the page image: a link to its item."""

    element_id: str
    href: str
    tag: str  # what the mark shows: the decision number, "Note" or "✓"
    name: str  # the rest of the link's accessible name (after the tag)
    # "required", "repeat" (a group's heading on another page than the one its decision
    # counts on), "optional" or "accepted"
    tier: str
    current: bool = False


@dataclass
class PageLink:
    number: int  # 1-based
    href: str
    to_decide: int
    current: bool


@dataclass
class PageView:
    number: int  # 1-based
    count: int
    image_url: str
    width: float | None  # points; None when unknown (no marks then)
    height: float | None
    boxes: list[Box]
    hidden_accepted: int  # accepted items on this page not shown
    prev_href: str | None
    next_href: str | None
    accepted_href: str  # toggles "Show accepted"

    @property
    def placed(self) -> bool:
        return self.width is not None and self.height is not None

    @property
    def tiers(self) -> set[str]:
        """The kinds of mark on the page (the legend lists only these)."""
        return {b.tier for b in self.boxes}


@dataclass
class StepLink:
    """One decision in the progress row: its number, state and a link to it."""

    number: int
    href: str
    done: bool
    current: bool


@dataclass
class OptionalLine:
    """One item in a folded list ("Optional checks", "Your decisions"), named by what it
    is as well as its number (UX review MF15)."""

    label: str  # "Equation 7, page 3"
    note: str  # the note, or what the reviewer decided
    href: str
    what: str = ""  # a heading's text, the start of the alt text
    mathml: Markup | None = None  # an equation, small


@dataclass
class Panel:
    """The decision (or item) shown on the right."""

    mode: str  # "decision", "item" (an optional check or accepted item) or "done"
    title: str
    subtitle: str = ""
    status: str = ""  # in words; the pill's colour only repeats it
    status_class: str = ""
    item: ReviewItem | None = None
    group: HeadingGroupView | None = None
    why: str | None = None
    checks: list[CheckView] = field(default_factory=list)
    reason_code: str | None = None
    compare: EquationCompare | None = None
    compare_size: tuple[float, float] | None = None  # crop size in CSS px (aspect ratio)
    # The proposal's reading and the PDF text's, the symbols that differ marked; only
    # when the checks found a difference (`web.speech_diff`).
    spoken: SpokenDiff | None = None
    # A decision whose proposal changes the meaning: the fix comes first (the suggested
    # one-click fix, else the edit box, open), and Accept is the secondary "Accept anyway".
    changes_meaning: bool = False
    suggestion: Suggestion | None = None
    region_url: str | None = None  # the item with the page around it
    region_alt: str = ""
    show_on_page: str | None = None
    prev_href: str | None = None
    prev_label: str = ""
    next_href: str | None = None
    next_label: str = ""
    decision_key: str | None = None
    confidence: str = ""
    group_pages: list[PageLink] = field(default_factory=list)
    # A decided heading group: what was decided, and which of the two choices would
    # change something if made again for all of them ("Change all 11").
    group_state: str = ""
    redo_keep: bool = False
    redo_bold: bool = False
    back_href: str | None = None  # an item outside the queue: back to the decision at hand
    back_label: str = ""


def review_url(
    job_id: str,
    *,
    item: str | None = None,
    page: int | None = None,
    accepted: bool = False,
    anchor: str | None = None,
) -> str:
    query = {}
    if item:
        query["item"] = item
    if page:
        query["page"] = str(page)
    if accepted:
        query["accepted"] = "1"
    url = f"/jobs/{job_id}/review"
    if query:
        url += "?" + urlencode(query)
    return url + (f"#{anchor}" if anchor else "")


def _label(fix: Fix, numbers: dict[str, int]) -> str:
    return f"{KIND_LABELS.get(fix.kind, fix.kind.value.title())} {numbers.get(fix.element_id, 0)}"


def _page_count(view: JobView, result: DocumentResult) -> int:
    pages = [view.page_count, result.stats.pages, *(e.page + 1 for e in result.elements)]
    return max(1, *pages)


def _owned_page_count(request: Request, job_id: str, user: CurrentUser) -> int:
    """The job's page count, without loading its upload; 404 for someone else's job, a
    deleted one or one past its retention time (`owned_job`)."""
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        return job.page_count


def _upload(request: Request, job_id: str, user: CurrentUser) -> bytes:
    """The job's own upload (404 as `_owned_page_count`). Loaded only while a render slot is
    held (`page_images.render_slot`), so waiting requests hold no copy of it."""
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        pdf = session.scalar(
            select(Blob.content).where(Blob.job_id == job.id, Blob.kind == "upload")
        )
    if pdf is None:
        raise PageError(404, *NOT_FOUND)
    return pdf


SIZES = TypeAdapter(dict[int, tuple[float, float]])


def resolve_page_sizes(
    request: Request,
    job_id: str,
    user: CurrentUser,
    result: DocumentResult,
    *,
    slot_held: bool = False,
) -> dict[int, tuple[float, float]]:
    """The result's page sizes. A result stored before it recorded them gets the PDF's
    own (one child process) and keeps them: they are stored in the job's result the
    first time, so the next view, its marks.css and its images start no child for them.
    Empty when neither is available (no slot free, the child failed): the page is then
    shown without marks. `slot_held`: the caller already holds a render slot."""
    if result.page_sizes:
        return result.page_sizes
    try:
        if slot_held:
            sizes = page_images.page_sizes(_upload(request, job_id, user))
        else:
            with page_images.render_slot(user.id):
                sizes = page_images.page_sizes(_upload(request, job_id, user))
    except page_images.RenderFailed as exc:
        log_event(logger, "page_sizes_failed", job_id=job_id, code=exc.code)
        return {}
    _store_page_sizes(request, job_id, user, sizes)
    result.page_sizes = sizes
    return sizes


def _store_page_sizes(
    request: Request, job_id: str, user: CurrentUser, sizes: dict[int, tuple[float, float]]
) -> None:
    """Add the page sizes to a stored result that has none (only that key changes, so
    a result stored before still loads as it did, with its sizes now)."""
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user, lock=True)
        data = dict(job.result_json or {})
        if data.get("page_sizes"):
            return  # stored meanwhile by another request
        data["page_sizes"] = SIZES.dump_python(sizes, mode="json")
        job.result_json = data
    log_event(logger, "page_sizes_stored", job_id=job_id, pages=len(sizes))


def _tier(fix: Fix) -> str:
    if fix.pending:
        return "required"
    if fix.optional_note:
        return "optional"
    return "accepted"


def _first_note(fix: Fix, compare: EquationCompare | None = None) -> str:
    if triage.second_check_note(fix):  # the one note worth a look comes first
        return SHORT_NOTES[triage.SECOND_CHECK_MISMATCH]
    for check in fix.failed_checks:
        code = check.code or check.name
        if (
            code == "style_cosmetic"
            and compare is not None
            and compare.unseen
            and not any(row.what == "Style" for row in compare.rows)
        ):
            # Not a difference: a style the PDF's text cannot carry (web.compare).
            shown = ", ".join(compare.unseen)
            return f"The proposal has a {shown}, a style the PDF's text cannot show."
        if code in SHORT_NOTES:
            return SHORT_NOTES[code]
        return explain_check(check).text
    return "Accepted with a note."


def _why(fix: Fix, compare: EquationCompare | None) -> str | None:
    """The one sentence that says what to look at."""
    if compare is not None and compare.summary:
        return compare.summary
    if fix.reason:
        return explain_reason(fix.reason).text
    for check in fix.failed_checks:
        return explain_check(check).text
    if fix.pending and fix.confidence is not None:
        return (
            "The proposal passed every check, but the model was not sure enough of it to "
            "accept it without you."
        )
    return None


class SplitBuilder:
    """Everything the split view shows, from one load of the result."""

    def __init__(
        self,
        request: Request,
        job_id: str,
        user: CurrentUser,
        *,
        show_accepted: bool,
    ) -> None:
        self.request = request
        self.user = user
        self.view, self.result, self.crop_ids, self.version = _load_versioned(request, job_id, user)
        self.job_id = self.view.id
        self.show_accepted = show_accepted
        self.elements = {e.id: e for e in self.result.elements}
        self.numbers = item_numbers(self.result)
        self.places = heading_places(self.result)
        self.queue = queue.decisions(self.result)
        self.sizes = resolve_page_sizes(request, job_id, user, self.result)
        self.page_count = _page_count(self.view, self.result)
        self.items: dict[str, ReviewItem] = {}

    # ------------------------------------------------------------------ helpers

    def item(self, fix: Fix) -> ReviewItem:
        if fix.element_id not in self.items:
            self.items[fix.element_id] = _item(
                fix,
                self.elements.get(fix.element_id),
                self.numbers.get(fix.element_id, 0),
                self.job_id,
                self.crop_ids,
                self.places.get(fix.element_id),
            )
        return self.items[fix.element_id]

    def url(
        self, *, item: str | None = None, page: int | None = None, anchor: str | None = None
    ) -> str:
        return review_url(
            self.job_id, item=item, page=page, accepted=self.show_accepted, anchor=anchor
        )

    def page_of(self, fix: Fix) -> int | None:
        page = queue.page_of(fix, self.elements)
        return None if page is None else page + 1

    def decision_url(self, decision: queue.Decision) -> str:
        return self.url(item=decision.key, anchor="decision-title")

    # ------------------------------------------------------------------ selection

    def select(self, key: str) -> tuple[queue.Decision | None, Fix | None]:
        """The decision named by `key`, or an item outside the queue (an optional check or
        an accepted item), or the first decision still open."""
        if key and ELEMENT_ID.match(key):
            decision = queue.find(self.queue, key)
            if decision is not None:
                return decision, None
            fix = self.result.fix_for(key)
            if fix is not None and fix.kind in {Kind.EQUATION, Kind.FIGURE, Kind.HEADING}:
                return None, fix
        return queue.first_open(self.queue), None

    # ------------------------------------------------------------------ panel

    def panel(self, decision: queue.Decision | None, fix: Fix | None) -> Panel:
        total = len(self.queue)
        if decision is None and fix is None:
            return Panel(mode="done", title="All decisions made" if total else "Nothing to decide")
        if decision is not None:
            lead = decision.lead
            panel = Panel(
                mode="decision",
                title=f"Decision {decision.number} of {total}",
                decision_key=decision.key,
            )
            if decision.open:
                panel.status, panel.status_class = "Needs your decision", "pill-required"
            else:
                states = {self.item(f).status_label for f in decision.fixes}
                panel.status = "Decided: " + ", ".join(sorted(states)).lower().capitalize()
                panel.status_class = "pill-done"
            before = queue.previous(self.queue, decision)
            after = queue.following(self.queue, decision)
            if before is not None:
                panel.prev_href, panel.prev_label = (
                    self.decision_url(before),
                    f"Decision {before.number}",
                )
            if after is not None:
                panel.next_href, panel.next_label = (
                    self.decision_url(after),
                    f"Decision {after.number}",
                )
            if decision.is_group:
                self._group_panel(panel, decision)
                return panel
            fix = lead
        else:
            assert fix is not None
            tier = _tier(fix)
            panel = Panel(
                mode="item",
                title="Optional check" if tier == "optional" else self.item(fix).status_label,
            )
            panel.status = (
                "Accepted, with a note" if tier == "optional" else self.item(fix).status_label
            )
            panel.status_class = "pill-optional" if tier == "optional" else "pill-done"
            current = queue.first_open(self.queue)
            if current is not None:
                panel.back_href = self.decision_url(current)
                panel.back_label = f"Back to decision {current.number}"
        self._fix_panel(panel, fix)
        return panel

    def _fix_panel(self, panel: Panel, fix: Fix) -> None:
        item = self.item(fix)
        element = self.elements.get(fix.element_id)
        page = self.page_of(fix)
        panel.item = item
        where = {
            Kind.EQUATION: "in a sentence"
            if element and element.display == "inline"
            else "on its own line",
        }.get(fix.kind, "")
        panel.subtitle = ", ".join(
            p for p in (item.label, f"page {page}" if page else "", where) if p
        )
        panel.confidence = item.confidence_label
        panel.checks = item.checks
        panel.reason_code = fix.reason
        if page:
            panel.show_on_page = self.url(
                item=fix.element_id, page=page, anchor=f"box-{fix.element_id}"
            )
        size = self.sizes.get(element.page) if element else None
        if fix.kind is Kind.EQUATION and element is not None:
            compare = compare_equation(element, fix)
            panel.compare = compare
            if fix.pending and compare.meaning:
                panel.changes_meaning = True
                panel.suggestion = suggest_fix(element, fix, compare)
            item.mathml = try_sanitize_mathml(
                fix.equation.mathml if fix.equation else None, flag=compare.proposal_marks
            )
            if compare.rows:
                panel.spoken = spoken_diff(item.speech, item.text_layer)
            clip = context_clip(element, size)
            panel.compare_size = (
                round((clip[2] - clip[0]) * COMPARE_PX_PER_PT, 1),
                round((clip[3] - clip[1]) * COMPARE_PX_PER_PT, 1),
            )
            panel.region_url = f"/jobs/{self.job_id}/regions/{fix.element_id}.png"
            outlined = ", the symbol to check outlined" if compare.pdf_marks else ""
            panel.region_alt = f"{item.label} in the PDF, with the words around it{outlined}."
        elif fix.kind is Kind.HEADING and element is not None:
            panel.region_url = f"/jobs/{self.job_id}/regions/{fix.element_id}.png"
            panel.region_alt = f"{item.label} in the PDF, with the lines around it."
        panel.why = _why(fix, panel.compare)

    def _group_panel(self, panel: Panel, decision: queue.Decision) -> None:
        # The card asks about the headings still waiting; once none is, it only says so
        # (the group's label and style come from all of them).
        members = list(decision.waiting) or list(decision.fixes)
        for fix in members:
            self.item(fix)
        group = _heading_group(self.result, members, self.items, self.elements, self.places)
        if not decision.open:
            group.members, group.keep = [], 0
            kept = sum(f.status in KEPT for f in decision.fixes)
            bold = sum(f.status is Status.REJECTED for f in decision.fixes)
            panel.group_state = _group_state(kept, bold, len(decision.fixes))
            panel.redo_keep = kept < len(decision.fixes)
            panel.redo_bold = bold < len(decision.fixes)
        panel.group = group
        lead = decision.lead
        page = self.page_of(lead)
        panel.subtitle = f"Repeated heading, {len(decision.fixes)} times"
        panel.show_on_page = (
            self.url(item=decision.key, page=page, anchor=f"box-{lead.element_id}")
            if page
            else None
        )
        if self.elements.get(lead.element_id) is not None:
            panel.region_url = f"/jobs/{self.job_id}/regions/{lead.element_id}.png"
            where = f" on page {page}" if page else ""
            panel.region_alt = f"The first one{where}, with the lines around it."
        pages = sorted({p for f in decision.fixes if (p := self.page_of(f))})
        panel.group_pages = [
            PageLink(p, self.url(item=decision.key, page=p, anchor="page-view"), 0, False)
            for p in pages
        ]

    # ------------------------------------------------------------------ page

    def page_view(self, number: int, selected: set[str], current_key: str | None) -> PageView:
        index = number - 1
        size = self.sizes.get(index)
        boxes: list[Box] = []
        hidden = 0
        decision_of = {f.element_id: d for d in self.queue for f in d.fixes}
        for fix in self.result.fixes:
            element = self.elements.get(fix.element_id)
            if element is None or element.page != index:
                continue
            if fix.kind not in {Kind.EQUATION, Kind.FIGURE, Kind.HEADING}:
                continue
            tier = _tier(fix)
            is_current = fix.element_id in selected
            if tier == "accepted" and not (self.show_accepted or is_current):
                hidden += 1
                continue
            label = _label(fix, self.numbers)
            decision = decision_of.get(fix.element_id)
            key = decision.key if decision is not None and decision.open else fix.element_id
            href = self.url(item=key, page=number, anchor="decision-title")
            if tier == "required" and decision is not None:
                what = label
                if decision.is_group:
                    text = fix.heading.text if fix.heading else ""
                    what = (
                        f"{label}, “{text}”, repeated heading"
                        if text
                        else f"{label}, repeated heading"
                    )
                of = f"{decision.number} of {len(self.queue)}"
                home = queue.page_of(decision.lead, self.elements)
                if decision.is_group and home is not None and home != index:
                    # A group's heading on another page than the one the decision counts
                    # on (`queue.open_by_page`): a lighter mark, so a page without "N to
                    # decide" never shows a solid decision mark.
                    name = f"{what}, part of decision {of} (first on page {home + 1})"
                    tier = "repeat"
                else:
                    name = f"{what}, needs your decision ({of})"
                boxes.append(
                    Box(fix.element_id, href, str(decision.number), name, tier, is_current)
                )
            elif tier == "optional":
                name = f"{label}, optional check"
                boxes.append(Box(fix.element_id, href, "Note", name, "optional", is_current))
            else:
                status = self.item(fix).status_label.lower()
                boxes.append(
                    Box(fix.element_id, href, "✓", f"{label}, {status}", "accepted", is_current)
                )
        if size is None:
            boxes = []
        return PageView(
            number=number,
            count=self.page_count,
            image_url=f"/jobs/{self.job_id}/pages/{number}.png",
            width=size[0] if size else None,
            height=size[1] if size else None,
            boxes=boxes,
            hidden_accepted=hidden,
            prev_href=self._page_href(number - 1, current_key),
            next_href=self._page_href(number + 1, current_key),
            accepted_href=review_url(
                self.job_id,
                item=current_key,
                page=number,
                accepted=not self.show_accepted,
                anchor="page-view",
            ),
        )

    def _page_href(self, number: int, current_key: str | None) -> str | None:
        if not 1 <= number <= self.page_count:
            return None
        return self.url(item=current_key, page=number, anchor="page-view")

    def page_links(self, shown: int, current_key: str | None) -> list[PageLink]:
        counts = queue.open_by_page(self.queue, self.elements)
        return [
            PageLink(
                n,
                self.url(item=current_key, page=n, anchor="page-view"),
                counts.get(n - 1, 0),
                n == shown,
            )
            for n in range(1, self.page_count + 1)
        ]

    def steps(self, current: queue.Decision | None) -> list[StepLink]:
        return [
            StepLink(
                d.number,
                self.decision_url(d),
                not d.open,
                current is not None and d.key == current.key,
            )
            for d in self.queue
        ]

    def optional_lines(self) -> list[OptionalLine]:
        """Accepted automatically with a note nobody has acted on yet, in page and reading
        order."""
        far = (1 << 30, 1 << 30)
        rank = {e.id: (e.page, e.order) for e in self.result.elements}
        notes = sorted(
            (
                f
                for f in self.result.fixes
                if f.optional_note and f.kind in {Kind.EQUATION, Kind.FIGURE, Kind.HEADING}
            ),
            key=lambda f: rank.get(f.element_id, far),
        )
        out = []
        for fix in notes:
            href = self.url(item=fix.element_id, anchor="decision-title")
            compare = (
                compare_equation(self.elements.get(fix.element_id), fix)
                if fix.kind is Kind.EQUATION
                else None
            )
            out.append(self._line(fix, _first_note(fix, compare), href))
        return out

    def _line(self, fix: Fix, note: str, href: str) -> OptionalLine:
        page = self.page_of(fix)
        item = self.item(fix)
        return OptionalLine(
            label=_label(fix, self.numbers) + (f", page {page}" if page else ""),
            note=note,
            href=href,
            what=item.preview,
            mathml=try_sanitize_mathml(fix.equation.mathml if fix.equation else None),
        )

    def decided_lines(self) -> list[OptionalLine]:
        """What the reviewer decided, apart from what was accepted for them (UX review
        MF14): each decision of the queue the reviewer made, then any other item they
        changed (an optional check edited, say), in page and reading order."""
        out: list[OptionalLine] = []
        seen: set[str] = set()
        for decision in self.queue:
            mine = [f for f in decision.fixes if f.decided_by_reviewer]
            if decision.open or not mine:
                continue
            seen.update(f.element_id for f in decision.fixes)
            href = self.decision_url(decision)
            if decision.is_group:
                lead = decision.fixes[0]
                text = lead.heading.text if lead.heading else ""
                kept = sum(f.status in KEPT for f in decision.fixes)
                bold = sum(f.status is Status.REJECTED for f in decision.fixes)
                out.append(
                    OptionalLine(
                        label=f"Repeated heading, {len(decision.fixes)} times",
                        note=_group_state(kept, bold, len(decision.fixes)),
                        href=href,
                        what=text,
                    )
                )
                continue
            fix = decision.fixes[0]
            out.append(self._line(fix, self.item(fix).status_label + ".", href))
        far = (1 << 30, 1 << 30)
        rank = {e.id: (e.page, e.order) for e in self.result.elements}
        others = sorted(
            (
                f
                for f in self.result.fixes
                if f.decided_by_reviewer
                and f.element_id not in seen
                and f.kind in {Kind.EQUATION, Kind.FIGURE, Kind.HEADING}
            ),
            key=lambda f: rank.get(f.element_id, far),
        )
        for fix in others:
            href = self.url(item=fix.element_id, anchor="decision-title")
            out.append(self._line(fix, self.item(fix).status_label + ".", href))
        return out


KEPT = frozenset({Status.ACCEPTED, Status.EDITED})  # a heading written as a heading


def _group_state(kept: int, bold: int, size: int) -> str:
    """What a decided heading group's headings are now, in one sentence."""
    if kept == size:
        return f"{'Both' if size == 2 else f'All {size}'} are kept as headings."
    if bold == size:
        return f"{'Both' if size == 2 else f'All {size}'} were made bold text."
    return f"{kept} kept as headings, {bold} made bold text."


def headline(open_count: int, total: int, optional: int) -> str:
    """The one sentence at the top. Counts decisions, never fixes."""
    rest = "Everything else was accepted"
    rest += "; optional checks are below." if optional else "."
    if total == 0:
        return "Nothing needs your decision. Everything was accepted" + (
            "; optional checks are below." if optional else "."
        )
    if open_count == 0:
        return "All decisions made. " + rest
    if open_count == total:
        things = "thing needs" if total == 1 else "things need"
        return f"{total} {things} your decision. {rest}"
    left = "decision" if total == 1 else "decisions"
    return f"{open_count} of {total} {left} left. {rest}"


def _notice(
    flashes: list[dict[str, Any]], result: DocumentResult, numbers: dict[str, int], open_count: int
) -> str | None:
    """What the last decision did, for the status line above the next one."""
    if not flashes:
        return None
    entry = flashes[-1]
    message = str(entry.get("message", ""))
    target = entry.get("element_id")
    target = target if isinstance(target, str) else ""
    label = ""
    if target.startswith(HEADING_GROUP_FLASH):
        key = target.removeprefix(HEADING_GROUP_FLASH)
        first = next((f for f in result.fixes if f.group_key == key), None)
        text = first.heading.text if first is not None and first.heading else ""
        label = f"Repeated heading “{text}”" if text else "Repeated heading"
    elif target and not target.startswith(GROUP_FLASH):
        fix = result.fix_for(target)
        if fix is not None:
            label = _label(fix, numbers)
    left = (
        "All decisions made."
        if open_count == 0
        else f"{open_count} {'decision' if open_count == 1 else 'decisions'} left."
    )
    return f"{label + ': ' if label else ''}{message} {left}"


def split_page(
    request: Request,
    job_id: str,
    user: CurrentUser,
    *,
    item: str = "",
    page: str = "",
    show_accepted: bool = False,
    invalid: tuple[str, ReviewInvalid] | None = None,
    stale: bool = False,
    status_code: int = 200,
) -> Response:
    builder = SplitBuilder(request, job_id, user, show_accepted=show_accepted)
    decision, fix = builder.select(item)
    panel = builder.panel(decision, fix)
    errors: list[FieldError] = []
    if invalid is not None:
        errors = invalid[1].errors
        _apply_invalid(panel, invalid)
    # The page shown: the one asked for, else the one the item is on.
    shown_fix = (decision.lead if decision is not None else fix) if (decision or fix) else None
    number = int(page) if page.isdigit() and 1 <= int(page) <= builder.page_count else 0
    if not number:
        number = (builder.page_of(shown_fix) if shown_fix is not None else None) or 1
    selected = (
        {f.element_id for f in decision.fixes}
        if decision is not None
        else ({fix.element_id} if fix is not None else set())
    )
    current_key = decision.key if decision is not None else (fix.element_id if fix else None)
    page_view = builder.page_view(number, selected, current_key)
    open_count = sum(d.open for d in builder.queue)
    optional = builder.optional_lines()
    flashes = pop_flash(request)
    notice = _notice(flashes, builder.result, builder.numbers, open_count)
    target = _flash_target(flashes)
    context: dict[str, Any] = {
        "job": builder.view,
        "panel": panel,
        "page": page_view,
        "pages": builder.page_links(number, current_key),
        "steps": builder.steps(decision),
        "total": len(builder.queue),
        "open_count": open_count,
        "decided": len(builder.queue) - open_count,
        "headline": headline(open_count, len(builder.queue), len(optional)),
        "optional": optional,
        "optional_open": panel.mode == "item" and panel.status_class == "pill-optional",
        "notice": notice,
        "focus_decision": bool(flashes) and not errors,
        "errors": [{"field_id": e.field_id, "message": e.message} for e in errors],
        "flash": [],  # shown as the decision's status line instead
        "alt_guideline": ALT_WARN_CHARS,
        "show_accepted": show_accepted,
        "list_url": f"/jobs/{builder.job_id}/review/list",
        "max_steps": MAX_STEPS,
        "result_version": builder.version,
        "stale": stale_notice(
            builder.result, builder.numbers, item, review_url(builder.job_id, item=item)
        )
        if stale
        else None,
        "decided_lines": builder.decided_lines(),
        # Undo, in the notice after a decision: only for the write this page shows.
        "undo": bool(notice) and undo_offer(request, builder.job_id, user, builder.version),
        # The item the last decision saved (app.js forgets a kept draft of it).
        "saved_target": target if ELEMENT_ID.match(target) else "",
    }
    return render(request, "review_split.html", context, status_code=status_code)


def _flash_target(flashes: list[dict[str, Any]]) -> str:
    """The element id the last notice is about ("" for a group or none)."""
    if not flashes:
        return ""
    target = flashes[-1].get("element_id")
    if not isinstance(target, str) or target.startswith((GROUP_FLASH, HEADING_GROUP_FLASH)):
        return ""
    return target


def _apply_invalid(panel: Panel, invalid: tuple[str, ReviewInvalid]) -> None:
    target, exc = invalid
    if panel.group is not None and panel.group.key == target:
        panel.group.error = " ".join(e.message for e in exc.errors)
        panel.group.values.update(exc.values)
        return
    item = panel.item
    if item is not None and item.element_id == target:
        item.edited = any(item.values.get(k, "") != v for k, v in exc.values.items())
        item.values.update(exc.values)
        item.override = exc.override
        for error in exc.errors:
            item.errors[error.field_id.split("-", 1)[0]] = error.message


# --------------------------------------------------------------------------- routes


@router.get("/jobs/{job_id}/review")
def review_screen(
    request: Request, job_id: str, user: UserDep, item: str = "", page: str = "", accepted: str = ""
) -> Response:
    return split_page(request, job_id, user, item=item, page=page, show_accepted=accepted == "1")


def live_status(check: EditCheck) -> str:
    """The one sentence the live check puts in the field's polite live region."""
    if check.unchanged:
        return ""
    if check.empty:
        return "Type the LaTeX for this equation."
    if check.blocking:
        return "Your LaTeX cannot be saved yet. " + _problems(len(check.blocking))
    if check.meaning:
        return "Your LaTeX differs from the PDF where it changes the meaning. " + _problems(
            len(check.meaning)
        )
    if check.notes:
        notes = "1 small note" if len(check.notes) == 1 else f"{len(check.notes)} small notes"
        return f"Your LaTeX can be saved, with {notes} below."
    if check.has_text_layer:
        return "Your LaTeX can be saved: it matches the PDF's own text."
    return "Your LaTeX can be saved. The PDF has no text here to compare it with."


def _problems(n: int) -> str:
    return "The problem is listed below." if n == 1 else f"The {n} problems are listed below."


@router.post("/jobs/{job_id}/review/{element_id}/check", dependencies=[Depends(require_csrf)])
def live_check(
    request: Request, job_id: str, element_id: str, latex: Annotated[str, Form()] = ""
) -> Response:
    """The checks Save edit would run, on the LaTeX being typed (htmx, debounced): an HTML
    fragment, nothing stored and nothing logged but the request line. Signed out (the
    session ended in another tab), it answers 204 and changes nothing on the page, so
    the background request never takes the reviewer away from what they are typing."""
    user = current_user(request)
    if user is None:
        return Response(status_code=204)
    if not ELEMENT_ID.match(element_id):
        raise PageError(404, *NOT_FOUND)
    _, result, _ = _load(request, job_id, user)
    fix = result.fix_for(element_id)
    if fix is None or fix.kind is not Kind.EQUATION:
        raise PageError(404, *NOT_FOUND)
    element = next((e for e in result.elements if e.id == element_id), None)
    typed = latex.strip()
    if len(typed) > MAX_LATEX_CHARS:
        check = EditCheck(latex=typed, mathml=None, speech="", checks=[], display="block")
        check.blocking = [f"Keep the LaTeX under {MAX_LATEX_CHARS} characters."]
    else:
        check = check_equation_edit(element, fix, typed)
    context = {
        "check": check,
        "eid": element_id,
        "label": f"Equation {item_numbers(result).get(element_id, 0)}",
        "mathml": try_sanitize_mathml(check.mathml),
        "status": live_status(check),
    }
    # Not `render`: that would take the session's one-time notices with it.
    return get_ctx(request).templates.TemplateResponse(request, "_edit_check.html", context)


def _pct(value: float, whole: float) -> str:
    return f"{100 * value / whole:.3f}%"


def _box_rules(mark: marks.Mark, size: tuple[float, float]) -> str:
    """A mark's link on the page (percent of the page), and as custom properties for
    app.css: its frame inside the link (--fl, --ft, --fw, --fh, percent of the link) and,
    for one in a line of text, where its number goes in the margin (--tag-left)."""
    width, height = size
    lx0, ly0, lx1, ly1 = mark.link
    fx0, fy0, fx1, fy1 = mark.frame
    lw, lh = max(lx1 - lx0, 0.01), max(ly1 - ly0, 0.01)
    rule = (
        f"#box-{mark.element_id}{{left:{_pct(lx0, width)};top:{_pct(ly0, height)};"
        f"width:{_pct(lw, width)};height:{_pct(lh, height)};"
        f"--fl:{_pct(fx0 - lx0, lw)};--ft:{_pct(fy0 - ly0, lh)};"
        f"--fw:{_pct(fx1 - fx0, lw)};--fh:{_pct(fy1 - fy0, lh)}"
    )
    if mark.tag_x is not None:
        # In the margin, on the item's line: the number, and a note's or tick's label.
        middle = _pct((fy0 + fy1) / 2 - ly0, lh)
        rule += (
            f";--tag-left:calc({_pct(mark.tag_x - lx0, lw)} - 11px);--tag-right:auto"
            f";--label-top:calc({middle} - 9px)"
        )
    return rule + "}"


@router.get("/jobs/{job_id}/review/marks.css")
def marks_css(request: Request, job_id: str, user: UserDep) -> Response:
    """Where each mark goes on its page image (`web.marks`: a frame round the element and
    a clickable area of at least 24 CSS px that overlaps no other), and the size the
    proposal of each equation is drawn at under its crop. Element ids and numbers only."""
    _, result, _ = _load(request, job_id, user)
    sizes = resolve_page_sizes(request, job_id, user, result)
    elements = {e.id: e for e in result.elements}
    rules = []
    by_page: dict[int, dict[str, tuple[float, float, float, float]]] = {}
    inline = frozenset(
        e.id for e in result.elements if e.kind is Kind.EQUATION and e.display == "inline"
    )
    text: dict[int, tuple[float, float]] = {}  # how far each page's content reaches
    for e in result.elements:
        left, right = text.get(e.page, (e.bbox[0], e.bbox[2]))
        text[e.page] = (min(left, e.bbox[0]), max(right, e.bbox[2]))
    for fix in result.fixes:
        element = elements.get(fix.element_id)
        if element is not None and fix.kind in {Kind.EQUATION, Kind.FIGURE, Kind.HEADING}:
            by_page.setdefault(element.page, {})[element.id] = element.bbox
    for page, boxes in sorted(by_page.items()):
        size = sizes.get(page)
        if size is not None:
            placed = marks.place(boxes, size, inline=inline, text=text.get(page))
            rules.extend(_box_rules(m, size) for m in placed.values())
    for fix in result.fixes:
        element = elements.get(fix.element_id)
        if element is None:
            continue
        size = sizes.get(element.page)
        if fix.kind is Kind.EQUATION:
            clip = context_clip(element, size)
            geometry = compare_geometry(element, clip, max_side_px=page_images.REGION_MAX_SIDE_PX)
            rules.append(
                f"#cmp-{element.id}{{max-width:{geometry.max_width_px}px}}"
                f"#cmp-{element.id} .cmp-math{{font-size:{geometry.font_cqw}cqw;"
                f"margin-left:{geometry.left_cqw}cqw}}"
            )
    return Response(
        "\n".join(rules) + "\n", media_type="text/css; charset=utf-8", headers=IMAGE_HEADERS
    )


def _png(png: bytes) -> Response:
    return Response(png, media_type="image/png", headers=IMAGE_HEADERS)


def _render_failed(job_id: str, exc: page_images.RenderFailed, what: str) -> Response:
    log_event(logger, "page_image_failed", job_id=job_id, what=what, code=exc.code)
    if isinstance(exc, page_images.RenderLimited):  # this user's own burst: slow down
        headers = IMAGE_HEADERS | {"Retry-After": "2"}
        return Response("Too many images at once.", status_code=429, headers=headers)
    headers = IMAGE_HEADERS | {"Retry-After": "5"}
    return Response("The image could not be made.", status_code=503, headers=headers)


@router.get("/jobs/{job_id}/pages/{number}.png")
def page_image(request: Request, job_id: str, number: str, user: UserDep) -> Response:
    """One page of the job's own upload, rendered now, never stored. The render slot is
    taken before the upload is loaded (`page_images.render_slot`)."""
    page_count = _owned_page_count(request, job_id, user)
    if not number.isdigit() or not 1 <= int(number) <= max(page_count, 1):
        raise PageError(404, *NOT_FOUND)
    try:
        with page_images.render_slot(user.id):
            return _png(page_images.render_page(_upload(request, job_id, user), int(number) - 1))
    except page_images.RenderFailed as exc:
        return _render_failed(job_id, exc, "page")


@router.get("/jobs/{job_id}/regions/{element_id}.png")
def region_image(request: Request, job_id: str, element_id: str, user: UserDep) -> Response:
    """An item with the page around it: an equation a little wider than itself (its
    differing symbol outlined), a heading with the lines around it, a figure."""
    if not ELEMENT_ID.match(element_id):
        raise PageError(404, *NOT_FOUND)
    _, result, _ = _load(request, job_id, user)
    element = next((e for e in result.elements if e.id == element_id), None)
    fix = result.fix_for(element_id)
    if element is None or fix is None:
        raise PageError(404, *NOT_FOUND)
    try:
        with page_images.render_slot(user.id):
            # The same sizes as marks.css, so the crop and the proposal under it line up.
            sizes = resolve_page_sizes(request, job_id, user, result, slot_held=True)
            pdf = _upload(request, job_id, user)
            png = _render_region(pdf, element, fix, sizes.get(element.page))
    except page_images.RenderFailed as exc:
        return _render_failed(job_id, exc, fix.kind.value)
    return _png(png)


def _render_region(
    pdf: bytes, element: Element, fix: Fix, size: tuple[float, float] | None
) -> bytes:
    if fix.kind is Kind.EQUATION:
        clip = context_clip(element, size)
        geometry: CompareGeometry = compare_geometry(
            element, clip, max_side_px=page_images.REGION_MAX_SIDE_PX
        )
        return page_images.render_region(
            pdf,
            element.page,
            zoom=geometry.zoom,
            max_side=page_images.REGION_MAX_SIDE_PX,
            clip=clip,
            mark=element.bbox,
            chars=compare_equation(element, fix).pdf_marks,
            trim=True,
        )
    if fix.kind is Kind.HEADING:
        return page_images.render_region(
            pdf,
            element.page,
            zoom=HEADING_REGION_ZOOM,
            max_side=page_images.REGION_MAX_SIDE_PX,
            clip=band_clip(element, size),
            box=element.bbox,
            snap=True,
        )
    x0, y0, x1, y1 = element.bbox
    return page_images.render_region(
        pdf,
        element.page,
        zoom=FIGURE_REGION_ZOOM,
        max_side=page_images.REGION_MAX_SIDE_PX,
        clip=(x0 - 4, y0 - 4, x1 + 4, y1 + 4),
    )
