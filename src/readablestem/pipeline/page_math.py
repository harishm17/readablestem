"""Page math pass: where is the mathematics on a page? (ADR 0006)

One model call per page, after layout and before the crops. The model sees the page image
and the page's text layer as numbered rows, the rows layout works with, and proposes the
page's math expressions: each anchored in the text layer by quoting it (row id, character
offset, the exact characters), with a display (inline or block), a role (math, an exercise
or item number, an equation number, something that looks like maths and is not) and its
LaTeX reading.

Nothing the model says becomes output text. `verify` accepts an expression only when every
quote equals the row's text where it says (or, failing that, where the quote is found in the
row); an expression with an anchor that does not hold is dropped whole. What survives is a
`MathPlan`: spans of the text layer that `page_math_reconcile` lays over layout's own
decisions, and a second reading that the equation step compares with its own. The text of
the document is still the PDF's, and the maths is still read from a crop.

The text layer is untrusted document text: it travels only as fenced context (with the
length limit raised, `VisionRequest.context_limit`), never in the instructions.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from readablestem.config import ConfigError, Settings
from readablestem.core.budget import BudgetExceeded
from readablestem.core.types import ModelCall
from readablestem.models.base import VisionClient, VisionRequest
from readablestem.models.pricing import UnknownModelError
from readablestem.models.prompts import load_prompt

log = logging.getLogger(__name__)

PROMPT_NAME = "page_math"
PROMPT_VERSION = load_prompt(PROMPT_NAME).version

Role = Literal["math", "exercise_or_item_number", "equation_number", "not_math_but_looks_like_it"]
Display = Literal["inline", "block"]
ROLES: tuple[str, ...] = (
    "math",
    "exercise_or_item_number",
    "equation_number",
    "not_math_but_looks_like_it",
)

# Characters of text layer sent for one page. The fence scans 8,000 characters for tag names
# (`openai_client._CONTEXT_SCAN_CHARS`); rows past this many are not offered to the model.
CONTEXT_CHARS = 7600
MAX_OUTPUT_TOKENS = 4096  # the default of `Settings.page_math_max_output_tokens`
# A page with less text than this has nothing to place.
MIN_PAGE_CHARS = 4
# Pages in flight at once.
DEFAULT_CONCURRENCY = 4
# Sentence punctuation left off the end of an inline span.
_TRAILING = ",.;:"
# Measured on dev slice A (docs/eval-page-math.md): the model's own spans match the answer key's
# at 0.20 (low) against 0.32 (medium) by fidelity, for 0.001 and 0.0016 dollars a page.
DEFAULT_EFFORT = "medium"


# ----------------------------------------------------------------------------- rows


@dataclass(frozen=True)
class PageRow:
    """One row of a page's text layer as the model sees it.

    `text`: the row's characters, control characters out, whitespace collapsed, ends
    trimmed (layout's `_clean` without the negation join, so one character of `text` is one
    character of the row). `chars[i]` is the index in layout's row of `text[i]`. Offsets in
    an anchor are offsets into `text`."""

    id: str
    text: str
    chars: tuple[int, ...]


def context_text(rows: Sequence[PageRow], limit: int = CONTEXT_CHARS) -> tuple[str, list[str]]:
    """The text layer as lines `L3 | text`, and the ids that fit in `limit` characters."""
    lines: list[str] = []
    ids: list[str] = []
    used = 0
    for row in rows:
        line = f"{row.id} | {row.text}"
        if used + len(line) + 1 > limit:
            break
        lines.append(line)
        ids.append(row.id)
        used += len(line) + 1
    return "\n".join(lines), ids


# ----------------------------------------------------------------------------- schema


class Anchor(BaseModel):
    """Where in the text layer: a row, the offset in its text, the exact characters."""

    model_config = ConfigDict(strict=True, extra="forbid")

    line: str = Field(description="Row id as shown, for example L12.")
    start: int = Field(description="0-based character offset of the quote in the row's text.")
    quote: str = Field(description="The characters, copied exactly from the row's text.")


class Expression(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    anchors: list[Anchor] = Field(
        description="One anchor per row the expression covers, in reading order."
    )
    display: Display
    role: Role
    latex: str = Field(description="LaTeX of the mathematics; the text itself for other roles.")


class PageMathOutput(BaseModel):
    """Structured output of the page math pass (strict: no coercion, no extra keys)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    expressions: list[Expression]


