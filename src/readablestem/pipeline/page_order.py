"""The model's page order: which lines belong together, and in what order are they read.

One model call per page, between two layout passes (the shape of the page math pass, ADR 0006).
The first pass hands layout's items for each page to `PageOrderHook`: the lines of text it
found (`r<k>`), each display equation (`e<k>`), each figure and table (`f<k>`, `t<k>`), in the
order the rules would read them. The model sees the page picture and those items as a numbered
list, and answers with blocks (each a list of item numbers and a kind) in reading order and the
numbers of the lines that are furniture. `verify` checks the answer; the second pass hands the
verified answer back to layout, which builds its runs and its order from it
(`layout.page._runs_by_answer`) and runs every later step of the rules on them, so kinds,
markers and checks are still layout's own.

Nothing the model says becomes output text: it chooses which of layout's lines go together,
in what order, and which are left out as furniture. The text of every line is still the PDF's,
and an equation, figure or table is still the item layout found.

The answer is used only when it holds: every number is an item of the page's list, none is named
twice, and at least one is named. An answer that does not parse, or does not hold, is no answer:
the rules read that page. The items' text is untrusted document text: it travels only as fenced
context (with the length limit raised, `VisionRequest.context_limit`), never in the instructions.

Off by default (`Settings.feature_layout_model_order`); `docs/layout.md` ("The model's page
order") has what it was measured to do.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from readablestem.config import ConfigError, Settings
from readablestem.core.budget import BudgetExceeded
from readablestem.core.types import ModelCall
from readablestem.models import router
from readablestem.models.base import VisionClient, VisionRequest
from readablestem.models.pricing import UnknownModelError
from readablestem.models.prompts import load_prompt

log = logging.getLogger(__name__)

PROMPT_NAME = "page_order"
PROMPT_VERSION = load_prompt(PROMPT_NAME).version

# Characters of item list sent for one page (the fence keeps as many as a request asks for).
# The longest list of the 267 development pages is about 10,600; items past this many are not
# offered to the model (the rules keep them, after the ones that are named).
CONTEXT_CHARS = 30_000
# Measured on the 267 development pages of layout-dev-1 (docs/layout.md): the cheap model at this
# effort answered 187 pages right where the rules' 182 were, for about $0.0012 a page.
DEFAULT_EFFORT = "medium"
# Pages in flight at once.
DEFAULT_CONCURRENCY = 4
# Characters of an equation's text shown (the rest says nothing about where it goes).
EQUATION_CHARS = 200

Size = tuple[float, float]  # a page's width and height

Kind = Literal[
    "heading",
    "paragraph",
    "list_item",
    "caption",
    "footnote",
    "equation",
    "figure",
    "table",
    "figure_text",
    "other",
]


# ----------------------------------------------------------------------------- schema


class OrderBlock(BaseModel):
    """One block of the page: its kind and the numbers of its items, in reading order."""

    model_config = ConfigDict(strict=True, extra="forbid")

    kind: Kind
    items: list[int]


class PageOrderOutput(BaseModel):
    """Structured output of the page order step (strict: no coercion, no extra keys)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    blocks: list[OrderBlock]
    furniture: list[int]


# ------------------------------------------------------------------------------ items


def listing(items: Sequence[Mapping[str, Any]], size: Size) -> tuple[str, int]:
    """The page's items as the model is shown them, one a line (`<number> | <left>,<top> |
    <kind> | ...`, the edges as a percentage of the page), and how many of them fit in
    `CONTEXT_CHARS` characters. The number of an item is its place in `items`."""
    width, height = size
    lines: list[str] = []
    used = 0
    for number, item in enumerate(items):
        x, y = 100 * item["box"][0] / width, 100 * item["box"][1] / height
        kind = item["kind"]
        if kind == "line":
            mark = " | rotated" if item.get("rotated") else ""
            line = f"{number} | {x:.0f},{y:.0f} | line | {item['size']:.0f}pt{mark} | "
            line += _one_line(item["text"])
        elif kind == "equation":
            text = _one_line(item["text"])[:EQUATION_CHARS]
            line = f"{number} | {x:.0f},{y:.0f} | equation | {text}"
        else:
            line = f"{number} | {x:.0f},{y:.0f} | {kind}"
        if used + len(line) + 1 > CONTEXT_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines), len(lines)


