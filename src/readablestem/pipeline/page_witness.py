"""The page witness: one strong-model reading of every formula on a maths page.

One call per maths page (off by default: `FEATURE_PAGE_WITNESS`). The model sees the page
image and the page's text layer as numbered rows (the rows `page_math` shows, so the same
`PageRow`s and the same fence) and returns the LaTeX of every formula on the page, table
cells, captions and figure labels included, each anchored in the text layer by quoting the
row or rows it covers (row id, character offset, the exact characters).

The witness is a second, independent reading, never output text. `verify_formulas` reuses
`page_math.verify`, so a formula is kept only when every quote holds in its row (found
elsewhere in the row when the offset is wrong); a formula whose anchor holds nowhere is
dropped whole, and a formula that overlaps one kept before it is dropped. What survives is
a `WitnessPlan` that `witness_reconcile` lays against layout's elements.

A dense page: the rows are offered in windows that each fit the fence (`CONTEXT_CHARS`), up
to `MAX_WINDOWS` calls with the same image (rows past that are counted, never offered), and
a window whose answer ran into the output limit (an `invalid_output` outcome) is asked again
as two halves, once. A refusal is final for its window: it is recorded, not retried (the
client already has the server-side refusal fallback), and the page keeps what layout and the
crops read. Every call is reserved against the job budget by the client like any other step;
`BudgetExceeded` stops the pass and keeps the pages answered before it.

The text layer is untrusted document text: it travels only as fenced context, never in the
instructions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from readablestem.config import ConfigError, Settings
from readablestem.core.budget import BudgetExceeded
from readablestem.core.types import ModelCall
from readablestem.models.base import VisionClient, VisionRequest
from readablestem.models.pricing import UnknownModelError
from readablestem.models.prompts import load_prompt
from readablestem.pipeline import page_math
from readablestem.pipeline.page_math import Anchor, Display, PageRow, Span

log = logging.getLogger(__name__)

PROMPT_NAME = "page_witness"
PROMPT_VERSION = load_prompt(PROMPT_NAME).version

Where = Literal["text", "table_cell", "caption", "figure_label"]
CONTEXT_CHARS = page_math.CONTEXT_CHARS
# Calls for one page: windows of rows, so a dense page is read whole when it can be.
MAX_WINDOWS = 3
# A window of fewer rows than this is not split again after an answer that ran out.
SPLIT_MIN_ROWS = 6
# Pages in flight at once.
DEFAULT_CONCURRENCY = 4
# A page is a maths page when layout found an equation on it or its text layer has at least
# this many glyphs of maths fonts, Greek letters and operators (`layout._Char.math`).
MIN_MATH_GLYPHS = 3
# Pages with less text than this have nothing to read.
MIN_PAGE_CHARS = page_math.MIN_PAGE_CHARS
DEFAULT_EFFORT = "medium"
IMAGE_DPI = 200
IMAGE_MAX_SIDE_PX = 2576  # the long edge the vision models read without downscaling


# ----------------------------------------------------------------------------- schema


class WitnessFormula(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    anchors: list[Anchor] = Field(
        description="One anchor per row the formula covers, in reading order."
    )
    display: Display
    where: Where = Field(description="Where on the page the formula sits.")
    latex: str = Field(description="LaTeX of the formula, math mode only.")


class PageWitnessOutput(BaseModel):
    """Structured output of the page witness (strict: no coercion, no extra keys)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    formulas: list[WitnessFormula]


# ----------------------------------------------------------------------------- plan


@dataclass(frozen=True)
class Formula:
    """A verified formula: the text-layer segments it covers, its display, where it sits and
    its LaTeX (as the other readings are stored)."""

    span: Span
    where: Where

    @property
    def latex(self) -> str:
        return self.span.latex

    @property
    def display(self) -> Display:
        return self.span.display


@dataclass
class WitnessStats:
    """What asking and verifying did for one page (content-free)."""

    windows: int = 0  # windows of rows offered
    splits: int = 0  # windows asked again as two halves after an answer that ran out
    unoffered_rows: int = 0  # rows no window held
    failed_windows: int = 0
    refused: int = 0
    proposed: int = 0
    verified: int = 0
    relocated: int = 0
    dropped: int = 0  # an anchor that held nowhere
    overlapped: int = 0  # overlaps a formula kept before it

    def add(self, other: WitnessStats) -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass
class PageWitness:
    rows: list[PageRow]
    formulas: list[Formula] = field(default_factory=list)
    stats: WitnessStats = field(default_factory=WitnessStats)
    calls: list[ModelCall] = field(default_factory=list)
    failed: str | None = None  # outcome code of the page when no window gave an answer
    stopped: BudgetExceeded | None = None


@dataclass
class WitnessPlan:
    """The witness for a document: per 0-based page, its rows and verified formulas."""

    pages: dict[int, PageWitness] = field(default_factory=dict)

    @property
    def calls(self) -> list[ModelCall]:
        return [c for page in self.pages.values() for c in page.calls]

    @property
    def stats(self) -> WitnessStats:
        total = WitnessStats()
        for page in self.pages.values():
            total.add(page.stats)
        return total

    def formulas(self, page: int) -> list[Formula]:
        found = self.pages.get(page)
        return found.formulas if found is not None else []


# ------------------------------------------------------------------- maths pages


def math_glyphs(row_objects: Mapping[str, Any]) -> int:
    """How many characters of the page's rows are maths glyphs (`layout._Char.math`)."""
    return sum(1 for row in row_objects.values() for char in row.chars if char.math)


def is_maths_page(row_objects: Mapping[str, Any], *, equations: int = 0) -> bool:
    """Is this page one the witness reads: layout found `equations` on it, or its text layer
    has maths glyphs enough. A page of prose is not asked about."""
    return equations > 0 or math_glyphs(row_objects) >= MIN_MATH_GLYPHS


# ----------------------------------------------------------------------- windows


def windows(
    rows: Sequence[PageRow], limit: int = CONTEXT_CHARS, max_windows: int = MAX_WINDOWS
) -> tuple[list[list[PageRow]], int]:
    """The rows in runs that each fit `limit` characters of context (as
    `page_math.context_text` counts them), at most `max_windows` of them, and how many rows
    no window holds (the rows past the last window, and a row too long to fit alone)."""
    out: list[list[PageRow]] = []
    current: list[PageRow] = []
    used = 0
    left_out = 0
    for index, row in enumerate(rows):
        size = len(f"{row.id} | {row.text}") + 1
        if size > limit:
            left_out += 1
            continue
        if used + size > limit:
            out.append(current)
            current, used = [], 0
            if len(out) == max_windows:
                return out, left_out + len(rows) - index
        current.append(row)
        used += size
    if current:
        out.append(current)
    return out, left_out


# ------------------------------------------------------------------ verification


def verify_formulas(
    output: PageWitnessOutput | None, rows: Sequence[PageRow]
) -> tuple[list[Formula], WitnessStats]:
    """The formulas of `output` whose anchors all hold in `rows`, in reading order of their
    first row, and what was dropped. `page_math.verify` decides whether an anchor holds (one
    formula at a time, so each keeps the `where` it was given); a formula overlapping one kept
    before it is dropped."""
    stats = WitnessStats()
    if output is None:
        return [], stats
    kept: list[Formula] = []
    for formula in output.formulas:
        stats.proposed += 1
        expression = page_math.Expression(
            anchors=formula.anchors, display=formula.display, role="math", latex=formula.latex
        )
        spans, checked = page_math.verify(page_math.PageMathOutput(expressions=[expression]), rows)
        stats.relocated += checked.relocated
        if not spans:
            stats.dropped += 1
            continue
        span = spans[0]
        if any(_overlaps(span, other.span) for other in kept):
            stats.overlapped += 1
            continue
        kept.append(Formula(span, formula.where))
    kept.sort(
        key=lambda f: (page_math._row_number(f.span.segments[0].row), f.span.segments[0].start)
    )
    stats.verified = len(kept)
    return kept, stats


def _overlaps(a: Span, b: Span) -> bool:
    return any(
        x.row == y.row and x.start < y.end and y.start < x.end
        for x in a.segments
        for y in b.segments
    )


# ------------------------------------------------------------------------- the call


def build_request(settings: Settings, rows: Sequence[PageRow], png: bytes) -> VisionRequest:
    """The request for one window of rows of a page."""
    text, _ = page_math.context_text(rows, CONTEXT_CHARS)
    prompt = load_prompt(PROMPT_NAME)
    return VisionRequest(
        step="page_witness",
        model=settings.page_witness_model,
        prompt_version=prompt.version,
        instructions=prompt.text,
        images=[png],
        context_text=text,
        max_output_tokens=settings.page_witness_max_output_tokens,
        context_limit=CONTEXT_CHARS + 600,
        image_detail="high",
        effort=settings.anthropic_effort_page_witness or DEFAULT_EFFORT,
    )


