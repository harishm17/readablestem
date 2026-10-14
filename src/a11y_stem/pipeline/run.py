"""The conversion pipeline end to end.

convert_pdf: layout.analyze -> per page: crops (serial, calling thread) -> equation/figure/
table steps (thread pool); once every crop is made and the steps are at work, still on the
calling thread: text_pass.apply and heading_fixes (they need no model, so they overlap the
model calls instead of delaying the first one) -> assemble.render_html ->
html_checks.static_checks.

Design notes:

- PyMuPDF is not thread-safe, so every crop is made on the calling thread. Model calls
  are I/O-bound, so the steps run in a `ThreadPoolExecutor(concurrency)`. Cropping page
  N+1 overlaps with the model calls for page N.
- One element's failure never sinks the document. A step that raises becomes a FAILED
  fix with reason "internal_error" (a crop that fails: "crop_error"). The exception
  message is not logged: it may quote document text.
- A misconfigured run is different: `config.ConfigError` (no API key; WP1's client reads
  it on its first call) or `pricing.UnknownModelError` (an unpriced model) would fail
  every element the same way, so the first one stops the run and is re-raised. The
  caller reports one clear error instead of a document with everything flagged.
- A `BudgetExceeded` from any step (or a step returning reason "budget_<scope>") stops
  new model work: queued tasks are cancelled and every element not yet processed
  becomes FAILED with reason "budget_<scope>". The document is still assembled, with
  the originals kept and flagged.
- Calls a step made before it raised were still billed, so each step gets a recording
  wrapper around the client and those calls are kept on the FAILED fix: cost and call
  counts stay true.
- Static checks of the HTML are stored on `DocumentResult.html_checks` and, as before,
  returned on `PipelineOutput.checks` (a subclass of `ConversionOutput`).
- Pages kept as images: layout says which pages are handwritten or scanned, or have
  something painted on them from which nothing was read (`Layout.kept_pages`,
  `pipeline.page_type`; hidden text is never read at all). Each becomes a figure element
  for the whole page that is FAILED before any model work, with the reason
  "handwritten_page", "scanned_page" or "unread_page" (cropped, so the output keeps the
  page image, marked "Not converted"); handwriting on a typed page arrives as figure
  elements for its regions and is FAILED the same way ("handwriting_on_page"). Nothing of
  them goes to a model. The fixes with the same reason are one decision (`Fix.group_key`
  "pg-..." and `group_size`), so a 20-page handwritten file asks once. The pages are
  listed on `DocumentResult.image_only_pages`, and every page's type on
  `DocumentResult.page_types` (content-free). The web app reports them to the user.
- Run-level outcomes are recorded on the result, content-free: `stopped_by` (the
  "budget_<scope>" that stopped model work early, else None) and `html_checks`.
- `layout` (WP2) keeps a few opened PDFs cached for cropping. Once the last crop is made
  (or the run fails) the document is released, so a long-lived worker does not keep a
  user's PDF in memory after its job.
- Tracer spans (document, headings, page, step, model_call, assemble) carry ids, page
  numbers, models, token counts, costs, statuses and reason codes, never document text.
  Attributes are scalars only (WP6 drops anything else); each model request gets its own
  `model_call` span opened with `model=`, which WP6 records as a Langfuse generation.
- Options (`ConversionOptions`: feature flags and the course's notation) are in force for
  the whole run (`pipeline.options.using`), so every step reads them with
  `options.current()`, pool threads included (each task runs in a copy of the context).
  They decide what the steps produce; the stored result alone decides the HTML.
- Content features (docs/design/content-features.md) hook in here: TABLE elements go to
  `table_step.rebuild` like equations and figures go to their steps; `stage_progress`
  receives `ProgressEvent`s; notices and source facts go on the result. The text pass
  (links, cross-references, the document and passage languages, colour runs) runs once
  after layout, before any step; it sets `lang`, `lang_source` and its own notices, and
  never fails the conversion.
- Live progress (`stage_progress`, for the job page): "layout" when analysis starts and
  again with the pages and the elements found per kind; "equations", "figures" and
  "tables" with how many of each are done, first with their totals and then after every
  element (the receiver decides how often to store them); "assemble" and "checks" as the
  HTML is written and checked. All on the calling thread, content-free.
- Finished steps can be kept and reused (`StepCache`, T3-1): with a cache, every model
  step's answer that is worth keeping (`reusable`: not a failure that a new try could
  change) is stored under a key made from the element, its crop, its nearby text, the
  options and the models (`step_key`), and a later run of the same document finds it
  there instead of calling the model again. The web app keeps them per job so that
  "Try again" on a stopped conversion pays only for what was not finished.
- After the steps, `pipeline.notices` derives the notices that follow from the fixes
  (a table kept as an image, a scanned page, a colour-only figure, a chart without a
  long description) and `pipeline.source_facts` measures the PDF for the before/after
  card. Neither can fail the run.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import threading
import time
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Callable, Generator, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Protocol

from pydantic import Field

from a11y_stem.config import ConfigError, Settings
from a11y_stem.core import triage
from a11y_stem.core.budget import BudgetExceeded
from a11y_stem.core.types import (
    BBox,
    Check,
    ConversionOptions,
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    Fix,
    Kind,
    ModelCall,
    Notice,
    PageStats,
    ProgressEvent,
    SourceFacts,
    Stage,
    Status,
    inline_marker,
)
from a11y_stem.models.base import T, VisionClient, VisionRequest
from a11y_stem.models.pricing import UnknownModelError
from a11y_stem.observability.tracing import NoopTracer, Tracer
from a11y_stem.pipeline import (
    assemble,
    equation_scale,
    equation_step,
    figure_step,
    heading_step,
    html_checks,
    layout,
    page_type,
    table_step,
    tagged,
    text_pass,
)
from a11y_stem.pipeline import notices as notice_rules
from a11y_stem.pipeline import options as run_options
from a11y_stem.pipeline import source_facts as pdf_facts

log = logging.getLogger(__name__)

# 0.3.0 (2026-10-08): review rules changed, a sign or relation that differs from the text
# layer is a required decision (`signs.compare_signs`). Results and eval reports from
# 0.2.0 were made under the old review policy.
# 0.4.0 (2026-10-09): the content-feature data model (docs/design/content-features.md):
# TABLE elements and fixes, figure long descriptions and data tables, links, colour
# runs, passage languages, notices and source facts on the result, conversion options.
# Results from 0.3.0 load unchanged (every new field has a default); a table detected by
# 0.4.0 is a required decision ("table_not_rebuilt") until rebuilding is switched on.
# 0.5.0 (2026-10-14): the Tier 1 and Tier 2 changes of the launch integration (CHANGELOG.md):
# layout and structure rules, the Claude second check, speech readings, review rules for
# equations as pictures and half fractions, and the model-cost settings. Results from 0.4.0
# load unchanged; their numbers were made under the old rules.
PIPELINE_VERSION = "0.5.0"

ProgressFn = Callable[[int, int], None]  # (pages_done, pages_total)
# Live progress by stage, for the job page (content-free; see `ProgressEvent`).
StageProgressFn = Callable[[ProgressEvent], None]

# Neighbouring text sent with each crop as untrusted context (the client fences it).
# List items are text too: a display equation inside a numbered solution step is
# introduced by that step, not by the paragraph before the list.
CONTEXT_CHARS = 600
_TEXT_KINDS = (Kind.PARAGRAPH, Kind.HEADING, Kind.LIST_ITEM)
_MODEL_KINDS = (Kind.EQUATION, Kind.FIGURE, Kind.TABLE)
# Errors that mean the run itself is misconfigured, not that one element is hard: they
# stop the run and propagate (see the module docstring).
FATAL_ERRORS: tuple[type[Exception], ...] = (ConfigError, UnknownModelError)
# Fix.reason of the figure that stands for a scanned page (nothing on it to convert).
SCANNED_PAGE_REASON = page_type.SCANNED_PAGE
# Fix reasons of what is kept as an image before any model work: a whole page, and a
# region of handwriting on a typed page (module docstring).
PAGE_REASONS = frozenset(
    {page_type.SCANNED_PAGE, page_type.HANDWRITTEN_PAGE, page_type.UNREAD_PAGE}
)
KEPT_REASONS = PAGE_REASONS | {page_type.HANDWRITING_ON_PAGE}
# `Fix.group_key` of the kept images with one reason: "pg-" + the first one's element id
# (content-free, like a heading group's "hg-...").
PAGE_GROUP_PREFIX = "pg-"
LETTER_BOX: BBox = (0.0, 0.0, 612.0, 792.0)  # a page whose size could not be read
# The live-progress stage of each kind that goes to a model step.
_STAGE_OF: dict[Kind, Stage] = {
    Kind.EQUATION: "equations",
    Kind.FIGURE: "figures",
    Kind.TABLE: "tables",
}


class PipelineOutput(ConversionOutput):
    """`ConversionOutput` plus the static checks of `result.html`.

    The same checks are on `result.html_checks` (stored with the result); this field is
    kept for callers written before that existed.
    """

    checks: list[Check] = Field(default_factory=list)


# --------------------------------------------------------------------------- helpers


class _RecordingClient:
    """Passes requests to the real client and keeps every `ModelCall` it returns.

    Each request also gets a `model_call` span. It is opened with `model=` (known from the
    request before anything is sent) so a WP6 tracer records it as a generation, and it
    is closed with the call's tokens, cost and outcome.
    """

    def __init__(
        self,
        inner: VisionClient,
        tracer: Tracer,
        observer: Callable[[ModelCall], None] | None = None,
    ) -> None:
        self._inner = inner
        self._tracer = tracer
        self._observer = observer
        self.calls: list[ModelCall] = []

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        with _span(
            self._tracer,
            "model_call",
            step=request.step,
            model=request.model,
            prompt_version=request.prompt_version,
            is_fallback=is_fallback,
        ) as span:
            parsed, call = self._inner.run(request, schema, is_fallback=is_fallback)
            self.calls.append(call)
            if self._observer is not None:
                self._observer(call)
            _annotate(
                span,
                model=call.model,
                outcome=call.outcome,
                attempt=call.attempt,
                input_tokens=call.input_tokens,
                cached_input_tokens=call.cached_input_tokens,
                output_tokens=call.output_tokens,
                reasoning_tokens=call.reasoning_tokens,
                cost_usd=call.cost_usd,
                latency_ms=call.latency_ms,
            )
        return parsed, call

    def __getattr__(self, name: str) -> Any:  # anything else a step may read
        return getattr(self._inner, name)


class _Stop:
    """Run-wide stop signals, set by workers and read by workers and the scheduler.

    `scope`: the first refused spend reservation; the rest of the run fails
    "budget_<scope>" and the document is still assembled. `fatal`: the first
    configuration error (`FATAL_ERRORS`); no further step runs and the run re-raises it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.scope: str | None = None
        self.fatal: Exception | None = None

    def trip(self, scope: str) -> None:
        with self._lock:
            if self.scope is None:
                self.scope = scope

    def abort(self, exc: Exception) -> None:
        with self._lock:
            if self.fatal is None:
                self.fatal = exc

    def reason(self) -> str:
        return f"budget_{self.scope or 'unknown'}"