def _one_line(text: str) -> str:
    """`text` on one line: a line break in an item's text would start a line the model reads
    as another item."""
    return " ".join(text.split())


def fingerprint(items: Sequence[Mapping[str, Any]]) -> str:
    """A hash of the items of a page (ids, kinds, boxes, texts): the second layout pass shows
    the same items when the page is the page the model was asked about. Never stored."""
    digest = hashlib.sha256()
    for item in items:
        digest.update(
            json.dumps(
                [item["id"], item["kind"], item["box"], item.get("text")], ensure_ascii=False
            ).encode("utf-8")
        )
    return digest.hexdigest()


# ------------------------------------------------------------------------------ plan


@dataclass
class PagePlan:
    """The step's work on one page. `answer` is the verified answer in item ids (what the hook
    gives layout), None when the rules read the page; `failed` says why in that case: a
    `ModelCall.outcome` code (the call gave nothing) or why verification refused the answer
    ("unknown_number", "repeated_number", "no_numbers")."""

    ids: list[str]
    fingerprint: str
    answer: dict[str, Any] | None = None
    calls: list[ModelCall] = field(default_factory=list)
    failed: str | None = None
    unoffered: int = 0  # items of the page left out of the list the model saw


@dataclass
class OrderPlan:
    """The step for a document: per 0-based page, its plan."""

    pages: dict[int, PagePlan] = field(default_factory=dict)

    @property
    def calls(self) -> list[ModelCall]:
        return [c for page in self.pages.values() for c in page.calls]


def verify(output: PageOrderOutput, ids: Sequence[str]) -> tuple[dict[str, Any] | None, str | None]:
    """The model's answer in the items' own ids, or why it is no answer: a number that is no
    item of the list shown (`unknown_number`), one named twice (`repeated_number`), or none
    named (`no_numbers`). `ids` are the items the model was shown, in the order shown."""
    named = [number for block in output.blocks for number in block.items] + output.furniture
    if not named:
        return None, "no_numbers"
    if any(not 0 <= number < len(ids) for number in named):
        return None, "unknown_number"
    if len(set(named)) != len(named):
        return None, "repeated_number"
    return {
        "blocks": [
            {"kind": block.kind, "items": [ids[number] for number in block.items]}
            for block in output.blocks
        ],
        "furniture": [ids[number] for number in output.furniture],
    }, None


# ------------------------------------------------------------------------- the call


def build_request(settings: Settings, text: str, png: bytes) -> VisionRequest:
    """The request for one page: its picture and its numbered list of items."""
    prompt = load_prompt(PROMPT_NAME)
    return VisionRequest(
        step="page_order",
        model=settings.page_order_model or settings.cheap_model,
        prompt_version=prompt.version,
        instructions=prompt.text,
        images=[png],
        context_text=text,
        max_output_tokens=settings.page_order_max_output_tokens,
        context_limit=CONTEXT_CHARS + 600,
        effort=settings.anthropic_effort_page_order or DEFAULT_EFFORT,
    )


def run_page(
    client: VisionClient,
    settings: Settings,
    items: Sequence[Mapping[str, Any]],
    size: Size,
    png: bytes,
) -> PagePlan:
    """Ask for one page's grouping and order and verify it. Never raises for a bad answer: a
    refused, invalid or unverified answer is a plan with no answer and `failed` set.
    `BudgetExceeded`, `ConfigError` and `UnknownModelError` propagate."""
    text, offered = listing(items, size)
    ids = [str(item["id"]) for item in items]
    plan = PagePlan(ids, fingerprint(items), unoffered=len(ids) - offered)
    request = build_request(settings, text, png)
    # A refusal or a service error is asked once more of the other model (`models.router`);
    # a second failure is the page's `failed` reason and nothing more is sent for it.
    parsed, calls = router.call_with_fallback(
        client, request, PageOrderOutput, settings=settings, retry_on=router.PAGE_RETRYABLE
    )
    plan.calls = calls
    if parsed is None:
        plan.failed = calls[-1].outcome
        return plan
    plan.answer, plan.failed = verify(parsed, ids[:offered])
    return plan