# ----------------------------------------------------------------------------- plan


@dataclass(frozen=True)
class Segment:
    """Characters `start:end` of the text of row `row`."""

    row: str
    start: int
    end: int


@dataclass(frozen=True)
class Span:
    """A verified expression: the segments it covers, its display, role and LaTeX."""

    segments: tuple[Segment, ...]
    display: Display
    role: Role
    latex: str


@dataclass
class PageStats:
    """What verification did with one page's expressions (content-free)."""

    proposed: int = 0
    verified: int = 0
    relocated: int = 0  # the quote was found elsewhere in its row
    dropped: int = 0  # an anchor held nowhere
    overlapped: int = 0  # dropped: overlaps a span kept before it
    unknown_line: int = 0

    def add(self, other: PageStats) -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass
class PagePlan:
    rows: list[PageRow]
    spans: list[Span]
    stats: PageStats = field(default_factory=PageStats)
    calls: list[ModelCall] = field(default_factory=list)
    failed: str | None = None  # outcome code when the page pass gave nothing


@dataclass
class MathPlan:
    """The page pass for a document: per 0-based page, its rows and verified spans."""

    pages: dict[int, PagePlan] = field(default_factory=dict)

    @property
    def calls(self) -> list[ModelCall]:
        return [c for plan in self.pages.values() for c in plan.calls]

    @property
    def stats(self) -> PageStats:
        total = PageStats()
        for plan in self.pages.values():
            total.add(plan.stats)
        return total

    def spans(self, page: int) -> list[Span]:
        plan = self.pages.get(page)
        return plan.spans if plan is not None else []

    def rows(self, page: int) -> list[PageRow]:
        plan = self.pages.get(page)
        return plan.rows if plan is not None else []


# ------------------------------------------------------------------ verification


def _folded(text: str) -> tuple[str, list[int]]:
    """`text` without whitespace, NFKC per character (a maths italic letter becomes its plain
    letter), and the index in `text` of each folded character."""
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        for g in unicodedata.normalize("NFKC", ch):
            out.append(g)
            index.append(i)
    return "".join(out), index


def locate(text: str, quote: str, start: int) -> tuple[int, int, bool] | None:
    """Where `quote` is in the row's `text`: `(start, end, relocated)`, or None.

    First exactly at `start`; then anywhere in the row as the same characters (whitespace
    and compatibility forms ignored), the occurrence nearest `start`."""
    if not quote.strip():
        return None
    if 0 <= start <= len(text) and text[start : start + len(quote)] == quote:
        return start, start + len(quote), False
    folded_text, index = _folded(text)
    folded_quote, _ = _folded(quote)
    if not folded_quote:
        return None
    hits: list[int] = []
    at = folded_text.find(folded_quote)
    while at != -1:
        hits.append(at)
        at = folded_text.find(folded_quote, at + 1)
    if not hits:
        return None
    best = min(hits, key=lambda h: abs(index[h] - start))
    first = index[best]
    last = index[best + len(folded_quote) - 1] + 1
    return first, last, True


def _trim(text: str, start: int, end: int, *, inline: bool) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and (text[end - 1].isspace() or (inline and text[end - 1] in _TRAILING)):
        end -= 1
    return start, end


def _row_number(row_id: str) -> int:
    match = re.fullmatch(r"L(\d+)", row_id.strip())
    return int(match.group(1)) if match else -1