def _failed(element: Element, reason: str, calls: Iterable[ModelCall] = ()) -> Fix:
    return Fix(
        element_id=element.id,
        kind=element.kind,
        status=Status.FAILED,
        reason=reason,
        calls=list(calls),
    )


class StepCache(Protocol):
    """Where finished steps are kept between runs of one document (module docstring).

    `lookup` returns the stored fix for this element when `key` is what it was stored
    under, else None; `store` keeps one. Both are called on the calling thread, and a
    cache must not raise: a failure to read or write is a miss, never a failed run."""

    def lookup(self, element: Element, key: str) -> Fix | None: ...

    def store(self, element: Element, key: str, fix: Fix) -> None: ...


# `Fix.reason` codes of a failure that trying again could change: the model did not answer
# or answered something unusable, or the step itself broke. Everything else a step ends
# with is its answer (a proposal, a refusal to propose, a check that failed).
_TRANSIENT_REASONS = frozenset({"internal_error", "crop_error"})
_TRANSIENT_PREFIXES = ("model_", "budget_")


def reusable(fix: Fix) -> bool:
    """Whether a later run may use this fix as it is instead of asking again."""
    if fix.status is not Status.FAILED:
        return True
    reason = fix.reason or ""
    return reason not in _TRANSIENT_REASONS and not reason.startswith(_TRANSIENT_PREFIXES)