def _ask(
    client: VisionClient,
    settings: Settings,
    window: Sequence[PageRow],
    png: bytes,
    result: PageWitness,
    *,
    may_split: bool,
) -> bool:
    """Ask for one window and add what verifies to `result`. True when it was answered."""
    request = build_request(settings, window, png)
    parsed, call = client.run(request, PageWitnessOutput)  # may raise BudgetExceeded
    result.calls.append(call)
    if parsed is not None:
        formulas, stats = verify_formulas(parsed, window)
        result.formulas.extend(formulas)
        result.stats.add(stats)
        return True
    if call.outcome == "refused":
        result.stats.refused += 1
    if call.outcome == "invalid_output" and may_split and len(window) >= SPLIT_MIN_ROWS:
        # The answer ran into the output limit (or was not valid): the page is dense, so ask
        # for the two halves of the rows. Once; a half is not split again.
        middle = len(window) // 2
        result.stats.splits += 1
        answered = False
        for half in (window[:middle], window[middle:]):
            answered = _ask(client, settings, half, png, result, may_split=False) or answered
        return answered
    result.stats.failed_windows += 1
    if result.failed is None:
        result.failed = call.outcome
    return False


def run_page(
    client: VisionClient, settings: Settings, rows: Sequence[PageRow], png: bytes
) -> PageWitness:
    """Ask for one page's formulas and verify them. Never raises for a bad answer: a
    refused, invalid or empty answer is a page with no formulas and `failed` set. A spend
    cap that stops the page is `stopped` (what was answered before it stays).
    `ConfigError` and `UnknownModelError` propagate."""
    result = PageWitness(list(rows))
    wins, left_out = windows(rows)
    result.stats.windows = len(wins)
    result.stats.unoffered_rows = left_out
    answered = False
    try:
        for window in wins:
            answered = _ask(client, settings, window, png, result, may_split=True) or answered
    except BudgetExceeded as exc:
        result.stopped = exc
        result.stats.failed_windows += 1
    if answered:
        result.failed = None  # a page with one answered window is a page with an answer
    elif result.failed is None and result.stopped is not None:
        result.failed = "budget_exceeded"
    # A formula asked for twice (the windows share no rows, so only a split half can) cannot
    # overlap; the overlap rule inside a window is `verify_formulas`'s.
    result.formulas.sort(
        key=lambda f: (page_math._row_number(f.span.segments[0].row), f.span.segments[0].start)
    )
    return result


@dataclass
class PassResult:
    plan: WitnessPlan
    stopped: BudgetExceeded | None = None
    calls: list[ModelCall] = field(default_factory=list)


def run(
    client: VisionClient,
    settings: Settings,
    pages: Mapping[int, tuple[Sequence[PageRow], Callable[[], bytes | None]]],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> PassResult:
    """The witness for the pages given: `{page: (rows, render)}` where `render()` makes the
    page's PNG (called on this thread: PyMuPDF is not thread-safe). A page with too little
    text, or whose render fails, is skipped. A budget refusal stops the pass; the pages
    answered before it stay."""
    plan = WitnessPlan()
    result = PassResult(plan)
    work: dict[int, tuple[Sequence[PageRow], bytes]] = {}
    for number, (rows, render) in sorted(pages.items()):
        if sum(len(r.text) for r in rows) < MIN_PAGE_CHARS:
            continue
        try:
            png = render()
        except Exception as exc:  # a page image is never worth failing the conversion for
            log.warning("page witness image failed: %s", type(exc).__name__)
            continue
        if png:
            work[number] = (rows, png)

    def one(number: int) -> tuple[int, PageWitness | None, Exception | None]:
        rows, png = work[number]
        try:
            return number, run_page(client, settings, rows, png), None
        except (ConfigError, UnknownModelError) as exc:
            return number, None, exc
        except Exception as exc:
            log.warning("page witness failed: %s", type(exc).__name__)
            return number, None, None

    with ThreadPoolExecutor(
        max_workers=max(1, concurrency), thread_name_prefix="pagewitness"
    ) as pool:
        outcomes = list(pool.map(one, sorted(work)))
    for number, page, error in outcomes:
        if error is not None:
            raise error
        if page is None:
            continue
        plan.pages[number] = page
        if page.stopped is not None:
            result.stopped = result.stopped or page.stopped
    result.calls = plan.calls
    return result