@dataclass
class PassResult:
    plan: OrderPlan
    stopped: BudgetExceeded | None = None
    calls: list[ModelCall] = field(default_factory=list)


def run(
    client: VisionClient,
    settings: Settings,
    pages: Mapping[int, tuple[Sequence[Mapping[str, Any]], Size, Callable[[], bytes | None]]],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> PassResult:
    """The step for the pages given: `{page: (items, size, render)}` where `size` is the page's
    width and height in the frame of the items' boxes and `render()` makes the page's PNG
    (called on this thread: PyMuPDF is not thread-safe). A page with no item or whose render
    fails is skipped. A budget refusal stops the pass; the pages answered before it stay."""
    plan = OrderPlan()
    result = PassResult(plan)
    work: dict[int, tuple[Sequence[Mapping[str, Any]], Size, bytes]] = {}
    for number, (items, size, render) in sorted(pages.items()):
        if not items or min(size) <= 0:
            continue
        try:
            png = render()
        except Exception as exc:  # a page image is never worth failing the conversion for
            log.warning("page image failed: %s", type(exc).__name__)
            continue
        if png:
            work[number] = (items, size, png)

    def one(number: int) -> tuple[int, PagePlan | None, Exception | None]:
        items, size, png = work[number]
        try:
            return number, run_page(client, settings, items, size, png), None
        except (BudgetExceeded, ConfigError, UnknownModelError) as exc:
            return number, None, exc
        except Exception as exc:
            log.warning("page order failed: %s", type(exc).__name__)
            return number, None, None

    with ThreadPoolExecutor(max(1, concurrency), thread_name_prefix="pageorder") as pool:
        outcomes = list(pool.map(one, sorted(work)))
    made: list[ModelCall] = []
    for number, page_plan, error in outcomes:
        if isinstance(error, (ConfigError, UnknownModelError)):
            raise error
        if isinstance(error, BudgetExceeded):
            result.stopped = error
            made.extend(router.calls_made(error))  # a refused retry leaves the first call paid
            continue
        if page_plan is not None:
            plan.pages[number] = page_plan
    result.calls = plan.calls + made
    return result


# ------------------------------------------------------------------------------ hook


@dataclass
class HookStats:
    """What layout did with the answers, content-free (`layout.page._runs_by_answer` tells
    `PageOrderHook.applied` the counts of each page)."""

    applied_pages: int = 0
    stale_pages: int = 0
    runs: int = 0
    furniture: int = 0
    left_out: int = 0
    bad_join: int = 0


@dataclass
class PageOrderHook:
    """What `layout.analyze(page_order=hook)` calls for each page: with no plan it keeps the
    items of every page (the collector of the first layout pass) and answers None; with a plan
    it answers each page from its verified answer (the applier of the second pass)."""

    plan: OrderPlan | None = None
    items: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    stats: HookStats = field(default_factory=HookStats)

    def on_rows(self, number: int, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        if self.plan is None:
            self.items[number] = items  # (a page shown twice keeps the last time)
            return None
        page = self.plan.pages.get(number)
        if page is None or page.answer is None:
            return None
        if fingerprint(items) != page.fingerprint:
            self.stats.stale_pages += 1  # not the page the model was asked about
            return None
        return page.answer

    def applied(self, number: int, said: Mapping[str, int]) -> None:
        self.stats.applied_pages += 1
        self.stats.runs += said.get("runs", 0)
        self.stats.furniture += said.get("furniture", 0)
        self.stats.left_out += said.get("left_out", 0)
        self.stats.bad_join += said.get("bad_join", 0)