def step_key(
    element: Element,
    png: bytes,
    context_text: str | None,
    options: ConversionOptions,
    settings: Settings,
) -> str:
    """What a step's answer depends on, as a digest: the pipeline version, the element
    (its text layer is checked against the answer), the picture, the nearby text sent with
    it, the conversion options (notation, exam mode, feature flags) and the models and
    checks in use. Equal keys mean the same question asked the same way."""
    parts = [
        PIPELINE_VERSION,
        element.model_dump_json(exclude={"id", "order"}),
        hashlib.sha256(png).hexdigest(),
        context_text or "",
        options.model_dump_json(),
        "|".join(
            str(getattr(settings, name, ""))
            for name in (
                "model_provider",
                "main_model",
                "cheap_model",
                "decisions_model",
                "decisions_figure_verify",
                "decisions_equation_shadow",
                "claude_check_model",
                "claude_check_scope",
            )
        ),
    ]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _budget_scope(fix: Fix) -> str | None:
    """Scope if a step reported a refused reservation itself instead of raising."""
    reason = fix.reason or ""
    if fix.status is Status.FAILED and reason.startswith("budget_"):
        return reason.removeprefix("budget_") or "unknown"
    return None


_SCALARS = (str, int, float, bool)


def _annotate(span: Any, **attrs: Any) -> None:
    """Add result attributes to an open span. Never raises.

    WP6's span handle takes `set(**attrs)`; `update(**attrs)` is accepted too (simpler
    tracers). Only scalar values are sent and None is left out: WP6 keeps numbers,
    booleans and short code-like strings and silently drops anything else, so a dict or
    list here would just disappear.
    """
    method = getattr(span, "set", None)
    if not callable(method):
        method = getattr(span, "update", None)
    if not callable(method):
        return
    values = {key: value for key, value in attrs.items() if isinstance(value, _SCALARS)}
    if not values:
        return
    try:
        method(**values)
    except Exception:  # tracing must never break a conversion
        log.debug("span update failed", exc_info=False)


def _status_counts(prefix: str, fixes: Iterable[Fix]) -> dict[str, int]:
    """{"<prefix>_accepted": 3, ...}: per-status counts as flat scalar span attributes."""
    counts = Counter(fix.status.value for fix in fixes)
    return {f"{prefix}_{status}": n for status, n in sorted(counts.items())}


def _single_model(calls: Iterable[ModelCall]) -> str | None:
    """The model a step used, if it used exactly one (each call has its own span)."""
    models = {call.model for call in calls}
    return models.pop() if len(models) == 1 else None


@contextmanager
def _span(tracer: Tracer, name: str, **attrs: Any) -> Generator[Any]:
    """`tracer.span`, except that tracing never changes the outcome of the work.

    A tracer that fails to open or close a span is logged at debug level and ignored;
    an exception from the work itself always propagates (a tracer cannot swallow it).
    """
    try:
        manager = tracer.span(name, **attrs)
        span = manager.__enter__()
    except Exception:
        log.debug("tracer could not open a %s span", name)
        yield None
        return
    try:
        yield span
    except BaseException as exc:
        try:
            manager.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            log.debug("tracer could not close a %s span", name)
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            log.debug("tracer could not close a %s span", name)


# --------------------------------------------------------------------------- context


def _without_markers(text: str) -> str:
    """Document text for the model: our inline markers become a readable placeholder."""
    return assemble.INLINE_MARKER_RE.sub("[equation]", text)