def verify(output: PageMathOutput | None, rows: Sequence[PageRow]) -> tuple[list[Span], PageStats]:
    """The expressions of `output` whose anchors all hold in `rows`, as `Span`s in reading
    order (the order of the first row), and what was dropped.

    An inline expression has one anchor. A block expression over several rows has its
    anchors sorted by row. A span that overlaps one kept before it is dropped; a segment of
    an expression overlapping a number or look-alike span is cut back to clear it, or the
    expression is dropped when the number lies inside it."""
    stats = PageStats()
    if output is None:
        return [], stats
    by_id = {row.id: row for row in rows}
    candidates: list[Span] = []
    for expression in output.expressions:
        stats.proposed += 1
        segments: list[Segment] = []
        ok = bool(expression.anchors)
        for anchor in expression.anchors:
            row = by_id.get(anchor.line.strip())
            if row is None:
                stats.unknown_line += 1
                ok = False
                break
            found = locate(row.text, anchor.quote, anchor.start)
            if found is None:
                ok = False
                break
            start, end, moved = found
            start, end = _trim(
                row.text,
                start,
                end,
                inline=expression.display == "inline"
                and expression.role == "math"
                and len(expression.anchors) == 1,
            )
            if end <= start:
                ok = False
                break
            stats.relocated += moved
            segments.append(Segment(row.id, start, end))
        if not ok:
            stats.dropped += 1
            continue
        if expression.display == "inline" and len(segments) != 1:
            stats.dropped += 1
            continue
        segments.sort(key=lambda s: (_row_number(s.row), s.start))
        candidates.append(
            Span(tuple(segments), expression.display, expression.role, expression.latex)
        )
    spans = [_retrimmed(span, by_id) for span in _resolve_overlaps(candidates, stats)]
    spans = [s for s in spans if s.segments]
    stats.verified = len(spans)
    return spans, stats


def _retrimmed(span: Span, rows: dict[str, PageRow]) -> Span:
    """The span with blanks trimmed off its segments again (cutting a number off the front
    leaves the space after it) and any segment left empty dropped."""
    segments: list[Segment] = []
    inline = span.display == "inline" and span.role == "math" and len(span.segments) == 1
    for seg in span.segments:
        start, end = _trim(rows[seg.row].text, seg.start, seg.end, inline=inline)
        if end > start:
            segments.append(Segment(seg.row, start, end))
    return Span(tuple(segments), span.display, span.role, span.latex)


def _overlap(a: Segment, b: Segment) -> bool:
    return a.row == b.row and a.start < b.end and b.start < a.end


def _resolve_overlaps(candidates: list[Span], stats: PageStats) -> list[Span]:
    """Numbers and look-alikes first (they carve out of maths), then maths in the order given;
    a maths span overlapping an earlier one is dropped."""
    carve = [s for s in candidates if s.role != "math"]
    carved = [seg for span in carve for seg in span.segments]
    kept: list[Span] = list(carve)
    taken: list[Segment] = []  # the maths kept so far
    for span in candidates:
        if span.role != "math":
            continue
        segments: list[Segment] = []
        dropped = False
        for seg in span.segments:
            for other in carved:
                if not _overlap(seg, other):
                    continue
                if other.start <= seg.start < other.end:  # a number at the front
                    seg = Segment(seg.row, other.end, seg.end)
                elif other.start < seg.end <= other.end:  # at the back
                    seg = Segment(seg.row, seg.start, other.start)
                else:  # inside: nothing sensible is left
                    dropped = True
                    break
                if seg.end <= seg.start:
                    dropped = True
                    break
            if dropped:
                break
            segments.append(seg)
        if dropped or not segments or any(_overlap(a, b) for a in segments for b in taken):
            stats.overlapped += 1
            continue
        taken.extend(segments)
        kept.append(Span(tuple(segments), span.display, span.role, span.latex))
    kept.sort(key=lambda s: (_row_number(s.segments[0].row), s.segments[0].start))
    return kept


# ------------------------------------------------------------------------- the call


def build_request(
    settings: Settings, rows: Sequence[PageRow], png: bytes
) -> tuple[VisionRequest, list[str]]:
    """The request for one page and the row ids it offers."""
    text, ids = context_text(rows)
    prompt = load_prompt(PROMPT_NAME)
    return (
        VisionRequest(
            step="page_math",
            model=settings.page_math_model or settings.cheap_model,
            prompt_version=prompt.version,
            instructions=prompt.text,
            images=[png],
            context_text=text,
            max_output_tokens=settings.page_math_max_output_tokens,
            context_limit=CONTEXT_CHARS + 600,
            effort=settings.anthropic_effort_page_math or DEFAULT_EFFORT,
        ),
        ids,
    )


