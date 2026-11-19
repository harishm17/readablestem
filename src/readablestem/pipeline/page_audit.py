"""The page audit: one look at a finished page next to the page it was made from.

Nothing else compares a whole page of the output with its source. A page that lost a column,
doubled a paragraph or mixed two columns reaches the student with no decision. This step
asks one model, once a page, whether a listener would be misled, and stores what it said
as codes and places.

What one request carries, and nothing else (no other page, no file name, no account data):

1. a picture of the page at 110 dpi;
2. the page's converted text as a list of its elements in reading order, each with its id
   as stored in the result, its kind and its text (`listing`);
3. when the caller has one, a picture of the page's output as a browser draws it, 800
   pixels wide.

The listing follows rules that were each measured as a cause of false flags: a table's text
is shown once, in its table; list markers are shown; text joined from the previous page is
quoted as such; a long element is shortened with "[N words not repeated here]" and never
with an ellipsis, which a model reads as missing text.

The reply is a list of findings (`Reply`): one of six codes, the ids of the elements
concerned, a box on the page picture and, for wrong text, one of five kinds of text fault.
It holds no text of the document. `audit_page` turns it into a `PageVerdict`:

- "pass": the model answered and no finding is of high severity;
- "flagged": a finding of high severity, or the test by code below;
- "not_audited": everything else, with the reason. A refused, failed or invalid answer, an
  id that is not on the page, a box that is not on the picture, a spend cap and a listing
  too long to send are all this, and never a pass.

Before any request, a test by code: a page that has ink and no elements is flagged
`page_missing` without asking. An empty element list is never a pass.

The step flags and never changes a page. Its key is the question's key
(`models.question`), which covers the pictures, the listing, the prompt, the model, the
effort and the reply format, so a verdict is tied to the exact input it was given. In this
release it runs only from `readablestem eval page-audit`, on public and generated sets.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import pymupdf
from pydantic import BaseModel, Field

from readablestem.core.budget import BudgetExceeded
from readablestem.core.types import (
    INLINE_MARKER_RE,
    DocumentResult,
    Element,
    Kind,
    ModelCall,
)
from readablestem.models import pricing
from readablestem.models.base import VisionClient, VisionRequest
from readablestem.models.prompts import load_prompt
from readablestem.models.question import question_key
from readablestem.pipeline import page_type

if TYPE_CHECKING:
    from readablestem.config import Settings

STEP = "page_audit"
PROMPT_NAME = "page_audit"
DPI = 110
OUTPUT_WIDTH_PX = 800
DEFAULT_EFFORT = "low"
# The longest listing sent. A longer page is "not audited": cutting it would show the
# model a page with elements missing.
LISTING_CHARS = 30_000
# Words of an element shown before it is shortened: body text, a figure's labels, a table.
ELEMENT_WORDS, LABEL_WORDS, TABLE_WORDS = 300, 60, 200
# A joined-from-the-previous-page quote: whole up to 40 words, else its first 25 and last 12.
JOINED_WORDS, JOINED_HEAD, JOINED_TAIL = 40, 25, 12
# The words of the next page a paragraph's end is looked for in.
NEXT_PAGE_WORDS = 900

Code = Literal[
    "page_missing", "page_extra", "page_order", "page_structure", "page_wrong", "page_duplicated"
]
TextFault = Literal["none", "garbled", "doubled", "missing", "order", "run_together"]


class Box(BaseModel):
    """A place on the page picture: fractions of its width and height from the top left."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def on_the_page(self) -> bool:
        return 0 <= self.left < self.right <= 1 and 0 <= self.top < self.bottom <= 1


WHOLE_PAGE = Box(left=0, top=0, right=1, bottom=1)


class Finding(BaseModel):
    """One fault. Codes, ids and a box: no text of the document."""

    code: Code
    severity: Literal["high", "low"]
    elements: list[str]
    box: Box
    text_fault: TextFault


class Reply(BaseModel):
    """What the model answers: its findings, the most serious first; none for a clean page."""

    findings: list[Finding]


class PageVerdict(BaseModel):
    """What the audit says of one page. Content-free."""

    page: int = Field(ge=0)  # 0-based
    verdict: Literal["pass", "flagged", "not_audited"]
    by: Literal["code", "model"]
    findings: list[Finding] = Field(default_factory=list)
    # not_audited: an outcome of the request ("refused", "invalid_output", "timeout",
    # "api_error"), or "unknown_element", "bad_box", "cap_reached", "over_page_ceiling",
    # "listing_too_long".
    reason: str | None = None
    # The key of the question asked (None when the code answered): the exact input, prompt,
    # model, effort and reply format this verdict belongs to.
    key: str | None = None
    model: str | None = None
    cost_usd: float = 0.0