def _head(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " ..."


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "... " + text[-limit:].lstrip()


def _inline_context(element: Element, parent: Element) -> str | None:
    """A window of the parent paragraph centred on this equation's marker."""
    text = parent.text or ""
    marker = inline_marker(element.id)
    at = text.find(marker)
    if at == -1:
        return None
    half = CONTEXT_CHARS // 2
    before = _without_markers(text[:at])
    after = _without_markers(text[at + len(marker) :])
    if not (before.strip() or after.strip()):
        return None
    return f"{_tail(before, half)}[this equation]{_head(after, half)}".strip()


_CONTEXT_LABELS = {Kind.FIGURE: "[this figure]", Kind.TABLE: "[this table]"}


def _neighbour_context(element: Element, before: str | None, after: str | None) -> str | None:
    half = CONTEXT_CHARS // 2
    label = _CONTEXT_LABELS.get(element.kind, "[this equation]")
    parts = []
    if before:
        parts.append(_tail(_without_markers(before), half))
    parts.append(label)
    if after:
        parts.append(_head(_without_markers(after), half))
    return "\n".join(parts) if (before or after) else None


def _first_text(elements: list[Element], indexes: Iterable[int], skip: str | None) -> str | None:
    """Text of the first element at `indexes` that is not `skip` (the parent paragraph)."""
    return next((elements[i].text for i in indexes if elements[i].id != skip), None)


def context_texts(elements: list[Element]) -> dict[str, str | None]:
    """Untrusted neighbouring text for each equation and figure (reading order).

    Inline equations get a window of their own paragraph around their marker. Block
    equations and figures (and an inline equation alone in its paragraph) get the end
    of the previous text element and the start of the next one, ~CONTEXT_CHARS in
    total. Inline markers become "[equation]".
    """
    by_id = {e.id: e for e in elements}
    text_at = [i for i, e in enumerate(elements) if e.kind in _TEXT_KINDS and e.text]
    result: dict[str, str | None] = {}
    for index, element in enumerate(elements):
        if element.kind not in _MODEL_KINDS:
            continue
        parent = by_id.get(element.parent_id or "")
        if parent is not None and parent.text and inline_marker(element.id) in parent.text:
            context = _inline_context(element, parent)
            if context is not None:
                result[element.id] = context
                continue
        skip = parent.id if parent is not None else None
        pos = bisect_left(text_at, index)  # first text element after this one
        before = _first_text(elements, (text_at[i] for i in range(pos - 1, -1, -1)), skip)
        after = _first_text(elements, (text_at[i] for i in range(pos, len(text_at))), skip)
        result[element.id] = _neighbour_context(element, before, after)
    return result


# --------------------------------------------------------------------------- steps


@dataclass(frozen=True)
class _Task:
    element: Element
    png: bytes
    context_text: str | None


@dataclass(frozen=True)
class _Done:
    element: Element
    fix: Fix
    elapsed_ms: int


def _step_for(kind: Kind) -> tuple[str, Callable[..., Fix]]:
    # Looked up at call time so tests (and the eval harness) can swap a step module's
    # function without touching this one.
    if kind is Kind.EQUATION:
        return "equation", equation_step.transcribe
    if kind is Kind.TABLE:
        return "table", table_step.rebuild
    return "figure", figure_step.describe


def _validated(fix: object, element: Element, calls: list[ModelCall]) -> Fix:
    if not isinstance(fix, Fix) or fix.element_id != element.id or fix.kind != element.kind:
        log.warning("step returned a fix for another element (kind=%s)", element.kind.value)
        return _failed(element, "internal_error", calls)
    if len(calls) > len(fix.calls):  # keep every billed call, even ones a step dropped
        return fix.model_copy(update={"calls": calls})
    return fix


def _run_step(
    task: _Task,
    *,
    client: VisionClient,
    settings: Settings,
    stop: _Stop,
    tracer: Tracer,
    observer: Callable[[ModelCall], None] | None = None,
) -> _Done:
    """Worker body: one element through its step.

    Raises only `FATAL_ERRORS` (after setting `stop.fatal`) and BaseException; any other
    exception becomes a FAILED fix.
    """
    element = task.element
    if stop.fatal is not None:  # the run is failing; this result is never used
        return _Done(element, _failed(element, "internal_error"), 0)
    if stop.scope is not None:  # budget gone while this task was queued
        return _Done(element, _failed(element, stop.reason()), 0)
    step_name, step = _step_for(element.kind)
    recorder = _RecordingClient(client, tracer, observer)
    started = time.perf_counter()
    with _span(
        tracer,
        "step",
        step=step_name,
        kind=element.kind.value,
        element_id=element.id,
        page=element.page,
    ) as span:
        try:
            fix = step(
                element,
                task.png,
                client=recorder,
                settings=settings,
                context_text=task.context_text,
            )
        except BudgetExceeded as exc:
            stop.trip(exc.scope)
            fix = _failed(element, f"budget_{exc.scope}", recorder.calls)
        except FATAL_ERRORS as exc:  # misconfigured: every element would fail the same way
            log.warning("%s step stopped the run: %s", step_name, type(exc).__name__)
            stop.abort(exc)
            raise
        except Exception as exc:  # isolate: one element never sinks the document
            log.warning("%s step failed: %s", step_name, type(exc).__name__)
            fix = _failed(element, "internal_error", recorder.calls)
        else:
            fix = _validated(fix, element, recorder.calls)
            if (scope := _budget_scope(fix)) is not None:
                stop.trip(scope)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        _annotate(
            span,
            status=fix.status.value,
            reason=fix.reason,
            model=_single_model(fix.calls),
            calls=len(fix.calls),
            cost_usd=fix.cost_usd,
            latency_ms=elapsed_ms,
        )
    return _Done(element, fix, elapsed_ms)


def _text_pass(
    pdf_bytes: bytes,
    elements: list[Element],
    pages: list[int],
    layout_lang: str,
    options: ConversionOptions,
    tracer: Tracer,
) -> text_pass.TextPassResult:
    """Links, cross-references, language and (with FEATURE_COLOUR) colour runs on the
    elements, before any step (`text_pass.apply`; it never raises)."""
    with _span(tracer, "text", elements=len(elements), colour=options.colour) as span:
        text = text_pass.apply(
            pdf_bytes, elements, pages=pages, layout_lang=layout_lang, colour=options.colour
        )
        _annotate(span, lang_source=text.lang_source, **text.counts())
    return text


def _heading_fixes(elements: list[Element], tracer: Tracer) -> list[Fix]:
    headings = [e for e in elements if e.kind is Kind.HEADING]
    with _span(tracer, "headings", count=len(headings)) as span:
        try:
            fixes = list(heading_step.heading_fixes(elements))
        except Exception as exc:
            log.warning("heading step failed: %s", type(exc).__name__)
            fixes = [_failed(e, "internal_error") for e in headings]
        _annotate(span, fixes=len(fixes), **_status_counts("fixes", fixes))
    return fixes


# --------------------------------------------------------------------------- scheduling


class _Pages:
    """Tracks outstanding tasks per page; reports a page once all of its tasks are done."""

    def __init__(self, pages: list[int], progress: ProgressFn | None) -> None:
        self.total = len(pages)
        self.done = 0
        self._pending: dict[int, int] = dict.fromkeys(pages, 0)
        self._open: set[int] = set(pages)  # still being cropped/submitted
        self._progress = progress

    def add(self, page: int) -> None:
        self._pending[page] = self._pending.get(page, 0) + 1

    def submitted(self, page: int) -> None:
        self._open.discard(page)
        self._maybe_done(page)

    def finished(self, page: int) -> None:
        self._pending[page] -= 1
        self._maybe_done(page)

    def _maybe_done(self, page: int) -> None:
        if page in self._open or self._pending.get(page, 0) or page not in self._pending:
            return
        del self._pending[page]
        self.done += 1
        if self._progress is not None:
            self._progress(self.done, self.total)


def _send(
    stage_progress: StageProgressFn | None,
    stage: Stage,
    done: int = 0,
    total: int = 0,
    counts: dict[str, int] | None = None,
) -> None:
    """One `ProgressEvent` to the caller, if it asked for them. Like `progress`, an
    exception from the receiver aborts the run."""
    if stage_progress is not None:
        stage_progress(ProgressEvent(stage=stage, done=done, total=total, counts=counts or {}))


def _kind_counts(elements: Iterable[Element]) -> dict[str, int]:
    """Elements found per kind ("equation": 21), for the "layout" event."""
    return dict(sorted(Counter(e.kind.value for e in elements).items()))


def _unit_scale(_element: Element) -> float:
    return 1.0


def recrop(pdf: bytes, elements: list[Element]) -> dict[str, bytes]:
    """The pictures a conversion of `pdf` cuts for these elements, without converting
    anything: the crop of every equation, figure and table, an equation's cut sharp for the
    size it is shown at (T3-15), exactly as `_Run.crop_and_submit` does. For showing a
    stored result again (the sample document, the demo): no model is asked and nothing is
    paid. An element whose crop fails has none."""
    scale = equation_scale.scaler(elements)
    crops: dict[str, bytes] = {}
    try:
        for element in elements:
            if element.kind not in _MODEL_KINDS:
                continue
            try:
                png = layout.crop(pdf, element)
                if element.kind is Kind.EQUATION:
                    png = layout.crop(
                        pdf, element, dpi=equation_scale.crop_dpi(scale(element)), tag_dpi=True
                    )
            except Exception as exc:
                log.warning("crop failed: %s", type(exc).__name__)
                continue
            crops[element.id] = png
    finally:
        layout.release(pdf)  # the cached copy of a PDF is not kept
    return crops


@dataclass
class _Run:
    """State of one conversion while its steps are in flight."""

    pdf_bytes: bytes
    settings: Settings
    options: ConversionOptions
    stage_progress: StageProgressFn | None  # live progress by stage, for the job page
    client: VisionClient
    tracer: Tracer
    pages: _Pages
    contexts: dict[str, str | None]
    pool: ThreadPoolExecutor
    stop: _Stop
    fixes: dict[str, Fix]
    crops: dict[str, bytes]
    page_ms: Counter[int]
    futures: dict[Future[_Done], Element]
    # Per model kind: how many elements it has, and the ids that have their fix.
    stage_totals: dict[Kind, int] = field(default_factory=dict)
    stage_done: dict[Kind, set[str]] = field(default_factory=dict)
    # Finished steps kept between runs (T3-1), and the key each element's step was asked under.
    cache: StepCache | None = None
    keys: dict[str, str] = field(default_factory=dict)
    # Told how each model request ended (`ModelCall`), for the status line about the AI service.
    observer: Callable[[ModelCall], None] | None = None
    # The scale an equation's picture is shown at in the student file (T3-15).
    eq_scale: Callable[[Element], float] = field(default_factory=lambda: _unit_scale)

    def start_stages(self, elements: list[Element]) -> None:
        """Each model stage's total, counting what was decided before any model work (a
        scanned page) as done."""
        self.eq_scale = equation_scale.scaler(elements)
        for kind, stage in _STAGE_OF.items():
            members = [e.id for e in elements if e.kind is kind]
            self.stage_totals[kind] = len(members)
            self.stage_done[kind] = {i for i in members if i in self.fixes}
            if members:
                _send(self.stage_progress, stage, len(self.stage_done[kind]), len(members))

    def step_done(self, element: Element) -> None:
        """Count one equation, figure or table as done and say so."""
        stage = _STAGE_OF.get(element.kind)
        done = self.stage_done.get(element.kind)
        if stage is None or done is None or element.id in done:
            return
        done.add(element.id)
        _send(self.stage_progress, stage, len(done), self.stage_totals.get(element.kind, 0))

    def process_page(self, page: int, elements: list[Element]) -> None:
        """Crop this page's equations, figures and tables here (PyMuPDF), queue their steps."""
        work = [e for e in elements if e.kind in _MODEL_KINDS]
        with _span(self.tracer, "page", page=page, elements=len(elements), tasks=len(work)):
            for element in work:
                self.crop_and_submit(element)
        self.pages.submitted(page)

    def sharp_equation(self, element: Element) -> bytes | None:
        """An equation's crop for the student file, or None (not an equation, or the cut
        failed: the ordinary crop is used then)."""
        if element.kind is not Kind.EQUATION:
            return None
        try:
            return layout.crop(
                self.pdf_bytes,
                element,
                dpi=equation_scale.crop_dpi(self.eq_scale(element)),
                tag_dpi=True,
            )
        except Exception as exc:
            log.warning("sharp crop failed: %s", type(exc).__name__)
            return None

    def crop_and_submit(self, element: Element) -> None:
        self.raise_if_fatal()
        try:
            png = layout.crop(self.pdf_bytes, element)
        except Exception as exc:
            log.warning("crop failed: %s", type(exc).__name__)
            self.fixes[element.id] = _failed(element, "crop_error")
            self.step_done(element)
            return
        # The student file shows an unconverted equation at the size of its text, cut
        # sharp for that size (T3-15); the model is sent the same crop as ever, and the
        # review screen shows this one. Any other crop is as it was.
        self.crops[element.id] = self.sharp_equation(element) or png
        if element.id in self.fixes:  # decided before any model work: a scanned page
            return
        if self.stop.scope is not None:  # still cropped: the output shows the original
            self.fixes[element.id] = _failed(element, self.stop.reason())
            self.step_done(element)
            return
        self.raise_if_fatal()  # a step may have failed while this element was being cut
        if self.cache is not None:
            key = step_key(element, png, self.contexts.get(element.id), self.options, self.settings)
            if (kept := self.cache.lookup(element, key)) is not None:
                self.fixes[element.id] = kept  # finished in an earlier run: no model call
                self.step_done(element)
                return
            self.keys[element.id] = key
        work = partial(
            _run_step,
            _Task(element, png, self.contexts.get(element.id)),
            client=self.client,
            settings=self.settings,
            stop=self.stop,
            tracer=self.tracer,
            observer=self.observer,
        )
        # Each task runs in a copy of this context, so a contextvars-based tracer nests the
        # step span under this page's span even though it runs on a pool thread.
        context = contextvars.copy_context()
        future: Future[_Done] = self.pool.submit(lambda: context.run(work))
        self.futures[future] = element
        self.pages.add(element.page)

    def collect(self, *, block: bool) -> None:
        """Record finished tasks; cancel the queue once the budget is gone."""
        while self.futures:
            done, _ = wait(
                list(self.futures), timeout=None if block else 0, return_when=FIRST_COMPLETED
            )
            if not done:
                return
            for future in done:
                self.record(future, self.futures.pop(future))
            self.raise_if_fatal()
            if self.stop.scope is not None:
                for future in self.futures:
                    future.cancel()

    def raise_if_fatal(self) -> None:
        """Re-raise a worker's configuration error here (the pool is then shut down)."""
        if self.stop.fatal is not None:
            raise self.stop.fatal

    def record(self, future: Future[_Done], element: Element) -> None:
        if future.cancelled():
            self.fixes[element.id] = _failed(element, self.stop.reason())
        else:
            done = future.result()  # re-raises a FATAL_ERRORS from the worker
            self.fixes[element.id] = done.fix
            self.page_ms[element.page] += done.elapsed_ms
            if self.cache is not None and (key := self.keys.get(element.id)) and reusable(done.fix):
                self.cache.store(element, key, done.fix)
        self.pages.finished(element.page)
        self.step_done(element)


# --------------------------------------------------------------------------- results


def _ordered_fixes(elements: list[Element], fixes: dict[str, Fix]) -> list[Fix]:
    ids = [e.id for e in elements]
    known = set(ids)
    return [fixes[i] for i in ids if i in fixes] + [f for i, f in fixes.items() if i not in known]


def _stats(
    pages: list[int],
    elements: list[Element],
    fixes: list[Fix],
    page_ms: Counter[int],
    wall_ms: int,
) -> DocumentStats:
    """Totals plus per-page numbers.

    `latency_ms` on the document is wall-clock time for the whole conversion. On a page
    it is the time spent inside that page's steps (model calls, retries, validation),
    summed: pages overlap in the thread pool, so per-page wall time would mislead.
    """
    page_of = {e.id: e.page for e in elements}
    per_page = []
    for page in pages:
        page_fixes = [f for f in fixes if page_of.get(f.element_id) == page]
        per_page.append(
            PageStats(
                page=page,
                elements=sum(1 for e in elements if e.page == page),
                fixes=len(page_fixes),
                cost_usd=round(sum(f.cost_usd for f in page_fixes), 8),
                latency_ms=page_ms[page],
            )
        )
    return DocumentStats(
        pages=len(pages),
        cost_usd=round(sum(f.cost_usd for f in fixes), 8),
        latency_ms=wall_ms,
        model_calls=sum(len(f.calls) for f in fixes),
        by_kind=dict(Counter(f.kind.value for f in fixes)),
        by_status=dict(Counter(f.status.value for f in fixes)),
        by_review=dict(Counter(f.review for f in fixes)),
        required_decisions=triage.required_decisions(fixes),
        per_page=per_page,
    )


def _prompt_versions(fixes: list[Fix]) -> dict[str, str]:
    """Declared step prompt versions, overridden by the versions actually sent."""
    versions: dict[str, str] = {}
    for step, module in (
        ("equation", equation_step),
        ("figure", figure_step),
        ("table", table_step),
    ):
        declared = getattr(module, "PROMPT_VERSION", None)
        if isinstance(declared, str) and declared:
            versions[step] = declared
    used: defaultdict[str, set[str]] = defaultdict(set)
    for fix in fixes:
        for call in fix.calls:
            used[call.step].add(call.prompt_version)
    for step, seen in used.items():
        versions[step] = ",".join(sorted(seen))
    return versions


# --------------------------------------------------------------------------- kept pages


def _page_boxes(
    pdf_bytes: bytes, pages: Iterable[int], sizes: dict[int, tuple[float, float]]
) -> dict[int, BBox]:
    """Each page's box in layout's frame, from the sizes layout measured; a page it did
    not measure (a layout stand-in in tests) is read from the PDF. PyMuPDF is not
    thread-safe: hold layout's lock (when it has one) like every other PyMuPDF call."""
    pages = list(pages)
    boxes: dict[int, BBox] = {p: (0.0, 0.0, *sizes[p]) for p in pages if p in sizes}
    missing = [p for p in pages if p not in boxes]
    if not missing:
        return boxes
    import pymupdf

    lock = getattr(layout, "_LOCK", None)
    with lock if lock is not None else contextlib.nullcontext():
        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            try:
                for number in missing:
                    rect = doc[number].rect
                    boxes[number] = (rect.x0, rect.y0, rect.x1, rect.y1)
            finally:
                doc.close()
        except Exception as exc:  # a size is never worth failing the conversion for
            log.warning("page size unreadable: %s", type(exc).__name__)
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()  # the warning store can quote the PDF
    return {p: boxes.get(p, LETTER_BOX) for p in pages}


def _next_id(page: int, elements: list[Element]) -> str:
    """A free "p{page+1}-e{index}" id on this page (one past the highest index used)."""
    prefix = f"p{page + 1}-e"
    used = [
        int(e.id.removeprefix(prefix))
        for e in elements
        if e.id.startswith(prefix) and e.id.removeprefix(prefix).isdecimal()
    ]
    return f"{prefix}{max(used) + 1 if used else 0}"


def _with_page_images(
    elements: list[Element], kept: dict[int, str], boxes: dict[int, BBox]
) -> tuple[list[Element], list[Fix]]:
    """`elements` plus one figure per kept page (placed after the page's other elements,
    orders renumbered), and the FAILED fixes that keep them, and any handwriting region
    layout cut out of a typed page, from the model. Fixes with the same reason share a
    group key: one decision for all of them."""
    added: list[Element] = []
    result: list[Element] = []
    pending = sorted(kept)
    for element in elements:
        while pending and pending[0] < element.page:
            added.append(_page_image(pending.pop(0), kept, boxes, elements))
            result.append(added[-1])
        result.append(element)
    for page in pending:
        added.append(_page_image(page, kept, boxes, elements))
        result.append(added[-1])
    renumbered = [e.model_copy(update={"order": i}) for i, e in enumerate(result)]
    fixes = [_failed(e, e.reasons[0]) for e in added]
    fixes += [
        _failed(e, page_type.HANDWRITING_ON_PAGE)
        for e in renumbered
        if e.kind is Kind.FIGURE and page_type.HANDWRITING_REGION in e.reasons
    ]
    order = {e.id: e.order for e in renumbered}
    return renumbered, _grouped(sorted(fixes, key=lambda f: order.get(f.element_id, 0)))


def _page_image(
    page: int, kept: dict[int, str], boxes: dict[int, BBox], elements: list[Element]
) -> Element:
    return Element(
        id=_next_id(page, elements),
        kind=Kind.FIGURE,
        page=page,
        bbox=boxes[page],
        order=0,  # renumbered by the caller
        reasons=[kept[page]],
    )


def _grouped(fixes: list[Fix]) -> list[Fix]:
    """Kept images with the same reason as one decision: "pg-" + the first one's element
    id, and how many there are (one page alone is a group of one, so the review screen
    shows every kept page the same way)."""
    by_reason: defaultdict[str, list[Fix]] = defaultdict(list)
    for fix in fixes:
        by_reason[fix.reason or ""].append(fix)
    for members in by_reason.values():
        key = PAGE_GROUP_PREFIX + members[0].element_id
        for fix in members:
            fix.group_key, fix.group_size = key, len(members)
    return fixes


def _source_facts(
    pdf_bytes: bytes, pages: list[int], elements: list[Element], fixes: list[Fix]
) -> SourceFacts | None:
    """`source_facts.measure`, which never raises; None if it did anyway (a bug there
    must not cost the user a finished conversion)."""
    try:
        return pdf_facts.measure(pdf_bytes, pages=pages, elements=elements, fixes=fixes)
    except Exception as exc:
        log.warning("source facts failed: %s", type(exc).__name__)
        return None


# --------------------------------------------------------------------------- entry point


class _CachedDocument:
    """Releases `layout`'s cached copy of one PDF, once.

    WP2's `layout` keeps a few opened documents (and their bytes) so crops do not reopen
    the file, and asks the pipeline to call `release(pdf_bytes)` when a document is done
    so user content does not stay in a long-lived worker's memory. Looked up at call
    time: a `layout` without `release` (the stub, a test fake) is fine.
    """

    def __init__(self, pdf_bytes: bytes) -> None:
        self._pdf_bytes = pdf_bytes
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        release = getattr(layout, "release", None)
        if not callable(release):
            return
        try:
            release(self._pdf_bytes)
        except Exception as exc:  # a cache problem must not fail the conversion
            log.warning("layout.release failed: %s", type(exc).__name__)


def _process(
    run: _Run,
    elements: list[Element],
    page_list: list[int],
    cached: _CachedDocument,
    while_waiting: Callable[[], None] | None = None,
) -> None:
    """Crop page by page on this thread, run the steps in the pool, collect every result.

    `while_waiting` runs once every crop is made and the steps are at work: the text pass
    and the heading decisions, which no step needs, are done while the models answer."""
    by_page: defaultdict[int, list[Element]] = defaultdict(list)
    for element in elements:
        by_page[element.page].append(element)
    try:
        run.start_stages(elements)
        for page in page_list:
            run.process_page(page, by_page[page])
            run.collect(block=False)  # report finished pages while cropping the rest
        cached.release()  # every crop is made; the steps only need the PNGs
        if while_waiting is not None:
            while_waiting()
        run.collect(block=True)
    finally:
        run.pool.shutdown(wait=True, cancel_futures=True)


def convert_pdf(
    pdf_bytes: bytes,
    *,
    settings: Settings,
    client: VisionClient,
    pages: list[int] | None = None,
    progress: ProgressFn | None = None,
    tracer: Tracer | None = None,
    concurrency: int = 4,
    notice: str | None = None,
    source_note: str | None = None,
    options: ConversionOptions | None = None,
    stage_progress: StageProgressFn | None = None,
    step_cache: StepCache | None = None,
    call_observer: Callable[[ModelCall], None] | None = None,
) -> ConversionOutput:
    """Convert one PDF (0-based `pages`, all by default) into a `PipelineOutput`.

    Raises `layout.LayoutError` when the PDF cannot be read (or has more than
    `settings.max_pages_per_job` pages), and `config.ConfigError` or
    `pricing.UnknownModelError` when the model client is misconfigured (no API key, an
    unpriced model): the first one stops the run, since every element would fail the
    same way. Nothing else from the steps escapes. Calls `progress(pages_done,
    pages_total)` on the calling thread each time a page's steps have all finished. An
    exception from `progress` aborts the run (queued steps are cancelled, running ones
    finish) and propagates.

    `notice` is plain text shown at the top of the HTML (the CLI uses it to mark an
    offline test run); `source_note` is plain text for its "About this conversion"
    section, e.g. an OpenStax attribution.

    `options` (feature flags, the course's notation) are in force for the whole run, for
    every step to read with `pipeline.options.current()`; None means
    `ConversionOptions.from_settings(settings)`. `stage_progress` receives
    `ProgressEvent`s on the calling thread as each stage starts and moves on (see the
    module docstring); like `progress`, an exception from it aborts the run.

    `step_cache` keeps the answers of finished model steps and gives them back to a later
    run of the same document (module docstring); without one every step is asked.
    `call_observer` is told each model request's `ModelCall` when it ends (any thread).
    """
    if options is None:
        options = ConversionOptions.from_settings(settings)
    with run_options.using(options):
        return _convert(
            pdf_bytes,
            settings=settings,
            client=client,
            pages=pages,
            progress=progress,
            tracer=tracer,
            concurrency=concurrency,
            notice=notice,
            source_note=source_note,
            options=options,
            stage_progress=stage_progress,
            step_cache=step_cache,
            call_observer=call_observer,
        )


def _convert(
    pdf_bytes: bytes,
    *,
    settings: Settings,
    client: VisionClient,
    pages: list[int] | None,
    progress: ProgressFn | None,
    tracer: Tracer | None,
    concurrency: int,
    notice: str | None,
    source_note: str | None,
    options: ConversionOptions,
    stage_progress: StageProgressFn | None,
    step_cache: StepCache | None = None,
    call_observer: Callable[[ModelCall], None] | None = None,
) -> ConversionOutput:
    """`convert_pdf`'s body, with its options already in force."""
    tracer = tracer or NoopTracer()
    started = time.perf_counter()
    with _span(tracer, "document", pipeline_version=PIPELINE_VERSION) as doc_span:
        cached = _CachedDocument(pdf_bytes)
        try:
            _send(stage_progress, "layout")
            doc = layout.analyze(pdf_bytes, pages=pages, max_pages=settings.max_pages_per_job)
            elements = sorted(doc.elements, key=lambda e: e.order)
            page_list = list(dict.fromkeys([*doc.pages, *(e.page for e in elements)]))
            kept: dict[int, str] = dict(getattr(doc, "kept_pages", None) or {})
            sizes = dict(zip(doc.pages, getattr(doc, "page_sizes", None) or [], strict=False))
            boxes = _page_boxes(pdf_bytes, kept, sizes)
            elements, scan_fixes = _with_page_images(elements, kept, boxes)
            # Tagged-PDF structure (headings, lists, tables the author marked up) is applied
            # before the steps start: they read the elements it settles. The text pass and
            # the heading decisions run later, beside the steps.
            elements = tagged.apply(elements, tagged.read(pdf_bytes, page_list))
            read = len(page_list)
            _send(stage_progress, "layout", read, read, _kind_counts(elements))
            fixes = {f.element_id: f for f in scan_fixes}
            run = _Run(
                pdf_bytes=pdf_bytes,
                settings=settings,
                options=options,
                stage_progress=stage_progress,
                client=client,
                tracer=tracer,
                pages=_Pages(page_list, progress),
                contexts=context_texts(elements),
                pool=ThreadPoolExecutor(max_workers=max(1, concurrency), thread_name_prefix="step"),
                stop=_Stop(),
                fixes=fixes,
                crops={},
                page_ms=Counter(),
                futures={},
                cache=step_cache,
                observer=call_observer,
            )
            # The text pass adds links, colour runs and passage languages to the elements
            # without touching their text or anything a step reads, and the heading
            # decisions need no model: both are done once the steps are at work, not
            # before the first one starts (they were 2-3% of a job's wall time).
            made: list[text_pass.TextPassResult] = []

            def text_and_headings() -> None:
                made.append(_text_pass(pdf_bytes, elements, page_list, doc.lang, options, tracer))
                for fix in _heading_fixes(made[0].elements, tracer):
                    run.fixes.setdefault(fix.element_id, fix)  # a kept page's fix wins

            _process(run, elements, page_list, cached, text_and_headings)
            text = made[0]
            elements = text.elements
        finally:
            cached.release()  # also when analysis, a step or `progress` failed

        ordered = _ordered_fixes(elements, run.fixes)
        source_facts = _source_facts(pdf_bytes, page_list, elements, ordered)
        with _span(tracer, "assemble", elements=len(elements), fixes=len(ordered)):
            _send(stage_progress, "assemble")
            html = assemble.render_html(
                title=doc.title,
                lang=text.lang,
                elements=elements,
                fixes=ordered,
                crops=run.crops,
                source_note=source_note,
                notice=notice,
                math_alttext=settings.mathml_alttext,
            )
            _send(stage_progress, "checks")
            checks = html_checks.static_checks(html)
        wall_ms = round((time.perf_counter() - started) * 1000)
        stats = _stats(page_list, elements, ordered, run.page_ms, wall_ms)
        _annotate(
            doc_span,
            pages=stats.pages,
            fixes=len(ordered),
            model_calls=stats.model_calls,
            cost_usd=stats.cost_usd,
            latency_ms=stats.latency_ms,
            reason=run.stop.reason() if run.stop.scope is not None else None,
            image_only_pages=len(kept),
            checks_failed=sum(1 for c in checks if not c.passed),
            **_status_counts("fixes", ordered),
        )
    log.info(
        "convert_pdf done: pages=%d fixes=%d calls=%d cost_usd=%.4f latency_ms=%d",
        stats.pages,
        len(ordered),
        stats.model_calls,
        stats.cost_usd,
        stats.latency_ms,
    )
    # Content-free additions of the content features (docs/design/content-features.md):
    # each feature adds its notices here (the text pass's first); those that follow from
    # the fixes are derived once, then everything is merged (one notice per code and
    # element, or code and page) and ordered by page and reading order.
    notices: list[Notice] = [*text.notices, *doc.notices]
    derived = notice_rules.from_fixes(
        elements, ordered, long_descriptions=options.long_descriptions
    )
    notices = notice_rules.ordered(notice_rules.merge(derived, notices), elements)
    result = DocumentResult(
        pipeline_version=PIPELINE_VERSION,
        prompt_versions=_prompt_versions(ordered),
        title=doc.title,
        lang=text.lang,
        elements=elements,
        fixes=ordered,
        stats=stats,
        html=html,
        stopped_by=run.stop.reason() if run.stop.scope is not None else None,
        image_only_pages=sorted(kept),
        html_checks=checks,
        page_sizes=dict(zip(doc.pages, doc.page_sizes, strict=False)),
        notices=notices,
        source_facts=source_facts,
        lang_source=text.lang_source,
        page_types=[
            kind.to_record(page)
            for page, kind in sorted((getattr(doc, "page_types", None) or {}).items())
        ],
    )
    return PipelineOutput(result=result, crops=run.crops, checks=checks)