def run_page(
    client: VisionClient, settings: Settings, rows: Sequence[PageRow], png: bytes
) -> PagePlan:
    """Ask for one page's expressions and verify them. Never raises for a bad answer: a
    refused, invalid or empty answer is a plan with no spans and `failed` set.
    `BudgetExceeded`, `ConfigError` and `UnknownModelError` propagate."""
    request, ids = build_request(settings, rows, png)
    offered = [row for row in rows if row.id in set(ids)]
    parsed, call = client.run(request, PageMathOutput)
    if parsed is None:
        return PagePlan(list(rows), [], calls=[call], failed=call.outcome)
    spans, stats = verify(parsed, offered)
    return PagePlan(list(rows), spans, stats, [call])


@dataclass
class PassResult:
    plan: MathPlan
    stopped: BudgetExceeded | None = None
    calls: list[ModelCall] = field(default_factory=list)


def run(
    client: VisionClient,
    settings: Settings,
    pages: Mapping[int, tuple[Sequence[PageRow], Callable[[], bytes | None]]],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> PassResult:
    """The page pass for the pages given: `{page: (rows, render)}` where `render()` makes the
    page's PNG (called on this thread: PyMuPDF is not thread-safe). A page with too little
    text, or whose render fails, is skipped. A budget refusal stops the pass; the pages
    answered before it stay."""
    plan = MathPlan()
    result = PassResult(plan)
    work: dict[int, tuple[Sequence[PageRow], bytes]] = {}
    for number, (rows, render) in sorted(pages.items()):
        if sum(len(r.text) for r in rows) < MIN_PAGE_CHARS:
            continue
        try:
            png = render()
        except Exception as exc:  # a page image is never worth failing the conversion for
            log.warning("page image failed: %s", type(exc).__name__)
            continue
        if png:
            work[number] = (rows, png)

    def one(number: int) -> tuple[int, PagePlan | None, Exception | None]:
        rows, png = work[number]
        try:
            return number, run_page(client, settings, rows, png), None
        except (BudgetExceeded, ConfigError, UnknownModelError) as exc:
            return number, None, exc
        except Exception as exc:
            log.warning("page math failed: %s", type(exc).__name__)
            return number, None, None

    with ThreadPoolExecutor(max_workers=max(1, concurrency), thread_name_prefix="pagemath") as pool:
        outcomes = list(pool.map(one, sorted(work)))
    for number, page_plan, error in outcomes:
        if isinstance(error, (ConfigError, UnknownModelError)):
            raise error
        if isinstance(error, BudgetExceeded):
            result.stopped = error
            continue
        if page_plan is not None:
            plan.pages[number] = page_plan
    result.calls = plan.calls
    return result


# -------------------------------------------------------------------- two readings


_STYLE_PREFIX = re.compile(r"^[a-z][a-z-]*:(?=.)")


def _unstyled(stream: list[str]) -> list[str]:
    """The stream without the style that letters carry (`bold:d`): the two readings of a span
    are compared for symbols, and a letter's weight is the text-layer checks' business
    (`equation.text_layer_style`), where the PDF's own glyphs say what it is. The page pass
    reads a bold vector as a plain letter often enough that comparing weight made it
    disagree with a right answer."""
    return [_STYLE_PREFIX.sub("", atom) for atom in stream]


def latex_stream(latex: str) -> list[str] | None:
    """The normalised symbol stream of a LaTeX reading (`eval.equations_symbols`, the
    stream the eval's secondary scorer compares) without letter styles; None if it does not
    convert."""
    from readablestem.eval import equations_symbols as symbols
    from readablestem.eval.equations import MathMLError

    try:
        return _unstyled(symbols.symbols_of_latex(latex))
    except (MathMLError, ValueError, RecursionError):
        return None


def mathml_stream(mathml: str) -> list[str] | None:
    """The same stream for MathML (the equation step's reading)."""
    from readablestem.eval import equations_symbols as symbols
    from readablestem.eval.equations import MathMLError

    try:
        return _unstyled(symbols.symbols_of(mathml))
    except (MathMLError, ValueError, RecursionError):
        return None