@dataclass(frozen=True)
class Listing:
    """A page's output as the request shows it, and the ids a reply may name."""

    text: str
    ids: frozenset[str]


@dataclass
class Audit:
    """The verdicts of a document's pages, in page order."""

    verdicts: list[PageVerdict] = field(default_factory=list)
    calls: list[ModelCall] = field(default_factory=list)
    stopped: str | None = None  # the scope of the cap that stopped the run

    def count(self, verdict: str) -> int:
        return sum(1 for v in self.verdicts if v.verdict == verdict)


# ----------------------------------------------------------------------------- the listing

_OWN_TAGS = re.compile(
    r"</?\s*(?:elements|joined[\s_\-]*from[\s_\-]*previous[\s_\-]*page|page[\s_\-]*info)\s*>",
    re.IGNORECASE,
)
_PROSE = (Kind.PARAGRAPH, Kind.LIST_ITEM)


def _tokens(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _page_tokens(page: pymupdf.Page) -> list[str]:
    flags = pymupdf.TEXTFLAGS_TEXT | page_type.actualtext_flag(page)  # as the page was read
    return _tokens(page.get_text(flags=flags))


def page_words(doc: pymupdf.Document) -> Callable[[int], Sequence[str]]:
    """The words printed on each page of `doc` (0-based; none for a page it does not have),
    read once a page. Words are runs of letters and digits in lower case; this is the
    `words_of` every function below takes."""
    kept: dict[int, list[str]] = {}

    def words(page: int) -> Sequence[str]:
        if page not in kept:
            inside = 0 <= page < doc.page_count
            kept[page] = _page_tokens(doc[page]) if inside else []
        return kept[page]

    return words


def page_png(doc: pymupdf.Document, page: int) -> bytes:
    """The picture of a page the request carries."""
    return doc[page].get_pixmap(dpi=DPI).tobytes("png")


def _triples(tokens: Sequence[str]) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + 3]) for i in range(len(tokens) - 2)}


def _printed_later(words: Sequence[str], here: Sequence[str], later: Sequence[str]) -> int:
    """How many words at the end of an element are printed on the next page and not on its
    own: the longest ending of which at most 15% of the word triples are on its own page and
    at least 70% on the next one. It starts at a triple that is on the next page, so the
    words that straddle the break are not counted with the next page."""
    if len(words) < 8 or not later:
        return 0
    on_here, on_later = _triples(here), _triples(later[:NEXT_PAGE_WORDS])
    triples = [tuple(_tokens(" ".join(words[k : k + 3]))[:3]) for k in range(len(words) - 2)]
    best = seen_here = seen_later = 0
    for k in range(len(triples) - 1, -1, -1):
        seen_here += triples[k] in on_here
        seen_later += triples[k] in on_later
        length = len(triples) - k
        fits = seen_here <= 0.15 * length and seen_later >= 0.7 * length
        starts_there = triples[k] in on_later and triples[k] not in on_here
        if length >= 4 and fits and starts_there:
            best = len(words) - k
    return best


def _shortened(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    left_out = len(words) - limit
    return (
        f"{' '.join(words[:head])} [{left_out} words not repeated here] {' '.join(words[-tail:])}"
    )


def _one_line(text: str | None) -> str:
    """Document text for one line of the listing: no line breaks, and none of this
    listing's own tags, so text cannot end the list or start a note."""
    return " ".join(_OWN_TAGS.sub("", text or "").split())


def _heard(element: Element, equations: Mapping[str, Element]) -> str:
    """An element's text with each inline formula where it is read."""

    def formula(match: re.Match[str]) -> str:
        inline = equations.get(match.group(1))
        return f" {(inline.text if inline else None) or '[formula]'} "

    return _one_line(INLINE_MARKER_RE.sub(formula, element.text or ""))


def _pages_of(element: Element) -> range:
    return range(element.page, (element.last_page or element.page) + 1)


def _joined(
    result: DocumentResult,
    page: int,
    equations: Mapping[str, Element],
    words_of: Callable[[int], Sequence[str]],
) -> str:
    """The text printed at the top of `page` that was read with an earlier page: the part
    on this page of the last paragraph that began before it."""
    before = [
        e
        for e in result.elements
        if e.kind in _PROSE and e.page < page and (e.page == page - 1 or page in _pages_of(e))
    ]
    for element in sorted(before, key=lambda e: e.order, reverse=True):
        words = _heard(element, equations).split()
        earlier = [w for p in range(element.page, page) for w in words_of(p)]
        from_here = _printed_later(words, earlier, words_of(page))
        if not from_here:
            continue
        after = 0
        if page + 1 in _pages_of(element):
            after = _printed_later(words, [*earlier, *words_of(page)], words_of(page + 1))
        part = words[len(words) - from_here : len(words) - after]
        if len(part) <= JOINED_WORDS:
            return " ".join(part)
        left_out = len(part) - JOINED_HEAD - JOINED_TAIL
        return (
            f"{' '.join(part[:JOINED_HEAD])} [{left_out} words not repeated here] "
            f"{' '.join(part[-JOINED_TAIL:])}"
        )
    return ""


def _line(
    element: Element,
    result: DocumentResult,
    equations: Mapping[str, Element],
    words_of: Callable[[int], Sequence[str]],
) -> str:
    text = _heard(element, equations)
    caption = _one_line(element.caption or (element.table.caption if element.table else None))
    label = element.kind.value
    if element.kind is Kind.HEADING:
        fix = result.fix_for(element.id)
        if fix is not None and fix.heading is not None:
            label = f"heading, level {fix.heading.level}"
    elif element.kind is Kind.LIST_ITEM:
        label = "list item"
        if (element.list_level or 1) > 1:
            label += f", level {element.list_level}"
        text = f"{_one_line(element.list_marker)} {text}".strip()
    if element.kind is Kind.FIGURE:
        shown = "[picture]"
        shown += f" labels: {_shortened(text, LABEL_WORDS)}" if text else ""
        shown += f" caption: {caption}" if caption else ""
    elif element.kind is Kind.TABLE:
        shown = f"caption: {caption} | " if caption else ""
        shown += f"cells: {_shortened(text, TABLE_WORDS)}"
    elif element.kind is Kind.EQUATION:
        shown = text or "[formula]"
    else:
        shown = _shortened(text, ELEMENT_WORDS)
        words = text.split()
        later = 0
        if element.kind in _PROSE:
            later = _printed_later(words, words_of(element.page), words_of(element.page + 1))
        if later:
            start = " ".join(words[-later:][:5])
            shown += (
                f' [continues on the next page: the last {later} words, from "{start}", '
                "are printed there]"
            )
        shown += f" caption: {caption}" if caption else ""
    return f"{element.id} ({label}): {shown}"


def listing(result: DocumentResult, page: int, words_of: Callable[[int], Sequence[str]]) -> Listing:
    """What the request says of `page` (0-based): where it is in the document, the text
    read with an earlier page, and its elements in reading order. An inline formula is
    shown inside its text and not a second time."""
    equations = {e.id: e for e in result.elements if e.kind is Kind.EQUATION}
    inline = {
        match.group(1) for e in result.elements for match in INLINE_MARKER_RE.finditer(e.text or "")
    }
    shown = [
        e
        for e in sorted(result.elements, key=lambda e: e.order)
        if e.page == page and e.id not in inline and e.parent_id is None
    ]
    pages = max([page + 1, len(result.page_sizes), result.stats.pages])
    parts = [f"<page_info>Page {page + 1} of {pages}.</page_info>"]
    joined = _joined(result, page, equations, words_of)
    if joined:
        parts.append(f"<joined_from_previous_page>{joined}</joined_from_previous_page>")
    lines = [_line(e, result, equations, words_of) for e in shown]
    parts += ["<elements>", *lines, "</elements>"]
    return Listing("\n".join(parts), frozenset(e.id for e in shown))


# -------------------------------------------------------------------------------- the call


def has_ink(result: DocumentResult, page: int, words_of: Callable[[int], Sequence[str]]) -> bool:
    """Whether something is printed or drawn on `page`: its page type when the result has
    one, else whether the page has words."""
    kinds = {t.page: t.kind for t in result.page_types}
    if page in kinds:
        return kinds[page] != "blank"
    return bool(words_of(page))


def build_request(
    settings: Settings, shown: Listing, page_picture: bytes, output_picture: bytes | None = None
) -> VisionRequest:
    """The request for one page: its picture, the listing, and the picture of its output
    when there is one."""
    prompt = load_prompt(PROMPT_NAME)
    return VisionRequest(
        step=STEP,
        model=settings.page_audit_model,
        prompt_version=prompt.version,
        instructions=prompt.text,
        images=[page_picture] if output_picture is None else [page_picture, output_picture],
        context_text=shown.text,
        max_output_tokens=settings.page_audit_max_output_tokens,
        context_limit=LISTING_CHARS,
        image_detail="high",
        effort=settings.anthropic_effort_page_audit or DEFAULT_EFFORT,
    )


def worst_case_usd(request: VisionRequest) -> float:
    """The most one request can cost: what the per-page ceiling is held against."""
    return pricing.estimate_max_cost_usd(
        request.model,
        n_images=len(request.images),
        text_chars=len(request.context_text or ""),
        max_output_tokens=request.max_output_tokens,
        images=request.images,
    )


def _judged(
    reply: Reply, ids: frozenset[str]
) -> tuple[Literal["pass", "flagged", "not_audited"], str | None, list[Finding]]:
    """(verdict, reason, findings) of a reply that parsed. A finding of an element that is
    not on the page, or with a box off the picture, makes the whole reply unusable."""
    if any(one not in ids for finding in reply.findings for one in finding.elements):
        return "not_audited", "unknown_element", []
    if not all(finding.box.on_the_page for finding in reply.findings):
        return "not_audited", "bad_box", []
    findings = [
        f if f.code == "page_wrong" else f.model_copy(update={"text_fault": "none"})
        for f in reply.findings
    ]
    flagged = any(f.severity == "high" for f in findings)
    return ("flagged" if flagged else "pass"), None, findings


def audit_page(
    client: VisionClient,
    settings: Settings,
    result: DocumentResult,
    page: int,
    page_picture: Callable[[int], bytes],
    words_of: Callable[[int], Sequence[str]],
    output_picture: Callable[[int], bytes | None] | None = None,
) -> tuple[PageVerdict, ModelCall | None]:
    """The verdict on one page (0-based), and the request's record when one was sent.
    Raises `BudgetExceeded` when the spend reservation is refused; never for a bad answer."""
    shown = listing(result, page, words_of)
    if not shown.ids:
        if not has_ink(result, page, words_of):
            return PageVerdict(page=page, verdict="pass", by="code"), None
        missing = Finding(
            code="page_missing", severity="high", elements=[], box=WHOLE_PAGE, text_fault="none"
        )
        return PageVerdict(page=page, verdict="flagged", by="code", findings=[missing]), None

    def unasked(reason: str) -> tuple[PageVerdict, None]:
        return PageVerdict(page=page, verdict="not_audited", by="code", reason=reason), None

    if len(shown.text) > LISTING_CHARS:
        return unasked("listing_too_long")
    drawn = output_picture(page) if output_picture is not None else None
    request = build_request(settings, shown, page_picture(page), drawn)
    if worst_case_usd(request) > settings.page_audit_max_usd_per_page:
        return unasked("over_page_ceiling")
    reply, call = client.run(request, Reply)
    if reply is None:
        verdict, reason, findings = "not_audited", call.outcome, []
    else:
        verdict, reason, findings = _judged(reply, shown.ids)
    return (
        PageVerdict(
            page=page,
            verdict=verdict,
            by="model",
            findings=findings,
            reason=reason,
            key=question_key(settings, request, Reply),
            model=call.model,
            cost_usd=call.cost_usd,
        ),
        call,
    )


def pages_of(result: DocumentResult) -> list[int]:
    """The pages of a result the audit looks at: every page it holds. A page the reviewer
    left out is not in the output, so there is nothing to audit on it."""
    left_out = {o.page for o in result.omitted}
    return [p for p in sorted(result.page_sizes) if p not in left_out]


def audit(
    client: VisionClient,
    settings: Settings,
    result: DocumentResult,
    page_picture: Callable[[int], bytes],
    words_of: Callable[[int], Sequence[str]],
    output_picture: Callable[[int], bytes | None] | None = None,
    pages: Iterable[int] | None = None,
) -> Audit:
    """Audit the pages of one document, one request a page. A spend cap stops the run:
    the page it stopped at and every page after it are "not audited", and `stopped` names
    the cap."""
    done = Audit()
    for page in pages_of(result) if pages is None else pages:
        if done.stopped is not None:
            done.verdicts.append(
                PageVerdict(page=page, verdict="not_audited", by="code", reason="cap_reached")
            )
            continue
        try:
            verdict, call = audit_page(
                client, settings, result, page, page_picture, words_of, output_picture
            )
        except BudgetExceeded as exc:
            done.stopped = exc.scope
            verdict, call = (
                PageVerdict(page=page, verdict="not_audited", by="code", reason="cap_reached"),
                None,
            )
        done.verdicts.append(verdict)
        if call is not None:
            done.calls.append(call)
    return done
